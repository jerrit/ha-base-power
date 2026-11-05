"""Coordinator rules for v2 data. Needs Home Assistant installed; skipped otherwise.

- An off-grid battery snapshot turns Grid Power off. It never turns it on.
- Daily Battery to Home is 0 only when the 24-hour mix shows no battery use.
- A failed snapshot call clears the old state instead of keeping it.
"""

from __future__ import annotations

import asyncio
import time
import importlib
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import frame  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _modules():
    components = sys.modules.get("custom_components")
    if components is None or not hasattr(components, "__path__"):
        components = types.ModuleType("custom_components")
        components.__path__ = [str(_ROOT / "custom_components")]
        sys.modules["custom_components"] = components
    coordinator = importlib.import_module("custom_components.base_power.coordinator")
    api = importlib.import_module("custom_components.base_power.api")
    v2 = importlib.import_module("custom_components.base_power.api_v2")
    poll = importlib.import_module("custom_components.base_power.poll")
    return coordinator, api, v2, poll


def _grid(api, *, volts: float | None, outage: bool = False, storage_kw=None):
    context = api.BasePowerApiClient._empty_service_context()
    context.update(available=True, is_active_outage=outage, power_from_storage_kw=storage_kw)
    voltage = api.BasePowerApiClient._empty_grid_voltage()
    if volts is not None:
        voltage.update(available=True, voltage_volts=volts)
    return api.BasePowerApiClient.combine_grid_telemetry(context, voltage)


def _compose(
    snapshot_state=None,
    *,
    volts=245.0,
    mix=None,
    energy_battery=None,
    energy_solar=0.0,
    fail_snapshot=False,
    storage_kw=None,
    support_intervals=None,
):
    coordinator_mod, api, v2, poll = _modules()

    async def run():
        hass = HomeAssistant(str(_ROOT))
        frame.async_setup(hass)
        try:
            coordinator = coordinator_mod.BasePowerCoordinator.__new__(
                coordinator_mod.BasePowerCoordinator
            )
            coordinator.hass = hass
            coordinator._config_entry = types.SimpleNamespace(options={})
            coordinator._prev_soc = None
            coordinator._poll = poll.PollState()
            coordinator._poll.energy = {
                "grid_to_home_kwh": 6.8,
                "solar_to_home_kwh": energy_solar,
                "battery_to_home_kwh": energy_battery,
            }
            overview = v2.empty_overview()
            if mix is not None:
                overview.update(available=True, energy_mix_24h=mix)
            if support_intervals is not None:
                overview.update(available=True, grid_support=support_intervals)
            coordinator._poll.overview = overview
            coordinator._grid = _grid(api, volts=volts, storage_kw=storage_kw)
            coordinator.service_location_id = "loc-1"
            snapshot = v2.empty_snapshot()
            if snapshot_state:
                snapshot.update(available=True, state=snapshot_state)

            async def get_battery_snapshot(address):
                if fail_snapshot:
                    raise ConnectionError("down")
                return snapshot

            coordinator.api = types.SimpleNamespace(get_battery_snapshot=get_battery_snapshot)
            coordinator._snapshot = v2.empty_snapshot()
            coordinator._snapshot.update(available=True, state="outage")  # stale state
            coordinator._snapshot = await coordinator._async_fetch_snapshot()
            return coordinator._compose()
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(run())


@pytest.mark.parametrize("state", ["outage", "no_home_power", "overcurrent", "overcurrent_standby"])
def test_off_grid_snapshot_turns_grid_power_off(state):
    data = _compose(state, volts=245.0)
    assert data["grid"]["grid_is_up"] is False
    assert data["snapshot"]["state"] == state


def test_on_grid_snapshot_does_not_turn_grid_power_on():
    assert _compose("on_grid", volts=245.0)["grid"]["grid_is_up"] is True
    # Voltage between the thresholds stays unknown even if the snapshot says on grid.
    assert _compose("on_grid", volts=75.0)["grid"]["grid_is_up"] is None
    assert _compose("on_grid", volts=0.0)["grid"]["grid_is_up"] is False


