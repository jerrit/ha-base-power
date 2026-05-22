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

            # Fetch dashboard root (backup hours, status)
            dashboard = await self.api.get_dashboard_root(self.service_location_id)

            # Fetch recent usage (15-min kWh readings)
            usage = await self.api.get_recent_usage(self.service_location_id)

            # Fetch grid status (may be empty for new installs)
            grid = await self.api.get_grid_status(self.service_location_id)

            # Derive additional values
            derived = self._derive_values(dashboard, usage)

            # Battery percentage: track max backup_seconds as 100% calibration
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
