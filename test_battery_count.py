"""
Probe Base Power API endpoints and dump full protobuf structures.

Usage:
  python3 test_battery_count.py <email>

Probes:
  - MobileGetAvailableLocations  (may contain unit count in location record)
  - MobileGetDashboardRoot       (with discovered service_location_id)
  - MobileGetGridStatus          (may have per-unit data)
  - MobileGetUsageCycles         (asset_id may encode unit info)

Each response is fully dumped — all fields recursively — so we can spot
where the battery unit count lives without guessing.
"""
import asyncio
import struct
import sys
import aiohttp

CLERK_DOMAIN = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5.56.0-snapshot.v20250409124055"
PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"
API_HOST = "https://dashboard.baseapis.net"
API_SERVICE = "dashboard.DashboardAPI"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
}


def decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    val, shift = 0, 0
    while offset < len(data):
        b = data[offset]
        val |= (b & 0x7F) << shift
        shift += 7
        offset += 1
        if not (b & 0x80):
            break
    return val, offset


def skip_field(data: bytes, offset: int, wire_type: int) -> int:
    if wire_type == 0:
        while offset < len(data) and (data[offset] & 0x80):
            offset += 1
        offset += 1
    elif wire_type == 2:
        length, offset = decode_varint(data, offset)
        offset += length
    elif wire_type == 5:
        offset += 4
    elif wire_type == 1:
        offset += 8
    else:
        offset += 1
    return offset


