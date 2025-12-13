"""In-memory queue for tracking companies needing country hydration."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
import threading


@dataclass
class PendingHydration:
    """Represents a company awaiting country hydration."""
    company_id: str
    ticker: str
    exchange: Optional[str]
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0
    last_attempt: Optional[datetime] = None


# Thread-safe pending hydration queue
_pending_lock = threading.Lock()
_pending_hydrations: Dict[str, PendingHydration] = {}


def queue_for_hydration(company_id: str, ticker: str, exchange: Optional[str] = None) -> None:
    """
    Add a company to the pending hydration queue.

    Args:
        company_id: UUID of the company
        ticker: Stock ticker symbol
        exchange: Exchange code (optional)
    """
    with _pending_lock:
        if company_id not in _pending_hydrations:
            _pending_hydrations[company_id] = PendingHydration(
                company_id=company_id,
                ticker=ticker,
                exchange=exchange,
            )


def get_pending_hydrations(limit: int = 10) -> List[PendingHydration]:
    """
    Get oldest pending hydrations up to limit.

    Args:
        limit: Maximum number of items to return

    Returns:
        List of PendingHydration objects, sorted by added_at (oldest first)
    """
    with _pending_lock:
        sorted_items = sorted(
            _pending_hydrations.values(),
            key=lambda x: x.added_at
        )
        return sorted_items[:limit]


def mark_hydrated(company_id: str) -> None:
    """
    Remove company from pending queue after successful hydration.

    Args:
        company_id: UUID of the company to remove
    """
    with _pending_lock:
        _pending_hydrations.pop(company_id, None)


def mark_attempt_failed(company_id: str) -> None:
    """
    Increment retry count for a failed hydration attempt.

    Args:
        company_id: UUID of the company
    """
    with _pending_lock:
        if company_id in _pending_hydrations:
            _pending_hydrations[company_id].retry_count += 1
            _pending_hydrations[company_id].last_attempt = datetime.now(timezone.utc)


def remove_expired(max_retries: int = 5) -> Set[str]:
    """
    Remove entries that have exceeded max retries.

    Args:
        max_retries: Maximum retry attempts before removal

    Returns:
        Set of removed company IDs
    """
    with _pending_lock:
        to_remove = {
            cid for cid, p in _pending_hydrations.items()
            if p.retry_count >= max_retries
        }
        for cid in to_remove:
            del _pending_hydrations[cid]
        return to_remove


def get_queue_size() -> int:
    """Return the current size of the pending hydration queue."""
    with _pending_lock:
        return len(_pending_hydrations)


def clear_queue() -> None:
    """Clear all pending hydrations (useful for testing)."""
    with _pending_lock:
        _pending_hydrations.clear()
