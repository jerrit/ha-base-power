"""Rebuild base_api_schema.json from a full gRPC reflection dump.

Usage: python tests/fixtures/make_schema_fixture.py descriptors.json

The dump is the JSON written by the reflection script: "messages" maps a
message name to [number, name, type] rows, "enums" maps an enum name to
{number: name}. Only the messages and enums the parsers read are kept.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LEGACY = "dashboard."
V2 = "dashboard.mobile.v2."

MESSAGES = [
    LEGACY + name
    for name in (
        "MobileGetDashboardRootRequest",
        "MobileGetDashboardRootResponse",
        "DashboardAddress",
        "Battery",
        "GetServiceStatusRequest",
        "MobileGetServiceContextResponse",
        "MobileGetServiceContextResponse.GatewayWifi",
        "MobileGetServiceContextResponse.PowerFlow",
        "MobileGetGridStatusRequest",
        "MobileGetGridStatusResponse",
        "MobileGetGridStatusResponse.GridVoltageEntry",
        "GetUsageRequest",
        "GetUsageResponse",
        "MobileGetRecentUsageRequest",
        "MobileGetRecentUsageResponse",
        "PowerLevelDataPoint",
        "MobileGetWifiMetricsRequest",
        "MobileGetWifiMetricsResponse",
        "WifiMetrics",
        "MobileGetBillingMetadataRequest",
        "MobileGetBillingMetadataResponse",
        "MobileGetBillingMetadataResponse.Rep",
        "MobileGetUsageCyclesRequest",
        "MobileGetUsageCyclesResponse",
        "UsageCycle",
        "MobileGetAvailableLocationsResponse",
        "MobileGetAvailableLocationsResponse.Location",
    )
] + [
    V2 + name
    for name in (
        "GetBatterySnapshotRequest",
        "GetBatterySnapshotResponse",
        "BatterySnapshot",
        "BatteryOnGrid",
        "BatteryOffGridOutage",
        "BatteryOffGridNoHomePower",
        "BatteryOffGridOvercurrent",
        "BatteryOffGridOvercurrentStandby",
        "BatteryPowerFlow",
        "GetDailyOverviewRequest",
        "GetDailyOverviewResponse",
        "HomePowerSample",
        "GridSupportInterval",
        "HourlyEnergySample",
        "HourlyEnergyCostSample",
        "EnergySourceMix",
        "GetDailyEnergyRequest",
        "GetDailyEnergyResponse",
        "DailyEnergySample",
        "GetRecentGridVoltageRequest",
        "GetRecentGridVoltageResponse",
        "GridVoltageSample",
        "GetLocationRequest",
        "GetLocationResponse",
        "ListLocationsResponse",
        "Location",
        "LocationSummary",
        "DisplayAddress",
        "EnergyState",
        "BatteryState",
        "BatteryPlanned",
        "BatteryActivating",
        "BatteryOnsite",
        "Battery",
        "LocationCapabilities",
        "AccountReference",
        "GetCurrentBillingCycleRequest",
        "GetCurrentBillingCycleResponse",
        "CurrentBillingCycle",
        "GetAccountRequest",
        "GetAccountResponse",
        "Account",
        "Plan",
        "BatteryPlan",
        "BatteryEquipment",
        "ListAccountsRequest",
        "ListAccountsResponse",
        "AccountSummary",
        "OpenObligation",
    )
]

ENUMS = [
    LEGACY + "Battery.Status",
    LEGACY + "GatewayWifiState",
    V2 + "AccountType",
    V2 + "BatteryGeneration",
    V2 + "BatteryVariant",
]


def main() -> None:
    dump = json.loads(Path(sys.argv[1]).read_text())
    missing = [name for name in MESSAGES if name not in dump["messages"]]
    missing += [name for name in ENUMS if name not in dump["enums"]]
    if missing:
        sys.exit("Not in the dump (renamed or removed by Base?):\n  " + "\n  ".join(missing))
    fixture = {
        "source": "gRPC reflection of dashboard.baseapis.net",
        "messages": {name: dump["messages"][name] for name in MESSAGES},
        "enums": {name: dump["enums"][name] for name in ENUMS},
    }
    out = Path(__file__).with_name("base_api_schema.json")
    out.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"wrote {out}: {len(MESSAGES)} messages, {len(ENUMS)} enums")


if __name__ == "__main__":
    main()
