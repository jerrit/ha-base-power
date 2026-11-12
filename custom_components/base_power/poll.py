"""Tiered polling for the Base Power API.

The 60-second tier is ServiceContext and GridStatus. The battery snapshot
runs every 10 minutes, and on every 60-second poll while something points
to an outage. The coordinator refresh is the 15-minute usage tier: the v2
daily overview, with RecentUsage and UsageEnergy only as fallbacks. It does
not repeat the 60-second calls. Dashboard, Wi-Fi, billing, usage cycles,
the billing cycle, the location, and the daily totals are the 6-hour tier.
A 6-hour refresh with a failed call runs again after 15 minutes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .api import local_day_interval
from .api_v2 import (
    empty_battery_account,
    empty_billing_cycle,
    empty_energy_account,
    empty_location,
    empty_overview,
)
from .auth import AuthenticationError
from .const import (
    DAILY_ENERGY_LOOKBACK_SECONDS,
    GRID_SCAN_INTERVAL_SECONDS,
    SLOW_SCAN_INTERVAL_SECONDS,
    USAGE_SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

FAST_METHODS = ("MobileGetServiceContext", "MobileGetGridStatus")
SNAPSHOT_METHODS = ("GetSnapshot",)
# While everything looks normal the snapshot runs on every Nth 60-second poll.
SNAPSHOT_EVERY_FAST_POLLS = 10
USAGE_METHODS = ("GetDailyOverview",)
# Called only when the overview fails or is missing that data.
USAGE_FALLBACK_METHODS = ("MobileGetRecentUsage", "MobileGetUsageEnergy")
SLOW_METHODS = (
    "MobileGetDashboardRoot",
    "MobileGetWifiMetrics",
    "MobileGetBillingMetadata",
    "MobileGetUsageCycles",
    "GetCurrentBillingCycle",
    "GetAccount",
    "ListAccounts",
    "GetLocation",
    "GetDailyEnergy",
)


def include_fast_on_coordinator_refresh(first: bool) -> bool:
    """The first refresh fills grid data before the 60-second timer starts.

    Later coordinator ticks do not call ServiceContext or GridStatus.
    """
    return first


def steady_state_calls_per_hour() -> float:
    """Average API calls in one hour after startup."""
    fast = (3600 / GRID_SCAN_INTERVAL_SECONDS) * len(FAST_METHODS)
    snapshot = (3600 / GRID_SCAN_INTERVAL_SECONDS / SNAPSHOT_EVERY_FAST_POLLS) * len(
        SNAPSHOT_METHODS
    )
    usage = (3600 / USAGE_SCAN_INTERVAL_SECONDS) * len(USAGE_METHODS)
    slow = (3600 / SLOW_SCAN_INTERVAL_SECONDS) * len(SLOW_METHODS)
    return fast + snapshot + usage + slow


def _empty_energy() -> dict[str, Any]:
    return {
        "grid_to_home_kwh": None,
        "solar_to_home_kwh": None,
        "battery_to_home_kwh": None,
    }


def _empty_wifi() -> dict[str, Any]:
    return {"scan": {}, "ssid": None, "signal": None, "connected": False, "last_updated": None}


class PollState:
    """Which tier is due, and the last successful payload for each one."""

    def __init__(self) -> None:
        self.numeric_id: int | None = None
        self.last_slow_at: float | None = None
        # True when a call in the last slow refresh failed
        self.slow_failed = False
        self.dashboard: dict[str, Any] = {
            "service_location_id": None,
            "battery_status": 0,
            "battery_count": 1,
            "has_solar": False,
            "time_zone": None,
            "upcoming_service_at": None,
        }
        self.usage: list[dict[str, Any]] = []
        self.wifi: dict[str, Any] = _empty_wifi()
        self.energy: dict[str, Any] = _empty_energy()
        # Local midnight (Unix seconds) of the day self.energy belongs to.
        self.energy_day: int | None = None
        self.billing: dict[str, Any] = {
            "amount_cents": None,
            "last_bill_cents": None,
        }
        self.cycles: dict[str, Any] = {"account_id": None, "cycle_start": None}
        self.overview: dict[str, Any] = empty_overview()
        # "overview" or "legacy" for the last 15-minute usage and today's energy
        self.usage_source: str | None = None
        self.energy_source: str | None = None
        self.billing_cycle: dict[str, Any] = empty_billing_cycle()
        self.battery_account: dict[str, Any] = empty_battery_account()
        self.energy_account: dict[str, Any] = empty_energy_account()
        self.location: dict[str, Any] = empty_location()
        # Base's daily totals, oldest first. Used to repair the running totals.
        self.daily_energy: list[dict[str, Any]] = []

    def slow_due(self, now: float, *, first: bool) -> bool:
        """Slow tier on startup, every 6 hours, or sooner after a failure.

        A missing numeric id or a failed call retries on the usage interval,
        not on every fast poll.
        """
        if first or self.last_slow_at is None:
            return True
        retry = self.numeric_id is None or self.slow_failed
        if retry and now - self.last_slow_at >= USAGE_SCAN_INTERVAL_SECONDS:
            return True
        return now - self.last_slow_at >= SLOW_SCAN_INTERVAL_SECONDS

    def tiers_for_periodic_refresh(self, now: float, *, first: bool) -> tuple[str, ...]:
        """Tiers for a coordinator tick. The fast grid tier is never included."""
        tiers: list[str] = []
        if self.slow_due(now, first=first):
            tiers.append("slow")
        tiers.append("usage")
        return tuple(tiers)

    def mark_slow(self, now: float) -> None:
        self.last_slow_at = now


def apply_wifi_preference(wifi: dict[str, Any], preferred_ssid: str | None) -> dict[str, Any]:
    """Keep the selected network. Do not substitute a stronger one."""
    scan: dict[str, int | None] = wifi.get("scan") or {}
    if preferred_ssid and preferred_ssid in scan:
        wifi["ssid"] = preferred_ssid
        wifi["signal"] = scan[preferred_ssid]
        wifi["connected"] = True
    elif preferred_ssid:
        wifi["ssid"] = preferred_ssid
        wifi["connected"] = False
    return wifi


def resolve_wifi(
    wifi: dict[str, Any],
    live_ssid: str | None,
    live_state: str | None,
    saved_ssid: str | None,
) -> dict[str, Any]:
    """The battery's Wi-Fi network, its signal, and whether it is connected.

    Base's service context names the network the battery is on, so that
    wins. Without it, the network saved in the options is used, then the
    only network in the scan. The signal comes from the scan.
    """
    scan: dict[str, int | None] = wifi.get("scan") or {}
    ssid = live_ssid or saved_ssid or (next(iter(scan)) if len(scan) == 1 else None)
    if live_ssid:
        source = "base"
    elif saved_ssid:
        source = "option"
    else:
        source = "scan" if ssid else None
    if live_state is not None:
        connected = live_state == "connected"
    else:
        connected = ssid is not None and ssid in scan
    return {
        "scan": scan,
        "ssid": ssid,
        "signal": scan.get(ssid) if ssid else None,
        "connected": connected,
        "state": live_state,
        "source": source,
        "last_updated": wifi.get("last_updated"),
    }


def _keep(previous: dict[str, Any], fetched: dict[str, Any] | None) -> dict[str, Any]:
    if fetched and any(value is not None for value in fetched.values()):
        return fetched
    return previous


_FAILED: Any = object()


async def refresh_slow(
    api: Any,
    state: PollState,
    address_id: str,
    preferred_ssid: str | None,
    now: float,
) -> None:
    """Refresh the 6-hour tier. One failure does not stop the others.

    A failed call sets ``state.slow_failed``, so the tier runs again on the
    next usage refresh in place of waiting 6 hours.
    """
    failed = False

    async def fetch(name: str, call: Callable[[], Awaitable[Any]]) -> Any:
        nonlocal failed
        try:
            return await call()
        except AuthenticationError:
            raise
        except Exception as err:
            _LOGGER.debug("%s fetch failed: %s", name, err)
            failed = True
            return _FAILED

    dashboard = await fetch("DashboardRoot", lambda: api.get_dashboard_root(address_id))
    if dashboard is not _FAILED:
        numeric_id = dashboard.get("service_location_id") if dashboard else None
        if numeric_id:
            state.numeric_id = numeric_id
            state.dashboard = dashboard

    wifi = await fetch("WifiMetrics", lambda: api.get_wifi_metrics(address_id))
    if wifi is not _FAILED:
        if wifi and wifi.get("scan"):
            state.wifi = apply_wifi_preference(wifi, preferred_ssid)
        elif not state.wifi.get("scan"):
            state.wifi = apply_wifi_preference(wifi or _empty_wifi(), preferred_ssid)

    kept_pieces: tuple[tuple[str, str, Callable[[], Awaitable[Any]]], ...] = (
        ("BillingMetadata", "billing", lambda: api.get_billing_metadata(address_id)),
        ("UsageCycles", "cycles", lambda: api.get_usage_cycles(address_id)),
        ("CurrentBillingCycle", "billing_cycle", lambda: api.get_current_billing_cycle(address_id)),
        ("Battery account", "battery_account", lambda: api.get_battery_account(address_id)),
        ("Energy account", "energy_account", lambda: api.get_energy_account(address_id)),
    )
    for name, attribute, call in kept_pieces:
        fetched = await fetch(name, call)
        if fetched is not _FAILED:
            setattr(state, attribute, _keep(getattr(state, attribute), fetched))

    location = await fetch("Location", lambda: api.get_location(address_id))
    if location is not _FAILED:
        if location and location.get("available"):
            state.location = location
        else:
            failed = True

    wall = int(time.time())
    days = await fetch(
        "DailyEnergy",
        lambda: api.get_daily_energy(address_id, wall - DAILY_ENERGY_LOOKBACK_SECONDS, wall),
    )
    if days is not _FAILED and days:
        state.daily_energy = days

    state.slow_failed = failed
    state.mark_slow(now)


async def refresh_usage(
    api: Any,
    state: PollState,
    address_id: str,
    time_zone: str | None,
    now: datetime | None = None,
) -> bool:
    """Refresh the daily overview. Fall back to the older calls per piece.

    Returns True when Base sent new usage or energy data, False when every
    call failed or came back empty.

    RecentUsage runs only when the overview has no 15-minute samples.
    UsageEnergy runs only when it has no hourly samples for today.
    Failures keep the previous data, but never past local midnight: Base
    posts the first hour of a new day about an hour late, and until then
    today's energy is 0, not yesterday's total.
    """
    day_start = local_day_interval(time_zone, now)[0]
    fresh = False
    overview: dict[str, Any] | None = None
    try:
        overview = await api.get_daily_overview(address_id, time_zone)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("DailyOverview fetch failed: %s", err)
    if overview and overview.get("available"):
        state.overview = overview
        fresh = True
    else:
        overview = None

    home_power = (overview or {}).get("home_power") or []
    if home_power:
        state.usage = home_power
        state.usage_source = "overview"
    else:
        try:
            usage = await api.get_recent_usage(address_id)
        except AuthenticationError:
            raise
        except Exception as err:
            _LOGGER.debug("RecentUsage fetch failed: %s", err)
        else:
            if usage:
                state.usage = usage
                state.usage_source = "legacy"
                fresh = True

    energy_today = (overview or {}).get("energy_today")
    if energy_today:
        state.energy = energy_today
        state.energy_source = "overview"
        state.energy_day = day_start
    else:
        try:
            energy = await api.get_usage_energy(
                address_id,
                numeric_service_location_id=state.numeric_id,
                time_zone=time_zone,
            )
        except AuthenticationError:
            raise
        except Exception as err:
            _LOGGER.debug("UsageEnergy fetch failed: %s", err)
        else:
            kept = _keep(state.energy, energy)
            if kept is energy:
                state.energy_source = "legacy"
                state.energy_day = day_start
                fresh = True
            state.energy = kept

    if state.energy_day is not None and state.energy_day < day_start:
        state.energy = {
            "grid_to_home_kwh": 0.0,
            "solar_to_home_kwh": 0.0,
            "battery_to_home_kwh": None,
        }
        state.energy_source = "day_start"
        state.energy_day = day_start
    return fresh
