"""Test: Can we auto-discover service_location_id?"""
import asyncio
import struct
import base64
import json
import aiohttp

CLERK_DOMAIN = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5.56.0-snapshot.v20250409124055"
PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"
EMAIL = "jerrit.pruyn@gmail.com"
API_HOST = "https://dashboard.baseapis.net:443"
API_SERVICE = "dashboard.DashboardAPI"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
}


async def main():
    async with aiohttp.ClientSession() as session:
        # Use existing session (from previous auth)
        # We need a fresh session - let's do full auth
        print("=== Step 1: Sign in ===")
        url = f"{CLERK_DOMAIN}/v1/client/sign_ins?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {**_HEADERS, "Authorization": f"Bearer {PUBLISHABLE_KEY}"}
        data = {"identifier": EMAIL}
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status != 200:
                print(f"Sign-in failed: {await resp.text()}")
                return
            client_token = resp.headers.get("Authorization", "")
            body = await resp.json()
            sign_in_id = body["response"]["id"]
            # Check if there's already an active session we can use
            sessions = body.get("client", {}).get("sessions", [])
            if sessions:
                session_id = sessions[0].get("id")
                print(f"  Found existing session: {session_id}")
                # Get JWT from existing session
                jwt_url = f"{CLERK_DOMAIN}/v1/client/sessions/{session_id}/tokens?_clerk_js_version={CLERK_JS_VERSION}"
                cookies = {"__client": client_token}
                async with session.post(jwt_url, headers=_HEADERS, cookies=cookies) as jwt_resp:
                    if jwt_resp.status == 200:
                        jwt_data = await jwt_resp.json()
                        jwt = jwt_data["jwt"]
                        print(f"  Got JWT (len={len(jwt)})")
                        
                        # Decode JWT payload
                        parts = jwt.split(".")
                        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        claims = json.loads(base64.b64decode(payload))
                        print(f"\n=== JWT Claims ===")
                        for k, v in claims.items():
                            print(f"  {k}: {v}")
                        
                        # Try API call with empty body
                        print(f"\n=== Try MobileGetDashboardRoot with empty body ===")
                        api_url = f"{API_HOST}/{API_SERVICE}/MobileGetDashboardRoot"
                        grpc_headers = {
                            "Content-Type": "application/grpc-web+proto",
                            "authorization": jwt,
                            "x-grpc-web": "1",
                        }
                        empty_frame = b"\x00" + struct.pack(">I", 0)
                        async with session.post(api_url, headers=grpc_headers, data=empty_frame) as api_resp:
                            raw = await api_resp.read()
                            print(f"  Status: {api_resp.status}")
                            print(f"  grpc-status: {api_resp.headers.get('grpc-status', 'none')}")
                            print(f"  Response len: {len(raw)}")
                            if len(raw) > 5:
                                print(f"  Got data! (might contain location)")
                                print(f"  Raw hex: {raw[:50].hex()}")
                    else:
                        print(f"  JWT refresh failed: {jwt_resp.status}")
            else:
                print("  No existing sessions, would need OTP")


asyncio.run(main())
