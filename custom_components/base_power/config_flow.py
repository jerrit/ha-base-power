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
from .auth import BasePowerAuth, AuthenticationError, _CLERK_HEADERS
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
                    # Keep original client_token from step 1 for __client cookie
                    # The Authorization header from attempt_first_factor may be
                    # a session JWT, not a client JWT
                    user_data = result.get("user_data", {})

                    # Try to auto-discover service location
                    location_id = await self._async_discover_location(session, user_data)
                    if location_id is not None:
                        _LOGGER.info("Auto-discovered location: %s", location_id)
                        await self.async_set_unique_id(location_id or self._email)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Base Power ({location_id or self._email})",
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
        self, session: aiohttp.ClientSession, user_data: dict[str, Any] | None = None
    ) -> str | None:
        """Try to discover service location ID from user data, JWT, or API."""
        try:
            # Method 1: Check user metadata from OTP response
            if user_data:
                _LOGGER.debug("User data keys: %s", list(user_data.keys()))
                for meta_key in ("public_metadata", "unsafe_metadata", "private_metadata"):
                    metadata = user_data.get(meta_key, {})
                    if isinstance(metadata, dict):
                        _LOGGER.debug("%s: %s", meta_key, metadata)
                        for loc_key in ("service_location_id", "serviceLocationId",
                                        "slid", "location_id", "locationId"):
                            if loc_key in metadata:
                                return metadata[loc_key]

            # Method 2: Get JWT and check claims
            auth = BasePowerAuth(session, self._client_token, self._session_id)
            jwt = await auth.async_refresh_token()

            parts = jwt.split(".")
            if len(parts) >= 2:
                payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                claims = json.loads(base64.b64decode(payload))
                _LOGGER.debug("JWT claims keys: %s", list(claims.keys()))
                _LOGGER.debug("JWT full claims: %s", claims)

                metadata = claims.get("metadata", {})
                if isinstance(metadata, dict):
                    for loc_key in ("service_location_id", "serviceLocationId",
                                    "slid", "location_id", "locationId"):
                        if loc_key in metadata:
                            return metadata[loc_key]

            # Method 3: Try Clerk /v1/me endpoint for user profile
            me_url = f"https://clerk.basepowercompany.com/v1/me?_clerk_js_version=5.56.0-snapshot.v20250409124055"
            cookies = {"__client": self._client_token, "__session": jwt}
            async with session.get(me_url, headers=_CLERK_HEADERS, cookies=cookies) as me_resp:
                if me_resp.status == 200:
                    me_data = await me_resp.json()
                    _LOGGER.debug("Clerk /v1/me response keys: %s", list(me_data.keys()))
                    for meta_key in ("public_metadata", "unsafe_metadata"):
                        metadata = me_data.get("response", {}).get(meta_key, {})
                        if not metadata:
                            metadata = me_data.get(meta_key, {})
                        if isinstance(metadata, dict):
                            _LOGGER.debug("/v1/me %s: %s", meta_key, metadata)
                            for loc_key in ("service_location_id", "serviceLocationId",
                                            "slid", "location_id", "locationId"):
                                if loc_key in metadata:
                                    return metadata[loc_key]
                else:
                    _LOGGER.debug("Clerk /v1/me failed: %s", me_resp.status)

            # Method 4: Try API with empty body
            api = BasePowerApiClient(session)
            api.set_jwt(jwt)
            result = await api.get_dashboard_root("")
            if result.get("backup_seconds", 0) > 0 or result.get("battery_count", 0) > 0:
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
