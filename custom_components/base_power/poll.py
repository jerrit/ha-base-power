"""Tiered polling for the Base Power API.

The 60-second tier is only ServiceContext and GridStatus. The coordinator
refresh is the 15-minute usage tier and does not repeat those two calls.
Dashboard, Wi-Fi, billing, and usage cycles are the 6-hour tier.
"""

from __future__ import annotations

import logging
from typing import Any

from .auth import AuthenticationError
from .const import (
    GRID_SCAN_INTERVAL_SECONDS,
    SLOW_SCAN_INTERVAL_SECONDS,
    USAGE_SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

FAST_METHODS = ("MobileGetServiceContext", "MobileGetGridStatus")
USAGE_METHODS = ("MobileGetRecentUsage", "MobileGetUsageEnergy")
SLOW_METHODS = (
    "MobileGetDashboardRoot",
    "MobileGetWifiMetrics",
    "MobileGetBillingMetadata",
    "MobileGetUsageCycles",
)


def include_fast_on_coordinator_refresh(first: bool) -> bool:
    """The first refresh fills grid data before the 60-second timer starts.

    Later coordinator ticks do not call ServiceContext or GridStatus.
    """
    return first


def steady_state_calls_per_hour() -> float:
    """Average API calls in one hour after startup."""
    fast = (3600 / GRID_SCAN_INTERVAL_SECONDS) * len(FAST_METHODS)
    usage = (3600 / USAGE_SCAN_INTERVAL_SECONDS) * len(USAGE_METHODS)
    slow = (3600 / SLOW_SCAN_INTERVAL_SECONDS) * len(SLOW_METHODS)
    return fast + usage + slow


def _empty_energy() -> dict[str, Any]:
    return {
        "grid_to_home_kwh": None,
        "solar_to_home_kwh": None,
        "battery_to_home_kwh": None,
    }


def _empty_wifi() -> dict[str, Any]:
    return {"scan": {}, "ssid": None, "signal": None, "connected": False}


class PollState:
    """Which tier is due, and the last successful payload for each one."""

    def __init__(self) -> None:
        self.numeric_id: int | None = None
        self.last_slow_at: float | None = None
        self.dashboard: dict[str, Any] = {
            "service_location_id": None,
            "battery_status": 0,
            "battery_count": 1,
            "has_solar": False,
        }
        self.usage: list[dict[str, Any]] = []
        self.wifi: dict[str, Any] = _empty_wifi()
        self.energy: dict[str, Any] = _empty_energy()
        self.billing: dict[str, Any] = {"amount_cents": None, "due_date": None}
        self.cycles: dict[str, Any] = {"asset_id": None, "cycle_start": None}

    def slow_due(self, now: float, *, first: bool) -> bool:
        """Slow tier on startup, every 6 hours, or while the numeric id is missing.

        A missing id retries on the usage interval, not on every fast poll.
        """
        if first or self.last_slow_at is None:
            return True
        if self.numeric_id is None and now - self.last_slow_at >= USAGE_SCAN_INTERVAL_SECONDS:
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


def _keep(previous: dict[str, Any], fetched: dict[str, Any] | None) -> dict[str, Any]:
    if fetched and any(value is not None for value in fetched.values()):
        return fetched
    return previous


async def refresh_slow(
    api: Any,
    state: PollState,
    address_id: str,
    preferred_ssid: str | None,
    now: float,
) -> None:
    """Refresh dashboard, Wi-Fi, billing, and cycles. One failure does not stop the others."""
    try:
        dashboard = await api.get_dashboard_root(address_id)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("DashboardRoot fetch failed: %s", err)
    else:
        numeric_id = dashboard.get("service_location_id") if dashboard else None
        if numeric_id:
            state.numeric_id = numeric_id
            state.dashboard = dashboard
    try:
        wifi = await api.get_wifi_metrics(address_id)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("WifiMetrics fetch failed: %s", err)
    else:
        if wifi and wifi.get("scan"):
            state.wifi = apply_wifi_preference(wifi, preferred_ssid)
        elif not state.wifi.get("scan"):
            state.wifi = apply_wifi_preference(wifi or _empty_wifi(), preferred_ssid)
    try:
        billing = await api.get_billing_metadata(address_id)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("BillingMetadata fetch failed: %s", err)
    else:
        state.billing = _keep(state.billing, billing)
    try:
        cycles = await api.get_usage_cycles(address_id)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("UsageCycles fetch failed: %s", err)
    else:
        state.cycles = _keep(state.cycles, cycles)
    state.mark_slow(now)


async def refresh_usage(
    api: Any,
    state: PollState,
    address_id: str,
    time_zone: str | None,
) -> None:
    """Refresh recent usage and usage energy. Failures keep the previous series."""
    try:
        usage = await api.get_recent_usage(address_id)
    except AuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug("RecentUsage fetch failed: %s", err)
    else:
        if usage:
            state.usage = usage
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
        state.energy = _keep(state.energy, energy)
