"""Data coordinator for Base Power integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import BasePowerApiClient
from .auth import BasePowerAuth, AuthenticationError
from .const import (
    SCAN_INTERVAL_SECONDS,
    BATTERY_CAPACITY_PER_UNIT_KWH,
    DEFAULT_BATTERY_COUNT,
    CONF_WIFI_SSID,
    CONF_BATTERY_COUNT,
    CONF_BATTERY_CAPACITY,
    resolve_battery_capacity,
)

_LOGGER = logging.getLogger(__name__)


class BasePowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate data fetching from Base Power API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: BasePowerApiClient,
        auth: BasePowerAuth,
        service_location_id: str,
        config_entry,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Base Power",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.api = api_client
        self.auth = auth
        self.service_location_id = service_location_id
        self._config_entry = config_entry
        self._numeric_service_location_id: int | None = None
        self._prev_soc: float | None = None
        self._last_energy: dict[str, Any] = {
            "grid_to_home_kwh": None,
            "solar_to_home_kwh": None,
            "battery_to_home_kwh": None,
        }
        self._last_billing: dict[str, Any] = {"amount_cents": None, "due_date": None}
        self._last_cycles: dict[str, Any] = {"asset_id": None}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Base Power API."""
        try:
            # Refresh JWT
            jwt = await self.auth.async_ensure_valid_token()
            self.api.set_jwt(jwt)

            # Fetch core data (required)
            dashboard = await self.api.get_dashboard_root(self.service_location_id)
            usage = await self.api.get_recent_usage(self.service_location_id)

            # Fetch optional data (non-fatal if they fail).
            # An empty grid poll must stay unknown. Do not reuse an older
            # "grid is up" reading, or an outage that stops telemetry looks fine.
            numeric_id = dashboard.get("service_location_id")
            if numeric_id:
                self._numeric_service_location_id = numeric_id
            grid = await self._async_fetch_grid(self._numeric_service_location_id)
            wifi: dict[str, Any] = {"ssid": None, "signal": None, "connected": False}

            try:
                wifi = await self.api.get_wifi_metrics(self.service_location_id)
                scan: dict[str, int | None] = wifi.get("scan", {})
                preferred_ssid: str | None = self._config_entry.options.get(CONF_WIFI_SSID) or None
                if preferred_ssid and preferred_ssid in scan:
                    wifi["ssid"] = preferred_ssid
                    wifi["signal"] = scan[preferred_ssid]
                    wifi["connected"] = True
                elif preferred_ssid:
                    # Keep the user's network. Do not substitute a stronger one.
                    wifi["ssid"] = preferred_ssid
                    wifi["connected"] = False
            except Exception as err:
                _LOGGER.debug("WifiMetrics fetch failed: %s", err)

            try:
                fetched_energy = await self.api.get_usage_energy(self.service_location_id)
                if any(value is not None for value in fetched_energy.values()):
                    self._last_energy = fetched_energy
            except Exception as err:
                _LOGGER.debug("UsageEnergy fetch failed: %s", err)
            energy = self._last_energy

            try:
                fetched_billing = await self.api.get_billing_metadata(self.service_location_id)
                if any(value is not None for value in fetched_billing.values()):
                    self._last_billing = fetched_billing
            except Exception as err:
                _LOGGER.debug("BillingMetadata fetch failed: %s", err)
            billing = self._last_billing

            try:
                fetched_cycles = await self.api.get_usage_cycles(self.service_location_id)
                if any(value is not None for value in fetched_cycles.values()):
                    self._last_cycles = fetched_cycles
            except Exception as err:
                _LOGGER.debug("UsageCycles fetch failed: %s", err)
            cycles = self._last_cycles

            # Capacity dropdown wins. Older installs still store an integer unit count.
            resolved = resolve_battery_capacity(
                self._config_entry.options.get(CONF_BATTERY_CAPACITY)
            )
            if resolved is not None:
                units, kwh = resolved
            else:
                units = self._config_entry.options.get(CONF_BATTERY_COUNT, DEFAULT_BATTERY_COUNT)
                kwh = units * BATTERY_CAPACITY_PER_UNIT_KWH
            dashboard["battery_count"] = units
            dashboard["capacity_kwh"] = kwh

            # Derive additional values, then overlay live service-context readings.
            derived = self._derive_values(dashboard, usage)
            self._apply_live_readings(derived, grid)

            # Self-sufficiency and total home consumption from energy data
            grid_kwh = energy.get("grid_to_home_kwh") or 0.0
            solar_kwh = energy.get("solar_to_home_kwh") or 0.0
            battery_kwh = energy.get("battery_to_home_kwh") or 0.0
            total_home = grid_kwh + solar_kwh + battery_kwh
            derived["total_home_kwh"] = round(total_home, 3) if total_home > 0 else None
            if total_home > 0:
                derived["self_sufficiency_percent"] = round(
                    (solar_kwh + battery_kwh) / total_home * 100, 1
                )
            else:
                derived["self_sufficiency_percent"] = None

            # has_solar: trust API field or fall back to solar production > 0
            if not dashboard.get("has_solar") and solar_kwh > 0:
                dashboard["has_solar"] = True

            return {
                "dashboard": dashboard,
                "usage": usage,
                "grid": grid,
                "wifi": wifi,
                "energy": energy,
                "billing": billing,
                "cycles": cycles,
                "derived": derived,
            }
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Base Power API: {err}") from err

    async def _async_fetch_grid(self, numeric_id: int | None) -> dict[str, Any]:
        """Read service context and grid voltage for the numeric location id."""
        context = self.api._empty_service_context()
        voltage = self.api._empty_grid_voltage()
        if not numeric_id:
            _LOGGER.debug("No numeric service location id yet")
            return self.api.combine_grid_telemetry(context, voltage)
        try:
            context = await self.api.get_service_context(numeric_id)
        except Exception as err:
            _LOGGER.debug("ServiceContext fetch failed: %s", err)
        try:
            voltage = await self.api.get_grid_status(numeric_id)
        except Exception as err:
            _LOGGER.debug("GridStatus fetch failed: %s", err)
        return self.api.combine_grid_telemetry(context, voltage)

    def _apply_live_readings(self, derived: dict[str, Any], grid: dict[str, Any]) -> None:
        """Apply state of charge, backup hours, home load, and charging."""
        soc = grid.get("battery_soc_percent")
        if soc is not None:
            derived["battery_percent"] = round(float(soc), 1)
            derived["battery_percent_source"] = "service_context"
        else:
            derived["battery_percent"] = None
            derived["battery_percent_source"] = None

        backup_hours = grid.get("backup_hours")
        derived["backup_hours"] = (
            round(float(backup_hours), 2) if backup_hours is not None else None
        )

        home_kw = grid.get("power_to_home_kw")
        if home_kw is not None:
            derived["current_power_watts"] = round(home_kw * 1000)

        percent = derived.get("battery_percent")
        full_soc = 99.5
        if percent is not None and self._prev_soc is not None:
            delta = percent - self._prev_soc
            if delta > 0.5:
                derived["battery_charging"] = True
            elif delta < -0.5:
                derived["battery_charging"] = False
            elif percent >= full_soc:
                derived["battery_charging"] = False
            else:
                derived["battery_charging"] = None
        elif percent is not None and percent >= full_soc:
            derived["battery_charging"] = False
        else:
            derived["battery_charging"] = None
        self._prev_soc = percent

    async def async_poll_grid_status(self, now=None) -> None:
        """Fetch grid status between full coordinator refreshes."""
        if self.data is None or not self._numeric_service_location_id:
            return
        try:
            jwt = await self.auth.async_ensure_valid_token()
            self.api.set_jwt(jwt)
            grid = await self._async_fetch_grid(self._numeric_service_location_id)
            derived = dict(self.data.get("derived") or {})
            self._apply_live_readings(derived, grid)
            self.async_set_updated_data({**self.data, "grid": grid, "derived": derived})
        except Exception as err:
            _LOGGER.debug("Fast grid poll failed: %s", err)

    @staticmethod
    def _derive_values(
        dashboard: dict[str, Any], usage: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Derive additional sensor values from raw data."""
        battery_count = dashboard.get("battery_count", DEFAULT_BATTERY_COUNT)
        capacity_kwh = dashboard.get("capacity_kwh", battery_count * BATTERY_CAPACITY_PER_UNIT_KWH)

        derived: dict[str, Any] = {
            "battery_percent": None,
            "battery_count": battery_count,
            "capacity_kwh": capacity_kwh,
            "current_power_watts": 0,
            "current_kwh": 0.0,
            "daily_peak_kwh": 0.0,
            "daily_low_kwh": 0.0,
            "daily_total_kwh": 0.0,
            "intervals_today": 0,
        }

        # Usage-derived values
        if usage:
            values = [p["kwh"] for p in usage]
            derived["current_kwh"] = values[-1]
            derived["current_power_watts"] = round(values[-1] * 4000)
            derived["daily_peak_kwh"] = max(values)
            derived["daily_low_kwh"] = min(values)
            derived["daily_total_kwh"] = round(sum(values), 2)
            derived["intervals_today"] = len(values)

        return derived
