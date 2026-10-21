"""Constants for the Base Power integration."""

DOMAIN = "base_power"

# API
API_HOST = "https://dashboard.baseapis.net"
API_SERVICE = "dashboard.DashboardAPI"
API_CONTENT_TYPE = "application/grpc-web+proto"

# Clerk Auth
CLERK_DOMAIN = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5"

# Polling intervals (seconds)
SCAN_INTERVAL_SECONDS = 300  # 5 minutes
GRID_SCAN_INTERVAL_SECONDS = 60  # 1 minute

# Battery capacity options: label → (units, kWh). Nameplate sizes from Base Power:
# original hardware is 25 kWh per unit; Base Core is 39.2 kWh per unit.
BATTERY_CAPACITY_OPTIONS: dict[str, tuple[int, float]] = {
    "25 kWh (1 unit)": (1, 25.0),
    "39.2 kWh (1 Core)": (1, 39.2),
    "50 kWh (2 units)": (2, 50.0),
    "78.4 kWh (2 Core)": (2, 78.4),
}
# Rounded label used by the unreleased 1.10.0 build.
_BATTERY_CAPACITY_ALIASES = {
    "40 kWh (1 unit)": "39.2 kWh (1 Core)",
}
DEFAULT_BATTERY_CAPACITY = "25 kWh (1 unit)"
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


def resolve_battery_capacity(label: str | None) -> tuple[int, float] | None:
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
