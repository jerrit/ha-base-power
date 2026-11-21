"""Data coordinator for Base Power integration."""

from __future__ import annotations

import logging
import math
import time
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BasePowerApiClient, local_day_interval, summarize_recent_usage
from .api_v2 import (
    GRID_SUPPORT_STORAGE_KW,
    OFF_GRID_STATES,
    empty_snapshot,
    grid_support_active,
    live_grid_support,
)
from .auth import AuthenticationError, BasePowerAuth
from .capacity import CapacityEstimate, nearest_system
from .const import (
    BATTERY_CAPACITY_PER_UNIT_KWH,
    CHARGING_BOUNCE_SECONDS,
    CHARGING_FIRST_HOLD_SECONDS,
    CHARGING_HOLD_SECONDS,
    CHARGING_STORAGE_KW,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_COUNT,
    CONF_WIFI_SSID,
    CONTEXT_HOLD_SECONDS,
    DEFAULT_BATTERY_COUNT,
    HARDWARE_SKU_CAPACITY,
    NAMEPLATE_MATCH_TOLERANCE,
    SCAN_INTERVAL_SECONDS,
    STORAGE_VERSION,
    USAGE_STALE_AFTER_FAILURES,
    battery_model_capacity,
    battery_variant_count,
    resolve_battery_capacity,
    totals_store_key,
)
from .poll import (
    SNAPSHOT_EVERY_FAST_POLLS,
    PollState,
    include_fast_on_coordinator_refresh,
    refresh_slow,
    refresh_usage,
    resolve_wifi,
)
from .totals import EnergyTotals

_LOGGER = logging.getLogger(__name__)

_SOURCE_TEXT = {
    "nameplate": "the size Base reports for the product",
    "estimated": "an estimate from Base's backup time",
}


class BasePowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate data fetching from Base Power API."""

    # Usage refreshes in a row that brought no new data
    _usage_failures = 0
    # True while a 60-second poll is still waiting on Base
    _fast_poll_running = False
    # Last good service context, and when it arrived
    _last_context: dict[str, Any] | None = None
    _last_context_at = 0.0
    # Last known direction of the battery's charge, and when it last rose
    _charging: bool | None = None
    _charge_rose_at: float | None = None
    # How long the current rise holds Battery Charging on
    _charge_hold = CHARGING_FIRST_HOLD_SECONDS
    # The level the charge last fell from, and when
    _fell_from: float | None = None
    _fell_at: float | None = None
    # True while live battery power shows a charge
    _power_charging = False
    # Unmapped hardware already logged, so the warning shows once
    _warned_hardware: str | None = None

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
            config_entry=config_entry,
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
        self._totals = EnergyTotals()
        self._totals_store: Store | None = None
        self._capacity_estimate = CapacityEstimate()

    async def async_load_totals(self) -> None:
        """Load the running energy totals saved before the last restart."""
        entry_id = getattr(self._config_entry, "entry_id", self.service_location_id)
        self._totals_store = Store(self.hass, STORAGE_VERSION, totals_store_key(entry_id))
        self._totals = EnergyTotals.from_dict(await self._totals_store.async_load())

    def _update_totals(self) -> None:
        """Add new hours from the overview and save when they change.

        Finished days are then checked against Base's daily totals, which
        fills in hours missed while Home Assistant or Base was down.
        """
        now = time.time()
        hours = (self._poll.overview or {}).get("hours") or []
        changed = self._totals.add_hours(hours, now)
        days = getattr(self._poll, "daily_energy", None) or []
        if days and self._totals.reconcile_days(days, now):
            changed = True
            _LOGGER.info("Running energy totals caught up from Base's daily totals")
        if changed and self._totals_store is not None:
            self._totals_store.async_delay_save(self._totals.as_dict, 30)

    @property
    def usage_stale(self) -> bool:
        """True when usage and energy data has stopped updating."""
        return self._usage_failures >= USAGE_STALE_AFTER_FAILURES

    def _note_usage_refresh(self, fresh: bool) -> None:
        """Track failed usage refreshes and log once when the data goes stale."""
        if fresh:
            if self.usage_stale:
                _LOGGER.info("Base Power usage data is updating again")
            self._usage_failures = 0
            return
        self._usage_failures += 1
        if self._usage_failures == USAGE_STALE_AFTER_FAILURES:
            _LOGGER.warning(
                "Base Power usage data has not updated for %d refreshes. "
                "Usage and energy sensors are unavailable until it does",
                USAGE_STALE_AFTER_FAILURES,
            )

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
                fresh = await refresh_usage(
                    self.api,
                    self._poll,
                    self.service_location_id,
                    self._time_zone(),
                )
                self._note_usage_refresh(bool(fresh))
                self._update_totals()
            if include_fast_on_coordinator_refresh(first):
                self._grid = await self._async_fetch_grid(self._poll.numeric_id)
                self._sample_capacity()
                self._snapshot = await self._async_fetch_snapshot()
            return self._compose()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except Exception as err:
            if first:
                raise UpdateFailed(f"Error communicating with Base Power API: {err}") from err
            _LOGGER.debug("Usage refresh failed; keeping cached data: %s", err)
            self._note_usage_refresh(False)
            # Compose again so the stale flag reaches the sensors.
            return self._compose()

    def _monotonic(self) -> float:
        """Clock for tier spacing. Tests can replace this."""
        return time.monotonic()

    def _time_zone(self) -> str | None:
        """The site's time zone from Base, or Home Assistant's when Base sent none.

        Base's days start at the site's local midnight, so day totals use
        the same zone.
        """
        for source in (self._poll.location, self._poll.dashboard):
            name = (source or {}).get("time_zone")
            if not name:
                continue
            try:
                ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
            return name
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
        context = self._hold_context(context)
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

    def _sample_capacity(self) -> None:
        """Add the latest charge and 750 W backup time to the size estimate."""
        self._capacity_estimate.add(
            self._grid.get("battery_soc_percent"), self._grid.get("backup_hours_750w")
        )

    def _hold_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the last good context when this one missed and is recent.

        Base sometimes fails one call. Without this, battery level, power,
        and backup time go unknown for a minute. A longer gap still shows
        unknown, so an old reading is never passed off as current.
        """
        now = self._monotonic()
        if context.get("available"):
            self._last_context = context
            self._last_context_at = now
            return context
        if (
            self._last_context is not None
            and now - self._last_context_at <= CONTEXT_HOLD_SECONDS
        ):
            _LOGGER.debug(
                "ServiceContext missed; using the one from %.0f s ago",
                now - self._last_context_at,
            )
            return self._last_context
        return context

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

        derived["battery_charging"] = self._charging_state(
            derived.get("battery_percent"), grid.get("power_from_storage_kw")
        )

    def _charging_state(self, percent: float | None, storage_kw: Any) -> bool | None:
        """Whether the battery is charging.

        Live battery power comes first: power going into the battery means
        charging, even at 99 or 100%. When it stops, so does Battery Charging.

        Without it, the charge percent decides. Base reports it as a whole
        percent, so one poll to the next rarely differs. A rise means
        charging, and that holds for a while: an hour after a single rise, or
        twice the time between the last two rises (up to
        CHARGING_HOLD_SECONDS) once the pace is known. A slow charge can take
        hours per percent, and may not show in the power reading at all.

        A rise back to the level the charge just fell from is rounding, not
        charging, so a battery idling at its reserve does not flick on. A
        fall, a full battery, or the battery powering the home means not
        charging. Unknown until the charge has moved once.
        """
        storage = (
            float(storage_kw)
            if isinstance(storage_kw, (int, float))
            and not isinstance(storage_kw, bool)
            and math.isfinite(storage_kw)
            else None
        )
        power_charging = storage is not None and storage <= -CHARGING_STORAGE_KW
        if percent is None:
            self._prev_soc = None
            self._power_charging = power_charging
            return True if power_charging else None
        now = self._monotonic()
        if self._prev_soc is not None:
            delta = percent - self._prev_soc
            if delta >= 0.5:
                bounce = (
                    not self._charging
                    and self._fell_from is not None
                    and self._fell_at is not None
                    and percent <= self._fell_from
                    and now - self._fell_at <= CHARGING_BOUNCE_SECONDS
                )
                if bounce:
                    if self._charging is None:
                        self._charging = False
                else:
                    if self._charging and self._charge_rose_at is not None:
                        gap = now - self._charge_rose_at
                        self._charge_hold = min(
                            max(2 * gap, CHARGING_FIRST_HOLD_SECONDS),
                            CHARGING_HOLD_SECONDS,
                        )
                    else:
                        self._charge_hold = CHARGING_FIRST_HOLD_SECONDS
                    self._charging = True
                    self._charge_rose_at = now
            elif delta <= -0.5:
                self._charging = False
                self._fell_from = self._prev_soc
                self._fell_at = now
        self._prev_soc = percent
        if power_charging:
            self._power_charging = True
            return True
        if self._power_charging and storage is not None:
            # Live power showed a charge and now shows it stopped.
            self._power_charging = False
            self._charging = False
        discharging = storage is not None and storage >= GRID_SUPPORT_STORAGE_KW
        if percent >= 99.5 or discharging or (
            self._charging
            and self._charge_rose_at is not None
            and now - self._charge_rose_at > self._charge_hold
        ):
            self._charging = False
        return self._charging

    def _warn_unknown_hardware(
        self,
        sku: str | None,
        detected: tuple[int, float] | None,
        batteries: list[dict[str, Any]],
        model: tuple[int, float] | None,
        source: str,
    ) -> None:
        """Log once when Base reports battery hardware this version can't size.

        Capacity then comes from Base's nameplate, the backup-time estimate,
        or the saved option. The raw values stay on System Capacity's
        attributes. The source is part of the key, so the log shows again
        when it changes.
        """
        unknown_sku = bool(sku) and detected is None and sku != "NO_BATTERY"
        if detected is not None or not (unknown_sku or (batteries and model is None)):
            return
        described = ", ".join(
            f"{b.get('generation') or b.get('generation_code')}"
            f"/{b.get('variant') or b.get('variant_code')}"
            for b in batteries
        )
        key = f"{sku}|{described}|{source}"
        if key == self._warned_hardware:
            return
        self._warned_hardware = key
        _LOGGER.warning(
            "Base Power reports battery hardware this version doesn't know "
            "(SKU %s, batteries %s). System Capacity uses %s. Please open an "
            "issue with your diagnostics so it can be added",
            sku or "none",
            described or "none",
            _SOURCE_TEXT.get(source, "the Battery Capacity option"),
        )

    def _resolve_capacity(self, batteries: list[dict[str, Any]]) -> dict[str, Any]:
        """Battery count and size, from the most reliable source available.

        Size: a known SKU, then a known battery model, then Base's nameplate
        from GetHome, then the estimate from Base's backup time, then the
        saved option. The settings form pre-fills 25 kWh, so a saved value
        may never have been chosen on purpose, which is why the estimate
        ranks above it.

        Count: from the SKU or model when known, otherwise from the battery
        types, which give the unit count for any generation.
        """
        options = self._config_entry.options
        sku = (self._poll.battery_account or {}).get("hardware_sku")
        detected = HARDWARE_SKU_CAPACITY.get(sku) if sku else None
        model = battery_model_capacity(batteries)
        type_units = battery_variant_count(batteries)
        home = getattr(self._poll, "home", None) or {}
        nameplate = home.get("nameplate_capacity_kwh")
        estimate = self._capacity_estimate.system(type_units)

        if detected is not None:
            (units, kwh), source = detected, "sku"
        elif model is not None:
            (units, kwh), source = model, "model"
        elif nameplate:
            # The unit count comes from the matching known size, if any.
            known = nearest_system(nameplate, tolerance=NAMEPLATE_MATCH_TOLERANCE)
            units = known[0] if known else DEFAULT_BATTERY_COUNT
            kwh, source = nameplate, "nameplate"
        elif estimate is not None:
            (units, kwh), source = estimate, "estimated"
        else:
            resolved = resolve_battery_capacity(options.get(CONF_BATTERY_CAPACITY))
            if resolved is not None:
                (units, kwh), source = resolved, "option"
            elif CONF_BATTERY_COUNT in options:
                units = options[CONF_BATTERY_COUNT]
                kwh = units * BATTERY_CAPACITY_PER_UNIT_KWH
                source = "option"
            else:
                units = DEFAULT_BATTERY_COUNT
                kwh = units * BATTERY_CAPACITY_PER_UNIT_KWH
                source = "default"
        count_source = source
        if source not in ("sku", "model") and type_units is not None:
            units, count_source = type_units, "battery_type"
        self._warn_unknown_hardware(sku, detected, batteries, model, source)
        return {
            "battery_count": units,
            "battery_count_source": count_source,
            "capacity_kwh": kwh,
            "capacity_source": source,
            "hardware_sku": sku,
            "detected_capacity_kwh": detected[1] if detected else None,
            "model_capacity_kwh": model[1] if model else None,
            "nameplate_capacity_kwh": nameplate,
            "product_name": home.get("product_name"),
            "estimated_usable_kwh": self._capacity_estimate.usable_kwh,
            "estimated_capacity_kwh": estimate[1] if estimate else None,
        }

    def _compose(self) -> dict[str, Any]:
        """Build coordinator data from the cached tiers and the latest grid."""
        dashboard = dict(self._poll.dashboard)
        location = self._poll.location or {}
        dashboard.update(self._resolve_capacity(location.get("batteries") or []))
        dashboard["batteries"] = location.get("batteries") or []
        dashboard["upcoming_service_at"] = (
            location.get("upcoming_service_at") or dashboard.get("upcoming_service_at")
        )

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
        # The service context has the 750 W estimate every minute. The
        # snapshot has it every 10 minutes and covers a missing context.
        backup_750w = grid.get("backup_hours_750w")
        if backup_750w is None:
            backup_750w = snapshot.get("backup_hours_750w")
        derived["backup_hours_750w"] = backup_750w
        wifi = resolve_wifi(
            self._poll.wifi,
            grid.get("wifi_ssid"),
            grid.get("wifi_state"),
            self._config_entry.options.get(CONF_WIFI_SSID) or None,
        )

        # grid_to_home_kwh is Base's home load: everything the house used,
        # solar and battery included. Adding solar and battery to it would
        # count them twice.
        # At midnight the day starts at 0 kWh. Show 0, not unknown, until
        # Base posts the first hour. Unknown means Base sent no reading.
        home_kwh = energy.get("grid_to_home_kwh")
        total_home = home_kwh or 0.0
        solar_kwh = energy.get("solar_to_home_kwh") or 0.0
        battery_kwh = energy.get("battery_to_home_kwh") or 0.0
        derived["total_home_kwh"] = round(total_home, 3) if home_kwh is not None else None
        if total_home > 0:
            # Solar can exceed home use when it exports, so cap at 100.
            derived["self_sufficiency_percent"] = round(
                min((solar_kwh + battery_kwh) / total_home, 1.0) * 100, 1
            )
        elif home_kwh is not None:
            derived["self_sufficiency_percent"] = 0.0
        else:
            derived["self_sufficiency_percent"] = None
        if not dashboard.get("has_solar") and (solar_kwh > 0 or location.get("has_solar")):
            dashboard["has_solar"] = True
        totals = self._totals
        derived["home_usage_total_kwh"] = round(totals.home_kwh, 3) if totals.started else None
        derived["solar_total_kwh"] = round(totals.solar_kwh, 3) if totals.started else None
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
            "wifi": wifi,
            "energy": energy,
            "billing": self._poll.billing,
            "cycles": self._poll.cycles,
            "derived": derived,
            "snapshot": snapshot,
            "overview": overview,
            "billing_cycle": self._poll.billing_cycle,
            "battery_account": self._poll.battery_account,
            "energy_account": self._poll.energy_account,
            "location": location,
            "home": getattr(self._poll, "home", None) or {},
            "sources": {
                "usage": self._poll.usage_source,
                "energy": self._poll.energy_source,
                "voltage": grid.get("voltage_source"),
                "time_zone": self._time_zone(),
            },
            "usage_stale": self.usage_stale,
        }

    async def async_poll_grid_status(self, now=None) -> None:
        """ServiceContext and GridStatus. This is the only periodic caller."""
        if self.data is None:
            return
        # A slow reply must not pile up polls behind it. Skip this one.
        if self._fast_poll_running:
            _LOGGER.debug("Fast grid poll skipped; the previous one is still running")
            return
        self._fast_poll_running = True
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
            self._sample_capacity()
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
        finally:
            self._fast_poll_running = False

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
