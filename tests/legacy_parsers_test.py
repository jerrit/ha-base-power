"""Parsers for the older dashboard.DashboardAPI calls, built from the schema."""

from __future__ import annotations

import struct

from custom_components.base_power.api import BasePowerApiClient, _encode_varint, _parse_first_location_id


def msg(field: int, body: bytes) -> bytes:
    return _encode_varint(field << 3 | 2) + _encode_varint(len(body)) + body


def num(field: int, value: int) -> bytes:
    return _encode_varint(field << 3) + _encode_varint(value)


def flt(field: int, value: float) -> bytes:
    return _encode_varint(field << 3 | 5) + struct.pack("<f", value)


def ts(field: int, seconds: int) -> bytes:
    return msg(field, num(1, seconds))


def test_dashboard_root_reads_id_status_solar_zone_and_service_date():
    address = msg(2, b"1 Main St") + msg(4, b"Austin") + msg(9, b"America/Chicago")
    battery = num(1, 1) + num(2, 3) + num(3, 1) + num(4, 1) + ts(5, 1_800_500_000)
    parsed = BasePowerApiClient._parse_dashboard_root(
        msg(1, b"site-1") + msg(2, address) + msg(3, battery) + num(7, 49652)
    )
    assert parsed == {
        "service_location_id": 49652,
        "battery_status": 3,
        "has_solar": True,
        "time_zone": "America/Chicago",
        "upcoming_service_at": 1_800_500_000,
    }


def test_dashboard_root_show_telemetry_is_not_a_battery_count():
    # battery.showTelemetry (field 1) is a bool. It was once read as a unit count.
    parsed = BasePowerApiClient._parse_dashboard_root(msg(3, num(1, 1) + num(2, 2)) + num(7, 5))
    assert "battery_count" not in parsed
    assert parsed["battery_status"] == 2
    assert parsed["has_solar"] is False


def test_dashboard_root_empty_reply():
    parsed = BasePowerApiClient._parse_dashboard_root(b"")
    assert parsed["service_location_id"] is None
    assert parsed["time_zone"] is None


def test_recent_usage_keeps_a_zero_power_point():
    # Proto3 leaves out a 0.0 float. The old fixed-width parser stopped there.
    points = BasePowerApiClient._parse_recent_usage(
        msg(1, ts(1, 1000) + flt(2, 2.0))
        + msg(1, ts(1, 1900))
        + msg(1, ts(1, 2800) + flt(2, 4.0))
        + msg(2, ts(1, 1000) + flt(2, 28.0))  # duration_data, not power
    )
    assert points == [
        {"timestamp": 1000, "kwh": 0.5, "watts": 2000},
        {"timestamp": 1900, "kwh": 0.0, "watts": 0},
        {"timestamp": 2800, "kwh": 1.0, "watts": 4000},
    ]


def test_recent_usage_drops_bad_points():
    points = BasePowerApiClient._parse_recent_usage(
        msg(1, flt(2, 2.0)) + msg(1, ts(1, 1000) + flt(2, float("nan")))
    )
    assert points == []
    assert BasePowerApiClient._parse_recent_usage(b"") == []


def test_wifi_metrics_reads_networks_and_when_they_were_seen():
    parsed = BasePowerApiClient._parse_wifi_metrics(
        msg(1, msg(1, b"HomeNet") + num(2, 68)) + msg(1, msg(1, b"Other")) + ts(2, 1_800_000_000)
    )
    assert parsed["scan"] == {"HomeNet": 68, "Other": None}
    assert parsed["last_updated"] == 1_800_000_000
    assert BasePowerApiClient._parse_wifi_metrics(b"")["scan"] == {}


def test_service_context_reads_wifi_and_750w_backup():
    wifi = msg(1, b"HomeNet")  # state 0 (connected) is left out by proto3
    parsed = BasePowerApiClient._parse_service_context(
        num(1, 1) + num(7, 100) + flt(8, 32.2) + msg(4, wifi) + flt(11, 30.0)
    )
    assert parsed["wifi_ssid"] == "HomeNet"
    assert parsed["wifi_state"] == "connected"
    assert parsed["available_backup_hours_750w"] == 30.0
    for state, name in ((1, "connecting"), (2, "not_connected"), (9, None)):
        parsed = BasePowerApiClient._parse_service_context(msg(4, wifi + num(2, state)))
        assert parsed["wifi_state"] == name


def test_service_context_without_wifi_leaves_it_unknown():
    parsed = BasePowerApiClient._parse_service_context(num(1, 1))
    assert parsed["wifi_ssid"] is None
    assert parsed["wifi_state"] is None
    assert parsed["available_backup_hours_750w"] is None


def test_combined_grid_carries_wifi_and_750w_backup():
    context = BasePowerApiClient._parse_service_context(msg(4, msg(1, b"HomeNet")) + flt(11, 29.96))
    grid = BasePowerApiClient.combine_grid_telemetry(context, BasePowerApiClient._empty_grid_voltage())
    assert grid["wifi_ssid"] == "HomeNet"
    assert grid["wifi_state"] == "connected"
    assert grid["backup_hours_750w"] == 29.96


def test_billing_metadata_reads_only_the_rep_message():
    rep = num(1, 2) + num(2, 18853) + num(3, 33840)
    parsed = BasePowerApiClient._parse_billing_metadata(msg(1, rep) + msg(2, num(1, 2)))
    assert parsed == {"amount_cents": 18853, "last_bill_cents": 33840}
    # A top-level number is not part of the message.
    assert BasePowerApiClient._parse_billing_metadata(num(2, 500)) == {
        "amount_cents": None,
        "last_bill_cents": None,
    }


def test_first_location_id():
    reply = msg(1, msg(1, b"site-1") + msg(2, msg(2, b"1 Main St"))) + msg(1, msg(1, b"site-2"))
    assert _parse_first_location_id(reply) == "site-1"
    assert _parse_first_location_id(msg(1, msg(2, b"no id"))) is None
    assert _parse_first_location_id(b"") is None
