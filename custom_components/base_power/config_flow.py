"""Config flow for Base Power integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import BasePowerApiClient
from .auth import AuthenticationError, BasePowerAuth
from .const import (
    BATTERY_CAPACITY_OPTIONS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_COUNT,
    CONF_CLIENT_TOKEN,
    CONF_SERVICE_LOCATION_ID,
    CONF_SESSION_ID,
    CONF_WIFI_SSID,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_BATTERY_COUNT,
    DOMAIN,
    canonical_battery_capacity,
)

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
        current_capacity = canonical_battery_capacity(
            self.config_entry.options.get(CONF_BATTERY_CAPACITY)
        )
        if current_capacity is None:
            legacy_count = self.config_entry.options.get(CONF_BATTERY_COUNT, DEFAULT_BATTERY_COUNT)
            current_capacity = (
                "50 kWh (2 units)" if legacy_count == 2 else DEFAULT_BATTERY_CAPACITY
            )

        # Pull live WiFi scan from the running coordinator (no extra API call needed)
        scan: dict[str, int | None] = {}
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is not None and coordinator.data:
            scan = (coordinator.data.get("wifi") or {}).get("scan") or {}

        if scan:
            options: list[dict[str, str]] = []
            seen: set[str] = set()
            for ssid, signal in sorted(scan.items(), key=lambda x: x[1] or 0, reverse=True):
                label = f"{ssid} ({signal}%)" if signal is not None else ssid
                options.append({"value": ssid, "label": label})
                seen.add(ssid)
            if current_ssid and current_ssid not in seen:
                options.append({"value": current_ssid, "label": f"{current_ssid} (not currently visible)"})

            ssid_field = SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        else:
            ssid_field = str  # type: ignore[assignment]
        # Optional: Base normally reports the battery's network itself.
        wifi_key = vol.Optional(
            CONF_WIFI_SSID, description={"suggested_value": current_ssid or None}
        )

        capacity_field = SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": label, "label": label}
                    for label in BATTERY_CAPACITY_OPTIONS
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    wifi_key: ssid_field,
                    vol.Optional(CONF_BATTERY_CAPACITY, default=current_capacity): capacity_field,
                }
            ),
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
        self._service_location_id: str = ""
        self._locations: list[dict[str, str]] = []

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

                    # During reauth, location ID is already known — skip discovery
                    if self._service_location_id:
                        return await self.async_step_location(
                            {CONF_SERVICE_LOCATION_ID: self._service_location_id}
                        )

                    # Find the account's sites. Only the lookup is guarded:
                    # the next step can abort the flow (already configured),
                    # and that must not be treated as a lookup failure.
                    locations: list[dict[str, str]] = []
                    try:
                        auth = BasePowerAuth(
                            session, self._client_token, self._session_id
                        )
                        jwt = await auth.async_ensure_valid_token()
                        # Asking for the token can rotate the client token.
                        self._client_token = auth.client_token
                        locations = await BasePowerApiClient.async_discover_locations(
                            session, jwt
                        )
                    except Exception:
                        _LOGGER.warning(
                            "Location auto-discovery failed, falling back to manual entry",
                            exc_info=True,
                        )

                    # Skip sites already set up. If all are, the first one
                    # falls through to the already-configured abort.
                    configured = self._async_current_ids()
                    locations = [
                        loc for loc in locations if loc["address_id"] not in configured
                    ] or locations[:1]
                    if len(locations) == 1:
                        _LOGGER.info("Found one Base Power site")
                        return await self.async_step_location(
                            {CONF_SERVICE_LOCATION_ID: locations[0]["address_id"]}
                        )
                    if locations:
                        self._locations = locations
                        return await self.async_step_pick_location()
                    return await self.async_step_location()
            except AbortFlow:
                raise
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

    async def async_step_pick_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3a: Choose a site when the account has more than one."""
        if user_input is not None:
            return await self.async_step_location(
                {CONF_SERVICE_LOCATION_ID: user_input[CONF_SERVICE_LOCATION_ID]}
            )
        return self.async_show_form(
            step_id="pick_location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVICE_LOCATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": loc["address_id"], "label": loc["label"]}
                                for loc in self._locations
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Enter or confirm service location ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            service_location_id = user_input[CONF_SERVICE_LOCATION_ID]
            entry_data = {
                CONF_EMAIL: self._email,
                CONF_CLIENT_TOKEN: self._client_token,
                CONF_SESSION_ID: self._session_id,
                CONF_SERVICE_LOCATION_ID: service_location_id,
            }

            # During reauth, update the existing entry instead of creating a new one
            if self.source == config_entries.SOURCE_REAUTH:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data=entry_data,
                )

            # Set unique ID to prevent duplicates
            await self.async_set_unique_id(service_location_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Base Power ({service_location_id})",
                data=entry_data,
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
        self._service_location_id = entry_data.get(CONF_SERVICE_LOCATION_ID, "")
        return await self.async_step_user()
