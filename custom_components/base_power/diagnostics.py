"""Diagnostics for Base Power, with account and home details removed.

Download from the integration's menu and attach to a bug report. Tokens,
the email address, site and account ids, and Wi-Fi names are replaced with
"**REDACTED**".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIENT_TOKEN,
    CONF_SERVICE_LOCATION_ID,
    CONF_SESSION_ID,
    CONF_SESSION_JWT,
    CONF_WIFI_SSID,
)
from .entity import BasePowerConfigEntry

TO_REDACT = {
    CONF_CLIENT_TOKEN,
    CONF_SESSION_ID,
    CONF_SESSION_JWT,
    CONF_EMAIL,
    CONF_SERVICE_LOCATION_ID,
    CONF_WIFI_SSID,
    "account_id",
    "scan",
    "ssid",
    "wifi_ssid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BasePowerConfigEntry
) -> dict[str, Any]:
    """Return what the integration last read from Base."""
    coordinator = entry.runtime_data
    poll = coordinator._poll
    totals = coordinator._totals
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "poll": {
            "has_numeric_id": poll.numeric_id is not None,
            "slow_failed": poll.slow_failed,
            "usage_source": poll.usage_source,
            "energy_source": poll.energy_source,
            "usage_failures": coordinator._usage_failures,
            "daily_energy_days": len(poll.daily_energy),
        },
        "totals": {
            "home_kwh": totals.home_kwh,
            "solar_kwh": totals.solar_kwh,
            "hours_counted": len(totals.counted),
            "days_repaired": len(totals.repaired),
            "since": totals.since,
        },
    }
