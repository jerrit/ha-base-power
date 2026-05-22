"""Clerk authentication handler for Base Power."""

from __future__ import annotations

import time
import logging
from typing import Any

import aiohttp

from .const import CLERK_DOMAIN, CLERK_JS_VERSION

_LOGGER = logging.getLogger(__name__)

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
    ) -> None:
        """Initialize auth handler."""
        self._session = session
        self._client_token = client_token
        self._session_id = session_id
        self._jwt: str | None = None
        self._jwt_expires_at: float = 0

    @property
    def jwt(self) -> str | None:
        """Get current JWT."""
        return self._jwt

    @property
    def is_token_valid(self) -> bool:
        """Check if current JWT is still valid."""
        return self._jwt is not None and time.time() < self._jwt_expires_at

    async def async_refresh_token(self) -> str:
        """Refresh the JWT from Clerk."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sessions/"
            f"{self._session_id}/tokens?_clerk_js_version={CLERK_JS_VERSION}"
        )
        headers = {**_CLERK_HEADERS, "Authorization": self._client_token}
        cookies = {"__client": self._client_token}

        # Try with Authorization header first, then cookie
        async with self._session.post(url, headers=headers, cookies=cookies) as resp:
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.error("Failed to refresh JWT: %s %s", resp.status, text)
                raise AuthenticationError(
                    f"Clerk token refresh failed: {resp.status}"
                )
            # Update client token if Clerk rotates it
            new_token = resp.headers.get("Authorization")
            if new_token:
                self._client_token = new_token
            data = await resp.json()
            self._jwt = data["jwt"]
            self._jwt_expires_at = time.time() + JWT_LIFETIME - JWT_REFRESH_BUFFER
            return self._jwt

    async def async_ensure_valid_token(self) -> str:
        """Ensure we have a valid JWT, refreshing if needed."""
        if not self.is_token_valid:
            return await self.async_refresh_token()
        return self._jwt

    @staticmethod
    async def async_initiate_sign_in(
        session: aiohttp.ClientSession,
        email: str,
        publishable_key: str,
    ) -> dict[str, Any]:
        """Step 1: Initiate Clerk sign-in with email."""
        url = f"{CLERK_DOMAIN}/v1/client/sign_ins?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {
            **_CLERK_HEADERS,
            "Authorization": f"Bearer {publishable_key}",
        }
        data = {"identifier": email}

        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.error("Sign-in initiation failed: %s", text)
                raise AuthenticationError(f"Sign-in initiation failed: {resp.status}")

            # Client token from Authorization response header
            client_token = resp.headers.get("Authorization", "")
            body = await resp.json()

            # Fallback: extract token from response body if header is empty
            if not client_token:
                client_token = body.get("client", {}).get("id", "")
                _LOGGER.debug(
                    "Authorization header empty, using client id: %s",
                    client_token[:20] if client_token else "none",
                )

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
                "Sign-in initiated: id=%s email_id=%s token_len=%d",
                sign_in_id, email_id, len(client_token) if client_token else 0,
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
    ) -> bool:
        """Step 2: Send OTP code to email."""
        url = (
            f"{CLERK_DOMAIN}/v1/client/sign_ins/{sign_in_id}/"
            f"prepare_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
        )
        cookies = {"__client": client_token}
        data = {"strategy": "email_code", "email_address_id": email_id}

        async with session.post(url, headers=_CLERK_HEADERS, data=data, cookies=cookies) as resp:
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.error(
                    "prepare_first_factor failed: status=%s body=%s",
                    resp.status, text,
                )
                raise AuthenticationError(
                    f"Failed to send verification email: {resp.status}"
                )
            return True

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
            f"attempt_first_factor?_clerk_js_version={CLERK_JS_VERSION}"
        )
        cookies = {"__client": client_token}
        data = {"strategy": "email_code", "code": code}

        async with session.post(url, headers=_CLERK_HEADERS, data=data, cookies=cookies) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise AuthenticationError(f"OTP verification failed: {text}")

            # Updated client token from response header
            new_client_token = resp.headers.get("Authorization", client_token)
            body = await resp.json()

            # Extract session ID from active sessions
            sessions = body.get("client", {}).get("sessions", [])
            session_id = None
            if sessions:
                session_id = sessions[0].get("id")

            # Log full response structure for discovery debugging
            _LOGGER.debug(
                "attempt_first_factor response keys: %s",
                list(body.keys()),
            )
            if sessions:
                _LOGGER.debug(
                    "Session keys: %s", list(sessions[0].keys())
                )
                user = sessions[0].get("user", {})
                _LOGGER.debug("User keys: %s", list(user.keys()))
                _LOGGER.debug(
                    "User public_metadata: %s",
                    user.get("public_metadata"),
                )
                _LOGGER.debug(
                    "User unsafe_metadata: %s",
                    user.get("unsafe_metadata"),
                )

            return {
                "session_id": session_id,
                "client_token": new_client_token,
                "user_data": sessions[0].get("user", {}) if sessions else {},
            }


class AuthenticationError(Exception):
    """Raised when authentication fails."""
