"""In-memory fallback caches used when external services are unavailable."""

from __future__ import annotations

from typing import Any, Dict, List

# Stores serialized company dictionaries keyed by company ID (as string)
fallback_companies: Dict[str, Dict[str, Any]] = {}

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


