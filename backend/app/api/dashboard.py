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
    save_fallback_companies,
)
from app.services.summary_activity import get_summary_generation_metrics
from app.utils.supabase_errors import is_supabase_table_missing_error
from app.services.eodhd_client import should_hydrate_country
from app.services.country_hydration_queue import mark_hydrated, queue_for_hydration
from app.services.country_resolver import (
    infer_country_from_company_name,
    infer_country_from_exchange,
    infer_country_from_ticker,
    normalize_country,
)

router = APIRouter()

MAX_HISTORY_RESULTS = 50


def _hydrate_and_persist_countries(company_map: Dict[str, Dict[str, Any]], supabase) -> None:
    """
    Hydrate and persist country data for companies loaded in the dashboard.

    This acts as a safety net - if a company was added but country hydration
    failed, this will attempt to hydrate it when the dashboard is loaded.
    Successfully hydrated companies are also removed from the pending queue.
    """
    for company in company_map.values():
        original = company.get("country")
        original_missing = should_hydrate_country(original)
        resolved_confidently = False

        normalized = normalize_country(original)
        if normalized and normalized != original:
            company["country"] = normalized

        if should_hydrate_country(company.get("country")):
            inferred = infer_country_from_company_name(company.get("name"))
            if inferred:
                company["country"] = inferred
                resolved_confidently = True

        if should_hydrate_country(company.get("country")) and company.get("ticker"):
            inferred_from_ticker = infer_country_from_ticker(company.get("ticker"))
            if inferred_from_ticker:
                company["country"] = inferred_from_ticker
                resolved_confidently = True

        if should_hydrate_country(company.get("country")):
            inferred_exchange = infer_country_from_exchange(company.get("exchange"))
            if inferred_exchange and inferred_exchange != "US":
                company["country"] = inferred_exchange
                resolved_confidently = True

        # Defer network-based country hydration (SEC/Yahoo/EODHD) so the dashboard stays fast.
        # We queue missing countries for the background hydrator, but do not block this request.
        if should_hydrate_country(company.get("country")) and company.get("ticker") and company.get("id"):
            queue_for_hydration(str(company.get("id")), str(company.get("ticker")), company.get("exchange"))

        # Avoid persisting a US placeholder when no domicile/HQ signal is available.
        if should_hydrate_country(company.get("country")) and not resolved_confidently and original_missing:
            company["country"] = None

        company_id = company.get("id")
        try:
            if company.get("country") != original and company_id:
                supabase.table("companies").update({"country": company.get("country")}).eq("id", company_id).execute()
                if not should_hydrate_country(company.get("country")):
                    mark_hydrated(str(company_id))
        except Exception as exc:  # noqa: BLE001
            print(f"Dashboard: could not persist hydrated country for {company.get('ticker')}: {exc}")


def _hydrate_fallback_countries(company_map: Dict[str, Dict[str, Any]]) -> None:
    """
    Hydrate country data for companies in fallback mode (no Supabase).

    Successfully hydrated companies are also removed from the pending queue.
    """
    updated = False
    for company in company_map.values():
        original = company.get("country")
        original_missing = should_hydrate_country(original)
        resolved_confidently = False

        normalized = normalize_country(original)
        if normalized and normalized != original:
            company["country"] = normalized

        if should_hydrate_country(company.get("country")):
            inferred = infer_country_from_company_name(company.get("name"))
            if inferred:
                company["country"] = inferred
                resolved_confidently = True

        if should_hydrate_country(company.get("country")) and company.get("ticker"):
            inferred_from_ticker = infer_country_from_ticker(company.get("ticker"))
            if inferred_from_ticker:
                company["country"] = inferred_from_ticker
                resolved_confidently = True

        if should_hydrate_country(company.get("country")):
            inferred_exchange = infer_country_from_exchange(company.get("exchange"))
            if inferred_exchange and inferred_exchange != "US":
                company["country"] = inferred_exchange
                resolved_confidently = True

        if should_hydrate_country(company.get("country")) and company.get("ticker") and company.get("id"):
            queue_for_hydration(str(company.get("id")), str(company.get("ticker")), company.get("exchange"))

        if should_hydrate_country(company.get("country")) and not resolved_confidently and original_missing:
            company["country"] = None

        company_id = company.get("id")
        if company.get("country") != original:
            fallback_companies[str(company_id)] = company
            if company_id and not should_hydrate_country(company.get("country")):
                mark_hydrated(str(company_id))
            updated = True
    if updated:
        save_fallback_companies()


