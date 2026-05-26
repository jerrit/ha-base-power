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
from .const import SCAN_INTERVAL_SECONDS, BATTERY_CAPACITY_PER_UNIT_KWH, DEFAULT_BATTERY_COUNT, CONF_WIFI_SSID

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
        self._max_backup_seconds: int = 0  # Track max for % calibration
        self._prev_soc: float | None = None
        self._last_wifi_ssid: str | None = None

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
            grid: dict[str, Any] = {"available": False, "grid_is_up": True, "battery_soc_percent": None, "battery_remaining_seconds": 0, "current_power_amps": None, "hourly_usage": []}
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
                scan: dict[str, int | None] = wifi.get("scan", {})
                preferred_ssid: str | None = self._config_entry.options.get(CONF_WIFI_SSID)
                if preferred_ssid and preferred_ssid in scan:
                    # User configured a specific SSID — report its signal from the scan
                    wifi["ssid"] = preferred_ssid
                    wifi["signal"] = scan[preferred_ssid]
                    wifi["connected"] = True
                    self._last_wifi_ssid = preferred_ssid
                elif scan:
                    # No preference set — use strongest visible network with sticky fallback
                    best_ssid = max(scan, key=lambda s: scan[s] or 0)
                    wifi["ssid"] = best_ssid
                    wifi["signal"] = scan[best_ssid]
                    wifi["connected"] = True
                    if not self._last_wifi_ssid:
                        self._last_wifi_ssid = best_ssid
                elif self._last_wifi_ssid:
                    wifi["ssid"] = self._last_wifi_ssid
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

            # Estimated backup hours: (SoC% × capacity_kWh) ÷ avg_home_power_kW
            battery_percent = derived.get("battery_percent")
            intervals = derived.get("intervals_today", 0)
            daily_total = derived.get("daily_total_kwh", 0.0)
            capacity_kwh = derived.get("capacity_kwh", 0.0)
            if (
                battery_percent is not None
                and battery_percent > 0
                and intervals > 0
                and daily_total > 0
                and capacity_kwh > 0
            ):
                avg_power_kw = daily_total / (intervals / 4)
                battery_energy_kwh = battery_percent / 100 * capacity_kwh
                derived["estimated_backup_hours"] = round(battery_energy_kwh / avg_power_kw, 1)
            else:
                derived["estimated_backup_hours"] = None

            # Battery charging/discharging: infer from SoC delta between polls
            if battery_percent is not None and self._prev_soc is not None:
                delta = battery_percent - self._prev_soc
                if delta > 0.5:
                    derived["battery_charging"] = True
                elif delta < -0.5:
                    derived["battery_charging"] = False
                else:
                    derived["battery_charging"] = None
            else:
                derived["battery_charging"] = None
            self._prev_soc = battery_percent

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