def test_failed_snapshot_clears_the_old_outage():
    data = _compose("outage", volts=245.0, fail_snapshot=True)
    assert data["snapshot"]["state"] is None
    assert data["grid"]["grid_is_up"] is True


def test_battery_to_home_is_zero_only_when_the_24h_mix_is_zero():
    zero = {"grid_to_home_kwh": 32.0, "solar_to_home_kwh": 0.0, "battery_to_home_kwh": 0.0}
    used = {"grid_to_home_kwh": 30.0, "solar_to_home_kwh": 0.0, "battery_to_home_kwh": 2.0}
    assert _compose("on_grid", mix=zero)["energy"]["battery_to_home_kwh"] == 0.0
    assert _compose("on_grid", mix=used)["energy"]["battery_to_home_kwh"] is None
    assert _compose("on_grid", mix=None)["energy"]["battery_to_home_kwh"] is None
    # A real daily value from usage energy is never replaced.
    assert _compose("on_grid", mix=zero, energy_battery=1.5)["energy"]["battery_to_home_kwh"] == 1.5


def test_total_home_energy_does_not_change_when_battery_is_filled_with_zero():
    zero = {"grid_to_home_kwh": 32.0, "solar_to_home_kwh": 0.0, "battery_to_home_kwh": 0.0}
    data = _compose("on_grid", mix=zero)
    assert data["derived"]["total_home_kwh"] == 6.8


def test_total_home_energy_does_not_count_solar_twice():
    # Home load is 6.8 kWh, 2.0 of it from solar. The total stays 6.8.
    data = _compose("on_grid", energy_solar=2.0, energy_battery=1.4)
    assert data["derived"]["total_home_kwh"] == 6.8
    assert data["derived"]["self_sufficiency_percent"] == 50.0


def test_self_sufficiency_caps_at_100_when_solar_exports():
    data = _compose("on_grid", energy_solar=9.0)
    assert data["derived"]["self_sufficiency_percent"] == 100.0


# --- snapshot cadence and voltage fallback -------------------------------------


class FakeApi:
    """Records calls. Each response can be changed between polls."""

    def __init__(self, api_mod, v2):
        self._api = api_mod.BasePowerApiClient
        self._v2 = v2
        self.calls: list[str] = []
        self.context = {"available": True, "is_active_outage": False, "home_has_power": True}
        self.voltage: float | None = 245.0
        self.fail_voltage = False
        self.v2_voltage: float | None = 244.8
        self.snapshot_state = "on_grid"
        self.fail_snapshot = False

    combine_grid_telemetry = None  # set in __init__ below

    async def get_service_context(self, numeric_id):
        self.calls.append("ServiceContext")
        context = self._api._empty_service_context()
        context.update(self.context)
        return context

    async def get_grid_status(self, numeric_id):
        self.calls.append("GridStatus")
        if self.fail_voltage:
            raise ConnectionError("down")
        voltage = self._api._empty_grid_voltage()
        if self.voltage is not None:
            voltage.update(available=True, voltage_volts=self.voltage, voltage_samples=1)
        return voltage

    async def get_recent_grid_voltage(self, address):
        self.calls.append("RecentGridVoltage")
        voltage = self._api._empty_grid_voltage()
        if self.v2_voltage is not None:
            voltage.update(available=True, voltage_volts=self.v2_voltage, voltage_samples=1)
        return voltage

    async def get_battery_snapshot(self, address):
        self.calls.append("Snapshot")
        if self.fail_snapshot:
            raise ConnectionError("down")
        snapshot = self._v2.empty_snapshot()
        snapshot.update(available=True, state=self.snapshot_state)
        return snapshot


