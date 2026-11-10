"""Shared fixtures: a fake Base API and auth, and a way to set the integration up."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.base_power import api_v2
from custom_components.base_power.api import BasePowerApiClient
from custom_components.base_power.const import DOMAIN

SITE = "loc-1"
ENTRY_DATA = {
    "email": "someone@example.com",
    "client_token": "client-token-1",
    "session_id": "sess_1",
    "service_location_id": SITE,
}


class FakeApi(BasePowerApiClient):
    """Stands in for the API client. Returns parsed data, records each call.

    Put a method name in ``fail`` to make that call raise. Change the
    attributes to change what Base "returns".
    """

    def __init__(self) -> None:
        super().__init__(session=None)
        self.calls: list[str] = []
        self.fail: set[str] = set()
        now = int(time.time())
        hour = now - now % 3600
        self.dashboard = {
            "service_location_id": 7,
            "battery_status": 3,
            "has_solar": False,
            "time_zone": "America/Chicago",
            "upcoming_service_at": None,
        }
        self.wifi = {
            "scan": {"HomeNet": 68},
            "ssid": None,
            "signal": None,
            "connected": False,
            "last_updated": now,
        }
        self.location = {
            **api_v2.empty_location(),
            "available": True,
            "energy_service": "active",
            "has_solar": False,
            "batteries": [{"generation": "GEN1", "variant": "SINGLE"}],
            "time_zone": "America/Chicago",
            "upcoming_service_at": now + 86400,
        }
        self.overview = {
            **api_v2.empty_overview(),
            "available": True,
            "home_power": [{"timestamp": hour, "kwh": 0.25, "watts": 1000}],
            "energy_today": {
                "grid_to_home_kwh": 4.0,
                "solar_to_home_kwh": 0.0,
                "battery_to_home_kwh": None,
            },
            "cost_today_cents": 250.0,
            "hours": [
                {"start": hour - 3600, "home_kwh": 1.5, "solar_kwh": 0.0},
                {"start": hour, "home_kwh": 0.5, "solar_kwh": 0.0},
            ],
        }
        self.context = {
            **self._empty_service_context(),
            "available": True,
            "has_gateway": True,
            "gateway_connected": True,
            "is_active_outage": False,
            "home_has_power": True,
            "state_of_energy_raw": 87,
            "available_backup_hours": 20.5,
            "available_backup_hours_750w": 30.0,
            "wifi_ssid": "HomeNet",
            "wifi_state": "connected",
            "power_from_grid_kw": 0.8,
            "power_from_storage_kw": 0.0,
            "power_from_solar_kw": 0.0,
            "power_to_home_kw": 0.8,
        }
        self.voltage = {
            **self._empty_grid_voltage(),
            "available": True,
            "has_gateway": True,
            "voltage_volts": 244.2,
            "voltage_samples": 150,
        }
        self.snapshot = {
            **api_v2.empty_snapshot(),
            "available": True,
            "state": "on_grid",
            "backup_hours_750w": 29.0,
        }
        self.energy_account = {**api_v2.empty_energy_account(), "last_bill_amount_cents": 18853}
        self.billing_cycle = {**api_v2.empty_billing_cycle(), "net_accrued_cents": 500}
        self.daily_energy: list[dict[str, Any]] = []

    async def _hit(self, name: str, value: Any) -> Any:
        self.calls.append(name)
        if name in self.fail:
            raise ConnectionError(name)
        return value

    async def get_dashboard_root(self, address):
        return await self._hit("get_dashboard_root", dict(self.dashboard))

    async def get_wifi_metrics(self, address):
        return await self._hit("get_wifi_metrics", {**self.wifi, "scan": dict(self.wifi["scan"])})

    async def get_billing_metadata(self, address):
        return await self._hit(
            "get_billing_metadata", {"amount_cents": None, "last_bill_cents": None}
        )

    async def get_usage_cycles(self, address):
        return await self._hit(
            "get_usage_cycles", {"account_id": "acct-secret", "cycle_start": None}
        )

    async def get_current_billing_cycle(self, address):
        return await self._hit("get_current_billing_cycle", dict(self.billing_cycle))

    async def get_battery_account(self, address):
        return await self._hit(
            "get_battery_account",
            {"plan_name": "Single", "hardware_sku": "GRO_SINGLE", "equipment_description": None},
        )

    async def get_energy_account(self, address):
        return await self._hit("get_energy_account", dict(self.energy_account))

    async def get_location(self, address):
        return await self._hit("get_location", dict(self.location))

    async def get_daily_energy(self, address, start, end):
        return await self._hit("get_daily_energy", list(self.daily_energy))

    async def get_daily_overview(self, address, time_zone=None):
        return await self._hit("get_daily_overview", dict(self.overview))

    async def get_recent_usage(self, address):
        return await self._hit("get_recent_usage", [])

    async def get_usage_energy(self, address, numeric_service_location_id=None, time_zone=None):
        return await self._hit(
            "get_usage_energy",
            {"grid_to_home_kwh": None, "solar_to_home_kwh": None, "battery_to_home_kwh": None},
        )

    async def get_service_context(self, numeric_id):
        return await self._hit("get_service_context", dict(self.context))

    async def get_grid_status(self, numeric_id):
        return await self._hit("get_grid_status", dict(self.voltage))

    async def get_recent_grid_voltage(self, address):
        return await self._hit("get_recent_grid_voltage", dict(self.voltage))

    async def get_battery_snapshot(self, address):
        return await self._hit("get_battery_snapshot", dict(self.snapshot))


class FakeAuth:
    """Stands in for Clerk auth. Set ``error`` to make the next token request raise."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.requests = 0
        self.on_client_token = None

    async def async_ensure_valid_token(self, *, force: bool = False) -> str:
        self.requests += 1
        if self.error is not None:
            raise self.error
        return "jwt"


@pytest.fixture
def entry_data() -> dict[str, str]:
    return dict(ENTRY_DATA)


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def fake_auth() -> FakeAuth:
    return FakeAuth()


@pytest.fixture
async def setup_integration(hass, enable_custom_integrations, fake_api, fake_auth):
    """Return a function that adds a config entry and sets the integration up."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    def make_auth(*_args, on_client_token=None, **_kwargs):
        fake_auth.on_client_token = on_client_token
        return fake_auth

    async def setup(options: dict[str, Any] | None = None, data: dict[str, Any] | None = None):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=data or dict(ENTRY_DATA),
            options=options or {},
            unique_id=SITE,
            title="Base Power (loc-1)",
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    with (
        patch("custom_components.base_power.BasePowerApiClient", return_value=fake_api),
        patch("custom_components.base_power.BasePowerAuth", side_effect=make_auth),
    ):
        yield setup
