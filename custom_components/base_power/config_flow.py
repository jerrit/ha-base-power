"""Config flow for Base Power integration."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL

from .const import (
    DOMAIN,
    CONF_CLIENT_TOKEN,
    CONF_SESSION_ID,
    CONF_SERVICE_LOCATION_ID,
)
from .auth import BasePowerAuth, AuthenticationError
from .api import BasePowerApiClient

_LOGGER = logging.getLogger(__name__)

CLERK_PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"


class BasePowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Base Power."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._email: str = ""
        self._sign_in_id: str = ""
        self._email_id: str = ""
        self._client_token: str = ""
        self._session_id: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Get email address and initiate sign-in."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]

            try:
                async with aiohttp.ClientSession() as session:
                    result = await BasePowerAuth.async_initiate_sign_in(
                        session, self._email, CLERK_PUBLISHABLE_KEY
                    )
                    self._sign_in_id = result["sign_in_id"]
                    self._email_id = result["email_id"]
                    self._client_token = result["client_token"]

                    if not self._client_token:
                        _LOGGER.error("No client token received from Clerk")
                        errors["base"] = "auth_failed"
                    else:
                        # Send OTP code
                        await BasePowerAuth.async_prepare_first_factor(
                            session,
                            self._sign_in_id,
                            self._email_id,
                            self._client_token,
                        )
                        return await self.async_step_verify_code()
            except AuthenticationError as err:
                _LOGGER.error("Authentication error: %s", err)
                errors["base"] = "auth_failed"
            except Exception:
                _LOGGER.exception("Unexpected error during sign-in")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def async_step_verify_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Verify the OTP code from email."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"]

            try:
                async with aiohttp.ClientSession() as session:
                    result = await BasePowerAuth.async_attempt_first_factor(
                        session,
                        self._sign_in_id,
                        code,
                        self._client_token,
                    )
                    self._session_id = result["session_id"]
                    self._client_token = result["client_token"]

                    # Try to auto-discover service location
                    location_id = await self._async_discover_location(session)
                    if location_id:
                        _LOGGER.info("Auto-discovered location: %s", location_id)
                        await self.async_set_unique_id(location_id)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Base Power ({location_id})",
                            data={
                                CONF_EMAIL: self._email,
                                CONF_CLIENT_TOKEN: self._client_token,
                                CONF_SESSION_ID: self._session_id,
                                CONF_SERVICE_LOCATION_ID: location_id,
                            },
                        )

                    # Fallback: ask user for location ID
                    return await self.async_step_location()
            except AuthenticationError:
                errors["base"] = "invalid_code"
            except Exception:
                _LOGGER.exception("Unexpected error during verification")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="verify_code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    async def _async_discover_location(
        self, session: aiohttp.ClientSession
    ) -> str | None:
        """Try to discover service location ID from JWT claims or API."""
        try:
            # Get a JWT from the session
            auth = BasePowerAuth(session, self._client_token, self._session_id)
            jwt = await auth.async_refresh_token()

            # Decode JWT payload to check for location in claims
            parts = jwt.split(".")
            if len(parts) >= 2:
                payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                claims = json.loads(base64.b64decode(payload))
                _LOGGER.debug("JWT claims: %s", list(claims.keys()))

                # Check common claim locations for service location
                metadata = claims.get("metadata", {})
                if isinstance(metadata, dict):
                    loc = metadata.get("service_location_id") or metadata.get("serviceLocationId")
                    if loc:
                        return loc

                # Check top-level claims
                for key in ("service_location_id", "serviceLocationId", "slid", "location_id"):
                    if key in claims:
                        return claims[key]

            # Try calling the API with empty body to see if location is auto-resolved
            api = BasePowerApiClient(session)
            api.set_jwt(jwt)
            result = await api.get_dashboard_root("")
            if result.get("backup_seconds", 0) > 0 or result.get("battery_count", 0) > 0:
                _LOGGER.debug("API works with empty location ID")
                return ""

        except Exception:
            _LOGGER.debug("Location auto-discovery failed", exc_info=True)

        return None

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Enter or confirm service location ID (fallback)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            service_location_id = user_input[CONF_SERVICE_LOCATION_ID]

            # Set unique ID to prevent duplicates
            await self.async_set_unique_id(service_location_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Base Power ({service_location_id})",
                data={
                    CONF_EMAIL: self._email,
                    CONF_CLIENT_TOKEN: self._client_token,
                    CONF_SESSION_ID: self._session_id,
                    CONF_SERVICE_LOCATION_ID: service_location_id,
                },
            )

        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {vol.Required(CONF_SERVICE_LOCATION_ID): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauthentication."""
        self._email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_user()
