"""Clerk token refresh and the gRPC-Web transport, against mocked HTTP."""

from __future__ import annotations

import struct

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.base_power.api import BasePowerApiClient, _encode_service_location_request
from custom_components.base_power.auth import (
    AuthenticationError,
    AuthTemporaryError,
    BasePowerAuth,
    is_auth_rejection,
)

TOKEN_URL = (
    "https://clerk.basepowercompany.com/v1/client/sessions/sess_1/tokens"
    "?_is_native=1&_clerk_js_version=5"
)
API_URL = "https://dashboard.baseapis.net/dashboard.DashboardAPI/MobileGetUsageCycles"


def _frame(flag: int, payload: bytes) -> bytes:
    return bytes([flag]) + struct.pack(">I", len(payload)) + payload


def _grpc(message: bytes = b"", status: str = "0") -> bytes:
    return _frame(0, message) + _frame(0x80, f"grpc-status: {status}\r\n".encode())


def _auth(hass, on_client_token=None) -> BasePowerAuth:
    return BasePowerAuth(async_get_clientsession(hass), "client-1", "sess_1", on_client_token)


@pytest.mark.parametrize(
    ("status", "rejected"),
    [(400, True), (401, True), (403, True), (404, True), (422, True),
     (408, False), (425, False), (429, False), (500, False), (502, False), (503, False)],
)
def test_only_a_client_error_means_sign_in_again(status, rejected):
    assert is_auth_rejection(status) is rejected


async def test_refresh_returns_the_jwt_and_reuses_it(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json={"jwt": "jwt-1"})
    auth = _auth(hass)
    assert await auth.async_ensure_valid_token() == "jwt-1"
    assert await auth.async_ensure_valid_token() == "jwt-1"
    assert aioclient_mock.call_count == 1
    assert await auth.async_ensure_valid_token(force=True) == "jwt-1"
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_busy_or_broken_clerk_is_temporary(hass, aioclient_mock, status):
    aioclient_mock.post(TOKEN_URL, status=status, text="try later")
    with pytest.raises(AuthTemporaryError):
        await _auth(hass).async_ensure_valid_token()


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_rejected_session_is_an_auth_error(hass, aioclient_mock, status):
    aioclient_mock.post(TOKEN_URL, status=status, text="no")
    with pytest.raises(AuthenticationError):
        await _auth(hass).async_ensure_valid_token()


async def test_a_temporary_error_is_not_an_authentication_error():
    assert not issubclass(AuthTemporaryError, AuthenticationError)


async def test_rotated_client_token_is_reported_once(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json={"jwt": "jwt-1"}, headers={"Authorization": "client-2"})
    seen: list[str] = []
    auth = _auth(hass, seen.append)
    await auth.async_ensure_valid_token()
    await auth.async_ensure_valid_token(force=True)
    assert seen == ["client-2"]
    assert auth.client_token == "client-2"
    # The new token is the one sent on the next refresh.
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "client-2"


async def test_unchanged_client_token_is_not_reported(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json={"jwt": "jwt-1"}, headers={"Authorization": "client-1"})
    seen: list[str] = []
    await _auth(hass, seen.append).async_ensure_valid_token()
    assert seen == []


async def test_every_call_asks_for_the_token(hass, aioclient_mock):
    aioclient_mock.post(API_URL, content=_grpc(b"\x12\x03abc"))
    forced: list[bool] = []

    async def provider(*, force: bool = False) -> str:
        forced.append(force)
        return f"jwt-{len(forced)}"

    client = BasePowerApiClient(async_get_clientsession(hass), provider)
    await client.get_usage_cycles("loc-1")
    await client.get_usage_cycles("loc-1")
    assert forced == [False, False]
    assert [call[3]["authorization"] for call in aioclient_mock.mock_calls] == ["jwt-1", "jwt-2"]


