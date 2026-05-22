"""Config flow for Base Power integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_CLIENT_TOKEN,
    CONF_SESSION_ID,
    CONF_SERVICE_LOCATION_ID,
)
from .auth import BasePowerAuth, AuthenticationError

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
            session = async_get_clientsession(self.hass)

            try:
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
            except AuthenticationError:
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
            session = async_get_clientsession(self.hass)

            try:
                result = await BasePowerAuth.async_attempt_first_factor(
                    session,
                    self._sign_in_id,
                    code,
                    self._client_token,
                )
                self._session_id = result["session_id"]
                self._client_token = result["client_token"]

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
