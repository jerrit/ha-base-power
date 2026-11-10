"""Sensor setup: Last Bill Amount source, running totals, and battery-only sites."""

from __future__ import annotations

import asyncio
import importlib
import types
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

_ROOT = Path(__file__).resolve().parents[1]


def _sensor_module():
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


def _setup(data, preexisting=(), listeners=None):
    sensor = _sensor_module()

    async def run():
        hass = HomeAssistant(str(_ROOT))
        frame.async_setup(hass)
        try:
            registry = FakeRegistry(f"loc-1_{unique}" for unique in preexisting)
            original = sensor.er
            sensor.er = types.SimpleNamespace(async_get=lambda _hass: registry)
            added = []

            def add_listener(update):
                if listeners is not None:
                    listeners.append((update, coordinator, added))
                return lambda: None

            coordinator = types.SimpleNamespace(
                data=data, usage_stale=False, async_add_listener=add_listener
            )
            entry = types.SimpleNamespace(
                entry_id="e1",
                data={"service_location_id": "loc-1"},
                options={},
                runtime_data=coordinator,
                async_on_unload=lambda _unsub: None,
            )
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
        assert set(entities) >= BILLING


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


def test_solar_sensors_are_added_when_base_reports_solar_later():
    listeners = []
    entities, _ = _setup(_data("active"), listeners=listeners)
    assert "solar_power" not in entities
    update, coordinator, added = listeners[0]
    before = len(added)
    update()  # still no solar: nothing is added
    assert len(added) == before
    coordinator.data = _data("active", has_solar=True)
    update()
    new = {e.unique_id.removeprefix("loc-1_") for e in added[before:]}
    assert new == {"solar_power", "solar_to_home_total", "solar_export_today"}
    update()  # only once
    assert len(added) == before + 3


def test_solar_site_has_no_listener():
    listeners = []
    _setup(_data("active", has_solar=True), listeners=listeners)
    assert listeners == []


def test_money_sensors_use_the_monetary_class():
    entities, _ = _setup(_data("active", last_bill=18853))
    bill = entities["bill_amount"]
    assert bill.device_class == "monetary"
    assert bill.native_unit_of_measurement == "USD"


def test_duplicate_daily_sensors_are_off_by_default():
    entities, _ = _setup(_data("active"))
    assert entities["daily_total_grid"].entity_registry_enabled_default is False
    assert entities["total_home_energy"].entity_registry_enabled_default is False
    assert entities["grid_to_home"].entity_registry_enabled_default is True


def test_battery_status_names_follow_the_api_enum():
    data = _data("active")
    for status, name in ((2, "Installed"), (4, "Updating Firmware"), (5, "No Install Planned"), (9, "Unknown (9)")):
        data["dashboard"]["battery_status"] = status
        entities, _ = _setup(data)
        assert entities["battery_status"].native_value == name


def test_unique_ids_are_unique_and_keep_their_old_names():
    entities, _ = _setup(_data("active", has_solar=True))
    sensor = _sensor_module()
    keys = [description.key for description in sensor.SENSORS]
    assert len(keys) == len(set(keys))
    # Ids from earlier releases. Renaming one would orphan the entity and its history.
    assert {
        "backup_hours", "battery_percent", "battery_count", "capacity", "current_power",
        "current_energy", "daily_peak", "daily_low", "daily_total", "intervals",
        "battery_status", "wifi_signal", "wifi_ssid", "grid_to_home", "solar_to_home",
        "battery_to_home", "bill_amount", "bill_due_date", "asset_id", "grid_power_amps",
        "daily_total_grid", "daily_avg_grid", "total_home_energy", "self_sufficiency",
        "battery_state", "backup_hours_750w", "grid_to_home_kwh_24h", "solar_to_home_kwh_24h",
        "battery_to_home_kwh_24h", "grid_support_events_24h", "energy_cost_today",
        "cycle_net_accrued_cents", "cycle_projected_charges_cents", "cycle_solar_credit_cents",
        "hardware_sku", "home_usage_total", "solar_to_home_total",
    } <= set(entities)
