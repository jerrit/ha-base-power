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

# Battery
BATTERY_CAPACITY_PER_UNIT_KWH = 25  # Each Base Power battery is 25 kWh
DEFAULT_BATTERY_COUNT = 1  # Fallback if API doesn't report count

# Config keys
CONF_CLIENT_TOKEN = "client_token"
CONF_SESSION_ID = "session_id"
CONF_SESSION_JWT = "session_jwt"
CONF_SERVICE_LOCATION_ID = "service_location_id"
CONF_EMAIL = "email"

# Platforms
PLATFORMS = ["sensor", "binary_sensor"]
