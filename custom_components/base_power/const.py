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
# Steady state is about 132 calls/hour:
#   every 60 s, ServiceContext + GridStatus (120/hour)
#   every 10 min, the v2 battery snapshot (6/hour). Every 60 s while the
#     outage flag, voltage, or the last snapshot points to an outage.
#   every 15 min, the v2 DailyOverview (4/hour). RecentUsage and UsageEnergy
#     run only when the overview fails or lacks that data.
#   every 6 hours, DashboardRoot + Wifi + Billing + UsageCycles
#     + v2 CurrentBillingCycle + v2 battery GetAccount + v2 ListAccounts
#     + v2 GetLocation + v2 GetDailyEnergy + GetHome (about 1.7/hour). After a failed
#     call this tier runs again after 15 minutes, then 30, 60, and so on,
#     up to 6 hours.
# The v2 GetRecentGridVoltage runs only when GridStatus has no voltage.
# The coordinator interval is the 15-minute tier. It does not repeat the
# 60-second grid calls.
REQUEST_TIMEOUT_SECONDS = 20
GRID_SCAN_INTERVAL_SECONDS = 60
USAGE_SCAN_INTERVAL_SECONDS = 15 * 60
SLOW_SCAN_INTERVAL_SECONDS = 6 * 60 * 60
# After a failed slow refresh, the retry waits 15 minutes, then doubles after
# each failure in a row, up to the normal 6 hours.
SLOW_RETRY_MAX_SECONDS = SLOW_SCAN_INTERVAL_SECONDS
SCAN_INTERVAL_SECONDS = USAGE_SCAN_INTERVAL_SECONDS
# MobileGetGridStatus reports about 240 V while the grid is up.
# 100 V is still grid. Under 50 V the feed has collapsed.
GRID_PRESENT_VOLTS = 100.0
GRID_COLLAPSED_VOLTS = 50.0

# Battery capacity options: label → (units, kWh).
# Core uses Base's published sizes, 39.2 kWh per unit (78.4 kWh for two), the
# same as SKU detection. Older versions saved the rounded 40 and 80 labels.
BATTERY_CAPACITY_OPTIONS: dict[str, tuple[int, float]] = {
    "20 kWh (1 Wall)": (1, 20),
    "25 kWh (1 unit)": (1, 25),
    "39.2 kWh (1 Core)": (1, 39.2),
    "50 kWh (2 units)": (2, 50),
    "78.4 kWh (2 Core)": (2, 78.4),
}
_BATTERY_CAPACITY_ALIASES = {
    "40 kWh (1 Core)": "39.2 kWh (1 Core)",
    "40 kWh (1 unit)": "39.2 kWh (1 Core)",
    "80 kWh (2 Core)": "78.4 kWh (2 Core)",
}
DEFAULT_BATTERY_CAPACITY = "25 kWh (1 unit)"
# Battery hardware SKU from the v2 battery account → (units, kWh).
# Names and sizes match Base's web checkout. Core uses Base's exact sizes.
# NO_BATTERY is left out on purpose, so it falls back to the dropdown.
HARDWARE_SKU_CAPACITY: dict[str, tuple[int, float]] = {
    "GRO_SINGLE": (1, 25),
    "GRO_DUAL": (2, 50),
    "GRO_WALL": (1, 20),
    "BASE1_SINGLE": (1, 39.2),
    "BASE1_DUAL": (2, 78.4),
}
# Battery models from the site's location, used when the SKU is missing.
# GEN1 matches the GRO SKUs. GEN3 SINGLE is a Base Core, confirmed by an
# owner with BASE1_SINGLE (issue #26). GEN3 DUAL is not confirmed yet, so
# it falls back to the other sources.
BATTERY_MODEL_CAPACITY: dict[tuple[str, str], tuple[int, float]] = {
    ("GEN1", "SINGLE"): (1, 25),
    ("GEN1", "DUAL"): (2, 50),
    ("GEN1", "WALL"): (1, 20),
    ("GEN3", "SINGLE"): (1, 39.2),
}


def battery_model_capacity(batteries: list[dict[str, str | None]]) -> tuple[int, float] | None:
    """Add up (units, kWh) for the listed batteries. None if any is unknown."""
    if not batteries:
        return None
    units = 0
    kwh = 0.0
    for battery in batteries:
        known = BATTERY_MODEL_CAPACITY.get((battery.get("generation"), battery.get("variant")))
        if known is None:
            return None
        units += known[0]
        kwh += known[1]
    return units, round(kwh, 1)


# Units per battery type. Holds for every generation: a single is one unit.
BATTERY_VARIANT_UNITS: dict[str, int] = {"SINGLE": 1, "DUAL": 2, "WALL": 1}


