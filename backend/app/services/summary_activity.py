"""Summary generation activity tracking utilities.

This module provides:
  - Best-effort event logging whenever a summary completes.
  - Aggregation helpers for dashboard "Analysis Activity" (last N days) and all-time totals.

Events are stored in Supabase (table: `filing_summary_events`) when configured, and fall back
to a local on-disk cache when Supabase is unavailable.
"""

from __future__ import annotations

import logging
import os
from uuid import UUID
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.models.database import get_supabase_client
from app.services import local_cache
from app.utils.supabase_errors import is_supabase_table_missing_error

logger = logging.getLogger(__name__)

SUMMARY_ACTIVITY_DAYS = 8
SUMMARY_EVENTS_TABLE = "filing_summary_events"


def _supabase_configured(settings) -> bool:
    """Return True when Supabase keys are present and not placeholders."""
    key = (getattr(settings, "supabase_service_role_key", "") or "").strip()
    url = (getattr(settings, "supabase_url", "") or "").strip()
    if not key or not url:
        return False
    if key.lower().startswith("your_"):
        return False
    return True


def _resolve_client_timezone(tz_offset_minutes: Optional[int]) -> timezone:
    """
    Resolve a client timezone from a JS `Date#getTimezoneOffset()` style value.

    JS returns the offset in minutes as: UTC - local. Example:
      - Copenhagen (UTC+1) => -60
      - New York (UTC-5)   => 300
    """
    if tz_offset_minutes is None:
        return timezone.utc

    try:
        offset = int(tz_offset_minutes)
    except (TypeError, ValueError):
        return timezone.utc

    if offset < -14 * 60 or offset > 14 * 60:
        return timezone.utc

    return timezone(timedelta(minutes=-offset))


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _is_uuid_like(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    try:
        UUID(candidate)
        return True
    except Exception:
        return False


def _is_valid_summary_id(value: Any) -> bool:
    """
    We treat summary ids as valid when they are UUIDs, or when they are of the form
    "<uuid>:<suffix>" (persona summaries).
    """
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not raw:
        return False
    base = raw.split(":", 1)[0]
    return _is_uuid_like(base)


def _load_local_events() -> List[Dict[str, Any]]:
    """Load local summary events from disk so multi-process writers are included."""
    try:
        events = local_cache.load_summary_events_cache() or []
    except Exception:  # noqa: BLE001 - best-effort fallback
        events = local_cache.summary_events_cache or []

    # Guard against placeholder/test data polluting the on-disk cache (e.g. from running pytest).
    # Real summary ids are UUIDs (or UUID:persona_id). We keep only those for dashboard analytics.
    filtered: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_valid_summary_id(event.get("filing_id")):
            continue
        if _parse_datetime(event.get("created_at")) is None:
            continue
        filtered.append(event)

    # Optional: prune the cache on disk so the file doesn't grow with invalid entries.
    if (
        os.getenv("FINANCESUM_PRUNE_SUMMARY_EVENTS", "true").lower() in {"1", "true", "yes"}
        and len(filtered) < len(events)
    ):
        try:
            # Persist pruned events via the existing lock/write helpers.
            local_cache.summary_events_cache = filtered
            local_cache.save_summary_events_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Unable to prune local summary events cache: %s", exc)

    return filtered


def record_summary_generated_event(
    *,
    summary_id: str,
    company_id: Optional[str] = None,
    user_id: Optional[str] = None,
    kind: Optional[str] = None,
    cached: bool = False,
    source: Optional[str] = None,
    supabase_client=None,
) -> None:
    """Best-effort event logging for summary/analysis activity tracking."""
    payload: Dict[str, Any] = {
        "filing_id": str(summary_id),
        "company_id": str(company_id) if company_id else None,
        "mode": kind,
        "cached": bool(cached),
        "source": source,
    }
    if user_id:
        payload["user_id"] = str(user_id)

    if supabase_client is None:
        settings = get_settings()
        if _supabase_configured(settings):
            try:
                supabase_client = get_supabase_client()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Unable to create Supabase client for summary events: %s", exc)

    if supabase_client is not None:
        try:
            supabase_client.table(SUMMARY_EVENTS_TABLE).insert(payload).execute()
            return
        except Exception as exc:  # noqa: BLE001
            if not is_supabase_table_missing_error(exc):
                logger.debug("Unable to persist %s to Supabase: %s", SUMMARY_EVENTS_TABLE, exc)

    try:
        local_cache.append_summary_event({**payload, "created_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to persist local summary events cache: %s", exc)


def _looks_like_missing_user_id_column(error: Exception) -> bool:
    try:
        message = str(error)
    except Exception:  # noqa: BLE001
        return False
    lowered = message.lower()
    return "user_id" in lowered and "column" in lowered and "does not exist" in lowered


def count_user_summary_events(
    *,
    user_id: str,
    start: datetime,
    end: datetime,
    supabase_client=None,
) -> int:
    """Return summary generation events for a user within [start, end)."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    if supabase_client is None:
        settings = get_settings()
        if _supabase_configured(settings):
            try:
                supabase_client = get_supabase_client()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Unable to create Supabase client for usage counts: %s", exc)
                supabase_client = None

    if supabase_client is not None:
        try:
            events: List[Dict[str, Any]] = []
            page_size = 1000
            offset = 0
            while True:
                page = (
                    supabase_client.table(SUMMARY_EVENTS_TABLE)
                    .select("user_id, created_at")
                    .eq("user_id", user_id)
                    .gte("created_at", start.isoformat())
                    .lt("created_at", end.isoformat())
                    .order("created_at", desc=False)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                rows = page.data or []
                events.extend(rows)
                if len(rows) < page_size:
                    break
                offset += page_size

            supabase_count = sum(1 for row in events if str(row.get("user_id") or "") == user_id)
            local_events = _load_local_events()
            local_count = 0
            for event in local_events:
                if str(event.get("user_id") or "") != user_id:
                    continue
                created_at = _parse_datetime(event.get("created_at"))
                if not created_at:
                    continue
                if start <= created_at < end:
                    local_count += 1
            return supabase_count + local_count
        except Exception as exc:  # noqa: BLE001
            if is_supabase_table_missing_error(exc) or _looks_like_missing_user_id_column(exc):
                supabase_client = None
            else:
                raise

    events = _load_local_events()
    count = 0
    for event in events:
        if str(event.get("user_id") or "") != user_id:
            continue
        created_at = _parse_datetime(event.get("created_at"))
        if not created_at:
            continue
        if start <= created_at < end:
            count += 1
    return count


def build_activity_buckets(
    events: List[Dict[str, Any]],
    *,
    tz_offset_minutes: Optional[int] = None,
    days: int = SUMMARY_ACTIVITY_DAYS,
) -> List[Dict[str, Any]]:
    """Build last N days activity buckets inclusive, oldest->newest."""
    if days <= 0:
        return []

    client_tz = _resolve_client_timezone(tz_offset_minutes)
    today = datetime.now(client_tz).date()
    start = today - timedelta(days=days - 1)

    counts: Dict[date, int] = {}
    for event in events:
        dt = _parse_datetime(event.get("created_at"))
        if not dt:
            continue
        d = dt.astimezone(client_tz).date()
        if d < start or d > today:
            continue
        counts[d] = counts.get(d, 0) + 1

    return [{"date": (start + timedelta(days=i)).isoformat(), "count": counts.get(start + timedelta(days=i), 0)} for i in range(days)]


def get_summary_generation_metrics(
    *,
    tz_offset_minutes: Optional[int] = None,
    days: int = SUMMARY_ACTIVITY_DAYS,
    supabase_client=None,
    user_id: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Returns:
      (total_summaries_all_time, activity_last_N_days)
    activity_last_N_days is oldest->newest list of { date: 'YYYY-MM-DD', count: int }.
    """
    local_events = _load_local_events()
    if user_id:
        local_events = [
            event for event in local_events if str(event.get("user_id") or "") == user_id
        ]

    if supabase_client is None:
        settings = get_settings()
        if _supabase_configured(settings):
            try:
                supabase_client = get_supabase_client()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Unable to create Supabase client for metrics: %s", exc)

    if supabase_client is None:
        total = len(local_events)
        return total, build_activity_buckets(local_events, tz_offset_minutes=tz_offset_minutes, days=days)

    try:
        count_query = supabase_client.table(SUMMARY_EVENTS_TABLE).select("id", count="exact").limit(1)
        if user_id:
            count_query = count_query.eq("user_id", user_id)
        count_resp = count_query.execute()
        supabase_total = int(getattr(count_resp, "count", 0) or 0)

        client_tz = _resolve_client_timezone(tz_offset_minutes)
        today = datetime.now(client_tz).date()
        start_date = today - timedelta(days=days - 1)
        start_dt_local = datetime.combine(start_date, datetime.min.time(), tzinfo=client_tz)
        start_dt_utc = start_dt_local.astimezone(timezone.utc)

        events: List[Dict[str, Any]] = []
        page_size = 1000
        offset = 0
        while True:
            events_query = (
                supabase_client.table(SUMMARY_EVENTS_TABLE)
                .select("created_at")
                .gte("created_at", start_dt_utc.isoformat())
                .order("created_at", desc=False)
                .range(offset, offset + page_size - 1)
            )
            if user_id:
                events_query = events_query.eq("user_id", user_id)
            page = events_query.execute()
            rows = page.data or []
            events.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc) or (user_id and _looks_like_missing_user_id_column(exc)):
            total = len(local_events)
            return total, build_activity_buckets(local_events, tz_offset_minutes=tz_offset_minutes, days=days)
        raise

    buckets = build_activity_buckets([*events, *local_events], tz_offset_minutes=tz_offset_minutes, days=days)
    total = supabase_total + len(local_events)
    return total, buckets
