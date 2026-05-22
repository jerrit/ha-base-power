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
BATTERY_CAPACITY_KWH = 25
BATTERY_FULL_BACKUP_SECONDS = 49652  # Calibration: 100% = 49652 seconds

# Config keys
CONF_CLIENT_TOKEN = "client_token"
CONF_SESSION_ID = "session_id"
CONF_SERVICE_LOCATION_ID = "service_location_id"
CONF_EMAIL = "email"

# Platforms
PLATFORMS = ["sensor", "binary_sensor"]