async def _coordinator(hass):
    coordinator_mod, api, v2, poll = _modules()
    coordinator = coordinator_mod.BasePowerCoordinator.__new__(coordinator_mod.BasePowerCoordinator)
    coordinator_mod.DataUpdateCoordinator.__init__(
        coordinator, hass, coordinator_mod._LOGGER, config_entry=None, name="Base Power",
        update_interval=coordinator_mod.timedelta(seconds=900),
    )
    fake = FakeApi(api, v2)
    fake.combine_grid_telemetry = api.BasePowerApiClient.combine_grid_telemetry
    fake._empty_service_context = api.BasePowerApiClient._empty_service_context
    fake._empty_grid_voltage = api.BasePowerApiClient._empty_grid_voltage
    coordinator.api = fake
    coordinator.service_location_id = "loc-1"
    coordinator._config_entry = types.SimpleNamespace(options={})
    coordinator._prev_soc = None
    coordinator._poll = poll.PollState()
    coordinator._poll.numeric_id = 7
    coordinator._poll.mark_slow(coordinator._monotonic())
    coordinator._grid = _grid(api, volts=245.0)
    coordinator._snapshot = v2.empty_snapshot()
    coordinator._polls_since_snapshot = 0
    coordinator.data = {"seed": True}

    async def no_auth():
        return None

    coordinator._async_ensure_jwt = no_auth
    return coordinator, fake


def _run_polls(setup, polls: int):
    async def run():
        hass = HomeAssistant(str(_ROOT))
        frame.async_setup(hass)
        try:
            coordinator, fake = await _coordinator(hass)
            setup(fake)
            results = []
            for _ in range(polls):
                fake.calls.clear()
                await coordinator.async_poll_grid_status()
                results.append((list(fake.calls), dict(coordinator.data)))
            return results
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(run())


def _snapshot_polls(results):
    return [i + 1 for i, (calls, _) in enumerate(results) if "Snapshot" in calls]


def test_snapshot_runs_every_tenth_poll_while_normal():
    results = _run_polls(lambda fake: None, 25)
    assert _snapshot_polls(results) == [10, 20]


def test_snapshot_runs_every_poll_during_an_outage_flag():
    def outage(fake):
        fake.context = {"available": True, "is_active_outage": True, "home_has_power": True}

    results = _run_polls(outage, 4)
    assert _snapshot_polls(results) == [1, 2, 3, 4]


def test_snapshot_runs_every_poll_when_voltage_collapses():
    results = _run_polls(lambda fake: setattr(fake, "voltage", 0.0), 3)
    assert _snapshot_polls(results) == [1, 2, 3]


def test_off_grid_snapshot_keeps_the_fast_cadence():
    results = _run_polls(lambda fake: setattr(fake, "snapshot_state", "overcurrent"), 14)
    # First snapshot on poll 10 sees overcurrent; every poll after that checks again.
    assert _snapshot_polls(results) == [10, 11, 12, 13, 14]
    assert results[-1][1]["grid"]["grid_is_up"] is False


def test_failing_snapshot_does_not_speed_up_polling():
    results = _run_polls(lambda fake: setattr(fake, "fail_snapshot", True), 21)
    assert _snapshot_polls(results) == [10, 20]


def test_legacy_voltage_is_used_without_calling_v2():
    results = _run_polls(lambda fake: None, 2)
    for calls, data in results:
        assert "RecentGridVoltage" not in calls
        assert data["grid"]["voltage_volts"] == 245.0
        assert data["sources"]["voltage"] == "legacy"


def test_v2_voltage_fills_in_when_legacy_has_none_or_fails():
    for setup in (lambda fake: setattr(fake, "voltage", None),
                  lambda fake: setattr(fake, "fail_voltage", True)):
        calls, data = _run_polls(setup, 1)[0]
        assert calls[:3] == ["ServiceContext", "GridStatus", "RecentGridVoltage"]
        assert data["grid"]["voltage_volts"] == 244.8
        assert data["grid"]["grid_is_up"] is True
        assert data["sources"]["voltage"] == "v2"


def test_no_voltage_from_either_api_stays_unknown():
    def none(fake):
        fake.voltage = None
        fake.v2_voltage = None

    calls, data = _run_polls(none, 1)[0]
    assert data["grid"]["voltage_volts"] is None
    assert data["grid"]["grid_is_up"] is None
    assert data["sources"]["voltage"] is None


def test_missing_home_power_field_does_not_speed_up_the_snapshot():
    _, api, _, _ = _modules()
    # A present service context without field 9 parses home_has_power as False.
    parsed = api.BasePowerApiClient._parse_service_context(b"\x08\x01")
    assert parsed["home_has_power"] is False

    def no_field_9(fake):
        fake.context = {"available": True, "is_active_outage": False, "home_has_power": False}

    results = _run_polls(no_field_9, 21)
    assert _snapshot_polls(results) == [10, 20]