def battery_variant_count(batteries: list[dict[str, str | None]]) -> int | None:
    """Units from the battery types alone. None if any type is unknown."""
    if not batteries:
        return None
    units = [BATTERY_VARIANT_UNITS.get(battery.get("variant") or "") for battery in batteries]
    if None in units:
        return None
    return sum(units)


# Capacity estimate from Base's backup time, for hardware not in the tables
# above. Base's 750 W backup estimate is usable energy ÷ 0.75 kW.
ESTIMATE_LOAD_KW = 0.75
# Usable share of the nameplate. Measured on one GRO_SINGLE: 22.5 of 25 kWh.
ESTIMATE_USABLE_FRACTION = 0.9
# Readings below this charge are skipped. A small error in a low percent
# would scale up into a large error in size.
ESTIMATE_MIN_SOC_PERCENT = 50
# Median of the last hour of 60-second readings, once there are a few
ESTIMATE_SAMPLES = 60
ESTIMATE_MIN_SAMPLES = 3
# The estimate is used only within this share of a known size. Known sizes
# are at least 20% apart, so a reading outside this is not trusted.
ESTIMATE_SNAP_TOLERANCE = 0.12
# Base's own nameplate (GetHome) must be this close to a known size to
# borrow that size's unit count.
NAMEPLATE_MATCH_TOLERANCE = 0.03


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
# Street and city from the setup site picker. Names the device.
CONF_SITE_LABEL = "site_label"

# Running energy totals, saved across restarts
STORAGE_VERSION = 1
def totals_store_key(entry_id: str) -> str:
    """Storage key for one config entry's running totals."""
    return f"base_power.{entry_id}.totals"


def reserve_store_key(entry_id: str) -> str:
    """Storage key for one config entry's learned battery reserve."""
    return f"base_power.{entry_id}.reserve"


# Hours already counted, kept this long so a late hour is counted once and a
# finished day can be checked against Base's daily total.
COUNTED_HOURS_KEEP_SECONDS = 40 * 86400
# Base's daily totals are asked for this far back. Base keeps about a month.
DAILY_ENERGY_LOOKBACK_SECONDS = 34 * 86400
# A day is checked against Base's daily total once it has been over this long.
DAY_SETTLE_SECONDS = 6 * 3600
# A smaller gap between Base's daily total and the counted hours is rounding.
DAY_REPAIR_MIN_KWH = 0.05

# The usage tier is stale after this many refreshes in a row with no new data.
USAGE_STALE_AFTER_FAILURES = 4
# Battery Charging stays on for a while after a rise in charge, since Base
# reports whole percents. One rise holds it for the short hold. Once two
# rises are seen, the hold is twice the time between them, up to the long
# hold. A slow charge can take hours per percent.
CHARGING_FIRST_HOLD_SECONDS = 3600
CHARGING_HOLD_SECONDS = 3 * 3600
# A rise back to the level the charge just fell from is rounding, not
# charging, when it comes within this long of the fall.
CHARGING_BOUNCE_SECONDS = 3600
# Live battery power at or below minus this many kW means charging. Base
# reports it in 0.1 kW steps, so one step of -0.1 kW counts as noise.
CHARGING_STORAGE_KW = 0.15
# Base keeps about this much of the battery in reserve. Its 750 W backup
# time covers the rest: hours x 0.75 kW = (percent - reserve) x nameplate
# kWh. 6.4% fits an outage test from 98% down to 50% on a Base Core (issue
# #29). Each install learns its own value while off grid, when Base sends
# the real percent.
BACKUP_RESERVE_PERCENT = 6.4
# A learned reserve outside this range is a bad reading and is ignored.
RESERVE_MIN_PERCENT = 0.0
RESERVE_MAX_PERCENT = 20.0
# Readings are taken only in this charge range. The top end bends a little
# near full.
RESERVE_MIN_SOC_PERCENT = 15.0
RESERVE_MAX_SOC_PERCENT = 95.0
# The learned reserve is used once it has this many readings. Base sends
# whole percents, so one reading is off by up to half a point.
RESERVE_MIN_SAMPLES = 10
# The running average weighs the newest reading at least 1 in this many.
RESERVE_AVERAGE_WINDOW = 200
# The daily overview's backup samples are used for 750 W backup time and the
# battery percent while the newest one is at most this old.
BACKUP_SERIES_MAX_AGE_SECONDS = 2 * 3600
# Solar to Home today at or above this many kWh means the site has solar.
# Some sites without panels report 0.0001 kWh every hour (issue #26).
SOLAR_DETECT_KWH = 0.05
# A missed ServiceContext reuses the last good one for this long, so one
# failed 60-second call does not blank the live sensors.
CONTEXT_HOLD_SECONDS = 3 * 60

# Platforms
PLATFORMS = ["sensor", "binary_sensor"]
