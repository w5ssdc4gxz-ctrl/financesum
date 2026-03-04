"""Best-effort PostHog event capture helpers."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from app.config import ensure_env_loaded

logger = logging.getLogger(__name__)

DEFAULT_POSTHOG_HOST = "https://eu.i.posthog.com"
DEFAULT_TIMEOUT_SECONDS = 0.8
MAX_STRING_PROPERTY_LENGTH = 12_000
MAX_NESTED_SANITIZE_DEPTH = 4


def _is_enabled() -> bool:
    raw = (os.getenv("POSTHOG_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _project_key() -> str:
    return (os.getenv("POSTHOG_PROJECT_API_KEY") or "").strip()


def _host() -> str:
    host = (os.getenv("POSTHOG_HOST") or DEFAULT_POSTHOG_HOST).strip()
    if not host:
        host = DEFAULT_POSTHOG_HOST
    return host.rstrip("/")


def _timeout_seconds() -> float:
    raw = (os.getenv("POSTHOG_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """Return PostHog-safe primitives while preserving list/dict shapes."""
    if value is None:
        return None

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, str):
        return value[:MAX_STRING_PROPERTY_LENGTH]

    if depth >= MAX_NESTED_SANITIZE_DEPTH:
        return str(value)[:MAX_STRING_PROPERTY_LENGTH]

    if isinstance(value, list):
        out = []
        for item in value:
            normalized = _sanitize_value(item, depth=depth + 1)
            if normalized is None:
                continue
            out.append(normalized)
        return out

    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            normalized = _sanitize_value(item, depth=depth + 1)
            if normalized is None:
                continue
            out[key_text] = normalized
        return out

    return str(value)[:MAX_STRING_PROPERTY_LENGTH]


def _sanitize_properties(properties: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not properties:
        return {}

    sanitized: Dict[str, Any] = {}
    for key, value in properties.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        normalized = _sanitize_value(value)
        if normalized is None:
            continue
        sanitized[key_text] = normalized
    return sanitized


def capture_posthog_event(
    *,
    event: str,
    distinct_id: str,
    properties: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
) -> None:
    """Capture an event in PostHog using ingestion API.

    Never raises; any failure is logged at debug level.
    """
    ensure_env_loaded()

    if not _is_enabled():
        return

    api_key = _project_key()
    if not api_key:
        return

    event_name = str(event or "").strip()
    if not event_name:
        return

    subject = str(distinct_id or "").strip()
    if not subject:
        return

    payload: Dict[str, Any] = {
        "api_key": api_key,
        "event": event_name,
        "distinct_id": subject,
        "properties": _sanitize_properties(properties),
    }
    if timestamp:
        payload["timestamp"] = timestamp

    endpoint = f"{_host()}/capture/"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.post(endpoint, json=payload)
            if response.status_code >= 400:
                logger.debug(
                    "PostHog capture rejected (%s): %s",
                    response.status_code,
                    (response.text or "")[:500],
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("PostHog capture failed: %s", exc)
