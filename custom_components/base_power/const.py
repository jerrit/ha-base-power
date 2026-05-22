"""Constants for the Base Power integration."""

DOMAIN = "base_power"

# API endpoints
BASE_API_URL = "https://dashboard.baseapis.net/dashboard.DashboardAPI"
CLERK_BASE_URL = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5"

# Clerk publishable key — embedded in the Base Power mobile app, not a secret
CLERK_PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"

# Config entry keys
CONF_EMAIL = "email"
CONF_CLIENT_TOKEN = "client_token"
CONF_SESSION_ID = "session_id"
CONF_SERVICE_LOCATION_ID = "service_location_id"
CONF_SERVICE_LOCATION_NAME = "service_location_name"

# Polling intervals (seconds)
DEFAULT_SCAN_INTERVAL = 300   # 5 minutes
BILLING_SCAN_INTERVAL = 3600  # 1 hour
JWT_LIFETIME = 60             # Clerk JWT lasts 60 seconds
JWT_REFRESH_BUFFER = 10       # Refresh 10 seconds before expiry

# Battery status enum values from the API
BATTERY_STATUS = {
    0: "unknown",
    1: "not_installed",
    2: "installed",
    3: "in_service",
    4: "flashing_firmware",
    5: "no_install_planned",
}

# Service state enum values from the API
SERVICE_STATE = {
    0: "unknown",
    1: "not_in_service",
    2: "in_service",
    3: "pending",
}

# Account status enum values from the API
ACCOUNT_STATUS = {
    0: "unknown",
    1: "badge",
    2: "no_payment_due",
    3: "past_due",
    4: "scheduled",
}

# Utility enum values from the API
UTILITY = {
    0: "unknown",
    1: "oncor",
    2: "centerpoint",
    3: "tnmp",
    4: "aep_central",
    5: "aep_north",
    6: "lpl",
}
