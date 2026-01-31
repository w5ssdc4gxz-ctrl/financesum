"""In-memory fallback caches used when external services are unavailable."""

from __future__ import annotations

from contextlib import contextmanager
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

BACKEND_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BACKEND_DIR / "data" / "local_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
COMPANIES_CACHE_FILE = CACHE_DIR / "companies.json"
SUMMARY_EVENTS_CACHE_FILE = CACHE_DIR / "summary_events.json"
SUMMARY_EVENTS_LOCK_FILE = CACHE_DIR / "summary_events.lock"
SPOTLIGHT_KPIS_CACHE_FILE = CACHE_DIR / "spotlight_kpis.json"
SPOTLIGHT_KPIS_LOCK_FILE = CACHE_DIR / "spotlight_kpis.lock"


@contextmanager
def _exclusive_lock(path: Path):
    if fcntl is None:
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError:
            yield
            return

        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    try:
        tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, default=_json_default, indent=2))
        tmp_path.replace(path)
    except OSError as exc:  # pragma: no cover - best-effort persistence
        print(f"Unable to persist local cache {path}: {exc}")


# Stores serialized company dictionaries keyed by company ID (as string)
fallback_companies: Dict[str, Dict[str, Any]] = _load_json(COMPANIES_CACHE_FILE)

# Append-only list of summary generation events when Supabase is unavailable
summary_events_cache: List[Dict[str, Any]] = _load_json(SUMMARY_EVENTS_CACHE_FILE).get("events", [])

# Stores cached Spotlight KPI results keyed by filing ID.
_spotlight_payload = _load_json(SPOTLIGHT_KPIS_CACHE_FILE)
fallback_spotlight_kpis_by_id: Dict[str, Dict[str, Any]] = (
    _spotlight_payload.get("kpis", {}) if isinstance(_spotlight_payload, dict) else {}
)


def load_summary_events_cache() -> List[Dict[str, Any]]:
    """Reload summary generation events from disk (best-effort)."""
    global summary_events_cache
    with _exclusive_lock(SUMMARY_EVENTS_LOCK_FILE):
        payload = _load_json(SUMMARY_EVENTS_CACHE_FILE)
        events = payload.get("events", [])
        summary_events_cache = events if isinstance(events, list) else []
    return summary_events_cache


def append_summary_event(event: Dict[str, Any]) -> None:
    """Append a summary generation event to the on-disk cache (best-effort)."""
    global summary_events_cache
    with _exclusive_lock(SUMMARY_EVENTS_LOCK_FILE):
        payload = _load_json(SUMMARY_EVENTS_CACHE_FILE)
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []
        events.append(event)
        summary_events_cache = events
        _save_json(SUMMARY_EVENTS_CACHE_FILE, {"events": events})


def save_fallback_companies() -> None:
    """Persist current fallback companies to disk."""
    _save_json(COMPANIES_CACHE_FILE, fallback_companies)


def save_summary_events_cache() -> None:
    """Persist summary generation events cache to disk (best-effort)."""
    with _exclusive_lock(SUMMARY_EVENTS_LOCK_FILE):
        _save_json(SUMMARY_EVENTS_CACHE_FILE, {"events": summary_events_cache})


def load_spotlight_kpis_cache() -> Dict[str, Dict[str, Any]]:
    """Reload Spotlight KPI cache from disk (best-effort)."""
    global fallback_spotlight_kpis_by_id
    with _exclusive_lock(SPOTLIGHT_KPIS_LOCK_FILE):
        payload = _load_json(SPOTLIGHT_KPIS_CACHE_FILE)
        kpis = payload.get("kpis", {}) if isinstance(payload, dict) else {}
        fallback_spotlight_kpis_by_id = kpis if isinstance(kpis, dict) else {}
    return fallback_spotlight_kpis_by_id


def save_spotlight_kpis_cache() -> None:
    """Persist Spotlight KPI cache to disk (best-effort)."""
    with _exclusive_lock(SPOTLIGHT_KPIS_LOCK_FILE):
        _save_json(SPOTLIGHT_KPIS_CACHE_FILE, {"kpis": fallback_spotlight_kpis_by_id})


# Stores serialized filing dictionaries keyed by company ID (as string)
fallback_filings: Dict[str, List[Dict[str, Any]]] = {}

# Direct index of filings keyed by filing ID (as string)
fallback_filings_by_id: Dict[str, Dict[str, Any]] = {}

# Stores serialized financial statement dictionaries keyed by filing ID (as string)
fallback_financial_statements: Dict[str, Dict[str, Any]] = {}

# Stores serialized analysis dictionaries keyed by company ID (as string)
fallback_analyses: Dict[str, List[Dict[str, Any]]] = {}

# Direct index of analyses keyed by analysis ID (as string)
fallback_analysis_by_id: Dict[str, Dict[str, Any]] = {}

# Stores serialized task status dictionaries keyed by task ID (as string)
fallback_task_status: Dict[str, Dict[str, Any]] = {}

# Stores cached filing summaries keyed by filing ID (as string)
fallback_filing_summaries: Dict[str, str] = {}

# Stores real-time progress status keyed by filing ID (as string)
progress_cache: Dict[str, str] = {}

# Stores structured progress snapshots keyed by filing ID (as string)
summary_progress_cache: Dict[str, Dict[str, Any]] = {}
