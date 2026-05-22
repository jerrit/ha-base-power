"""Config flow for Base Power integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BasePowerAPI
from .auth import ClerkAuth, ClerkAuthError
from .const import (
    CONF_CLIENT_TOKEN,
    CONF_EMAIL,
    CONF_SERVICE_LOCATION_ID,
    CONF_SERVICE_LOCATION_NAME,
    CONF_SESSION_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class BasePowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow: email → OTP → (optional) location select."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._auth: ClerkAuth | None = None
        self._locations: list[dict] = []

    # ------------------------------------------------------------------
    # Step 1 — email address
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Collect the user's Base Power email address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            session = async_get_clientsession(self.hass)
            self._auth = ClerkAuth(session)

            try:
                sent = await self._auth.initiate_sign_in(self._email)
            except Exception:
                _LOGGER.exception("Unexpected error during sign-in initiation")
                errors["base"] = "cannot_connect"
            else:
                if sent:
                    return await self.async_step_verify_code()
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
            description_placeholders={},
        )

    # ------------------------------------------------------------------
    # Step 2 — OTP verification
    # ------------------------------------------------------------------

    async def async_step_verify_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user for the 6-digit code sent to their email."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"].strip()
            try:
                verified = await self._auth.verify_otp(code)
            except Exception:
                _LOGGER.exception("Unexpected error during OTP verification")
                errors["base"] = "cannot_connect"
            else:
                if verified:
                    return await self._async_finish_auth()
                errors["base"] = "invalid_code"

        return self.async_show_form(
            step_id="verify_code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
            description_placeholders={"email": self._email},
        )

    # ------------------------------------------------------------------
    # Step 3 — location select (only shown when multiple locations exist)
    # ------------------------------------------------------------------

    async def async_step_select_location(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user pick which Base Power location to monitor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            location_id = user_input[CONF_SERVICE_LOCATION_ID]
            location_name = next(
                (
                    _format_location_name(loc)
                    for loc in self._locations
                    if loc["service_location_id"] == location_id
                ),
                location_id,
            )
            return self._create_entry(location_id, location_name)

        location_options = {
            loc["service_location_id"]: _format_location_name(loc)
            for loc in self._locations
        }

        return self.async_show_form(
            step_id="select_location",
            data_schema=vol.Schema(
                {vol.Required(CONF_SERVICE_LOCATION_ID): vol.In(location_options)}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Re-auth flow — triggered when a stored session expires
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Initiate re-authentication."""
        self._email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show a form prompting the user to sign in again."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input.get(CONF_EMAIL, self._email).strip()
            self._email = email
            session = async_get_clientsession(self.hass)
            self._auth = ClerkAuth(session)

            try:
                sent = await self._auth.initiate_sign_in(self._email)
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                if sent:
                    return await self.async_step_verify_code()
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL, default=self._email): str}
            ),
            errors=errors,
            description_placeholders={"email": self._email},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _async_finish_auth(self) -> FlowResult:
        """Auth is complete — fetch locations and route to next step."""
        try:
            jwt = await self._auth.refresh_jwt()
        except ClerkAuthError as err:
            _LOGGER.error("Could not get JWT after successful OTP: %s", err)
            return self.async_show_form(
                step_id="verify_code",
                data_schema=vol.Schema({vol.Required("code"): str}),
                errors={"base": "cannot_connect"},
                description_placeholders={"email": self._email},
            )

        session = async_get_clientsession(self.hass)

        # Fetch locations using a temporary API client with a placeholder location ID
        # (get_service_locations uses an Empty request — no location ID needed)
        temp_api = BasePowerAPI(session, self._auth, "")
        try:
            self._locations = await temp_api.get_service_locations()
        except Exception as err:
            _LOGGER.warning("Could not fetch service locations: %s", err)
            self._locations = []

        if len(self._locations) == 0:
            _LOGGER.error("No service locations returned from Base Power API")
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
                errors={"base": "no_locations"},
            )

        if len(self._locations) == 1:
            loc = self._locations[0]
            return self._create_entry(
                loc["service_location_id"], _format_location_name(loc)
            )

        return await self.async_step_select_location()

    def _create_entry(self, location_id: str, location_name: str) -> FlowResult:
        """Persist the config entry with the gathered credentials."""
        return self.async_create_entry(
            title=location_name,
            data={
                CONF_EMAIL: self._email,
                CONF_CLIENT_TOKEN: self._auth.client_token,
                CONF_SESSION_ID: self._auth.session_id,
                CONF_SERVICE_LOCATION_ID: location_id,
                CONF_SERVICE_LOCATION_NAME: location_name,
            },
        )


def _format_location_name(loc: dict) -> str:
    """Turn a location dict into a human-readable label."""
    address = loc.get("address", {})
    parts = [
        address.get("line1"),
        address.get("city"),
        address.get("state"),
    ]
    label = ", ".join(p for p in parts if p)
    return label or loc.get("service_location_id", "Unknown location")
