"""Tier schedule, cache retention, and failure isolation."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "custom_components.base_power"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "custom_components" / "base_power" / filename
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ensure_package() -> None:
    if _PKG in sys.modules and hasattr(sys.modules[_PKG], "__path__"):
        return
    components = sys.modules.get("custom_components")
    if components is None:
        components = types.ModuleType("custom_components")
        components.__path__ = [str(_ROOT / "custom_components")]
        sys.modules["custom_components"] = components
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / "custom_components" / "base_power")]
    package.__package__ = _PKG
    sys.modules[_PKG] = package


def _load_poll():
    _ensure_package()
    if _PKG + ".const" not in sys.modules:
        _load(_PKG + ".const", "const.py")
    if _PKG + ".auth" not in sys.modules:
        _load(_PKG + ".auth", "auth.py")
    if _PKG + ".poll" not in sys.modules:
        _load(_PKG + ".poll", "poll.py")
    return sys.modules[_PKG + ".poll"]


_poll = _load_poll()
PollState = _poll.PollState
refresh_slow = _poll.refresh_slow
refresh_usage = _poll.refresh_usage
FAST_METHODS = _poll.FAST_METHODS
USAGE_METHODS = _poll.USAGE_METHODS
SLOW_METHODS = _poll.SLOW_METHODS
GRID_SCAN_INTERVAL_SECONDS = _poll.GRID_SCAN_INTERVAL_SECONDS
USAGE_SCAN_INTERVAL_SECONDS = _poll.USAGE_SCAN_INTERVAL_SECONDS
SLOW_SCAN_INTERVAL_SECONDS = _poll.SLOW_SCAN_INTERVAL_SECONDS


def _run(coro):
    return asyncio.run(coro)


class RecordingApi:
    """Records method names and can fail selected calls."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail or set()

    async def _hit(self, name: str, value):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(name)
        return value

    async def get_dashboard_root(self, address):
        return await self._hit(
            "MobileGetDashboardRoot",
            {"service_location_id": 7, "battery_count": 1, "has_solar": False},
        )

    async def get_wifi_metrics(self, address):
        return await self._hit("MobileGetWifiMetrics", {"scan": {"Home": 40}})

    async def get_billing_metadata(self, address):
        return await self._hit("MobileGetBillingMetadata", {"amount_cents": 10, "due_date": None})

    async def get_usage_cycles(self, address):
        return await self._hit("MobileGetUsageCycles", {"asset_id": "asset-1", "cycle_start": 10})

    async def get_recent_usage(self, address):
        return await self._hit(
            "MobileGetRecentUsage",
            [{"timestamp": 1, "kwh": 1.5, "watts": 1000}],
        )

    async def get_usage_energy(self, address, numeric_service_location_id=None, time_zone=None):
        return await self._hit(
            "MobileGetUsageEnergy",
            {"grid_to_home_kwh": 4.0, "solar_to_home_kwh": 0.0, "battery_to_home_kwh": None},
        )

    async def get_service_context(self, numeric_id):
        return await self._hit("MobileGetServiceContext", {"power_from_grid_kw": 1.3})

    async def get_grid_status(self, numeric_id):
        return await self._hit("MobileGetGridStatus", {"voltage_volts": 245.0})


def test_steady_state_is_under_130_calls_per_hour():
    per_hour = _poll.steady_state_calls_per_hour()
    fast = (3600 / GRID_SCAN_INTERVAL_SECONDS) * len(FAST_METHODS)
    usage = (3600 / USAGE_SCAN_INTERVAL_SECONDS) * len(USAGE_METHODS)
    slow = (3600 / SLOW_SCAN_INTERVAL_SECONDS) * len(SLOW_METHODS)
    assert fast == 120
    assert usage == 8
    assert slow == 4 / 6
    assert per_hour == fast + usage + slow
    assert per_hour < 130
    # The previous schedule was a 5-minute 8-call refresh plus the 60-second pair.
    previous = (3600 / 300) * 8 + (3600 / 60) * 2
    assert previous == 216
    assert per_hour < previous


