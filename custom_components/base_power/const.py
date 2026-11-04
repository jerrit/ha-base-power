"""Constants for the Base Power integration."""

DOMAIN = "base_power"

# API
API_HOST = "https://dashboard.baseapis.net"
API_SERVICE = "dashboard.DashboardAPI"
API_CONTENT_TYPE = "application/grpc-web+proto"

# Clerk Auth
CLERK_DOMAIN = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5"

# Polling intervals (seconds).
# Steady state is about 131 calls/hour:
#   every 60 s, ServiceContext + GridStatus (120/hour)
#   every 10 min, the v2 battery snapshot (6/hour). Every 60 s while the
#     outage flag, voltage, or the last snapshot points to an outage.
#   every 15 min, the v2 DailyOverview (4/hour). RecentUsage and UsageEnergy
#     run only when the overview fails or lacks that data.
#   every 6 hours, DashboardRoot + Wifi + Billing + UsageCycles
#     + v2 CurrentBillingCycle + v2 battery GetAccount (1/hour)
# The v2 GetRecentGridVoltage runs only when GridStatus has no voltage.
# The coordinator interval is the 15-minute tier. It does not repeat the
# 60-second grid calls.
GRID_SCAN_INTERVAL_SECONDS = 60
USAGE_SCAN_INTERVAL_SECONDS = 15 * 60
SLOW_SCAN_INTERVAL_SECONDS = 6 * 60 * 60
SCAN_INTERVAL_SECONDS = USAGE_SCAN_INTERVAL_SECONDS
# MobileGetGridStatus reports about 240 V while the grid is up.
# 100 V is still grid. Under 50 V the feed has collapsed.
GRID_PRESENT_VOLTS = 100.0
GRID_COLLAPSED_VOLTS = 50.0

# Battery capacity options: label → (units, kWh).
# Base lists Core as 39.2 kWh per unit (78.4 kWh for two). The dropdown uses
# the rounded sizes, 40 and 80. Original hardware is already 25 and 50.
BATTERY_CAPACITY_OPTIONS: dict[str, tuple[int, int]] = {
    "25 kWh (1 unit)": (1, 25),
    "40 kWh (1 Core)": (1, 40),
    "50 kWh (2 units)": (2, 50),
    "80 kWh (2 Core)": (2, 80),
}
_BATTERY_CAPACITY_ALIASES = {
    "39.2 kWh (1 Core)": "40 kWh (1 Core)",
    "40 kWh (1 unit)": "40 kWh (1 Core)",
    "78.4 kWh (2 Core)": "80 kWh (2 Core)",
}
DEFAULT_BATTERY_CAPACITY = "25 kWh (1 unit)"
# Battery hardware SKU from the v2 battery account → (units, kWh).
# Only SKUs confirmed on a real account. Add others as users report them.
HARDWARE_SKU_CAPACITY: dict[str, tuple[int, int]] = {
    "GRO_SINGLE": (1, 25),
}
# Legacy fallback for installs that still store an integer unit count.
BATTERY_CAPACITY_PER_UNIT_KWH = 25
DEFAULT_BATTERY_COUNT = 1


def canonical_battery_capacity(label: str | None) -> str | None:
    """Return the dropdown label for a saved value, including old aliases."""
    if not label:
        return None
    canonical = _BATTERY_CAPACITY_ALIASES.get(label, label)
    if canonical in BATTERY_CAPACITY_OPTIONS:
        return canonical
    return None


def resolve_battery_capacity(label: str | None) -> tuple[int, int] | None:
    """Return (units, kWh) for a saved capacity label."""
    canonical = canonical_battery_capacity(label)
    if canonical is None:
        return None
    return BATTERY_CAPACITY_OPTIONS[canonical]


# Config keys
CONF_CLIENT_TOKEN = "client_token"
CONF_SESSION_ID = "session_id"
CONF_SESSION_JWT = "session_jwt"
CONF_SERVICE_LOCATION_ID = "service_location_id"
CONF_EMAIL = "email"
CONF_WIFI_SSID = "wifi_ssid"
CONF_BATTERY_COUNT = "battery_count_override"
CONF_BATTERY_CAPACITY = "battery_capacity"

# Platforms
PLATFORMS = ["sensor", "binary_sensor", "select"]
