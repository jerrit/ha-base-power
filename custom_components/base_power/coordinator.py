"""Data coordinator for Base Power integration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import BasePowerApiClient
from .auth import BasePowerAuth, AuthenticationError
from .const import SCAN_INTERVAL_SECONDS, BATTERY_CAPACITY_PER_UNIT_KWH, DEFAULT_BATTERY_COUNT

_LOGGER = logging.getLogger(__name__)


class BasePowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate data fetching from Base Power API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: BasePowerApiClient,
        auth: BasePowerAuth,
        service_location_id: str,
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
        self._max_backup_seconds: int = 0  # Track max for % calibration

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Base Power API."""
        try:
            # Refresh JWT
            jwt = await self.auth.async_ensure_valid_token()
            self.api.set_jwt(jwt)

            # Fetch core data (required)
            dashboard = await self.api.get_dashboard_root(self.service_location_id)
            usage = await self.api.get_recent_usage(self.service_location_id)

            # Fetch optional data (non-fatal if they fail)
            grid: dict[str, Any] = {"available": False, "grid_is_up": True, "battery_soc_percent": None, "battery_remaining_seconds": 0}
            wifi: dict[str, Any] = {"ssid": None, "signal": None, "connected": False}
            energy: dict[str, Any] = {"grid_to_home_kwh": None, "solar_to_home_kwh": None, "battery_to_home_kwh": None}
            billing: dict[str, Any] = {"amount_cents": None, "due_date": None}
            cycles: dict[str, Any] = {"asset_id": None}

            try:
                grid = await self.api.get_grid_status(self.service_location_id)
            except Exception as err:
                _LOGGER.debug("GridStatus fetch failed: %s", err)

            try:
                wifi = await self.api.get_wifi_metrics(self.service_location_id)
            except Exception as err:
                _LOGGER.debug("WifiMetrics fetch failed: %s", err)

            try:
                energy = await self.api.get_usage_energy(self.service_location_id)
            except Exception as err:
                _LOGGER.debug("UsageEnergy fetch failed: %s", err)

            try:
                billing = await self.api.get_billing_metadata(self.service_location_id)
            except Exception as err:
                _LOGGER.debug("BillingMetadata fetch failed: %s", err)

            try:
                cycles = await self.api.get_usage_cycles(self.service_location_id)
            except Exception as err:
                _LOGGER.debug("UsageCycles fetch failed: %s", err)

            # Write debug dump of raw API responses
            try:
                dump_path = self.hass.config.path("base_power_debug.json")
                dump = {
                    "timestamp": datetime.now().isoformat(),
                    "raw_hex": self.api.raw_responses,
                    "parsed": {
                        "dashboard": dashboard,
                        "grid": grid,
                        "wifi": wifi,
                        "energy": energy,
                        "billing": billing,
                        "cycles": cycles,
                        "usage_count": len(usage),
                        "last_usage": usage[-1] if usage else None,
                    },
                }
                with open(dump_path, "w") as f:
                    json.dump(dump, f, indent=2, default=str)
                _LOGGER.warning("Base Power debug dump written to %s", dump_path)
            except Exception as dump_err:
                _LOGGER.error("Failed to write debug dump: %s", dump_err)

            # Derive additional values
            derived = self._derive_values(dashboard, usage)

            # Battery percentage: prefer SoC from grid status, fall back to calibration
            if grid.get("battery_soc_percent") is not None:
                derived["battery_percent"] = grid["battery_soc_percent"]
            else:
                backup_seconds = dashboard.get("backup_seconds", 0)
                if backup_seconds > self._max_backup_seconds:
                    self._max_backup_seconds = backup_seconds
                if self._max_backup_seconds > 0 and backup_seconds > 0:
                    derived["battery_percent"] = round(
                        min((backup_seconds / self._max_backup_seconds) * 100, 100.0), 1
                    )

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

    @staticmethod
    def _derive_values(
        dashboard: dict[str, Any], usage: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Derive additional sensor values from raw data."""
        battery_count = dashboard.get("battery_count", DEFAULT_BATTERY_COUNT)
        capacity_kwh = battery_count * BATTERY_CAPACITY_PER_UNIT_KWH

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