def dump_protobuf(data: bytes, indent: int = 0, max_depth: int = 6) -> None:
    """Fully recursive protobuf dump — every field, every level."""
    prefix = "  " * indent
    offset = 0

    while offset < len(data):
        tag_val, new_offset = decode_varint(data, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07

        if wire_type == 0:
            val, offset = decode_varint(data, offset)
            print(f"{prefix}Field {field_num} (varint) = {val}")

        elif wire_type == 2:
            length, offset = decode_varint(data, offset)
            sub = data[offset : offset + length]
            offset += length

            # Try to decode as UTF-8 string first
            str_val = None
            if length > 0 and length < 200:
                try:
                    candidate = sub.decode("utf-8")
                    if all(c.isprintable() or c in "\n\r\t" for c in candidate):
                        str_val = candidate
                except Exception:
                    pass

            if str_val is not None:
                print(f"{prefix}Field {field_num} (string, len={length}) = {str_val!r}")
            else:
                print(f"{prefix}Field {field_num} (bytes, len={length}) hex={sub.hex()}")
                if indent < max_depth and length > 0:
                    dump_protobuf(sub, indent + 1, max_depth)

        elif wire_type == 5:
            if offset + 4 <= len(data):
                fv = struct.unpack("<f", data[offset : offset + 4])[0]
                print(f"{prefix}Field {field_num} (float32) = {fv}")
            offset += 4

        elif wire_type == 1:
            if offset + 8 <= len(data):
                dv = struct.unpack("<d", data[offset : offset + 8])[0]
                print(f"{prefix}Field {field_num} (double) = {dv}")
            offset += 8

        else:
            print(f"{prefix}Field {field_num} (wire={wire_type}) unknown, stopping")
            break


def build_grpc_frame(payload: bytes = b"") -> bytes:
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def parse_grpc_frame(raw: bytes) -> bytes:
    if len(raw) < 5:
        return b""
    flag = raw[0]
    if flag == 0x80:
        return b""
    length = struct.unpack(">I", raw[1:5])[0]
    return raw[5 : 5 + length]


def encode_string_field(field_num: int, value: str) -> bytes:
    encoded = value.encode("utf-8")
    tag = (field_num << 3) | 2
    return bytes([tag, len(encoded)]) + encoded


async def call_endpoint(
    session: aiohttp.ClientSession, jwt: str, method: str, payload: bytes = b""
) -> bytes | None:
    url = f"{API_HOST}/{API_SERVICE}/{method}"
    headers = {
        "Content-Type": "application/grpc-web+proto",
        "authorization": jwt,
        "x-grpc-web": "1",
    }
    try:
        async with session.post(url, headers=headers, data=build_grpc_frame(payload)) as resp:
            raw = await resp.read()
            grpc_status = resp.headers.get("grpc-status", "0")
            if grpc_status and grpc_status != "0":
                print(f"  gRPC error: status={grpc_status} message={resp.headers.get('grpc-message','')}")
                return None
            return parse_grpc_frame(raw)
    except Exception as e:
        print(f"  Request failed: {e}")
        return None


async def probe_endpoint(
    session: aiohttp.ClientSession, jwt: str, method: str, payload: bytes = b""
) -> bytes | None:
    print(f"\n{'='*60}")
    print(f"Probing: {method}")
    print(f"{'='*60}")
    data = await call_endpoint(session, jwt, method, payload)
    if data is None or len(data) == 0:
        print("  (no data / error)")
        return None
    print(f"Raw hex ({len(data)} bytes): {data.hex()}\n")
    print("Protobuf structure:")
    dump_protobuf(data)
    return data


async def auth_and_fetch(email: str) -> None:
    async with aiohttp.ClientSession() as session:
        # --- Authentication ---
        print(f"=== Authenticating {email} ===")
        url = f"{CLERK_DOMAIN}/v1/client/sign_ins?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {**_HEADERS, "Authorization": f"Bearer {PUBLISHABLE_KEY}"}
        async with session.post(url, headers=headers, data={"identifier": email}) as resp:
            if resp.status != 200:
                print(f"Sign-in init failed: {resp.status} {await resp.text()}")
                return
            client_token = resp.headers.get("Authorization", "")
            body = await resp.json()

        sessions = body.get("client", {}).get("sessions", [])
        sign_in_id = body["response"]["id"]
        email_id = None
        for f in body["response"].get("supported_first_factors", []):
            if f.get("strategy") == "email_code":
                email_id = f.get("email_address_id")
                break

        if not sessions:
            print("No cached session — starting OTP flow...")
            otp_url = f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/prepare_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
            async with session.post(
                otp_url,
                headers={**_HEADERS, "Authorization": client_token},
                data={"strategy": "email_code", "email_address_id": email_id},
            ) as resp2:
                if resp2.status != 200:
                    print(f"OTP send failed: {resp2.status} {await resp2.text()}")
                    return
                client_token = resp2.headers.get("Authorization", client_token)
                print("  OTP email sent.")

            code = input("Enter the 6-digit code from your email: ").strip()
            verify_url = f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/attempt_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
            async with session.post(
                verify_url,
                headers={**_HEADERS, "Authorization": client_token},
                data={"strategy": "email_code", "code": code},
            ) as resp3:
                if resp3.status != 200:
                    print(f"OTP verify failed: {resp3.status} {await resp3.text()}")
                    return
                body = await resp3.json()
                client_token = resp3.headers.get("Authorization", client_token)
            sessions = body.get("client", {}).get("sessions", [])
            if not sessions:
                print("Still no sessions after OTP — auth failed.")
                return

        session_id = sessions[0]["id"]
        print(f"Found session: {session_id}")

        jwt_url = f"{CLERK_DOMAIN}/v1/client/sessions/{session_id}/tokens?_clerk_js_version={CLERK_JS_VERSION}"
        async with session.post(jwt_url, headers=_HEADERS, cookies={"__client": client_token}) as jr:
            if jr.status != 200:
                print(f"JWT refresh failed: {jr.status}")
                return
            jwt = (await jr.json())["jwt"]
        print(f"Got JWT (len={len(jwt)})")

        # --- Probe MobileGetAvailableLocations (no payload) ---
        loc_data = await probe_endpoint(session, jwt, "MobileGetAvailableLocations")

        # Extract location ID from response
        location_id = None
        if loc_data:
            # Parse field 1 → sub field 1 (string = location_id)
            off = 0
            while off < len(loc_data):
                tv, noff = decode_varint(loc_data, off)
                if noff == off:
                    break
                off = noff
                fn = tv >> 3
                wt = tv & 0x07
                if wt == 2:
                    ln, off = decode_varint(loc_data, off)
                    sub = loc_data[off : off + ln]
                    off += ln
                    if fn == 1:
                        # First string field inside is the location ID
                        soff = 0
                        while soff < len(sub):
                            stv, snoff = decode_varint(sub, soff)
                            if snoff == soff:
                                break
                            soff = snoff
                            sfn = stv >> 3
                            swt = stv & 0x07
                            if swt == 2:
                                sln, soff = decode_varint(sub, soff)
                                val = sub[soff : soff + sln]
                                soff += sln
                                if sfn == 1:
                                    try:
                                        location_id = val.decode("utf-8")
                                    except Exception:
                                        pass
                                    break
                            else:
                                soff = skip_field(sub, soff, swt)
                        if location_id:
                            break
                else:
                    off = skip_field(loc_data, off, wt)

        if location_id:
            print(f"\n>>> Discovered location_id: {location_id}")
            loc_payload = encode_string_field(1, location_id)
        else:
            print("\n>>> Could not discover location_id — using empty payload")
            loc_payload = b""

        # --- Probe DashboardRoot with location_id ---
        await probe_endpoint(session, jwt, "MobileGetDashboardRoot", loc_payload)

        # --- Probe WifiMetrics (primary WiFi source — currently bouncy) ---
        print("\n" + "="*60)
        print("Probing: MobileGetWifiMetrics  *** WiFi focus ***")
        print("="*60)
        wifi_data = await call_endpoint(session, jwt, "MobileGetWifiMetrics", loc_payload)
        if wifi_data:
            print(f"Raw hex ({len(wifi_data)} bytes): {wifi_data.hex()}\n")
            print("Protobuf structure:")
            dump_protobuf(wifi_data)
            # Count top-level repeated field-1 entries so we know how many networks
            off = 0
            field_counts: dict[int, int] = {}
            while off < len(wifi_data):
                tv, noff = decode_varint(wifi_data, off)
                if noff == off:
                    break
                off = noff
                fn = tv >> 3
                wt = tv & 0x07
                field_counts[fn] = field_counts.get(fn, 0) + 1
                if wt == 2:
                    ln, off = decode_varint(wifi_data, off)
                    off += ln
                elif wt == 0:
                    _, off = decode_varint(wifi_data, off)
                else:
                    off = skip_field(wifi_data, off, wt)
            print(f"\n>>> Top-level field occurrence counts: {field_counts}")
            print(">>> If field 1 count > 1, those are scan results.")
            print(">>> If field 2 exists, that is the connected-network entry.")
        else:
            print("  (no data / error)")

        # --- Probe GridStatus ---
        await probe_endpoint(session, jwt, "MobileGetGridStatus", loc_payload)

        # --- Probe UsageCycles (has asset_id) ---
        await probe_endpoint(session, jwt, "MobileGetUsageCycles", loc_payload)

        # --- Try endpoints that might expose equipment/inventory or stable WiFi ---
        for method in [
            "MobileGetSystemInfo",
            "MobileGetBatteryConfig",
            "MobileGetDevices",
            "MobileGetEquipment",
            "MobileGetInstallation",
            "MobileGetDeviceStatus",
            "MobileGetConnectivity",
            "MobileGetNetworkStatus",
        ]:
            await probe_endpoint(session, jwt, method, loc_payload)


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "user@example.com"
    asyncio.run(auth_and_fetch(email))
