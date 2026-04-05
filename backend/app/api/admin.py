"""Owner-only admin endpoints."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import CurrentUser, get_current_user
from app.services.ai_usage import (
    aggregate_ai_usage_by_request_id,
    load_ai_usage_events,
)

router = APIRouter()


def _owner_email() -> str:
    return (os.getenv("FINANCESUM_OWNER_EMAIL") or "").strip().lower()


def _require_owner(user: CurrentUser) -> None:
    owner = _owner_email()
    if not owner:
        raise HTTPException(
            status_code=503,
            detail="FINANCESUM_OWNER_EMAIL is not configured on the backend.",
        )
    if not user.email or (user.email or "").strip().lower() != owner:
        raise HTTPException(status_code=403, detail="Forbidden")


def _summary_budget_usd() -> float:
    raw = (os.getenv("OPENAI_COST_PER_SUMMARY_USD") or "").strip()
    try:
        return float(raw) if raw else 0.10
    except ValueError:
        return 0.10


@router.get("/ai-usage")
async def get_ai_usage(
    days: int = Query(default=7, ge=0, le=90),
    limit: int = Query(default=200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return aggregated AI usage grouped by request_id (owner only)."""
    _require_owner(user)

    budget_usd = _summary_budget_usd()
    events = load_ai_usage_events(days=int(days))
    requests = aggregate_ai_usage_by_request_id(events)[: int(limit)]

    total_calls = sum(int(r.get("call_count") or 0) for r in requests)
    total_cost = sum(float(r.get("total_cost_usd") or 0.0) for r in requests)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in requests)

    for req in requests:
        try:
            req_cost = float(req.get("total_cost_usd") or 0.0)
        except Exception:
            req_cost = 0.0
        req["over_budget"] = bool(budget_usd > 0 and req_cost > budget_usd)

    since_ts = datetime.now(timezone.utc).timestamp() - (int(days) * 86400)
    since_utc = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()

    return {
        "window": {"days": int(days), "since_utc": since_utc},
        "budget_usd": float(budget_usd),
        "totals": {
            "requests": len(requests),
            "calls": int(total_calls),
            "tokens": int(total_tokens),
            "cost_usd": round(float(total_cost), 6),
        },
        "requests": requests,
    }


@router.get("/gemini-usage")
async def get_gemini_usage_legacy(
    days: int = Query(default=7, ge=0, le=90),
    limit: int = Query(default=200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Legacy alias of /ai-usage."""
    return await get_ai_usage(days=days, limit=limit, user=user)