@router.get("/overview")
async def get_dashboard_overview(tz_offset_minutes: Optional[int] = None) -> Dict[str, Any]:
    """
    Return aggregated dashboard metrics and the most recent analyses.

    The endpoint automatically falls back to local cached data when Supabase
    credentials are not configured or the underlying tables are unavailable.
    """
    settings = get_settings()

    if not _supabase_configured(settings):
        return _build_fallback_overview(tz_offset_minutes=tz_offset_minutes)

    try:
        return _build_supabase_overview(tz_offset_minutes=tz_offset_minutes)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            return _build_fallback_overview(tz_offset_minutes=tz_offset_minutes)
        raise HTTPException(status_code=500, detail=f"Error loading dashboard overview: {exc}") from exc


def _build_supabase_overview(*, tz_offset_minutes: Optional[int] = None) -> Dict[str, Any]:
    supabase = get_supabase_client()

    response = (
        supabase.table("analyses")
        .select("*", count="exact")
        .order("analysis_date", desc=True)
        .limit(MAX_HISTORY_RESULTS)
        .execute()
    )

    analyses: List[Dict[str, Any]] = response.data or []
    total_analyses = getattr(response, "count", None) or len(analyses)

    company_ids = {analysis.get("company_id") for analysis in analyses if analysis.get("company_id")}
    company_map: Dict[str, Dict[str, Any]] = {}
    if company_ids:
        companies_response = supabase.table("companies").select("*").in_("id", list(company_ids)).execute()
        company_map = {str(company["id"]): company for company in (companies_response.data or [])}
        _hydrate_and_persist_countries(company_map, supabase)

    history = [_build_history_entry(analysis, company_map.get(analysis.get("company_id"))) for analysis in analyses]

    # Summary generation metrics (for "Analysis Activity" and totals that should not decrease on dashboard removal)
    summary_total, summary_activity = get_summary_generation_metrics(supabase_client=supabase, tz_offset_minutes=tz_offset_minutes)

    stats = _calculate_stats(
        history,
        total_analyses=total_analyses,
        total_summaries=summary_total,
        summary_activity=summary_activity,
    )

    companies = list(company_map.values())

    return {
        "history": history,
        "stats": stats,
        "companies": companies,
    }


def _build_fallback_overview(*, tz_offset_minutes: Optional[int] = None) -> Dict[str, Any]:
    _hydrate_fallback_countries(fallback_companies)

    history: List[Dict[str, Any]] = []
    for company_id, analyses in fallback_analyses.items():
        company = fallback_companies.get(str(company_id))
        for analysis in analyses:
            history.append(_build_history_entry(analysis, company))

    history.sort(
        key=lambda entry: _parse_datetime(entry.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    total_analyses = len(history)
    limited_history = history[:MAX_HISTORY_RESULTS]

    summary_total, summary_activity = get_summary_generation_metrics(tz_offset_minutes=tz_offset_minutes)

    stats = _calculate_stats(
        limited_history,
        total_analyses=total_analyses,
        total_summaries=summary_total,
        summary_activity=summary_activity,
    )

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


def _calculate_stats(
    history: List[Dict[str, Any]],
    *,
    total_analyses: int,
    total_summaries: int,
    summary_activity: List[Dict[str, Any]],
) -> Dict[str, Any]:
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
        "total_summaries": total_summaries,
        "summary_activity": summary_activity,
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
