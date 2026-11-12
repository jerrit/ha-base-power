"""The integration set up in a real Home Assistant, against a fake Base API."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.base_power.auth import AuthenticationError, AuthTemporaryError
from custom_components.base_power.const import DOMAIN, USAGE_STALE_AFTER_FAILURES
from custom_components.base_power.diagnostics import async_get_config_entry_diagnostics

PREFIX = "base_power_loc_1"
USAGE_CALLS = {"get_daily_overview", "get_recent_usage", "get_usage_energy"}


def _state(hass, platform: str, name: str) -> str:
    state = hass.states.get(f"{platform}.{PREFIX}_{name}")
    assert state is not None, f"{platform}.{PREFIX}_{name} does not exist"
    return state.state


async def test_setup_creates_sensors_with_live_values(hass, setup_integration):
    entry = await setup_integration()
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, "sensor", "grid_voltage") == "244.2"
    assert _state(hass, "sensor", "power_from_grid") == "800"
    assert _state(hass, "sensor", "battery_level") == "87.0"
    assert _state(hass, "sensor", "battery_status") == "In Service"
    assert _state(hass, "sensor", "home_usage_total") == "2.0"
    assert _state(hass, "binary_sensor", "grid_power") == "on"
    bill = hass.states.get(f"sensor.{PREFIX}_last_bill_amount")
    assert bill.state == "188.53"
    assert bill.attributes["unit_of_measurement"] == "USD"
    assert hass.states.get(f"sensor.{PREFIX}_next_service_visit").state not in ("unknown", "unavailable")
    # No solar on this site.
    assert hass.states.get(f"sensor.{PREFIX}_solar_power") is None


async def test_backup_at_750w_comes_from_the_minute_poll_then_the_snapshot(
    hass, setup_integration, fake_api
):
    entry = await setup_integration()
    assert _state(hass, "sensor", "battery_backup_time_750_w_essentials") == "30.0"
    fake_api.context["available_backup_hours_750w"] = None
    await entry.runtime_data.async_poll_grid_status()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "battery_backup_time_750_w_essentials") == "29.0"


async def test_wifi_network_comes_from_base_not_the_saved_option(hass, setup_integration, fake_api):
    fake_api.wifi["scan"] = {"HomeNet": 68, "Neighbor": 90}
    await setup_integration(options={"wifi_ssid": "Neighbor"})
    ssid = hass.states.get(f"sensor.{PREFIX}_wifi_ssid")
    assert ssid.state == "HomeNet"
    assert ssid.attributes["source"] == "base"
    assert ssid.attributes["state"] == "connected"
    assert _state(hass, "sensor", "wifi_signal") == "68"


async def test_wifi_falls_back_to_the_saved_option(hass, setup_integration, fake_api):
    fake_api.wifi["scan"] = {"HomeNet": 68, "Other": 40}
    fake_api.context["wifi_ssid"] = None
    fake_api.context["wifi_state"] = None
    await setup_integration(options={"wifi_ssid": "Other"})
    ssid = hass.states.get(f"sensor.{PREFIX}_wifi_ssid")
    assert ssid.state == "Other"
    assert ssid.attributes["source"] == "option"
    assert _state(hass, "sensor", "wifi_signal") == "40"


async def test_old_wifi_select_is_removed(hass, setup_integration):
    registry = er.async_get(hass)
    registry.async_get_or_create("select", DOMAIN, "loc-1_wifi_ssid_select")
    await setup_integration()
    assert registry.async_get_entity_id("select", DOMAIN, "loc-1_wifi_ssid_select") is None


async def test_duplicate_sensors_are_disabled_on_a_new_install(hass, setup_integration):
    await setup_integration()
    registry = er.async_get(hass)
    for key in ("daily_total_grid", "total_home_energy"):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"loc-1_{key}")
        assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_unload_and_set_up_again(hass, setup_integration):
    entry = await setup_integration()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _state(hass, "sensor", "grid_voltage") == "unavailable"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "grid_voltage") == "244.2"


async def test_setup_retries_when_base_is_down(hass, setup_integration, fake_auth):
    fake_auth.error = ConnectionError("no network")
    entry = await setup_integration()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_asks_for_sign_in_when_the_session_is_rejected(
    hass, setup_integration, fake_auth
):
    fake_auth.error = AuthenticationError("401")
    entry = await setup_integration()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == ["reauth"]


async def test_a_clerk_hiccup_does_not_ask_for_sign_in(hass, setup_integration, fake_auth):
    entry = await setup_integration()
    fake_auth.error = AuthTemporaryError("503")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress() == []
    assert entry.state is ConfigEntryState.LOADED
    # The last values stay until the data has been stale for a while.
    assert _state(hass, "sensor", "home_usage_total") == "2.0"
    assert _state(hass, "sensor", "grid_voltage") == "244.2"


async def test_a_rejected_session_asks_for_sign_in(hass, setup_integration, fake_auth):
    entry = await setup_integration()
    fake_auth.error = AuthenticationError("401")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == ["reauth"]


async def test_usage_sensors_go_unavailable_when_stale_and_come_back(
    hass, setup_integration, fake_api, caplog
):
    entry = await setup_integration()
    coordinator = entry.runtime_data
    fake_api.fail = set(USAGE_CALLS)
    for _ in range(USAGE_STALE_AFTER_FAILURES - 1):
        await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "home_usage_total") == "2.0"
    assert "has not updated" not in caplog.text

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.usage_stale is True
    assert _state(hass, "sensor", "home_usage_total") == "unavailable"
    assert _state(hass, "sensor", "home_usage_today_all_sources") == "unavailable"
    # Live sensors do not depend on the usage data.
    assert _state(hass, "sensor", "grid_voltage") == "244.2"
    assert _state(hass, "binary_sensor", "grid_power") == "on"
    assert caplog.text.count("has not updated") == 1

    await coordinator.async_refresh()
    assert caplog.text.count("has not updated") == 1  # logged once, not every refresh

    fake_api.fail = set()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.usage_stale is False
    assert _state(hass, "sensor", "home_usage_total") == "2.0"
    assert "updating again" in caplog.text


async def test_a_failed_slow_call_is_retried_on_the_next_usage_refresh(
    hass, setup_integration, fake_api
):
    fake_api.fail = {"get_location"}
    entry = await setup_integration()
    coordinator = entry.runtime_data
    assert coordinator._poll.slow_failed is True
    assert coordinator._poll.slow_due(coordinator._poll.last_slow_at + 15 * 60, first=False)
    fake_api.fail = set()
    coordinator._poll.last_slow_at -= 15 * 60
    await coordinator.async_refresh()
    assert coordinator._poll.slow_failed is False
    assert coordinator._poll.location["available"] is True
    assert not coordinator._poll.slow_due(coordinator._poll.last_slow_at + 15 * 60, first=False)


async def test_solar_sensors_appear_when_base_reports_solar_later(
    hass, setup_integration, fake_api
):
    entry = await setup_integration()
    assert hass.states.get(f"sensor.{PREFIX}_solar_power") is None
    fake_api.context["power_from_solar_kw"] = 2.5
    entry.runtime_data._poll.location["has_solar"] = True
    await entry.runtime_data.async_poll_grid_status()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "solar_power") == "2500"
    assert _state(hass, "binary_sensor", "solar_connected") == "on"


async def test_fast_polls_do_not_overlap(hass, setup_integration, fake_api):
    entry = await setup_integration()
    coordinator = entry.runtime_data
    fake_api.calls.clear()
    coordinator._fast_poll_running = True
    await coordinator.async_poll_grid_status()
    assert fake_api.calls == []
    coordinator._fast_poll_running = False
    await coordinator.async_poll_grid_status()
    assert "get_service_context" in fake_api.calls
    assert coordinator._fast_poll_running is False


async def test_fast_poll_guard_clears_after_an_error(hass, setup_integration, fake_auth):
    entry = await setup_integration()
    coordinator = entry.runtime_data
    fake_auth.error = ConnectionError("down")
    await coordinator.async_poll_grid_status()
    assert coordinator._fast_poll_running is False


async def test_rotated_client_token_is_saved(hass, setup_integration, fake_auth, entry_data):
    entry = await setup_integration(data={**entry_data, "session_jwt": "old-jwt"})
    fake_auth.on_client_token("client-token-2")
    assert entry.data["client_token"] == "client-token-2"
    assert "session_jwt" not in entry.data
    assert entry.state is ConfigEntryState.LOADED


async def test_site_time_zone_from_base_wins_over_home_assistant(hass, setup_integration, fake_api):
    await hass.config.async_set_time_zone("UTC")
    entry = await setup_integration()
    assert entry.runtime_data._time_zone() == "America/Chicago"
    fake_api.location["time_zone"] = "Not/AZone"
    entry.runtime_data._poll.location = dict(fake_api.location)
    entry.runtime_data._poll.dashboard["time_zone"] = None
    assert entry.runtime_data._time_zone() == "UTC"


async def test_diagnostics_hide_account_and_home_details(hass, setup_integration):
    entry = await setup_integration(options={"wifi_ssid": "HomeNet"})
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diagnostics)
    for secret in ("client-token-1", "sess_1", "someone@example.com", "loc-1", "HomeNet", "acct-secret"):
        assert secret not in text
    assert diagnostics["entry"]["data"]["client_token"] == "**REDACTED**"
    assert diagnostics["data"]["grid"]["voltage_volts"] == 244.2
    assert diagnostics["totals"]["home_kwh"] == 2.0


async def test_running_total_catches_up_from_the_daily_totals(hass, setup_integration, fake_api):
    entry = await setup_integration()
    coordinator = entry.runtime_data
    totals = coordinator._totals
    assert totals.since is not None
    # Pretend counting began ten days ago and one full day was missed.
    day = int(totals.since) - 5 * 86400
    totals.since -= 10 * 86400
    coordinator._poll.daily_energy = [
        {"start": day, "home_kwh": 30.0, "solar_kwh": 0.0},
        {"start": day + 86400, "home_kwh": 0.0, "solar_kwh": 0.0},
    ]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "home_usage_total") == "32.0"
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "home_usage_total") == "32.0"


async def test_daily_totals_are_fetched_with_the_slow_tier(hass, setup_integration, fake_api):
    fake_api.daily_energy = [{"start": 86400, "home_kwh": 20.0, "solar_kwh": 0.0}]
    entry = await setup_integration()
    assert "get_daily_energy" in fake_api.calls
    assert entry.runtime_data._poll.daily_energy == fake_api.daily_energy


async def test_voltage_falls_back_to_the_newer_call(hass, setup_integration, fake_api):
    entry = await setup_integration()
    fake_api.fail = {"get_grid_status"}
    await entry.runtime_data.async_poll_grid_status()
    await hass.async_block_till_done()
    grid = hass.states.get(f"binary_sensor.{PREFIX}_grid_power")
    assert grid.state == "on"
    assert grid.attributes["voltage_source"] == "v2"
    assert grid.attributes["voltage_volts"] == 244.2


async def test_a_missed_grid_poll_is_unknown_not_the_last_reading(hass, setup_integration, fake_api):
    entry = await setup_integration()
    coordinator = entry.runtime_data
    clock = {"t": coordinator._monotonic()}
    coordinator._monotonic = lambda: clock["t"]
    await coordinator.async_poll_grid_status()
    fake_api.fail = {"get_grid_status", "get_recent_grid_voltage", "get_service_context"}
    clock["t"] += 60
    await coordinator.async_poll_grid_status()
    await hass.async_block_till_done()
    # Grid Power never guesses "up" without live voltage.
    assert _state(hass, "binary_sensor", "grid_power") == "unknown"
    assert _state(hass, "sensor", "grid_voltage") == "unknown"
    # One missed minute keeps the last battery and power readings.
    assert _state(hass, "sensor", "power_from_grid") == "800"
    assert _state(hass, "sensor", "battery_level") not in ("unknown", "unavailable")
    # A longer gap shows unknown, not an old reading.
    clock["t"] += 180
    await coordinator.async_poll_grid_status()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "power_from_grid") == "unknown"
    assert _state(hass, "sensor", "battery_level") == "unknown"


async def test_an_outage_turns_grid_power_off(hass, setup_integration, fake_api):
    entry = await setup_integration()
    fake_api.context["is_active_outage"] = True
    fake_api.snapshot["state"] = "outage"
    await entry.runtime_data.async_poll_grid_status()
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", "grid_power") == "off"
    assert _state(hass, "sensor", "battery_state") == "outage"


async def test_battery_only_home_has_no_empty_billing_sensors(hass, setup_integration, fake_api):
    fake_api.location["energy_service"] = "no_service"
    fake_api.energy_account["last_bill_amount_cents"] = None
    fake_api.billing_cycle["net_accrued_cents"] = None
    fake_api.overview["cost_today_cents"] = None
    await setup_integration()
    for name in ("last_bill_amount", "bill_due_date", "bill_to_date", "energy_cost_today"):
        assert hass.states.get(f"sensor.{PREFIX}_{name}") is None
    assert _state(hass, "sensor", "home_usage_total") == "2.0"