def test_grid_power_data_records_the_battery_state_behind_an_off_state():
    data = _compose("overcurrent", volts=245.0)
    assert data["grid"]["grid_is_up"] is False
    assert data["grid"]["battery_state"] == "overcurrent"
    assert data["grid"]["voltage_volts"] == 245.0
    assert _compose("on_grid")["grid"]["battery_state"] == "on_grid"


def _capacity(options, sku):
    coordinator_mod, api, v2, poll = _modules()

    async def run():
        hass = HomeAssistant(str(_ROOT))
        frame.async_setup(hass)
        try:
            coordinator = coordinator_mod.BasePowerCoordinator.__new__(coordinator_mod.BasePowerCoordinator)
            coordinator.hass = hass
            coordinator._config_entry = types.SimpleNamespace(options=options)
            coordinator._prev_soc = None
            coordinator._poll = poll.PollState()
            coordinator._poll.battery_account = {"plan_name": None, "hardware_sku": sku,
                                                 "equipment_description": None}
            coordinator._grid = _grid(api, volts=245.0)
            coordinator._snapshot = v2.empty_snapshot()
            data = coordinator._compose()
            return data["dashboard"], data["derived"]["capacity_kwh"]
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(run())


def test_saved_capacity_option_wins_over_the_sku():
    dashboard, kwh = _capacity({"battery_capacity": "80 kWh (2 Core)"}, "GRO_SINGLE")
    assert (kwh, dashboard["battery_count"], dashboard["capacity_source"]) == (80, 2, "option")
    assert dashboard["detected_capacity_kwh"] == 25
    dashboard, kwh = _capacity({"battery_count_override": 2}, "GRO_SINGLE")
    assert (kwh, dashboard["capacity_source"]) == (50, "option")


def test_known_sku_fills_capacity_when_no_option_was_saved():
    dashboard, kwh = _capacity({}, "GRO_SINGLE")
    assert (kwh, dashboard["battery_count"], dashboard["capacity_source"]) == (25, 1, "sku")
    assert dashboard["hardware_sku"] == "GRO_SINGLE"
    dashboard, kwh = _capacity({}, "GRO_DUAL")
    assert (kwh, dashboard["battery_count"], dashboard["capacity_source"]) == (50, 2, "sku")


def test_unknown_or_missing_sku_uses_the_default():
    for sku in ("SOMETHING_NEW", None):
        dashboard, kwh = _capacity({}, sku)
        assert (kwh, dashboard["capacity_source"]) == (25, "default")
        assert dashboard["detected_capacity_kwh"] is None


# --- grid support detection ---------------------------------------------------


def test_battery_powering_the_home_on_grid_is_grid_support():
    # The reported case: app says grid support is active, event list has not caught up.
    data = _compose("on_grid", storage_kw=5.6, support_intervals=[])
    assert data["derived"]["grid_support_active"] is True
    assert data["derived"]["grid_support_source"] == "battery_power"
    assert data["grid"]["grid_is_up"] is True


def test_idle_battery_with_no_event_is_not_grid_support():
    data = _compose("on_grid", storage_kw=0.05, support_intervals=[])
    assert data["derived"]["grid_support_active"] is False
    assert data["derived"]["grid_support_source"] is None


def test_battery_during_an_outage_is_not_grid_support():
    data = _compose("outage", storage_kw=3.0, support_intervals=[])
    assert data["grid"]["grid_is_up"] is False
    assert data["derived"]["grid_support_active"] is False


def test_event_list_still_turns_grid_support_on():
    now = int(time.time())
    data = _compose("on_grid", storage_kw=0.0, support_intervals=[{"start": now - 60, "end": None}])
    assert data["derived"]["grid_support_active"] is True
    assert data["derived"]["grid_support_source"] == "event_list"


def test_grid_support_is_unknown_without_any_data():
    data = _compose("on_grid", storage_kw=None)
    assert data["derived"]["grid_support_active"] is None
