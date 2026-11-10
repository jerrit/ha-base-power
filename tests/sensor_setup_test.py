"""Sensor setup: Last Bill Amount source, running totals, and battery-only sites."""

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


def _sensor_module():
    components = sys.modules.get("custom_components")
    if components is None or not hasattr(components, "__path__"):
        components = types.ModuleType("custom_components")
        components.__path__ = [str(_ROOT / "custom_components")]
        sys.modules["custom_components"] = components
    return importlib.import_module("custom_components.base_power.sensor")


def _data(energy_service, *, has_solar=False, last_bill=None, legacy_last_bill=None, cost=None, grid=None):
    return {
        "grid": grid or {},
        "dashboard": {"has_solar": has_solar},
        "derived": {"home_usage_total_kwh": 12.5, "solar_total_kwh": 1.0},
        "energy": {},
        "billing": {"amount_cents": 999, "last_bill_cents": legacy_last_bill, "due_date": None},
        "energy_account": {"last_bill_amount_cents": last_bill},
        "billing_cycle": {},
        "overview": {"cost_today_cents": cost},
        "location": {"energy_service": energy_service},
    }


class FakeRegistry:
    """The two entity registry calls sensor setup makes."""

    def __init__(self, unique_ids):
        self.entities = {f"sensor.{u}": u for u in unique_ids}

    def async_get_entity_id(self, domain, platform, unique_id):
        return next((eid for eid, u in self.entities.items() if u == unique_id), None)

    def async_remove(self, entity_id):
        del self.entities[entity_id]


def _setup(data, preexisting=()):
    sensor = _sensor_module()

    async def run():
        hass = HomeAssistant(str(_ROOT))
        frame.async_setup(hass)
        try:
            registry = FakeRegistry(f"loc-1_{unique}" for unique in preexisting)
            original = sensor.er
            sensor.er = types.SimpleNamespace(async_get=lambda _hass: registry)
            entry = types.SimpleNamespace(entry_id="e1", data={"service_location_id": "loc-1"}, options={})
            coordinator = types.SimpleNamespace(data=data)
            hass.data["base_power"] = {"e1": coordinator}
            added = []
            try:
                await sensor.async_setup_entry(hass, entry, added.extend)
            finally:
                sensor.er = original
            left = set(registry.entities.values())
            return {e.unique_id.removeprefix("loc-1_"): e for e in added}, left
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(run())


BILLING = {"bill_amount", "bill_due_date", "energy_cost_today", "cycle_net_accrued_cents",
           "cycle_projected_charges_cents", "cycle_solar_credit_cents"}


def test_last_bill_prefers_the_newer_api_then_the_older_call():
    entities, _ = _setup(_data("active", last_bill=18853, legacy_last_bill=100))
    assert entities["bill_amount"].native_value == 188.53
    assert entities["bill_amount"].name == "Last Bill Amount"
    entities, _ = _setup(_data("active", legacy_last_bill=33840))
    assert entities["bill_amount"].native_value == 338.40
    # Spend so far (amount_cents) is Bill to Date, not the last bill.
    entities, _ = _setup(_data("active"))
    assert entities["bill_amount"].native_value is None


def test_battery_only_site_drops_empty_billing_sensors():
    entities, left = _setup(_data("no_service"), preexisting=["bill_amount", "bill_due_date", "battery_percent"])
    assert not BILLING & set(entities)
    assert "loc-1_bill_amount" not in left and "loc-1_bill_due_date" not in left
    assert "loc-1_battery_percent" in left
    assert "home_usage_total" in entities


def test_battery_only_site_keeps_a_billing_sensor_with_a_value():
    entities, _ = _setup(_data("no_service", cost=250))
    assert "energy_cost_today" in entities
    assert entities["energy_cost_today"].native_value == 2.5


def test_billing_sensors_stay_when_service_is_active_or_unknown():
    for service in ("active", None):
        entities, _ = _setup(_data(service))
        assert BILLING <= set(entities)


def test_solar_total_only_for_solar_sites():
    entities, _ = _setup(_data("active"))
    assert "solar_to_home_total" not in entities
    entities, _ = _setup(_data("active", has_solar=True))
    assert entities["solar_to_home_total"].native_value == 1.0
    assert entities["home_usage_total"].native_value == 12.5


def test_live_power_sensors_convert_kw_to_watts():
    grid = {
        "voltage_volts": 241.37,
        "power_from_grid_kw": -1.2345,
        "power_from_storage_kw": 0.75,
        "power_from_solar_kw": 3.1,
    }
    entities, _ = _setup(_data("active", has_solar=True, grid=grid))
    assert entities["grid_voltage"].native_value == 241.4
    assert entities["grid_voltage"].native_unit_of_measurement == "V"
    assert entities["power_from_grid"].native_value == -1234
    assert entities["battery_power"].native_value == 750
    assert entities["solar_power"].native_value == 3100
    assert entities["power_from_grid"].native_unit_of_measurement == "W"


def test_live_power_sensors_unknown_when_missing_or_not_finite():
    grid = {"voltage_volts": float("nan"), "power_from_grid_kw": None, "power_from_storage_kw": True}
    entities, _ = _setup(_data("active", grid=grid))
    assert entities["grid_voltage"].native_value is None
    assert entities["power_from_grid"].native_value is None
    assert entities["battery_power"].native_value is None


def test_solar_power_only_for_solar_sites():
    entities, _ = _setup(_data("active"))
    assert "solar_power" not in entities
    assert {"grid_voltage", "power_from_grid", "battery_power"} <= set(entities)
