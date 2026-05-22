"""Test Clerk auth - try different token passing methods."""
import asyncio
import aiohttp

CLERK_DOMAIN = "https://clerk.basepowercompany.com"
CLERK_JS_VERSION = "5.56.0-snapshot.v20250409124055"
PUBLISHABLE_KEY = "pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k"
EMAIL = "jerrit.pruyn@gmail.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
}


async def main():
    async with aiohttp.ClientSession() as session:
        # Step 1: Initiate sign-in
        url = f"{CLERK_DOMAIN}/v1/client/sign_ins?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {**_HEADERS, "Authorization": f"Bearer {PUBLISHABLE_KEY}"}
        data = {"identifier": EMAIL}

        async with session.post(url, headers=headers, data=data) as resp:
            print(f"Step 1 status: {resp.status}")
            if resp.status != 200:
                print(f"Error: {await resp.text()}")
                return

            client_token = resp.headers.get("Authorization", "")
            body = await resp.json()
            sign_in_id = body["response"]["id"]

            email_id = None
            for factor in body["response"].get("supported_first_factors", []):
                if factor.get("strategy") == "email_code":
                    email_id = factor.get("email_address_id")
                    break

            print(f"  sign_in_id: {sign_in_id}")
            print(f"  email_id: {email_id}")
            print(f"  client_token len: {len(client_token)}")

        # Step 2 attempt A: pass token as __client cookie
        url2 = f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/prepare_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
        data2 = {"strategy": "email_code", "email_address_id": email_id}

        print(f"\n--- Step 2A: __client cookie ---")
        cookies = {"__client": client_token}
        async with session.post(url2, headers=_HEADERS, data=data2, cookies=cookies) as resp2:
            print(f"  Status: {resp2.status}")
            if resp2.status != 200:
                print(f"  Error: {(await resp2.text())[:200]}")
            else:
                print("  SUCCESS - OTP sent!")
                return

        # Step 2 attempt B: Authorization with Bearer prefix
        print(f"\n--- Step 2B: Bearer prefix ---")
        headers2b = {**_HEADERS, "Authorization": f"Bearer {client_token}"}
        async with session.post(url2, headers=headers2b, data=data2) as resp2b:
            print(f"  Status: {resp2b.status}")
            if resp2b.status != 200:
                print(f"  Error: {(await resp2b.text())[:200]}")
            else:
                print("  SUCCESS - OTP sent!")
                return

        # Step 2 attempt C: pass as _clerk_session_id in query
        print(f"\n--- Step 2C: __client_uat query param ---")
        url2c = f"{url2}&__clerk_client_jwt={client_token}"
        async with session.post(url2c, headers=_HEADERS, data=data2) as resp2c:
            print(f"  Status: {resp2c.status}")
            if resp2c.status != 200:
                print(f"  Error: {(await resp2c.text())[:200]}")
            else:
                print("  SUCCESS - OTP sent!")


asyncio.run(main())
