"""Coordinator rules for v2 data. Needs Home Assistant installed; skipped otherwise.

- An off-grid battery snapshot turns Grid Power off. It never turns it on.
- Daily Battery to Home is 0 only when the 24-hour mix shows no battery use.
- A failed snapshot call clears the old state instead of keeping it.
"""

from __future__ import annotations

import asyncio
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


def _grid(api, *, volts: float | None, outage: bool = False):
    context = api.BasePowerApiClient._empty_service_context()
    context.update(available=True, is_active_outage=outage)
    voltage = api.BasePowerApiClient._empty_grid_voltage()
    if volts is not None:
        voltage.update(available=True, voltage_volts=volts)
    return api.BasePowerApiClient.combine_grid_telemetry(context, voltage)


def _compose(snapshot_state=None, *, volts=245.0, mix=None, energy_battery=None, fail_snapshot=False):
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
                "solar_to_home_kwh": 0.0,
                "battery_to_home_kwh": energy_battery,
            }
            overview = v2.empty_overview()
            if mix is not None:
                overview.update(available=True, energy_mix_24h=mix)
            coordinator._poll.overview = overview
            coordinator._grid = _grid(api, volts=volts)
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
