"""Clerk authentication handler for Base Power."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import CLERK_DOMAIN, CLERK_JS_VERSION, REQUEST_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)


def _mask_secret(value: str | None) -> str:
    """Keep the last four characters of a credential for a debug log."""
    if not value:
        return "none"
    if len(value) <= 4:
        return "****"
    return f"...{value[-4:]}"


_TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)


def is_auth_rejection(status: int) -> bool:
    """True when Clerk's answer means the session is gone and the user must sign in.

    A rate limit (429), a timeout (408, 425), or a server error is temporary.
    Treating those as a rejection would ask for a new sign-in after a blip.
    """
    return 400 <= status < 500 and status not in (408, 425, 429)


# Refresh JWT 10 seconds before expiry
JWT_REFRESH_BUFFER = 10
JWT_LIFETIME = 60

# Headers to pass Clerk's bot protection
_CLERK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
}


class BasePowerAuth:
    """Handle Clerk authentication and JWT refresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_token: str,
        session_id: str,
        on_client_token: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize auth handler.

        ``on_client_token`` is called with the new client token when Clerk
        rotates it, so the caller can save it for the next restart.
        """
        self._session = session
        self._client_token = client_token
        self._session_id = session_id
        self._on_client_token = on_client_token
        self._jwt: str | None = None
        self._jwt_expires_at: float = 0
        self._lock = asyncio.Lock()

    @property
    def client_token(self) -> str:
        """The current client token. Clerk can rotate it on a refresh."""
        return self._client_token

    @property
    def jwt(self) -> str | None:
        """Get current JWT."""
        return self._jwt

    @property
    def is_token_valid(self) -> bool:
        """Check if current JWT is still valid."""
        return self._jwt is not None and time.monotonic() < self._jwt_expires_at

    async def async_refresh_token(self) -> str:
        """Refresh the JWT from Clerk using native mobile API pattern."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sessions/"
            f"{self._session_id}/tokens"
            f"?_is_native=1&_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            **_CLERK_HEADERS,
            "Authorization": self._client_token,
            "x-mobile": "1",
        }

        async with self._session.post(url, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                await resp.read()
                if is_auth_rejection(resp.status):
                    _LOGGER.error("Clerk rejected the session: status=%s", resp.status)
                    raise AuthenticationError(
                        f"Clerk token refresh failed: {resp.status}"
                    )
                _LOGGER.debug("Clerk token refresh failed for now: status=%s", resp.status)
                raise AuthTemporaryError(
                    f"Clerk token refresh failed for now: {resp.status}"
                )
            # Clerk can rotate the client token. Keep the new one, and hand
            # it to the caller so it survives a restart.
            new_token = resp.headers.get("Authorization")
            if new_token and new_token != self._client_token:
                self._client_token = new_token
                if self._on_client_token is not None:
                    self._on_client_token(new_token)
            data = await resp.json()
            self._jwt = data["jwt"]
            self._jwt_expires_at = time.monotonic() + JWT_LIFETIME - JWT_REFRESH_BUFFER
            return self._jwt

    async def async_ensure_valid_token(self, *, force: bool = False) -> str:
        """Return a valid JWT, refreshing it when it is old or ``force`` is set."""
        async with self._lock:
            if force or not self.is_token_valid:
                return await self.async_refresh_token()
            return self._jwt

    @staticmethod
    async def async_initiate_sign_in(
        session: aiohttp.ClientSession,
        email: str,
        publishable_key: str,
    ) -> dict[str, Any]:
        """Step 1: Initiate Clerk sign-in with email."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sign_ins"
            f"?_is_native=1&_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            **_CLERK_HEADERS,
            "Authorization": f"Bearer {publishable_key}",
            "x-mobile": "1",
        }
        data = {"identifier": email}

        async with session.post(url, headers=headers, data=data, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                await resp.read()
                _LOGGER.error("Sign-in initiation failed: status=%s", resp.status)
                raise AuthenticationError(f"Sign-in initiation failed: {resp.status}")

            # Client token from Authorization response header
            client_token = resp.headers.get("Authorization", "")
            body = await resp.json()

            # Fallback: extract token from response body if header is empty
            if not client_token:
                client_token = body.get("client", {}).get("id", "")
                _LOGGER.debug("Authorization header empty; using the client id from the body")

            sign_in_id = body.get("response", {}).get("id")
            email_id = None
            first_factors = (
                body.get("response", {})
                .get("supported_first_factors", [])
            )
            for factor in first_factors:
                if factor.get("strategy") == "email_code":
                    email_id = factor.get("email_address_id")
                    break

            _LOGGER.debug(
                "Sign-in initiated: has_id=%s has_email_id=%s token_len=%d",
                bool(sign_in_id),
                bool(email_id),
                len(client_token) if client_token else 0,
            )

            return {
                "sign_in_id": sign_in_id,
                "email_id": email_id,
                "client_token": client_token,
            }

    @staticmethod
    async def async_prepare_first_factor(
        session: aiohttp.ClientSession,
        sign_in_id: str,
        email_id: str,
        client_token: str,
    ) -> dict[str, Any]:
        """Step 2: Send OTP code to email."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/"
            f"prepare_first_factor?_is_native=1&_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            **_CLERK_HEADERS,
            "Authorization": client_token,
            "x-mobile": "1",
        }
        data = {"strategy": "email_code", "email_address_id": email_id}

        async with session.post(url, headers=headers, data=data, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                await resp.read()
                _LOGGER.error("prepare_first_factor failed: status=%s", resp.status)
                raise AuthenticationError(
                    f"Failed to send verification email: {resp.status}"
                )
            # Capture rotated client token
            updated_token = resp.headers.get("Authorization", client_token)
            _LOGGER.debug(
                "prepare_first_factor token rotated: %s",
                updated_token != client_token,
            )
            return {"client_token": updated_token}

    @staticmethod
    async def async_attempt_first_factor(
        session: aiohttp.ClientSession,
        sign_in_id: str,
        code: str,
        client_token: str,
    ) -> dict[str, Any]:
        """Step 3: Verify OTP code and get session."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/"
            f"attempt_first_factor?_is_native=1&_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {
            **_CLERK_HEADERS,
            "Authorization": client_token,
            "x-mobile": "1",
        }
        data = {"strategy": "email_code", "code": code}

        async with session.post(url, headers=headers, data=data, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                await resp.read()
                raise AuthenticationError(f"OTP verification failed: {resp.status}")

            # Updated client token from response header
            new_client_token = resp.headers.get("Authorization", client_token)
            body = await resp.json()

            # Extract session ID from active sessions
            sessions = body.get("client", {}).get("sessions", [])
            session_id = None
            session_jwt = None
            if sessions:
                session_id = sessions[0].get("id")
                # Try to get JWT directly from response (avoids /tokens call)
                last_token = sessions[0].get("last_active_token", {})
                if isinstance(last_token, dict):
                    session_jwt = last_token.get("jwt")

            _LOGGER.debug(
                "attempt_first_factor: session_id=%s token_rotated=%s jwt_in_response=%s",
                _mask_secret(session_id),
                new_client_token != client_token,
                bool(session_jwt),
            )

            return {
                "session_id": session_id,
                "client_token": new_client_token,
                "session_jwt": session_jwt,
                "user_data": sessions[0].get("user", {}) if sessions else {},
            }


class AuthenticationError(Exception):
    """Raised when Clerk rejects the session. The user has to sign in again."""


class AuthTemporaryError(Exception):
    """Raised when Clerk cannot answer right now. The session is still good."""
