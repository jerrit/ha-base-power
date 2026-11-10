"""Sign-in, site choice, re-authentication, and the options form."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.base_power.auth import AuthenticationError, BasePowerAuth
from custom_components.base_power.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FLOW = "custom_components.base_power.config_flow"


@contextmanager
def clerk(locations, *, code_error: Exception | None = None):
    """Fake the three Clerk sign-in calls, the token request, and the site lookup."""
    attempt = AsyncMock(
        return_value={"session_id": "sess_new", "client_token": "client-3", "session_jwt": "jwt"},
        side_effect=code_error,
    )
    with (
        patch.object(
            BasePowerAuth,
            "async_initiate_sign_in",
            AsyncMock(return_value={"sign_in_id": "sia_1", "email_id": "idn_1", "client_token": "client-1"}),
        ),
        patch.object(
            BasePowerAuth, "async_prepare_first_factor", AsyncMock(return_value={"client_token": "client-2"})
        ),
        patch.object(BasePowerAuth, "async_attempt_first_factor", attempt),
        patch.object(BasePowerAuth, "async_ensure_valid_token", AsyncMock(return_value="jwt")),
        patch(f"{FLOW}.BasePowerApiClient.async_discover_locations", AsyncMock(return_value=locations)),
        patch("custom_components.base_power.async_setup_entry", AsyncMock(return_value=True)) as setup,
    ):
        yield setup


async def _to_code_step(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "me@example.com"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "verify_code"
    return result


async def test_one_site_is_added_without_asking(hass):
    with clerk([{"address_id": "site-1", "label": "1 Main St, Austin"}]) as setup:
        result = await _to_code_step(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Base Power (site-1)"
    assert result["data"] == {
        "email": "me@example.com",
        "client_token": "client-3",
        "session_id": "sess_new",
        "service_location_id": "site-1",
    }
    assert result["result"].unique_id == "site-1"
    assert setup.call_count == 1


async def test_several_sites_show_a_picker(hass):
    sites = [
        {"address_id": "site-1", "label": "1 Main St, Austin"},
        {"address_id": "site-2", "label": "2 Lake Rd, Dallas"},
    ]
    with clerk(sites):
        result = await _to_code_step(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "pick_location"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"service_location_id": "site-2"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["service_location_id"] == "site-2"


async def test_a_site_already_set_up_is_skipped(hass):
    MockConfigEntry(domain=DOMAIN, unique_id="site-1", data={"service_location_id": "site-1"}).add_to_hass(hass)
    sites = [
        {"address_id": "site-1", "label": "1 Main St, Austin"},
        {"address_id": "site-2", "label": "2 Lake Rd, Dallas"},
    ]
    with clerk(sites):
        result = await _to_code_step(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["service_location_id"] == "site-2"


async def test_no_site_found_asks_for_the_id(hass):
    with clerk([]):
        result = await _to_code_step(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "location"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"service_location_id": "typed-id"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["service_location_id"] == "typed-id"


async def test_a_wrong_code_shows_an_error_and_can_be_retried(hass):
    with clerk([], code_error=AuthenticationError("422")):
        result = await _to_code_step(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "verify_code"
    assert result["errors"] == {"base": "invalid_code"}


async def test_sign_in_failure_shows_an_error(hass):
    with patch.object(
        BasePowerAuth, "async_initiate_sign_in", AsyncMock(side_effect=AuthenticationError("403"))
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "me@example.com"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "auth_failed"}


async def test_reauth_updates_the_tokens_and_keeps_the_site(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-1",
        data={
            "email": "me@example.com",
            "client_token": "old-client",
            "session_id": "old-session",
            "session_jwt": "stale",
            "service_location_id": "site-1",
        },
    )
    entry.add_to_hass(hass)
    with clerk([{"address_id": "other", "label": "x"}]):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "me@example.com"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["client_token"] == "client-3"
    assert entry.data["session_id"] == "sess_new"
    assert entry.data["service_location_id"] == "site-1"
    assert "session_jwt" not in entry.data


async def test_options_save_capacity_without_a_wifi_network(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="site-1", data={"service_location_id": "site-1"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"battery_capacity": "50 kWh (2 units)"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"battery_capacity": "50 kWh (2 units)"}


async def test_options_can_still_name_a_wifi_network(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="site-1", data={"service_location_id": "site-1"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"wifi_ssid": "HomeNet", "battery_capacity": "25 kWh (1 unit)"}
    )
    assert entry.options["wifi_ssid"] == "HomeNet"
