"""Helpers for handling Supabase errors gracefully."""

from __future__ import annotations

from typing import Any, Dict, List


def is_supabase_table_missing_error(error: Exception) -> bool:
    """
    Return True when Supabase is unusable and callers should fall back.

    This typically surfaces as PostgREST error code PGRST205 with a message like
    "Could not find the table 'public.xyz' in the schema cache".
    We also guard against common phrasing like "relation ... does not exist".
    Additionally, treat obvious authentication/configuration failures (invalid API key,
    expired JWT, permission denied) as fallback-worthy so the app can run in local mode.
    """
    try:
        message = str(error)
    except Exception:  # pragma: no cover - extremely defensive
        return False

    if not message:
        return False

    lowered = message.lower()
    return (
        "could not find the table" in lowered
        or "pgrst205" in lowered
        or "does not exist" in lowered
        or "invalid api key" in lowered
        or "jwt expired" in lowered
        or "permission denied" in lowered
        or "not authorized" in lowered
        or "unauthorized" in lowered
        # Network / connectivity issues should fall back to local mode instead of hanging.
        or "timed out" in lowered
        or "timeout" in lowered
        or "connection refused" in lowered
        or "failed to establish a new connection" in lowered
        or "all connection attempts failed" in lowered
        or "name or service not known" in lowered
        or "temporary failure in name resolution" in lowered
        or "nodename nor servname provided" in lowered
        or "network is unreachable" in lowered
        or "connection error" in lowered
    )


def coerce_supabase_rows(response: Any) -> List[Dict[str, Any]]:
    """Return a list of rows from a Supabase response, raising on error payloads.

    Supabase client helpers typically return objects with a `.data` attribute. In
    misconfigured environments (invalid API key, network issues), some clients return
    a dict error payload in `.data` instead of raising. Treat those as fallback-worthy
    by raising with the message so callers can reuse `is_supabase_table_missing_error`.
    """
    data = getattr(response, "data", None)
    if data is None:
        return []

    if isinstance(data, dict):
        message = data.get("message") or data.get("error") or data.get("msg")
        if isinstance(message, str) and message.strip():
            raise RuntimeError(message.strip())
        raise RuntimeError("Supabase returned an error payload")

    if not isinstance(data, list):
        raise RuntimeError("Supabase returned a non-list payload")

    rows: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            rows.append(item)
        else:
            raise RuntimeError("Supabase returned a non-row item")
    return rows
