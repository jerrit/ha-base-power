"""
Probe MobileGetDashboardRoot and dump the full protobuf structure.

Usage:
  python3 test_battery_count.py <email>

The script will authenticate, call MobileGetDashboardRoot, and print:
  - Full hex dump of the response
  - Every top-level protobuf field (number, wire type, value/length)
  - Every sub-field inside field-3 sub-messages
  - The derived battery_count using both theories

Run this with the 2-stack user's email to identify the correct field.
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


def dump_protobuf(data: bytes, indent: int = 0) -> None:
    prefix = "  " * indent
    offset = 0
    field3_count = 0
    first_sub_fields: dict[int, int] = {}

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
            print(f"{prefix}Field {field_num} (len={length}) = {sub.hex()}")
            if field_num == 3:
                field3_count += 1
                print(f"{prefix}  [field3 #{field3_count} sub-fields]")
                sub_offset = 0
                sub_fields: dict[int, int] = {}
                while sub_offset < len(sub):
                    stag, snew = decode_varint(sub, sub_offset)
                    if snew == sub_offset:
                        break
                    sub_offset = snew
                    sf_num = stag >> 3
                    sf_wire = stag & 0x07
                    if sf_wire == 0:
                        sv, sub_offset = decode_varint(sub, sub_offset)
                        sub_fields[sf_num] = sv
                        print(f"{prefix}    sub-field {sf_num} (varint) = {sv}")
                    elif sf_wire == 5:
                        if sub_offset + 4 <= len(sub):
                            fv = struct.unpack("<f", sub[sub_offset : sub_offset + 4])[0]
                            print(f"{prefix}    sub-field {sf_num} (float32) = {fv}")
                        sub_offset += 4
                    elif sf_wire == 2:
                        slen, sub_offset = decode_varint(sub, sub_offset)
                        sv_bytes = sub[sub_offset : sub_offset + slen]
                        try:
                            sv_str = sv_bytes.decode("utf-8")
                            print(f"{prefix}    sub-field {sf_num} (string) = {sv_str!r}")
                        except Exception:
                            print(f"{prefix}    sub-field {sf_num} (bytes) = {sv_bytes.hex()}")
                        sub_offset += slen
                    else:
                        print(f"{prefix}    sub-field {sf_num} (wire={sf_wire}) skipped")
                        sub_offset = skip_field(sub, sub_offset, sf_wire)
                if field3_count == 1:
                    first_sub_fields = sub_fields
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

    count_subfield = first_sub_fields.get(1, 0)
    battery_count = max(field3_count, count_subfield)
    if battery_count == 0:
        battery_count = 1

    print(f"\n--- Battery count derivation ---")
    print(f"  field3 occurrences : {field3_count}")
    print(f"  subfield-1 value   : {count_subfield}")
    print(f"  → battery_count    : {battery_count} ({battery_count * 25} kWh)")


async def auth_and_fetch(email: str) -> None:
    async with aiohttp.ClientSession() as session:
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

        # Call MobileGetDashboardRoot with empty body (no service_location_id)
        # to let the API return whatever it has for this account.
        grpc_headers = {
            "Content-Type": "application/grpc-web+proto",
            "authorization": jwt,
            "x-grpc-web": "1",
        }
        api_url = f"{API_HOST}/{API_SERVICE}/MobileGetDashboardRoot"
        empty_frame = b"\x00" + struct.pack(">I", 0)
        async with session.post(api_url, headers=grpc_headers, data=empty_frame) as ar:
            raw = await ar.read()
            grpc_status = ar.headers.get("grpc-status", "0")
            print(f"gRPC status: {grpc_status}  response bytes: {len(raw)}")
            if grpc_status and grpc_status != "0":
                print(f"gRPC error: {ar.headers.get('grpc-message', '')}")
                return

        if len(raw) <= 5:
            print("Empty response — try passing a service_location_id")
            return

        # Strip gRPC-Web frame header (1 flag byte + 4 length bytes)
        payload = raw[5 : 5 + struct.unpack(">I", raw[1:5])[0]]
        print(f"\nFull hex: {payload.hex()}\n")
        print("=== Protobuf structure ===")
        dump_protobuf(payload)


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "user@example.com"
    asyncio.run(auth_and_fetch(email))
