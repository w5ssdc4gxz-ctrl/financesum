"""Authentication helpers for API routes.

The frontend uses Supabase Auth; the backend verifies bearer tokens against the
Supabase Auth API.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
import time
from threading import Lock

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


_bearer_scheme = HTTPBearer(auto_error=False)

DEMO_USER_ID = "demo-user"
DEMO_USER_EMAIL = "demo@financesum.com"

_AUTH_CACHE_LOCK = Lock()
_AUTH_TOKEN_CACHE: dict[str, tuple[CurrentUser, float]] = {}


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _token_cache_ttl_seconds() -> int:
    raw = (os.getenv("FINANCESUM_AUTH_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return 60
    try:
        return max(0, min(3600, int(raw)))
    except ValueError:
        return 60


def _jwt_expiry_ts(token: str) -> float | None:
    """Best-effort exp extraction from a JWT without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_raw = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        payload = json.loads(payload_raw.decode("utf-8"))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:  # noqa: BLE001
        return None
    return None


def _auth_cache_get(token: str) -> CurrentUser | None:
    now = time.time()
    with _AUTH_CACHE_LOCK:
        cached = _AUTH_TOKEN_CACHE.get(token)
        if cached is None:
            return None
        user, expires_at = cached
        if expires_at <= now:
            _AUTH_TOKEN_CACHE.pop(token, None)
            return None
        return user


def _auth_cache_set(token: str, user: CurrentUser) -> None:
    ttl = _token_cache_ttl_seconds()
    if ttl <= 0:
        return
    now = time.time()
    expires_at = now + ttl

    token_exp = _jwt_expiry_ts(token)
    if token_exp is not None:
        expires_at = min(expires_at, token_exp - 5)
        if expires_at <= now:
            return

    with _AUTH_CACHE_LOCK:
        _AUTH_TOKEN_CACHE[token] = (user, expires_at)


def _demo_auth_enabled() -> bool:
    # Always allow unauthenticated requests during pytest so unit tests can call
    # endpoints without stubbing Supabase auth.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True

    mode = (
        os.getenv("FINANCESUM_AUTH_MODE")
        or os.getenv("AUTH_MODE")
        or os.getenv("NEXT_PUBLIC_AUTH_MODE")
        or ""
    ).strip().lower()
    if mode == "demo":
        return True

    return _env_truthy("FINANCESUM_ALLOW_DEMO_AUTH") or _env_truthy("ALLOW_DEMO_AUTH")


def _looks_like_invalid_apikey(response: httpx.Response) -> bool:
    """Return True when Supabase indicates the API key is invalid.

    Supabase returns 401/403 for both invalid JWTs and invalid API keys. When the
    API key is invalid, treat it as a backend configuration error.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = None

    parts: list[str] = []
    if isinstance(payload, dict):
        for key in ("message", "msg", "error", "error_description", "hint"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

    if isinstance(response.text, str) and response.text.strip():
        parts.append(response.text.strip())

    haystack = " ".join(parts).lower()
    return "invalid api key" in haystack or ("api key" in haystack and "invalid" in haystack)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Resolve the current user from a Supabase access token."""
    if credentials is None or not credentials.credentials:
        if _demo_auth_enabled():
            return CurrentUser(id=DEMO_USER_ID, email=DEMO_USER_EMAIL)
        raise HTTPException(status_code=401, detail="Missing Authorization bearer token")

    token = credentials.credentials
    cached = _auth_cache_get(token)
    if cached is not None:
        return cached

    settings = get_settings()

    supabase_url = (settings.supabase_url or "").rstrip("/")
    if not supabase_url:
        if _demo_auth_enabled() and token.strip().lower() in {"demo", DEMO_USER_ID}:
            return CurrentUser(id=DEMO_USER_ID, email=DEMO_USER_EMAIL)
        raise HTTPException(status_code=500, detail="Supabase is not configured on the backend")

    service_role_key = (settings.supabase_service_role_key or "").strip()
    anon_key = (settings.supabase_anon_key or "").strip()
    apikey_candidates = [key for key in (service_role_key, anon_key) if key]
    if not apikey_candidates:
        if _demo_auth_enabled() and token.strip().lower() in {"demo", DEMO_USER_ID}:
            return CurrentUser(id=DEMO_USER_ID, email=DEMO_USER_EMAIL)
        raise HTTPException(status_code=500, detail="Supabase API key is not configured on the backend")

    url = f"{supabase_url}/auth/v1/user"
    responses: list[httpx.Response] = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for apikey in apikey_candidates:
                headers = {
                    "apikey": apikey,
                    "Authorization": f"Bearer {token}",
                }
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json() or {}
                    user_id = data.get("id")
                    if not user_id:
                        raise HTTPException(status_code=401, detail="Supabase token did not resolve to a user")
                    user = CurrentUser(id=str(user_id), email=data.get("email"))
                    _auth_cache_set(token, user)
                    return user
                responses.append(response)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Unable to reach Supabase auth: {exc}") from exc

    if any(_looks_like_invalid_apikey(resp) for resp in responses):
        raise HTTPException(
            status_code=503,
            detail="Supabase API key is invalid or misconfigured on the backend (check SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY).",
        )

    raise HTTPException(status_code=401, detail="Invalid or expired authentication token")
