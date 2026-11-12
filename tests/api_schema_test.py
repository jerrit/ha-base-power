"""Field numbers in the parsers, checked against Base's schema.

fixtures/base_api_schema.json holds the messages the parsers read, taken
from the server's gRPC reflection. The parsers decode protobuf by field
number, so a number that no longer means what the code assumes would be
read as the wrong thing without any error.

When Base changes its API: pull the reflection again, run
``python tests/fixtures/make_schema_fixture.py <dump>``, and run this test.
A failure names the message and field that moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.base_power import api, api_v2, sensor

SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "base_api_schema.json").read_text())
LEGACY = "dashboard."
V2 = "dashboard.mobile.v2."


def _fields(message: str) -> dict[int, str]:
    return {number: name for number, name, _type in SCHEMA["messages"][message]}


def _enum(name: str) -> dict[int, str]:
    return {int(number): label for number, label in SCHEMA["enums"][name].items()}


# (message, field number, field name) for every field a parser reads.
READ_FIELDS = [
    # api.py: older DashboardAPI calls
    (LEGACY + "MobileGetDashboardRootRequest", 1, "addressId"),
    (LEGACY + "MobileGetDashboardRootResponse", 2, "address"),
    (LEGACY + "MobileGetDashboardRootResponse", 3, "battery"),
    (LEGACY + "MobileGetDashboardRootResponse", 7, "serviceLocationId"),
    (LEGACY + "DashboardAddress", 9, "timezoneIdentifier"),
    (LEGACY + "Battery", 2, "status"),
    (LEGACY + "Battery", 3, "hasSolar"),
    (LEGACY + "Battery", 5, "upcomingServiceDate"),
    (LEGACY + "GetServiceStatusRequest", 1, "serviceLocationID"),
    (LEGACY + "MobileGetServiceContextResponse", 1, "hasGateway"),
    (LEGACY + "MobileGetServiceContextResponse", 2, "gatewayConnection"),
    (LEGACY + "MobileGetServiceContextResponse", 4, "gatewayWifi"),
    (LEGACY + "MobileGetServiceContextResponse", 5, "isActiveOutage"),
    (LEGACY + "MobileGetServiceContextResponse", 7, "stateOfEnergyRaw"),
    (LEGACY + "MobileGetServiceContextResponse", 8, "availableBackup"),
    (LEGACY + "MobileGetServiceContextResponse", 9, "homeHasPower"),
    (LEGACY + "MobileGetServiceContextResponse", 10, "powerFlow"),
    (LEGACY + "MobileGetServiceContextResponse", 11, "availableBackupAt750W"),
    (LEGACY + "MobileGetServiceContextResponse.GatewayWifi", 1, "ssid"),
    (LEGACY + "MobileGetServiceContextResponse.GatewayWifi", 2, "state"),
    (LEGACY + "MobileGetServiceContextResponse.PowerFlow", 1, "fromGrid"),
    (LEGACY + "MobileGetServiceContextResponse.PowerFlow", 2, "fromStorage"),
    (LEGACY + "MobileGetServiceContextResponse.PowerFlow", 3, "fromSolar"),
    (LEGACY + "MobileGetServiceContextResponse.PowerFlow", 5, "toHome"),
    (LEGACY + "MobileGetGridStatusRequest", 1, "serviceLocationID"),
    (LEGACY + "MobileGetGridStatusResponse", 1, "gridVoltage"),
    (LEGACY + "MobileGetGridStatusResponse", 2, "hasGateway"),
    (LEGACY + "MobileGetGridStatusResponse", 3, "voltage"),
    (LEGACY + "MobileGetGridStatusResponse.GridVoltageEntry", 1, "key"),
    (LEGACY + "MobileGetGridStatusResponse.GridVoltageEntry", 2, "value"),
    (LEGACY + "GetUsageRequest", 1, "serviceLocationID"),
    (LEGACY + "GetUsageResponse", 2, "load"),
    (LEGACY + "GetUsageResponse", 4, "solar"),
    (LEGACY + "GetUsageResponse", 5, "timestampsTz"),
    (LEGACY + "MobileGetRecentUsageRequest", 1, "address_id"),
    (LEGACY + "MobileGetRecentUsageResponse", 1, "power_level_data"),
    (LEGACY + "PowerLevelDataPoint", 1, "time"),
    (LEGACY + "PowerLevelDataPoint", 2, "power_to_home_kw"),
    (LEGACY + "MobileGetWifiMetricsRequest", 1, "address_id"),
    (LEGACY + "MobileGetWifiMetricsResponse", 1, "wifi_metrics"),
    (LEGACY + "MobileGetWifiMetricsResponse", 2, "last_updated"),
    (LEGACY + "WifiMetrics", 1, "ssid"),
    (LEGACY + "WifiMetrics", 2, "signal_strength"),
    (LEGACY + "MobileGetBillingMetadataRequest", 1, "addressId"),
    (LEGACY + "MobileGetBillingMetadataResponse", 1, "rep"),
    (LEGACY + "MobileGetBillingMetadataResponse.Rep", 2, "activeSpendCents"),
    (LEGACY + "MobileGetBillingMetadataResponse.Rep", 3, "lastBillAmountCents"),
    (LEGACY + "MobileGetUsageCyclesRequest", 1, "addressId"),
    (LEGACY + "MobileGetUsageCyclesResponse", 1, "usageCycles"),
    (LEGACY + "MobileGetUsageCyclesResponse", 2, "accountId"),
    (LEGACY + "UsageCycle", 2, "servicePeriodStart"),
    (LEGACY + "UsageCycle", 3, "servicePeriodEnd"),
    (LEGACY + "MobileGetAvailableLocationsResponse", 1, "locations"),
    (LEGACY + "MobileGetAvailableLocationsResponse.Location", 1, "addressId"),
    # api_v2.py: dashboard.mobile.v2
    (V2 + "GetBatterySnapshotRequest", 1, "address_id"),
    (V2 + "GetBatterySnapshotResponse", 1, "snapshot"),
    (V2 + "GetDailyOverviewRequest", 1, "address_id"),
    (V2 + "GetDailyOverviewResponse", 1, "home_power"),
    (V2 + "GetDailyOverviewResponse", 3, "grid_support_intervals"),
    (V2 + "GetDailyOverviewResponse", 4, "hourly_energy"),
    (V2 + "GetDailyOverviewResponse", 5, "hourly_energy_costs"),
    (V2 + "GetDailyOverviewResponse", 6, "cost_currency"),
    (V2 + "GetDailyOverviewResponse", 7, "energy_source_mix"),
    (V2 + "HomePowerSample", 1, "interval_start"),
    (V2 + "HomePowerSample", 2, "power_to_home_kw"),
    (V2 + "GridSupportInterval", 1, "interval_start"),
    (V2 + "GridSupportInterval", 2, "interval_end"),
    (V2 + "HourlyEnergySample", 1, "interval_start"),
    (V2 + "HourlyEnergySample", 2, "energy_to_home_kwh"),
    (V2 + "HourlyEnergySample", 3, "solar_to_home_kwh"),
    (V2 + "HourlyEnergySample", 4, "solar_export_kwh"),
    (V2 + "HourlyEnergyCostSample", 1, "interval_start"),
    (V2 + "HourlyEnergyCostSample", 2, "grid_energy_cents"),
    (V2 + "EnergySourceMix", 1, "grid_to_home_kwh"),
    (V2 + "EnergySourceMix", 2, "solar_to_home_kwh"),
    (V2 + "EnergySourceMix", 3, "battery_to_home_kwh"),
    (V2 + "GetDailyEnergyRequest", 1, "address_id"),
    (V2 + "GetDailyEnergyRequest", 2, "service_period_start"),
    (V2 + "GetDailyEnergyRequest", 3, "service_period_end"),
    (V2 + "GetDailyEnergyResponse", 1, "samples"),
    (V2 + "DailyEnergySample", 1, "interval_start"),
    (V2 + "DailyEnergySample", 2, "energy_to_home_kwh"),
    (V2 + "DailyEnergySample", 3, "solar_to_home_kwh"),
    (V2 + "GetRecentGridVoltageRequest", 1, "address_id"),
    (V2 + "GetRecentGridVoltageResponse", 1, "samples"),
    (V2 + "GridVoltageSample", 1, "observed_at"),
    (V2 + "GridVoltageSample", 2, "voltage_v"),
    (V2 + "GetLocationRequest", 1, "address_id"),
    (V2 + "GetLocationResponse", 1, "location"),
    (V2 + "ListLocationsResponse", 1, "locations"),
    (V2 + "Location", 1, "summary"),
    (V2 + "Location", 2, "energy"),
    (V2 + "Location", 3, "battery"),
    (V2 + "Location", 4, "capabilities"),
    (V2 + "LocationSummary", 1, "address_id"),
    (V2 + "LocationSummary", 2, "address"),
    (V2 + "DisplayAddress", 1, "line_1"),
    (V2 + "DisplayAddress", 3, "city"),
    (V2 + "DisplayAddress", 7, "timezone"),
    (V2 + "BatteryOnsite", 2, "upcoming_service_at"),
    (V2 + "Battery", 2, "generation"),
    (V2 + "Battery", 3, "variant"),
    (V2 + "LocationCapabilities", 1, "has_solar"),
    (V2 + "AccountReference", 1, "address_id"),
    (V2 + "AccountReference", 2, "account_type"),
    (V2 + "GetCurrentBillingCycleRequest", 1, "account"),
    (V2 + "GetCurrentBillingCycleResponse", 1, "cycle"),
    (V2 + "CurrentBillingCycle", 1, "currency"),
    (V2 + "CurrentBillingCycle", 2, "service_period_start"),
    (V2 + "CurrentBillingCycle", 4, "data_through"),
    (V2 + "CurrentBillingCycle", 5, "gross_consumption_charges_cents"),
    (V2 + "CurrentBillingCycle", 6, "solar_buyback_credit_cents"),
    (V2 + "CurrentBillingCycle", 7, "net_accrued_amount_cents"),
    (V2 + "CurrentBillingCycle", 8, "projected_gross_consumption_charges_cents"),
    (V2 + "GetAccountRequest", 1, "account"),
    (V2 + "GetAccountResponse", 1, "account"),
    (V2 + "Account", 2, "plan"),
    (V2 + "Plan", 1, "display_name"),
    (V2 + "Plan", 3, "battery"),
    (V2 + "BatteryPlan", 1, "equipment"),
    (V2 + "BatteryEquipment", 1, "description"),
    (V2 + "BatteryEquipment", 2, "hardware_sku"),
    (V2 + "ListAccountsRequest", 1, "address_id"),
    (V2 + "ListAccountsResponse", 1, "accounts"),
    (V2 + "AccountSummary", 1, "account_type"),
    (V2 + "AccountSummary", 4, "last_bill_amount_cents"),
    (V2 + "AccountSummary", 5, "outstanding_balance_cents"),
    (V2 + "AccountSummary", 7, "next_autopay_date"),
    (V2 + "AccountSummary", 8, "next_bill_date"),
    (V2 + "AccountSummary", 10, "oldest_open_obligation"),
    (V2 + "OpenObligation", 3, "original_amount_cents"),
    (V2 + "OpenObligation", 4, "due_at"),
    (V2 + "OpenObligation", 7, "past_due"),
]


@pytest.mark.parametrize(("message", "number", "name"), READ_FIELDS)
def test_field_number_still_means_what_the_parser_reads(message, number, name):
    assert _fields(message).get(number) == name


def test_battery_snapshot_states_match_the_oneof():
    fields = _fields(V2 + "BatterySnapshot")
    for number, state in api_v2.BATTERY_STATES.items():
        expected = state if state in ("on_grid", "telemetry_unavailable") else f"off_grid_{state}"
        assert fields[number] == expected


# Our short keys for the schema's long field names
_STATE_KEY_NAMES = {
    "backup_hours": "estimated_backup_hours_at_current_usage",
    "backup_hours_750w": "estimated_backup_hours_at_750_watts",
}
_STATE_MESSAGES = {
    "on_grid": "BatteryOnGrid",
    "outage": "BatteryOffGridOutage",
    "no_home_power": "BatteryOffGridNoHomePower",
    "overcurrent": "BatteryOffGridOvercurrent",
    "overcurrent_standby": "BatteryOffGridOvercurrentStandby",
}


@pytest.mark.parametrize("state", sorted(_STATE_MESSAGES))
def test_battery_state_layouts_match_their_messages(state):
    fields = _fields(V2 + _STATE_MESSAGES[state])
    for number, key in api_v2._STATE_FIELDS[state].items():
        assert fields[number] == _STATE_KEY_NAMES.get(key, key)


def test_power_flow_fields_match():
    assert _fields(V2 + "BatteryPowerFlow") == api_v2._POWER_FLOW_FIELDS


def test_energy_service_states_match_the_oneof():
    assert _fields(V2 + "EnergyState") == api_v2.ENERGY_SERVICE_STATES


def test_battery_list_fields_match_each_location_state():
    states = _fields(V2 + "BatteryState")
    messages = {"planned": "BatteryPlanned", "activating": "BatteryActivating", "onsite": "BatteryOnsite"}
    assert {states[number] for number in api_v2._BATTERY_LIST_FIELD} == set(messages)
    for number, list_field in api_v2._BATTERY_LIST_FIELD.items():
        assert _fields(V2 + messages[states[number]])[list_field] == "batteries"


def test_enums_match():
    generations = _enum(V2 + "BatteryGeneration")
    for number, name in api_v2.BATTERY_GENERATIONS.items():
        assert generations[number] == f"BATTERY_GENERATION_{name}"
    variants = _enum(V2 + "BatteryVariant")
    for number, name in api_v2.BATTERY_VARIANTS.items():
        assert variants[number] == f"BATTERY_VARIANT_{name}"
    account_types = _enum(V2 + "AccountType")
    assert account_types[api_v2.ACCOUNT_TYPE_ENERGY] == "ACCOUNT_TYPE_ENERGY"
    assert account_types[api_v2.ACCOUNT_TYPE_BATTERY] == "ACCOUNT_TYPE_BATTERY"
    wifi = _enum(LEGACY + "GatewayWifiState")
    assert {number: f"WIFI_{name.upper()}" for number, name in api.GATEWAY_WIFI_STATES.items()} == wifi


def test_every_battery_status_has_a_name():
    status = _enum(LEGACY + "Battery.Status")
    assert set(sensor.BATTERY_STATUS_MAP) == set(status)
    for number, label in status.items():
        words = label.replace("FLASHING", "UPDATING").replace("_", " ").title()
        assert sensor.BATTERY_STATUS_MAP[number] == words
