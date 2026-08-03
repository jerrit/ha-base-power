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
from .const import SCAN_INTERVAL_SECONDS, BATTERY_CAPACITY_PER_UNIT_KWH, DEFAULT_BATTERY_COUNT, CONF_WIFI_SSID, CONF_BATTERY_COUNT

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

        # FIX: last-known-good caches for the "optional" endpoints. Several of
        # these RPCs (GridStatus, UsageEnergy, UsageCycles, BillingMetadata)
        # have been observed returning a genuinely empty gRPC-Web frame on a
        # given poll even though they've returned real data on a previous poll
        # (or will again on a later one). Previously any empty poll reset the
        # sensor straight back to the all-None placeholder, which made sensors
        # like Total Home Energy / Self-Sufficiency / Daily Total (Grid) /
        # Bill Amount flap to "unknown" far more than the underlying data
        # actually warranted. We now only overwrite the cache when a fetch
        # actually returns something, and fall back to the last good value
        # otherwise -- the sensors then just hold their last real reading
        # until the next successful poll, same as any other polled sensor
        # that misses a beat.
        self._last_grid: dict[str, Any] = {
            "available": False, "grid_is_up": True, "battery_soc_percent": None,
            "battery_remaining_seconds": 0, "current_power_amps": None, "hourly_usage": [],
        }
        self._last_wifi: dict[str, Any] = {"ssid": None, "signal": None, "connected": False}
        self._last_energy: dict[str, Any] = {
            "grid_to_home_kwh": None, "solar_to_home_kwh": None, "battery_to_home_kwh": None,
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

            # Fetch optional data (non-fatal if they fail). Each block only
            # promotes the fetch into the "last good" cache when it actually
            # contains data, so a single empty/failed poll doesn't wipe out
            # sensors that were previously populated.
            try:
                fetched_grid = await self.api.get_grid_status(self.service_location_id)
                if fetched_grid.get("available"):
                    self._last_grid = fetched_grid
            except Exception as err:
                _LOGGER.debug("GridStatus fetch failed: %s", err)
            grid = self._last_grid

            try:
                fetched_wifi = await self.api.get_wifi_metrics(self.service_location_id)
                scan: dict[str, int | None] = fetched_wifi.get("scan", {})
                preferred_ssid: str | None = self._config_entry.options.get(CONF_WIFI_SSID)
                if preferred_ssid and preferred_ssid in scan:
                    # User configured a specific SSID — report its signal from the scan
                    fetched_wifi["ssid"] = preferred_ssid
                    fetched_wifi["signal"] = scan[preferred_ssid]
                    fetched_wifi["connected"] = True
                    self._last_wifi_ssid = preferred_ssid
                    self._last_wifi = fetched_wifi
                elif scan:
                    # No preference set — use strongest visible network with sticky fallback
                    best_ssid = max(scan, key=lambda s: scan[s] or 0)
                    fetched_wifi["ssid"] = best_ssid
                    fetched_wifi["signal"] = scan[best_ssid]
                    fetched_wifi["connected"] = True
                    if not self._last_wifi_ssid:
                        self._last_wifi_ssid = best_ssid
                    self._last_wifi = fetched_wifi
                elif self._last_wifi_ssid:
                    fetched_wifi["ssid"] = self._last_wifi_ssid
                    self._last_wifi = fetched_wifi
            except Exception as err:
                _LOGGER.debug("WifiMetrics fetch failed: %s", err)
            wifi = self._last_wifi

            try:
                fetched_energy = await self.api.get_usage_energy(self.service_location_id)
                if any(v is not None for v in fetched_energy.values()):
                    self._last_energy = fetched_energy
            except Exception as err:
                _LOGGER.debug("UsageEnergy fetch failed: %s", err)
            energy = self._last_energy

            try:
                fetched_billing = await self.api.get_billing_metadata(self.service_location_id)
                if any(v is not None for v in fetched_billing.values()):
                    self._last_billing = fetched_billing
            except Exception as err:
                _LOGGER.debug("BillingMetadata fetch failed: %s", err)
            billing = self._last_billing

            try:
                fetched_cycles = await self.api.get_usage_cycles(self.service_location_id)
                if any(v is not None for v in fetched_cycles.values()):
                    self._last_cycles = fetched_cycles
            except Exception as err:
                _LOGGER.debug("UsageCycles fetch failed: %s", err)
            cycles = self._last_cycles

            # Apply user-configured battery count (default 1; overrides API detection)
            dashboard["battery_count"] = self._config_entry.options.get(CONF_BATTERY_COUNT, 1)

            # Derive additional values
            derived = self._derive_values(dashboard, usage)

            # Battery percentage: prefer SoC from grid status, fall back to calibration
            derived["battery_percent_source"] = None
            if grid.get("battery_soc_percent") is not None:
                derived["battery_percent"] = grid["battery_soc_percent"]
                derived["battery_percent_source"] = "grid_status_telemetry"
            else:
                backup_seconds = dashboard.get("backup_seconds", 0)
                if backup_seconds > self._max_backup_seconds:
                    self._max_backup_seconds = backup_seconds
                if self._max_backup_seconds > 0 and backup_seconds > 0:
                    derived["battery_percent"] = round(
                        min((backup_seconds / self._max_backup_seconds) * 100, 100.0), 1
                    )
                    derived["battery_percent_source"] = "estimated_from_backup_time"
                    # NOTE: this estimate is calibrated against the highest
                    # backup_seconds value seen since this coordinator
                    # started (i.e. since the last HA/integration restart).
                    # That means the very first sample after every restart
                    # is mathematically guaranteed to read 100%, regardless
                    # of true state of charge, until a lower backup_seconds
                    # sample is observed to anchor a real range. Treat this
                    # value with skepticism for the first few polls after a
                    # restart -- battery_percent_source tells you which path
                    # produced the number.

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
