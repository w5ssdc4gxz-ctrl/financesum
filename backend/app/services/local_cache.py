"""In-memory fallback caches used when external services are unavailable."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

BACKEND_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BACKEND_DIR / "data" / "local_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
COMPANIES_CACHE_FILE = CACHE_DIR / "companies.json"


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
        path.write_text(json.dumps(payload, default=_json_default, indent=2))
    except OSError as exc:  # pragma: no cover - best-effort persistence
        print(f"Unable to persist local cache {path}: {exc}")


# Stores serialized company dictionaries keyed by company ID (as string)
fallback_companies: Dict[str, Dict[str, Any]] = _load_json(COMPANIES_CACHE_FILE)


def save_fallback_companies() -> None:
    """Persist current fallback companies to disk."""
    _save_json(COMPANIES_CACHE_FILE, fallback_companies)


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

