"""Data coordinator for Base Power integration."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import BasePowerApiClient, local_day_interval, summarize_recent_usage
from .api_v2 import OFF_GRID_STATES, empty_snapshot, grid_support_active, live_grid_support
from .auth import BasePowerAuth, AuthenticationError
from .const import (
    SCAN_INTERVAL_SECONDS,
    BATTERY_CAPACITY_PER_UNIT_KWH,
    DEFAULT_BATTERY_COUNT,
    CONF_WIFI_SSID,
    CONF_BATTERY_COUNT,
    CONF_BATTERY_CAPACITY,
    HARDWARE_SKU_CAPACITY,
    resolve_battery_capacity,
)
from .poll import (
    SNAPSHOT_EVERY_FAST_POLLS,
    PollState,
    include_fast_on_coordinator_refresh,
    refresh_slow,
    refresh_usage,
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
        self._poll = PollState()
        self._grid = self.api.combine_grid_telemetry(
            self.api._empty_service_context(), self.api._empty_grid_voltage()
        )
        self._prev_soc: float | None = None
        self._snapshot: dict[str, Any] = empty_snapshot()
        self._polls_since_snapshot = 0

    @property
    def _numeric_service_location_id(self) -> int | None:
        return self._poll.numeric_id

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh the usage tier, and the slow tier when it is due.

        ServiceContext and GridStatus are the 60-second poll. This refresh
        does not call them again after startup.
        """
        first = self.data is None
        try:
            await self._async_ensure_jwt()
            now = self._monotonic()
            tiers = self._poll.tiers_for_periodic_refresh(now, first=first)
            _LOGGER.debug("Coordinator tiers=%s", ",".join(tiers))
            if "slow" in tiers:
                await self._async_refresh_slow(now)
            if "usage" in tiers:
                await refresh_usage(
                    self.api,
                    self._poll,
                    self.service_location_id,
                    self._time_zone(),
                )
            if include_fast_on_coordinator_refresh(first):
                self._grid = await self._async_fetch_grid(self._poll.numeric_id)
                self._snapshot = await self._async_fetch_snapshot()
            return self._compose()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except Exception as err:
            if first:
                raise UpdateFailed(f"Error communicating with Base Power API: {err}") from err
            _LOGGER.debug("Usage refresh failed; keeping cached data: %s", err)
            return self.data

    def _monotonic(self) -> float:
        """Clock for tier spacing. Tests can replace this."""
        return time.monotonic()

    def _time_zone(self) -> str | None:
        return getattr(getattr(self.hass, "config", None), "time_zone", None)

    def _preferred_ssid(self) -> str | None:
        return self._config_entry.options.get(CONF_WIFI_SSID) or None

    async def _async_ensure_jwt(self) -> None:
        jwt = await self.auth.async_ensure_valid_token()
        self.api.set_jwt(jwt)

    async def _async_refresh_slow(self, now: float) -> None:
        day_start, day_end = local_day_interval(self._time_zone())
        _LOGGER.debug(
            "Slow tier numeric_service_location_id=%s day_start=%s day_end=%s",
            self._poll.numeric_id,
            day_start,
            day_end,
        )
        await refresh_slow(
            self.api,
            self._poll,
            self.service_location_id,
            self._preferred_ssid(),
            now,
        )

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
        source: str | None = None
        try:
            voltage = await self.api.get_grid_status(numeric_id)
        except Exception as err:
            _LOGGER.debug("GridStatus fetch failed: %s", err)
        if voltage.get("voltage_volts") is not None:
            source = "legacy"
        else:
            # Fallback: the v2 voltage series. It takes the address string.
            try:
                fallback = await self.api.get_recent_grid_voltage(self.service_location_id)
            except AuthenticationError:
                raise
            except Exception as err:
                _LOGGER.debug("RecentGridVoltage fetch failed: %s", err)
            else:
                if fallback.get("voltage_volts") is not None:
                    if voltage.get("has_gateway") is not None:
                        fallback["has_gateway"] = voltage["has_gateway"]
                    voltage = fallback
                    source = "v2"
        grid = self.api.combine_grid_telemetry(context, voltage)
        grid["voltage_source"] = source
        return grid

    def _snapshot_due(self) -> bool:
        """Every 10th poll, or every poll while something points to an outage.

        A failed snapshot does not speed this up, so an outage of the v2 API
        does not add a call every minute.
        """
        grid = self._grid
        # home_has_power is not a trigger. The service-context parser reads a
        # missing field as False, so an account that never sends it would
        # poll the snapshot every minute. The snapshot's own no_home_power
        # state still speeds this up.
        if (
            grid.get("is_active_outage")
            or grid.get("grid_is_up") is False
            or self._snapshot.get("state") in OFF_GRID_STATES
        ):
            return True
        return self._polls_since_snapshot >= SNAPSHOT_EVERY_FAST_POLLS

    async def _async_fetch_snapshot(self) -> dict[str, Any]:
        """Battery snapshot. A failed call is empty, not the previous state.

        Keeping an old "outage" would hold Grid Power off after the grid returns.
        """
        try:
            return await self.api.get_battery_snapshot(self.service_location_id)
        except AuthenticationError:
            raise
        except Exception as err:
            _LOGGER.debug("BatterySnapshot fetch failed: %s", err)
            return empty_snapshot()

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

    def _compose(self) -> dict[str, Any]:
        """Build coordinator data from the cached tiers and the latest grid."""
        dashboard = dict(self._poll.dashboard)
        options = self._config_entry.options
        resolved = resolve_battery_capacity(options.get(CONF_BATTERY_CAPACITY))
        sku = (self._poll.battery_account or {}).get("hardware_sku")
        detected = HARDWARE_SKU_CAPACITY.get(sku) if sku else None
        # A saved option always wins. The SKU fills in only when no capacity
        # was ever saved, and only for SKUs whose size is known.
        if resolved is not None:
            units, kwh = resolved
            source = "option"
        elif CONF_BATTERY_COUNT in options:
            units = options[CONF_BATTERY_COUNT]
            kwh = units * BATTERY_CAPACITY_PER_UNIT_KWH
            source = "option"
        elif detected is not None:
            units, kwh = detected
            source = "sku"
        else:
            units = DEFAULT_BATTERY_COUNT
            kwh = units * BATTERY_CAPACITY_PER_UNIT_KWH
            source = "default"
        dashboard["battery_count"] = units
        dashboard["capacity_kwh"] = kwh
        dashboard["capacity_source"] = source
        dashboard["hardware_sku"] = sku
        dashboard["detected_capacity_kwh"] = detected[1] if detected else None

        energy = dict(self._poll.energy)
        usage = self._poll.usage
        grid = dict(self._grid)
        snapshot = self._snapshot
        overview = self._poll.overview
        # The snapshot can only turn Grid Power off. Voltage and the outage
        # flag still decide "on".
        grid["battery_state"] = snapshot.get("state")
        if snapshot.get("state") in OFF_GRID_STATES:
            grid["available"] = True
            grid["grid_is_up"] = False
        # Base's energy mix covers the last 24 hours. Zero battery energy in
        # that window means zero today. Any other value cannot be split by day.
        mix = overview.get("energy_mix_24h")
        if (
            energy.get("battery_to_home_kwh") is None
            and mix is not None
            and mix.get("battery_to_home_kwh") == 0
        ):
            energy["battery_to_home_kwh"] = 0.0
        derived = self._derive_values(dashboard, usage, self._time_zone())
        self._apply_live_readings(derived, grid)

        # grid_to_home_kwh is Base's home load: everything the house used,
        # solar and battery included. Adding solar and battery to it would
        # count them twice.
        total_home = energy.get("grid_to_home_kwh") or 0.0
        solar_kwh = energy.get("solar_to_home_kwh") or 0.0
        battery_kwh = energy.get("battery_to_home_kwh") or 0.0
        derived["total_home_kwh"] = round(total_home, 3) if total_home > 0 else None
        if total_home > 0:
            # Solar can exceed home use when it exports, so cap at 100.
            derived["self_sufficiency_percent"] = round(
                min((solar_kwh + battery_kwh) / total_home, 1.0) * 100, 1
            )
        else:
            derived["self_sufficiency_percent"] = None
        if not dashboard.get("has_solar") and solar_kwh > 0:
            dashboard["has_solar"] = True
        intervals = overview.get("grid_support") or []
        derived["grid_support_events_24h"] = len(intervals) if overview.get("available") else None
        scheduled = (
            grid_support_active(intervals, int(time.time())) if overview.get("available") else None
        )
        live = live_grid_support(grid.get("grid_is_up"), grid.get("power_from_storage_kw"))
        # The event list can lag by 15 minutes. Live battery power catches
        # an event within a minute.
        if scheduled or live:
            derived["grid_support_active"] = True
            derived["grid_support_source"] = "battery_power" if live else "event_list"
        else:
            derived["grid_support_active"] = (
                False if scheduled is False or live is False else None
            )
            derived["grid_support_source"] = None

        return {
            "dashboard": dashboard,
            "usage": usage,
            "grid": grid,
            "wifi": self._poll.wifi,
            "energy": energy,
            "billing": self._poll.billing,
            "cycles": self._poll.cycles,
            "derived": derived,
            "snapshot": snapshot,
            "overview": overview,
            "billing_cycle": self._poll.billing_cycle,
            "battery_account": self._poll.battery_account,
            "energy_account": self._poll.energy_account,
            "sources": {
                "usage": self._poll.usage_source,
                "energy": self._poll.energy_source,
                "voltage": grid.get("voltage_source"),
            },
        }

    async def async_poll_grid_status(self, now=None) -> None:
        """ServiceContext and GridStatus. This is the only periodic caller."""
        if self.data is None:
            return
        try:
            await self._async_ensure_jwt()
            moment = self._monotonic()
            if self._poll.numeric_id is None and self._poll.slow_due(moment, first=False):
                await self._async_refresh_slow(moment)
            if self._poll.numeric_id is None:
                _LOGGER.debug("Fast poll skipped; numeric service location id is missing")
                return
            # An empty grid poll stays unknown. Do not keep the previous
            # "grid is up" reading across a miss.
            self._grid = await self._async_fetch_grid(self._poll.numeric_id)
            self._polls_since_snapshot += 1
            if self._snapshot_due():
                self._snapshot = await self._async_fetch_snapshot()
                self._polls_since_snapshot = 0
            # Do not use async_set_updated_data here. It restarts the
            # 15-minute refresh timer, and a 60-second poll would keep
            # pushing the usage refresh back so it never runs (issue #13).
            self.data = self._compose()
            self.async_update_listeners()
        except AuthenticationError as err:
            _LOGGER.debug("Fast grid poll auth failed: %s", err)
        except Exception as err:
            _LOGGER.debug("Fast grid poll failed: %s", err)

    @staticmethod
    def _derive_values(
        dashboard: dict[str, Any],
        usage: list[dict[str, Any]],
        time_zone: str | None = None,
    ) -> dict[str, Any]:
        """Derive additional sensor values from raw data."""
        battery_count = dashboard.get("battery_count", DEFAULT_BATTERY_COUNT)
        capacity_kwh = dashboard.get("capacity_kwh", battery_count * BATTERY_CAPACITY_PER_UNIT_KWH)

        derived: dict[str, Any] = {
            "battery_percent": None,
            "battery_count": battery_count,
            "capacity_kwh": capacity_kwh,
        }
        derived.update(summarize_recent_usage(usage, time_zone))
        return derived
