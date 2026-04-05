"""Companies API endpoints."""
import requests
import asyncio
from html import escape as html_escape
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from typing import List

from app.models.database import get_supabase_client
from app.models.schemas import (
    Company,
    CompanyLookupRequest,
    CompanyLookupResponse,
)
from app.services.edgar_fetcher import search_company_by_ticker_or_cik, resolve_country_from_sec_submission
from app.services.local_cache import fallback_companies, save_fallback_companies
from app.services.eodhd_client import hydrate_country_with_eodhd, hydrate_country_with_retry, should_hydrate_country
from app.services.country_resolver import (
    infer_country_from_company_name,
    infer_country_from_exchange,
    infer_country_from_ticker,
    normalize_country,
)
from app.services.yahoo_finance import resolve_country_from_yahoo_asset_profile
from app.services.country_hydration_queue import queue_for_hydration
from app.config import get_settings
from app.utils.supabase_errors import is_supabase_table_missing_error


def _supabase_configured(settings) -> bool:
    """Return True when Supabase keys are present and not placeholders."""
    key = (settings.supabase_service_role_key or "").strip()
    url = (settings.supabase_url or "").strip()
    if not key or not url:
        return False
    if key.lower().startswith("your_"):
        return False
    return True


def _search_fallback_companies(raw_query: str, limit: int = 10) -> List[Company]:
    query = (raw_query or "").strip()
    if not query:
        return []

    query_upper = query.upper()
    query_folded = query.casefold()
    cik_digits = "".join(ch for ch in query if ch.isdigit())
    cik_padded = cik_digits.zfill(10) if cik_digits else None

    scored: list[tuple[int, dict]] = []
    for record in fallback_companies.values():
        ticker = (record.get("ticker") or "").strip().upper()
        name = (record.get("name") or "").strip()
        name_folded = name.casefold()

        record_cik_digits = "".join(ch for ch in str(record.get("cik") or "") if ch.isdigit())
        record_cik_padded = record_cik_digits.zfill(10) if record_cik_digits else None

        score = None
        if ticker and ticker == query_upper:
            score = 300
        elif cik_padded and record_cik_padded and record_cik_padded == cik_padded:
            score = 250
        elif name_folded and name_folded.startswith(query_folded):
            score = 200
        elif query_folded and query_folded in name_folded:
            score = 150
        elif ticker and ticker.startswith(query_upper):
            score = 100

        if score is not None:
            scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)

    matches: list[Company] = []
    for _score, record in scored[:limit]:
        try:
            matches.append(Company(**record))
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping invalid cached company record: {exc}")

    return matches


async def _ensure_company_country(company: dict) -> dict:
    """
    Ensure company has country data, with retry and queuing for failures.

    Uses hydrate_country_with_retry() which attempts up to 3 times with
    exponential backoff. If all attempts fail, the company is queued
    for background processing.

    Args:
        company: Company dict to hydrate

    Returns:
        Company dict with country field populated (if successful)
    """
    ticker = company.get("ticker")
    company_id = company.get("id")

    original_country = company.get("country")
    original_missing = should_hydrate_country(original_country)
    resolved_confidently = False

    normalized_existing = normalize_country(company.get("country"))
    if normalized_existing and normalized_existing != company.get("country"):
        company["country"] = normalized_existing

    # Treat US placeholders as unresolved so we still attempt stronger inference.
    if should_hydrate_country(company.get("country")):
        inferred = infer_country_from_company_name(company.get("name"))
        if inferred:
            company["country"] = normalize_country(inferred) or inferred
            resolved_confidently = True

    if should_hydrate_country(company.get("country")) and ticker:
        inferred_from_ticker = infer_country_from_ticker(ticker)
        if inferred_from_ticker:
            company["country"] = inferred_from_ticker
            resolved_confidently = True

    # If the company appears on a non-US exchange, use that as a fast, safe hint.
    if should_hydrate_country(company.get("country")):
        inferred_exchange = infer_country_from_exchange(company.get("exchange"))
        if inferred_exchange and inferred_exchange != "US":
            company["country"] = inferred_exchange
            resolved_confidently = True

    if should_hydrate_country(company.get("country")) and company.get("cik"):
        sec_country = await asyncio.to_thread(resolve_country_from_sec_submission, company.get("cik"))
        if sec_country:
            company["country"] = normalize_country(sec_country) or sec_country
            resolved_confidently = True

    if ticker and should_hydrate_country(company.get("country")):
        yahoo_country = await asyncio.to_thread(resolve_country_from_yahoo_asset_profile, ticker)
        if yahoo_country:
            company["country"] = normalize_country(yahoo_country) or yahoo_country
            resolved_confidently = True

    # Try synchronous hydration with retry (up to 3 attempts with backoff)
    hydrated = None
    if ticker and should_hydrate_country(company.get("country")):
        hydrated = await asyncio.to_thread(
            hydrate_country_with_retry,
            ticker,
            company.get("exchange"),
            2,  # max_retries
            0.5  # base_delay
        )

    if hydrated:
        company["country"] = normalize_country(hydrated) or hydrated
    else:
        # Queue for background retry if we have an ID
        if ticker and should_hydrate_country(company.get("country")) and company_id:
            queue_for_hydration(str(company_id), ticker, company.get("exchange"))
            print(f"Queued {ticker} for background country hydration")

    # If we still only have a US placeholder (or missing) and we didn't find any
    # domicile/HQ signal, prefer "unknown" over wrongly plotting everything as US.
    if should_hydrate_country(company.get("country")) and not resolved_confidently and original_missing:
        company["country"] = None

    if not company.get("country"):
        inferred = infer_country_from_exchange(company.get("exchange"))
        # Do not default a US-listed company to US domicile without stronger evidence.
        if inferred and inferred != "US":
            company["country"] = inferred

    return company