async def test_an_expired_token_is_refreshed_and_the_call_retried_once(hass, aioclient_mock):
    replies = [_grpc(status="16"), _grpc(b"\x12\x03abc")]

    async def reply(method, url, data):
        return AiohttpClientMockResponse(method, url, response=replies.pop(0))

    aioclient_mock.post(API_URL, side_effect=reply)
    forced: list[bool] = []

    async def provider(*, force: bool = False) -> str:
        forced.append(force)
        return "new" if force else "old"

    client = BasePowerApiClient(async_get_clientsession(hass), provider)
    data, ok = await client._invoke("MobileGetUsageCycles", b"", optional=True)
    assert (data, ok) == (b"\x12\x03abc", True)
    assert forced == [False, True]
    assert aioclient_mock.mock_calls[-1][3]["authorization"] == "new"


async def test_a_token_rejected_twice_gives_up(hass, aioclient_mock):
    aioclient_mock.post(API_URL, content=_grpc(status="16"))

    async def provider(*, force: bool = False) -> str:
        return "jwt"

    client = BasePowerApiClient(async_get_clientsession(hass), provider)
    assert await client._invoke("MobileGetUsageCycles", b"", optional=True) == (b"", False)
    assert aioclient_mock.call_count == 2


async def test_without_a_provider_there_is_no_retry(hass, aioclient_mock):
    aioclient_mock.post(API_URL, content=_grpc(status="16"))
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    assert await client._invoke("MobileGetUsageCycles", b"", optional=True) == (b"", False)
    assert aioclient_mock.call_count == 1


async def test_an_http_error_without_a_grpc_status_is_a_failure(hass, aioclient_mock):
    aioclient_mock.post(API_URL, status=502, text="bad gateway")
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    assert await client._invoke("MobileGetUsageCycles", b"", optional=True) == (b"", False)


async def test_requests_carry_a_timeout(hass, aioclient_mock, monkeypatch):
    seen: dict[str, object] = {}
    session = async_get_clientsession(hass)
    original = session.post

    def post(url, **kwargs):
        seen.update(kwargs)
        return original(url, **kwargs)

    monkeypatch.setattr(session, "post", post)
    aioclient_mock.post(API_URL, content=_grpc())
    client = BasePowerApiClient(session)
    client.set_jwt("jwt")
    await client._invoke("MobileGetUsageCycles", b"", optional=True)
    assert seen["timeout"].total == 20


def test_long_address_ids_use_a_varint_length():
    address = "a" * 200
    encoded = _encode_service_location_request(address)
    # 200 as a varint is c8 01. One byte would have been wrong.
    assert encoded[:3] == b"\x0a\xc8\x01"
    assert encoded[3:] == address.encode()


# --- sign-in calls ---------------------------------------------------------------

CLERK = "https://clerk.basepowercompany.com/v1/client"
NATIVE = "?_is_native=1&_clerk_js_version=5"


async def test_sign_in_reads_the_ids_and_the_client_token(hass, aioclient_mock):
    aioclient_mock.post(
        f"{CLERK}/sign_ins{NATIVE}",
        json={
            "response": {
                "id": "sia_1",
                "supported_first_factors": [
                    {"strategy": "password"},
                    {"strategy": "email_code", "email_address_id": "idn_1"},
                ],
            }
        },
        headers={"Authorization": "client-1"},
    )
    result = await BasePowerAuth.async_initiate_sign_in(
        async_get_clientsession(hass), "me@example.com", "pk_test"
    )
    assert result == {"sign_in_id": "sia_1", "email_id": "idn_1", "client_token": "client-1"}
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer pk_test"


async def test_sign_in_falls_back_to_the_client_id_in_the_body(hass, aioclient_mock):
    aioclient_mock.post(
        f"{CLERK}/sign_ins{NATIVE}", json={"client": {"id": "client_abc"}, "response": {"id": "sia_1"}}
    )
    result = await BasePowerAuth.async_initiate_sign_in(
        async_get_clientsession(hass), "me@example.com", "pk_test"
    )
    assert result["client_token"] == "client_abc"
    assert result["email_id"] is None


async def test_sending_the_code_keeps_a_rotated_token(hass, aioclient_mock):
    url = f"{CLERK}/sign_ins/sia_1/prepare_first_factor{NATIVE}"
    aioclient_mock.post(url, json={}, headers={"Authorization": "client-2"})
    session = async_get_clientsession(hass)
    result = await BasePowerAuth.async_prepare_first_factor(session, "sia_1", "idn_1", "client-1")
    assert result == {"client_token": "client-2"}
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, json={})
    result = await BasePowerAuth.async_prepare_first_factor(session, "sia_1", "idn_1", "client-1")
    assert result == {"client_token": "client-1"}


