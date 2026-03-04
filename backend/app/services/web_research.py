"""Company web-research dossier service.

Builds a background dossier about a company using GPT-5.2 with web search,
then caches it in Supabase with a ~30-day TTL so subsequent summaries for
the same company skip the research step entirely.

Usage from the filing summary pipeline::

    from app.services.web_research import get_company_research_dossier

    dossier = await get_company_research_dossier(
        company_name="Apple Inc.",
        ticker="AAPL",
        sector="Technology",
        industry="Consumer Electronics",
        filing_type="10-K",
    )
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# Cache TTL: ~30 days (in seconds)
DOSSIER_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 2_592_000
DOSSIER_CACHE_TABLE = "company_research_cache"


def _parse_filing_date(value: str) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None
    iso_match = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", raw)
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()
        except Exception:
            return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _period_bucket(*, filing_type: str, filing_date: str) -> str:
    dt = _parse_filing_date(filing_date)
    if not dt:
        return "unknown"
    filing_type_upper = str(filing_type or "").strip().upper()
    if filing_type_upper.startswith("10-Q"):
        quarter = ((int(dt.month) - 1) // 3) + 1
        return f"{dt.year}-Q{quarter}"
    return str(dt.year)


def _legacy_cache_key(company_name: str, ticker: str) -> str:
    raw = f"{(company_name or '').strip().lower()}|{(ticker or '').strip().upper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _cache_key(
    company_name: str,
    ticker: str,
    *,
    filing_type: str = "",
    filing_date: str = "",
) -> str:
    """Deterministic cache key scoped by company + ticker + filing period bucket."""
    bucket = _period_bucket(filing_type=filing_type, filing_date=filing_date)
    raw = (
        f"dossier_v2|{(company_name or '').strip().lower()}"
        f"|{(ticker or '').strip().upper()}"
        f"|{(filing_type or '').strip().upper()}"
        f"|{bucket}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _get_supabase_client():
    """Best-effort Supabase client; returns None when unconfigured."""
    try:
        from app.models.database import get_supabase_client
        return get_supabase_client()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

def _read_cache(cache_key: str) -> Optional[str]:
    """Read a cached dossier from Supabase. Returns None on miss/error."""
    client = _get_supabase_client()
    if not client:
        return None

    try:
        response = (
            client.table(DOSSIER_CACHE_TABLE)
            .select("dossier_text, created_at")
            .eq("cache_key", cache_key)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None

        row = response.data[0]
        created_at_str = row.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(
                    str(created_at_str).replace("Z", "+00:00")
                )
                age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
                if age_seconds > DOSSIER_CACHE_TTL_SECONDS:
                    logger.debug("Dossier cache expired for key %s (age: %.0fs)", cache_key, age_seconds)
                    return None
            except Exception:
                pass  # If we can't parse the timestamp, use the cached value anyway

        dossier = row.get("dossier_text")
        if isinstance(dossier, str) and dossier.strip():
            return dossier.strip()

    except Exception as exc:
        logger.debug("Dossier cache read error: %s", exc)

    return None


def _write_cache(
    cache_key: str,
    company_name: str,
    ticker: str,
    dossier_text: str,
) -> None:
    """Write a dossier to the Supabase cache (best-effort)."""
    client = _get_supabase_client()
    if not client:
        return

    try:
        row = {
            "cache_key": cache_key,
            "company_name": (company_name or "").strip(),
            "ticker": (ticker or "").strip().upper(),
            "dossier_text": dossier_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Upsert: if key exists, overwrite (refresh the TTL)
        client.table(DOSSIER_CACHE_TABLE).upsert(row, on_conflict="cache_key").execute()
    except Exception as exc:
        logger.debug("Dossier cache write error: %s", exc)


def _evict_expired_cache() -> None:
    """Best-effort cleanup of expired dossier entries."""
    client = _get_supabase_client()
    if not client:
        return

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=DOSSIER_CACHE_TTL_SECONDS)).isoformat()
        client.table(DOSSIER_CACHE_TABLE).delete().lt("created_at", cutoff).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_research_dossier(
    *,
    company_name: str,
    ticker: str,
    sector: str = "",
    industry: str = "",
    filing_type: str = "",
    filing_date: str = "",
    timeout_seconds: float = 20.0,
    force_refresh: bool = False,
    return_structured: bool = False,
    usage_context: Optional[Dict[str, Any]] = None,
):
    """Get a cached-or-fresh web-research dossier for a company.

    Returns the dossier text (str), or empty string on failure. Never raises.

    When ``return_structured=True``, delegates to the Agent 1 pipeline via
    ``summary_agents.run_summary_agent_pipeline`` and returns a
    ``CompanyIntelligenceProfile`` instance instead of a flat string.  Old
    callers that don't pass ``return_structured`` are unaffected.
    """
    if return_structured:
        try:
            from app.services.openai_client import get_openai_client
            from app.services.summary_agents import _run_agent_1

            client = get_openai_client()
            set_usage_context = getattr(client, "set_usage_context", None)
            if callable(set_usage_context):
                set_usage_context(usage_context)
            profile = _run_agent_1(
                company_name=company_name,
                ticker=ticker,
                sector=sector,
                industry=industry,
                filing_type=filing_type,
                filing_date=filing_date,
                openai_client=client,
            )
            return profile
        except Exception as exc:
            logger.warning(
                "Structured research failed for %s (%s): %s — falling back to flat dossier",
                company_name, ticker, exc,
            )
            # Fall through to the flat dossier path

    if not company_name or not ticker:
        return ""

    key = _cache_key(
        company_name,
        ticker,
        filing_type=filing_type,
        filing_date=filing_date,
    )
    legacy_key = _legacy_cache_key(company_name, ticker)

    # 1. Check cache first (unless forced refresh)
    if not force_refresh:
        cached = _read_cache(key)
        if cached:
            logger.info(
                "Dossier cache HIT for %s (%s) — skipping web research",
                company_name, ticker,
            )
            return cached
        legacy_cached = _read_cache(legacy_key)
        if legacy_cached:
            logger.info(
                "Dossier legacy cache HIT for %s (%s) — hydrating period-scoped key",
                company_name,
                ticker,
            )
            _write_cache(key, company_name, ticker, legacy_cached)
            return legacy_cached

    # 2. Generate fresh dossier via GPT-5.2
    logger.info("Dossier cache MISS for %s (%s) — running web research", company_name, ticker)

    try:
        from app.services.openai_client import get_openai_client

        settings = get_settings()
        api_key = getattr(settings, "openai_api_key", "")
        if not api_key or not api_key.strip():
            logger.debug("No AI API key configured; skipping web research")
            return ""

        client = get_openai_client()
        set_usage_context = getattr(client, "set_usage_context", None)
        if callable(set_usage_context):
            set_usage_context(usage_context)
        dossier = client.research_company_background(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
            filing_type=filing_type,
            filing_date=filing_date,
            timeout_seconds=timeout_seconds,
        )

        if not dossier or not dossier.strip():
            return ""

        dossier = dossier.strip()

        # 3. Cache the result
        _write_cache(key, company_name, ticker, dossier)

        # 4. Best-effort: evict old entries periodically (~1% of calls)
        import random
        if random.random() < 0.01:
            _evict_expired_cache()

        return dossier

    except Exception as exc:
        logger.warning("Web research dossier failed for %s (%s): %s", company_name, ticker, exc)
        return ""


async def get_company_research_dossier_async(
    *,
    company_name: str,
    ticker: str,
    sector: str = "",
    industry: str = "",
    filing_type: str = "",
    filing_date: str = "",
    timeout_seconds: float = 20.0,
    force_refresh: bool = False,
    usage_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Async wrapper for use in FastAPI endpoints."""
    import anyio

    return await anyio.to_thread.run_sync(
        lambda: get_company_research_dossier(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
            filing_type=filing_type,
            filing_date=filing_date,
            timeout_seconds=timeout_seconds,
            force_refresh=force_refresh,
            usage_context=usage_context,
        ),
        abandon_on_cancel=True,
    )