async def _fix_and_persist_countries(records: list, supabase=None) -> list:
    """
    Hydrate and persist country data for a list of company records.

    Uses _ensure_company_country() which includes retry logic and
    queues failed hydrations for background processing.
    """
    updated: List[tuple[str, str]] = []
    fixed: List[dict] = []
    for record in records:
        company = await _ensure_company_country(dict(record))
        fixed.append(company)
        if supabase and company.get("id") and company.get("country") != record.get("country"):
            updated.append((company.get("id"), company.get("country")))
    if supabase and updated:
        for company_id, country in updated:
            try:
                supabase.table("companies").update({"country": country}).eq("id", company_id).execute()
            except Exception as exc:  # noqa: BLE001
                print(f"Country backfill failed for {company_id}: {exc}")
    return fixed


async def _hydrate_company_from_ticker(company_id: str, ticker: str) -> Company:
    """Attempt to rebuild a company record when only the ticker is available."""
    cleaned = (ticker or "").strip().upper()
    if not cleaned:
        raise HTTPException(status_code=404, detail="Company not found")

    try:
        matches = await search_company_by_ticker_or_cik(cleaned)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Unable to rebuild company from ticker: {exc}") from exc

    if not matches:
        raise HTTPException(status_code=404, detail="Company not found")

    source = await _ensure_company_country(matches[0])
    now = datetime.utcnow()
    fallback_company = Company(
        id=company_id,
        ticker=source.get("ticker") or cleaned,
        name=source.get("name") or cleaned,
        cik=source.get("cik"),
        exchange=source.get("exchange"),
        industry=source.get("industry"),
        sector=source.get("sector"),
        country=source.get("country"),
        created_at=now,
        updated_at=now,
    )
    fallback_companies[str(company_id)] = fallback_company.model_dump()
    save_fallback_companies()
    return fallback_company

router = APIRouter()


_EXCHANGE_ALIASES = {
    "NASDAQ": "US",
    "NYSE": "US",
    "NYSEARCA": "US",
    "AMEX": "US",
    "OTC": "US",
}


def _logo_placeholder_response(ticker: str) -> Response:
    label = (ticker or "").strip().upper()
    label = label[:4] if label else "CO"
    safe_label = html_escape(label)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect width="128" height="128" fill="#F3F4F6"/>
  <rect x="8" y="8" width="112" height="112" rx="16" fill="#E5E7EB" stroke="#D1D5DB"/>
  <text x="64" y="66" text-anchor="middle" dominant-baseline="middle" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial" font-size="40" font-weight="700" fill="#374151">{safe_label}</text>