async def test_verifying_the_code_returns_the_session(hass, aioclient_mock):
    aioclient_mock.post(
        f"{CLERK}/sign_ins/sia_1/attempt_first_factor{NATIVE}",
        json={"client": {"sessions": [{"id": "sess_9", "last_active_token": {"jwt": "jwt-9"}}]}},
        headers={"Authorization": "client-3"},
    )
    result = await BasePowerAuth.async_attempt_first_factor(
        async_get_clientsession(hass), "sia_1", "123456", "client-2"
    )
    assert result["session_id"] == "sess_9"
    assert result["client_token"] == "client-3"
    assert result["session_jwt"] == "jwt-9"


@pytest.mark.parametrize(
    ("path", "call"),
    [
        ("sign_ins", lambda s: BasePowerAuth.async_initiate_sign_in(s, "me@example.com", "pk")),
        (
            "sign_ins/sia_1/prepare_first_factor",
            lambda s: BasePowerAuth.async_prepare_first_factor(s, "sia_1", "idn_1", "client-1"),
        ),
        (
            "sign_ins/sia_1/attempt_first_factor",
            lambda s: BasePowerAuth.async_attempt_first_factor(s, "sia_1", "000000", "client-1"),
        ),
    ],
)
async def test_a_failed_sign_in_step_raises(hass, aioclient_mock, path, call):
    aioclient_mock.post(f"{CLERK}/{path}{NATIVE}", status=422, text="no")
    with pytest.raises(AuthenticationError):
        await call(async_get_clientsession(hass))


# --- newer v2 calls go to the right service ---------------------------------------

V2 = "https://dashboard.baseapis.net/dashboard.mobile.v2"


@pytest.mark.parametrize(
    ("method", "path", "empty"),
    [
        ("get_battery_snapshot", "BatteryService/GetSnapshot", "state"),
        ("get_daily_overview", "UsageService/GetDailyOverview", "energy_today"),
        ("get_recent_grid_voltage", "UsageService/GetRecentGridVoltage", "voltage_volts"),
        ("get_battery_account", "BillingService/GetAccount", "hardware_sku"),
        ("get_energy_account", "BillingService/ListAccounts", "due_at"),
        ("get_location", "LocationsService/GetLocation", "energy_service"),
        ("get_current_billing_cycle", "BillingService/GetCurrentBillingCycle", "net_accrued_cents"),
    ],
)
async def test_v2_calls_use_their_service_and_survive_an_empty_reply(
    hass, aioclient_mock, method, path, empty
):
    aioclient_mock.post(f"{V2}.{path}", content=_grpc())
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    result = await getattr(client, method)("loc-1")
    assert aioclient_mock.call_count == 1
    assert result[empty] is None


async def test_daily_energy_call(hass, aioclient_mock):
    aioclient_mock.post(f"{V2}.UsageService/GetDailyEnergy", content=_grpc())
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    assert await client.get_daily_energy("loc-1", 1, 2) == []
    assert aioclient_mock.mock_calls[0][2] == _frame(
        0, b"\x0a\x05loc-1" + b"\x12\x02\x08\x01" + b"\x1a\x02\x08\x02"
    )


async def test_site_discovery_prefers_the_newer_list_then_the_older_call(hass, aioclient_mock):
    listing = b"\x0a" + bytes([2 + 6]) + b"\x0a\x06site-1"
    aioclient_mock.post(f"{V2}.LocationsService/ListLocations", content=_grpc(listing))
    session = async_get_clientsession(hass)
    assert await BasePowerApiClient.async_discover_locations(session, "jwt") == [
        {"address_id": "site-1", "label": "site-1"}
    ]
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{V2}.LocationsService/ListLocations", content=_grpc(status="12"))
    aioclient_mock.post(
        "https://dashboard.baseapis.net/dashboard.DashboardAPI/MobileGetAvailableLocations",
        content=_grpc(listing),
    )
    assert await BasePowerApiClient.async_discover_locations(session, "jwt") == [
        {"address_id": "site-1", "label": "site-1"}
    ]
