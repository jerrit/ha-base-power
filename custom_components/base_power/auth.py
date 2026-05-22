"""Clerk authentication handler for Base Power."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .const import (
    CLERK_BASE_URL,
    CLERK_JS_VERSION,
    CLERK_PUBLISHABLE_KEY,
    JWT_LIFETIME,
    JWT_REFRESH_BUFFER,
)

_LOGGER = logging.getLogger(__name__)


class ClerkAuthError(Exception):
    """Raised when Clerk authentication fails."""


class ClerkAuth:
    """Manages Clerk authentication and JWT refresh for Base Power."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._client_token: str | None = None
        self._session_id: str | None = None
        self._jwt: str | None = None
        self._jwt_expires_at: datetime | None = None

        # Transient sign-in state (only used during config flow)
        self._sign_in_id: str | None = None
        self._email_address_id: str | None = None

    # ------------------------------------------------------------------
    # Sign-in flow (used during config entry setup)
    # ------------------------------------------------------------------

    async def initiate_sign_in(self, email: str) -> bool:
        """Step 1 — Start a Clerk sign-in and request an email OTP.

        Returns True when the OTP has been dispatched.
        """
        url = f"{CLERK_BASE_URL}/v1/client/sign_ins?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {
            "Authorization": f"Bearer {CLERK_PUBLISHABLE_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            async with self._session.post(
                url, headers=headers, data=f"identifier={email}"
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    _LOGGER.debug("Sign-in initiation failed %s: %s", resp.status, body)
                    return False

                # Client token lives in the Authorization response header
                auth_header = resp.headers.get("Authorization") or resp.headers.get("authorization", "")
                self._client_token = auth_header

                body = await resp.json()
                response = body.get("response", {})
                self._sign_in_id = response.get("id")

                for factor in response.get("supported_first_factors", []):
                    if factor.get("strategy") == "email_code":
                        self._email_address_id = factor.get("email_address_id")
                        break

                if not self._sign_in_id or not self._email_address_id:
                    _LOGGER.error("Clerk sign-in: missing sign_in_id or email_address_id")
                    return False

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during sign-in initiation: %s", err)
            return False

        return await self._prepare_otp()

    async def _prepare_otp(self) -> bool:
        """Step 2 — Ask Clerk to send the OTP to the user's email."""
        url = (
            f"{CLERK_BASE_URL}/v1/client/sign_ins/{self._sign_in_id}"
            f"/prepare_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            "Authorization": self._client_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = f"strategy=email_code&email_address_id={self._email_address_id}"

        try:
            async with self._session.post(url, headers=headers, data=data) as resp:
                return resp.status in (200, 201)
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error sending OTP: %s", err)
            return False

    async def verify_otp(self, code: str) -> bool:
        """Step 3 — Verify the OTP code and complete sign-in.

        On success, stores the session_id and updated client_token needed
        for all future JWT refreshes.
        """
        url = (
            f"{CLERK_BASE_URL}/v1/client/sign_ins/{self._sign_in_id}"
            f"/attempt_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            "Authorization": self._client_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = f"strategy=email_code&code={code}"

        try:
            async with self._session.post(url, headers=headers, data=data) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    _LOGGER.debug("OTP verification failed %s: %s", resp.status, body)
                    return False

                # Updated client token in response header
                new_token = resp.headers.get("Authorization") or resp.headers.get("authorization")
                if new_token:
                    self._client_token = new_token

                body = await resp.json()
                response = body.get("response", {})
                created_session_id = response.get("created_session_id")

                if not created_session_id:
                    _LOGGER.error("Clerk OTP: no created_session_id in response")
                    return False

                self._session_id = created_session_id
                return True

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error verifying OTP: %s", err)
            return False

    # ------------------------------------------------------------------
    # JWT refresh (called before every API request)
    # ------------------------------------------------------------------

    async def refresh_jwt(self) -> str:
        """Fetch a fresh JWT from Clerk. Raises ClerkAuthError on failure."""
        if not self._client_token or not self._session_id:
            raise ClerkAuthError("No active session — re-authentication required")

        url = (
            f"{CLERK_BASE_URL}/v1/client/sessions/{self._session_id}"
            f"/tokens?_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {"Authorization": self._client_token}

        try:
            async with self._session.post(url, headers=headers) as resp:
                if resp.status == 401:
                    raise ClerkAuthError("Session expired — re-authentication required")
                if resp.status != 200:
                    raise ClerkAuthError(f"JWT refresh failed with status {resp.status}")

                body = await resp.json()
                jwt = body.get("jwt")
                if not jwt:
                    raise ClerkAuthError("Clerk returned empty JWT")

                self._jwt = jwt
                expires_in = JWT_LIFETIME - JWT_REFRESH_BUFFER
                self._jwt_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                return self._jwt

        except aiohttp.ClientError as err:
            raise ClerkAuthError(f"Network error refreshing JWT: {err}") from err

    async def get_valid_jwt(self) -> str:
        """Return a valid JWT, refreshing proactively if it is about to expire."""
        now = datetime.now(timezone.utc)
        if (
            not self._jwt
            or not self._jwt_expires_at
            or now >= self._jwt_expires_at
        ):
            return await self.refresh_jwt()
        return self._jwt

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def restore_session(self, client_token: str, session_id: str) -> None:
        """Restore credentials from a stored config entry (no network call)."""
        self._client_token = client_token
        self._session_id = session_id

    @property
    def client_token(self) -> str | None:
        return self._client_token

    @property
    def session_id(self) -> str | None:
        return self._session_id