</svg>"""
    return Response(
        content=svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/logo/{ticker}")
def get_company_logo(ticker: str, exchange: str = "US"):
    """Proxy for EODHD company logo."""
    settings = get_settings()
    api_key = settings.eodhd_api_key
    
    # Clean ticker input
    clean_ticker = ticker.strip().upper()
    clean_exchange = _EXCHANGE_ALIASES.get(exchange.strip().upper(), exchange.strip().upper())

    if clean_ticker.endswith(f".{clean_exchange}"):
        clean_ticker = clean_ticker[: -(len(clean_exchange) + 1)]

    if not api_key:
        return _logo_placeholder_response(clean_ticker)
    
    url = f"https://eodhd.com/api/logo/{clean_ticker}.{clean_exchange}"
    params = {"api_token": api_key}
    
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return _logo_placeholder_response(clean_ticker)

        return Response(
            content=r.content,
            media_type=r.headers.get("content-type", "image/png"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return _logo_placeholder_response(clean_ticker)


def _deduplicate_companies(companies: list[Company]) -> list[Company]:
    """Remove duplicate companies, keeping the first occurrence per ticker."""
    seen_tickers: set[str] = set()
    unique: list[Company] = []
    for company in companies:
        ticker_key = (company.ticker or "").strip().upper()
        if ticker_key and ticker_key in seen_tickers:
            continue
        seen_tickers.add(ticker_key)
        unique.append(company)
    return unique


async def _lookup_companies(raw_query: str) -> CompanyLookupResponse:
    """
    Search for companies by ticker, CIK, or name.
    First checks local database, then queries EODHD/SEC EDGAR if not found.
    """
    settings = get_settings()
    query_raw = (raw_query or "").strip()
    if not query_raw:
        return CompanyLookupResponse(companies=[])
    query = query_raw.upper()
    
    # Search local database first (only if Supabase is configured)
    if _supabase_configured(settings):
        try:
            print(f"Searching Supabase for: {query}")
            supabase = get_supabase_client()
            
            # Helper to run supabase query in thread
            def run_supabase_query():
                # Try ticker match
                response = supabase.table("companies").select("*").eq("ticker", query).execute()
                if response.data:
                    return response
                
                # Try CIK match
                response = supabase.table("companies").select("*").eq("cik", query).execute()
                if response.data:
                    return response
                    
                # Try name match (case-insensitive partial match)
                return supabase.table("companies").select("*").ilike("name", f"%{query_raw}%").execute()

            response = await asyncio.to_thread(run_supabase_query)
            
            if response and response.data:
                supabase_client = get_supabase_client() if _supabase_configured(settings) else None
                hydrated_records = await _fix_and_persist_countries(response.data, supabase_client)
                companies = _deduplicate_companies([Company(**company) for company in hydrated_records])
                if companies:
                    print(f"Found {len(companies)} companies in Supabase")
                    return CompanyLookupResponse(companies=companies)
        
        except Exception as e:
            print(f"Database search error (skipping): {e}")
    else:
        print("Supabase not configured, skipping database search")

    fallback_matches = _search_fallback_companies(query_raw)
    if fallback_matches:
        return CompanyLookupResponse(companies=_deduplicate_companies(fallback_matches))
    
    # If not found in database, search EDGAR
    try:
        edgar_companies = await search_company_by_ticker_or_cik(query)
        
        if not edgar_companies:
            return CompanyLookupResponse(companies=[])
        
        # Save found companies to database (if Supabase is configured)
        saved_companies = []
        for company_data in edgar_companies:
            try:
                if _supabase_configured(settings):
                    supabase = get_supabase_client()
                    
                    def check_and_save():
                        # Check if already exists
                        existing = supabase.table("companies").select("*").eq("ticker", company_data["ticker"]).execute()
                        
                        if existing.data:
                            return existing.data[0]
                        else:
                            # Insert new company
                            result = supabase.table("companies").insert(company_data).execute()
                            if result.data:
                                return result.data[0]
                        return None

                    hydrated = await _ensure_company_country(company_data)

                    def check_and_save():
                        existing = supabase.table("companies").select("*").eq("ticker", hydrated["ticker"]).execute()

                        if existing.data:
                            return existing.data[0]
                        else:
                            result = supabase.table("companies").insert(hydrated).execute()
                            if result.data:
                                return result.data[0]
                        return None

                    saved_data = await asyncio.to_thread(check_and_save)
                    if saved_data:
                        saved_companies.append(Company(**saved_data))
                else:
                    # No database configured, return the company data with stub metadata
                    ticker = company_data.get("ticker", query)
                    existing_match = next(
                        (Company(**data) for data in fallback_companies.values() if data.get("ticker") == ticker),
                        None,
                    )
                    if existing_match:
                        saved_companies.append(existing_match)
                        continue

                    now = datetime.utcnow()
                    company_id = uuid4()
                    hydrated = await _ensure_company_country(company_data)
                    fallback_company = Company(
                        id=company_id,
                        ticker=ticker,
                        name=hydrated.get("name", query),
                        cik=hydrated.get("cik"),
                        exchange=hydrated.get("exchange"),
                        industry=hydrated.get("industry"),
                        sector=hydrated.get("sector"),
                        country=hydrated.get("country"),
                        created_at=now,
                        updated_at=now,
                    )
                    fallback_companies[str(company_id)] = fallback_company.model_dump()
                    save_fallback_companies()
                    saved_companies.append(fallback_company)
            except Exception as e:
                print(f"Error saving company: {e}")
                ticker = company_data.get("ticker", query)
                existing_match = next(
                    (Company(**data) for data in fallback_companies.values() if data.get("ticker") == ticker),
                    None,
                )
                if existing_match:
                    saved_companies.append(existing_match)
                    continue

                now = datetime.utcnow()
                company_id = uuid4()
                hydrated = await _ensure_company_country(company_data)
                fallback_company = Company(
                    id=company_id,
                    ticker=ticker,
                    name=hydrated.get("name", query),
                    cik=hydrated.get("cik"),
                    exchange=hydrated.get("exchange"),
                    industry=hydrated.get("industry"),
                    sector=hydrated.get("sector"),
                    country=hydrated.get("country"),
                    created_at=now,
                    updated_at=now,
                )
                fallback_companies[str(company_id)] = fallback_company.model_dump()
                save_fallback_companies()
                saved_companies.append(fallback_company)
                continue
        
        return CompanyLookupResponse(companies=_deduplicate_companies(saved_companies))
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching for company: {str(e)}")

@router.get("/lookup", response_model=CompanyLookupResponse)
async def lookup_company_get(query: str = Query(..., description="Ticker, CIK, or company name")):
    return await _lookup_companies(query)


@router.post("/lookup", response_model=CompanyLookupResponse)
async def lookup_company(request: CompanyLookupRequest):
    return await _lookup_companies(request.query)


@router.get("/{company_id}", response_model=Company)
async def get_company(company_id: str, ticker: str | None = None):
    """Get company details by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        cached = fallback_companies.get(str(company_id))
        if cached:
            hydrated = await _ensure_company_country(dict(cached))
            fallback_companies[str(company_id)] = hydrated
            save_fallback_companies()
            return Company(**hydrated)
        if ticker:
            return await _hydrate_company_from_ticker(str(company_id), ticker)
        raise HTTPException(status_code=404, detail="Company not available without Supabase configuration")

    supabase = get_supabase_client()

    try:
        response = supabase.table("companies").select("*").eq("id", company_id).execute()

        if not response.data:
            cached = fallback_companies.get(str(company_id))
            if cached:
                hydrated = await _ensure_company_country(dict(cached))
                fallback_companies[str(company_id)] = hydrated
                save_fallback_companies()
                return Company(**hydrated)
            if ticker:
                return await _hydrate_company_from_ticker(str(company_id), ticker)
            raise HTTPException(status_code=404, detail="Company not found")

        hydrated = await _ensure_company_country(dict(response.data[0]))
        if hydrated.get("country") != response.data[0].get("country"):
            try:
                supabase.table("companies").update({"country": hydrated.get("country")}).eq("id", company_id).execute()
            except Exception as exc:  # noqa: BLE001
                print(f"Country backfill failed for {company_id}: {exc}")
        return Company(**hydrated)

    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            cached = fallback_companies.get(str(company_id))
            if cached:
                hydrated = await _ensure_company_country(dict(cached))
                fallback_companies[str(company_id)] = hydrated
                save_fallback_companies()
                return Company(**hydrated)
            if ticker:
                return await _hydrate_company_from_ticker(str(company_id), ticker)
            raise HTTPException(status_code=404, detail="Company not found (Supabase tables missing and no cached data).")
        raise HTTPException(status_code=500, detail=f"Error retrieving company: {str(e)}")


@router.get("/", response_model=List[Company])
async def list_companies(
    limit: int = 100,
    offset: int = 0,
    sector: str = None,
    industry: str = None
):
    """List companies with optional filters."""
    settings = get_settings()

    if not _supabase_configured(settings):
        return [Company(**data) for data in fallback_companies.values()]

    supabase = get_supabase_client()

    try:
        query = supabase.table("companies").select("*")

        if sector:
            query = query.eq("sector", sector)
        if industry:
            query = query.eq("industry", industry)

        response = query.range(offset, offset + limit - 1).execute()

        hydrated_records = await _fix_and_persist_countries(response.data or [], supabase)

        return [Company(**company) for company in hydrated_records]

    except Exception as e:
        if is_supabase_table_missing_error(e):
            return [Company(**data) for data in fallback_companies.values()]
        raise HTTPException(status_code=500, detail=f"Error listing companies: {str(e)}")