def test_periodic_refresh_does_not_include_the_fast_tier():
    state = PollState()
    startup = state.tiers_for_periodic_refresh(0, first=True)
    assert startup == ("slow", "usage")
    assert "fast" not in startup
    assert _poll.include_fast_on_coordinator_refresh(True) is True
    assert _poll.include_fast_on_coordinator_refresh(False) is False

    state.mark_slow(0)
    state.numeric_id = 7
    assert state.tiers_for_periodic_refresh(USAGE_SCAN_INTERVAL_SECONDS, first=False) == ("usage",)
    assert state.tiers_for_periodic_refresh(SLOW_SCAN_INTERVAL_SECONDS, first=False) == (
        "slow",
        "usage",
    )

    state.numeric_id = None
    state.mark_slow(0)
    assert state.slow_due(USAGE_SCAN_INTERVAL_SECONDS, first=False) is True
    assert state.slow_due(60, first=False) is False


def test_one_hour_after_startup_matches_the_tier_counts():
    state = PollState()
    state.mark_slow(0)
    state.numeric_id = 7
    usage_ticks = 0
    slow_ticks = 0
    fast_ticks = 0
    for second in range(60, 3600 + 1, 60):
        fast_ticks += 1
        if second % USAGE_SCAN_INTERVAL_SECONDS == 0:
            tiers = state.tiers_for_periodic_refresh(second, first=False)
            assert "fast" not in tiers
            if "usage" in tiers:
                usage_ticks += 1
            if "slow" in tiers:
                slow_ticks += 1
                state.mark_slow(second)
    assert fast_ticks == 60
    assert usage_ticks == 4
    assert slow_ticks == 0
    calls = fast_ticks * len(FAST_METHODS) + usage_ticks * len(USAGE_METHODS)
    assert calls == 128


def test_failed_slow_tier_keeps_cache_and_does_not_block_fast_calls():
    state = PollState()
    state.numeric_id = 9
    state.usage = [{"timestamp": 1, "kwh": 2.0, "watts": 800}]
    state.energy = {
        "grid_to_home_kwh": 3.0,
        "solar_to_home_kwh": 0.0,
        "battery_to_home_kwh": None,
    }
    state.billing = {"amount_cents": 100, "due_date": "2026-02-01"}
    state.wifi = {"scan": {"Home": 10}, "ssid": "Home", "signal": 10, "connected": True}

    api = RecordingApi(
        fail=set(SLOW_METHODS) | set(USAGE_METHODS)
    )

    async def check() -> None:
        await refresh_slow(api, state, "loc-1", "Home", now=0)
        await refresh_usage(api, state, "loc-1", "UTC")
        await api.get_service_context(state.numeric_id)
        await api.get_grid_status(state.numeric_id)

    _run(check())
    assert state.numeric_id == 9
    assert state.usage[0]["kwh"] == 2.0
    assert state.energy["grid_to_home_kwh"] == 3.0
    assert state.billing["amount_cents"] == 100
    assert state.wifi["ssid"] == "Home"
    assert api.calls[-2:] == ["MobileGetServiceContext", "MobileGetGridStatus"]


def test_successful_usage_refresh_does_not_call_grid_and_keeps_wifi():
    state = PollState()
    state.numeric_id = 7
    state.mark_slow(0)
    state.wifi = {"scan": {"Home": 10}, "ssid": "Home", "signal": 10, "connected": True}
    api = RecordingApi()

    async def check() -> None:
        tiers = state.tiers_for_periodic_refresh(USAGE_SCAN_INTERVAL_SECONDS, first=False)
        assert tiers == ("usage",)
        await refresh_usage(api, state, "loc-1", "UTC")

    _run(check())
    assert api.calls == list(USAGE_METHODS)
    assert state.usage[0]["kwh"] == 1.5
    assert state.energy["grid_to_home_kwh"] == 4.0
    assert state.wifi["ssid"] == "Home"


def test_empty_usage_poll_does_not_replace_a_saved_series():
    state = PollState()
    state.numeric_id = 7
    state.usage = [{"timestamp": 5, "kwh": 1.25, "watts": 500}]
    state.energy = {
        "grid_to_home_kwh": 6.0,
        "solar_to_home_kwh": None,
        "battery_to_home_kwh": None,
    }

    class EmptyApi(RecordingApi):
        async def get_recent_usage(self, address):
            self.calls.append("MobileGetRecentUsage")
            return []

        async def get_usage_energy(self, address, numeric_service_location_id=None, time_zone=None):
            self.calls.append("MobileGetUsageEnergy")
            return {
                "grid_to_home_kwh": None,
                "solar_to_home_kwh": None,
                "battery_to_home_kwh": None,
            }

    _run(refresh_usage(EmptyApi(), state, "loc-1", "UTC"))
    assert state.usage[0]["kwh"] == 1.25
    assert state.energy["grid_to_home_kwh"] == 6.0
