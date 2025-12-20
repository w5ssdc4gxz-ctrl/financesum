"""Authentication helpers for API routes.

The frontend uses Supabase Auth; the backend verifies bearer tokens against the
Supabase Auth API.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


_bearer_scheme = HTTPBearer(auto_error=False)


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
        raise HTTPException(status_code=401, detail="Missing Authorization bearer token")

    token = credentials.credentials
    settings = get_settings()

    supabase_url = (settings.supabase_url or "").rstrip("/")
    if not supabase_url:
        raise HTTPException(status_code=500, detail="Supabase is not configured on the backend")

    service_role_key = (settings.supabase_service_role_key or "").strip()
    anon_key = (settings.supabase_anon_key or "").strip()
    apikey_candidates = [key for key in (service_role_key, anon_key) if key]
    if not apikey_candidates:
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
                    return CurrentUser(id=str(user_id), email=data.get("email"))
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
