"""Dashboard overview endpoints."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.api.companies import _supabase_configured
from app.config import get_settings
from app.models.database import get_supabase_client
from app.services.local_cache import (
    fallback_analyses,
    fallback_companies,
)
from app.utils.supabase_errors import is_supabase_table_missing_error

router = APIRouter()

MAX_HISTORY_RESULTS = 50


@router.get("/overview")
async def get_dashboard_overview() -> Dict[str, Any]:
    """
    Return aggregated dashboard metrics and the most recent analyses.

    The endpoint automatically falls back to local cached data when Supabase
    credentials are not configured or the underlying tables are unavailable.
    """
    settings = get_settings()

    if not _supabase_configured(settings):
        return _build_fallback_overview()

    try:
        return _build_supabase_overview()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            return _build_fallback_overview()
        raise HTTPException(status_code=500, detail=f"Error loading dashboard overview: {exc}") from exc


def _build_supabase_overview() -> Dict[str, Any]:
    supabase = get_supabase_client()

    response = supabase.table("analyses")\
        .select("*", count="exact")\
        .order("analysis_date", desc=True)\
        .limit(MAX_HISTORY_RESULTS)\
        .execute()

    analyses: List[Dict[str, Any]] = response.data or []
    total_analyses = getattr(response, "count", None) or len(analyses)

    company_ids = {analysis.get("company_id") for analysis in analyses if analysis.get("company_id")}
    company_map: Dict[str, Dict[str, Any]] = {}
    if company_ids:
        companies_response = supabase.table("companies").select("*").in_("id", list(company_ids)).execute()
        company_map = {str(company["id"]): company for company in (companies_response.data or [])}

    history = [_build_history_entry(analysis, company_map.get(analysis.get("company_id"))) for analysis in analyses]
    stats = _calculate_stats(history, total_analyses=total_analyses)

    companies = list(company_map.values())

    return {
        "history": history,
        "stats": stats,
        "companies": companies,
    }


def _build_fallback_overview() -> Dict[str, Any]:
    history: List[Dict[str, Any]] = []
    for company_id, analyses in fallback_analyses.items():
        company = fallback_companies.get(str(company_id))
        for analysis in analyses:
            history.append(_build_history_entry(analysis, company))

    history.sort(key=lambda entry: _parse_datetime(entry.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    total_analyses = len(history)
    limited_history = history[:MAX_HISTORY_RESULTS]
    stats = _calculate_stats(limited_history, total_analyses=total_analyses)

    companies = list(fallback_companies.values())

    return {
        "history": limited_history,
        "stats": stats,
        "companies": companies,
    }


def _build_history_entry(analysis: Dict[str, Any], company: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    generated_at = _isoformat(
        analysis.get("analysis_date")
        or analysis.get("analysis_datetime")
        or analysis.get("created_at")
        or analysis.get("updated_at")
    )
    company_data = company or {}
    analysis_id = analysis.get("id")
    company_id = analysis.get("company_id") or company_data.get("id")

    return {
        "analysis_id": str(analysis_id) if analysis_id else None,
        "company_id": str(company_id) if company_id else None,
        "ticker": analysis.get("ticker") or company_data.get("ticker"),
        "name": company_data.get("name") or analysis.get("company_name"),
        "company_name": company_data.get("name") or analysis.get("company_name"),
        "exchange": company_data.get("exchange"),
        "sector": company_data.get("sector"),
        "industry": company_data.get("industry"),
        "country": company_data.get("country"),
        "health_score": analysis.get("health_score"),
        "score_band": analysis.get("score_band"),
        "summary_md": analysis.get("summary_md"),
        "investor_persona_summaries": analysis.get("investor_persona_summaries"),
        "generated_at": generated_at,
        "created_at": _isoformat(analysis.get("created_at")),
        "updated_at": _isoformat(analysis.get("updated_at")),
    }


def _calculate_stats(history: List[Dict[str, Any]], *, total_analyses: int) -> Dict[str, Any]:
    scores = [
        float(entry["health_score"])
        for entry in history
        if isinstance(entry.get("health_score"), (int, float))
    ]
    average_health = round(sum(scores) / len(scores), 1) if scores else None

    latest_dt = None
    for entry in history:
        entry_dt = _parse_datetime(entry.get("generated_at"))
        if entry_dt and (latest_dt is None or entry_dt > latest_dt):
            latest_dt = entry_dt

    company_ids = {entry.get("company_id") for entry in history if entry.get("company_id")}

    return {
        "total_analyses": total_analyses,
        "average_health_score": average_health,
        "latest_analysis_at": latest_dt.isoformat() if latest_dt else None,
        "company_count": len(company_ids),
        "sectors": _counter_to_list(entry.get("sector") for entry in history),
        "countries": _counter_to_list(entry.get("country") for entry in history),
    }


def _counter_to_list(values) -> List[Dict[str, Any]]:
    counter = Counter(
        str(value).strip()
        for value in values
        if value and str(value).strip()
    )
    return [
        {"label": label, "value": count}
        for label, count in counter.most_common()
    ]


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
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    return str(value)
