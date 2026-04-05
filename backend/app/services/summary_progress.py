"""Structured summary progress tracking for long-running filing summaries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.local_cache import progress_cache, summary_progress_cache


def _now_ts() -> float:
    return time.time()


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class SummaryProgressSnapshot:
    status: str
    percent: int
    percent_exact: float
    eta_seconds: Optional[int]
    started_at: str
    updated_at: str
    error: bool
    last_failure_code: Optional[str]
    last_error_message: Optional[str]
    last_error_details: Optional[Dict[str, Any]]


def _truncate_progress_error_details(value: Any, *, depth: int = 0) -> Any:
    """Keep error payloads compact and JSON-safe for in-memory progress snapshots."""
    if depth > 3:
        return "..."
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        normalized = " ".join(value.split()).strip()
        return normalized[:500] + ("..." if len(normalized) > 500 else "")
    if isinstance(value, list):
        return [
            _truncate_progress_error_details(item, depth=depth + 1)
            for item in value[:12]
        ]
    if isinstance(value, tuple):
        return [
            _truncate_progress_error_details(item, depth=depth + 1)
            for item in list(value)[:12]
        ]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 20:
                out["truncated"] = True
                break
            out[str(key)] = _truncate_progress_error_details(item, depth=depth + 1)
        return out
    try:
        return _truncate_progress_error_details(str(value), depth=depth + 1)
    except Exception:  # noqa: BLE001
        return "<unserializable>"


def start_summary_progress(filing_id: str, *, expected_total_seconds: int) -> None:
    now = _now_ts()
    expected_total_seconds = max(30, int(expected_total_seconds))
    summary_progress_cache[str(filing_id)] = {
        "status": "Initializing AI Agent...",
        "started_ts": now,
        "updated_ts": now,
        "stage_set_ts": now,
        "expected_total_seconds": expected_total_seconds,
        "stage_percent": 0,
        "last_percent": 0.0,
        "done": False,
        "error": False,
        "last_failure_code": None,
        "last_error_message": None,
        "last_error_details": None,
    }
    progress_cache[str(filing_id)] = "Initializing AI Agent..."


def set_summary_progress(
    filing_id: str,
    *,
    status: Optional[str] = None,
    stage_percent: Optional[int] = None,
    done: Optional[bool] = None,
    error: Optional[bool] = None,
    last_failure_code: Optional[str] = None,
    last_error_message: Optional[str] = None,
    last_error_details: Optional[Any] = None,
    clear_error_details: bool = False,
) -> None:
    key = str(filing_id)
    entry = summary_progress_cache.get(key)
    now = _now_ts()

    if entry is None:
        summary_progress_cache[key] = {
            "status": status or progress_cache.get(key, "Initializing..."),
            "started_ts": now,
            "updated_ts": now,
            "stage_set_ts": now,
            "expected_total_seconds": 240,
            "stage_percent": int(stage_percent or 0),
            "last_percent": float(stage_percent or 0),
            "done": bool(done) if done is not None else False,
            "error": bool(error) if error is not None else False,
            "last_failure_code": None,
            "last_error_message": None,
            "last_error_details": None,
        }
        if status:
            progress_cache[key] = status
        if last_failure_code is not None:
            summary_progress_cache[key]["last_failure_code"] = str(last_failure_code)
        if last_error_message is not None:
            summary_progress_cache[key]["last_error_message"] = str(last_error_message)
        if last_error_details is not None:
            summary_progress_cache[key]["last_error_details"] = (
                _truncate_progress_error_details(last_error_details)
            )
        return

    if status is not None:
        entry["status"] = status
        progress_cache[key] = status

    if stage_percent is not None:
        next_stage = max(0, min(99, int(stage_percent)))
        current_stage = int(entry.get("stage_percent") or 0)
        if next_stage > current_stage:
            # New higher stage - record when it was set for interpolation
            entry["stage_percent"] = next_stage
            entry["stage_set_ts"] = now
            # Capture current displayed percent as base for interpolation
            entry["last_percent"] = float(entry.get("last_percent") or 0)

    if done is not None:
        entry["done"] = bool(done)
    if error is not None:
        entry["error"] = bool(error)
    if clear_error_details:
        entry["last_failure_code"] = None
        entry["last_error_message"] = None
        entry["last_error_details"] = None
    if last_failure_code is not None:
        entry["last_failure_code"] = str(last_failure_code)
    if last_error_message is not None:
        entry["last_error_message"] = str(last_error_message)
    if last_error_details is not None:
        entry["last_error_details"] = _truncate_progress_error_details(last_error_details)

    if bool(entry.get("done")):
        entry["error"] = False
        entry["last_failure_code"] = None
        entry["last_error_message"] = None
        entry["last_error_details"] = None

    entry["updated_ts"] = now


def complete_summary_progress(filing_id: str) -> None:
    set_summary_progress(
        filing_id,
        status="Complete",
        stage_percent=100,
        done=True,
        error=False,
        clear_error_details=True,
    )


def get_summary_progress_snapshot(filing_id: str) -> SummaryProgressSnapshot:
    """Return progress with smooth interpolation towards the current stage.

    Instead of jumping directly to stage_percent, we interpolate from the
    last reported percent towards the target stage_percent over time.
    This provides smoother progress updates when polled by the frontend.
    """
    key = str(filing_id)
    entry: Dict[str, Any] = summary_progress_cache.get(key) or {}

    status = str(entry.get("status") or progress_cache.get(key, "Initializing..."))
    started_ts = float(entry.get("started_ts") or _now_ts())
    updated_ts = float(entry.get("updated_ts") or started_ts)
    done = bool(entry.get("done"))
    error = bool(entry.get("error"))
    last_failure_code = (
        str(entry.get("last_failure_code"))
        if entry.get("last_failure_code") not in (None, "")
        else None
    )
    last_error_message = (
        str(entry.get("last_error_message"))
        if entry.get("last_error_message") not in (None, "")
        else None
    )
    raw_error_details = entry.get("last_error_details")
    last_error_details = (
        raw_error_details if isinstance(raw_error_details, dict) else None
    )

    if done:
        percent = 100
        percent_exact = 100.0
        eta_seconds: Optional[int] = 0
    else:
        # Get the target stage percent and the last reported percent
        stage_percent = int(entry.get("stage_percent") or 0)
        last_percent = float(entry.get("last_percent") or 0)

        # Calculate time-based interpolation towards the stage target
        now = _now_ts()
        stage_set_ts = float(entry.get("stage_set_ts") or updated_ts)
        time_since_stage_set = now - stage_set_ts

        # Interpolate: reach the stage_percent within ~2 seconds of it being set
        # This gives smooth progress even with fast stage transitions
        interpolation_duration = 2.0  # seconds to reach target
        if stage_percent > last_percent:
            # Calculate how far we should be towards the target
            progress_fraction = min(1.0, time_since_stage_set / interpolation_duration)
            delta = stage_percent - last_percent
            interpolated = last_percent + (delta * progress_fraction)
            percent_exact = min(float(stage_percent), interpolated)
        else:
            percent_exact = max(last_percent, float(stage_percent))

        # Update last_percent for next poll (only if we've made progress)
        if percent_exact > last_percent:
            entry["last_percent"] = percent_exact

        percent = int(percent_exact)

        # Estimate ETA based on progress rate
        if percent_exact > 0 and percent_exact < 100:
            elapsed = now - started_ts
            rate = percent_exact / elapsed  # percent per second
            remaining_percent = 100 - percent_exact
            if rate > 0:
                eta_seconds = int(remaining_percent / rate)
            else:
                eta_seconds = None
        else:
            eta_seconds = None

    return SummaryProgressSnapshot(
        status=status,
        percent=int(percent),
        percent_exact=float(percent_exact),
        eta_seconds=eta_seconds,
        started_at=_iso_from_ts(started_ts),
        updated_at=_iso_from_ts(updated_ts),
        error=error,
        last_failure_code=last_failure_code,
        last_error_message=last_error_message,
        last_error_details=last_error_details,
    )
