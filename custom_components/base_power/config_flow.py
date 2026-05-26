"""Config flow for Base Power integration."""

from __future__ import annotations

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
    CONF_SESSION_JWT,
    CONF_SERVICE_LOCATION_ID,
    CONF_WIFI_SSID,
)
from .api import BasePowerApiClient
from .auth import BasePowerAuth, AuthenticationError

_LOGGER = logging.getLogger(__name__)

CLERK_PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"


class BasePowerOptionsFlow(config_entries.OptionsFlow):
    """Handle Base Power options (WiFi SSID preference, etc.)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_ssid = self.config_entry.options.get(CONF_WIFI_SSID, "")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_WIFI_SSID, default=current_ssid): str,
                }
            ),
            description_placeholders={
                "wifi_ssid_hint": (
                    "Enter the exact SSID your Base Power battery is connected to "
                    "(e.g. USGOV). Leave blank to report the strongest visible network."
                )
            },
        )


class BasePowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Base Power."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return BasePowerOptionsFlow()

    def __init__(self) -> None:
        """Initialize flow."""
        self._email: str = ""
        self._sign_in_id: str = ""
        self._email_id: str = ""
        self._client_token: str = ""
        self._session_id: str = ""
        self._session_jwt: str | None = None

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
                        # Send OTP code (token may rotate)
                        prep_result = await BasePowerAuth.async_prepare_first_factor(
                            session,
                            self._sign_in_id,
                            self._email_id,
                            self._client_token,
                        )
                        self._client_token = prep_result["client_token"]
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
                    self._session_jwt = result.get("session_jwt")

                    # Auto-discover service location ID
                    try:
                        auth = BasePowerAuth(
                            session,
                            self._client_token,
                            self._session_id,
                            self._session_jwt,
                        )
                        jwt = await auth.async_ensure_valid_token()
                        location_id = await BasePowerApiClient.async_discover_location_id(
                            session, jwt
                        )
                        if location_id:
                            _LOGGER.info(
                                "Auto-discovered service location ID: %s", location_id
                            )
                            return await self.async_step_location(
                                {CONF_SERVICE_LOCATION_ID: location_id}
                            )
                    except Exception:
                        _LOGGER.warning(
                            "Location auto-discovery failed, falling back to manual entry",
                            exc_info=True,
                        )

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

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Enter or confirm service location ID."""
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
                    CONF_SESSION_JWT: self._session_jwt,
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
