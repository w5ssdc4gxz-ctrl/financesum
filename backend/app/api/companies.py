"""Companies API endpoints."""
import requests
import asyncio
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List

from app.models.database import get_supabase_client
from app.models.schemas import (
    Company,
    CompanyLookupRequest,
    CompanyLookupResponse,
)
from app.services.edgar_fetcher import search_company_by_ticker_or_cik
from app.services.local_cache import fallback_companies, save_fallback_companies
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

    source = matches[0]
    now = datetime.utcnow()
    fallback_company = Company(
        id=company_id,
        ticker=source.get("ticker") or cleaned,
        name=source.get("name") or cleaned,
        cik=source.get("cik"),
        exchange=source.get("exchange"),
        industry=source.get("industry"),
        sector=source.get("sector"),
        country=source.get("country", "US"),
        created_at=now,
        updated_at=now,
    )
    fallback_companies[str(company_id)] = fallback_company.model_dump()
    save_fallback_companies()
    return fallback_company

router = APIRouter()


@router.get("/logo/{ticker}")
def get_company_logo(ticker: str, exchange: str = "US"):
    """Proxy for EODHD company logo."""
    settings = get_settings()
    api_key = settings.eodhd_api_key
    
    if not api_key:
        raise HTTPException(status_code=500, detail="EODHD API key not configured")
    
    # Clean ticker input
    clean_ticker = ticker.strip().upper()
    clean_exchange = exchange.strip().upper()
    
    url = f"https://eodhd.com/api/logo/{clean_ticker}.{clean_exchange}"
    params = {"api_token": api_key}
    
    try:
        # Stream the response
        r = requests.get(url, params=params, stream=True, timeout=10)
        if r.status_code != 200:
            # If logo not found, return 404
            raise HTTPException(status_code=r.status_code, detail="Logo not found")
            
        return StreamingResponse(
            r.iter_content(chunk_size=8192), 
            media_type=r.headers.get("content-type", "image/png"),
            headers={"Cache-Control": "public, max-age=86400"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching logo: {str(e)}")


@router.post("/lookup", response_model=CompanyLookupResponse)
async def lookup_company(request: CompanyLookupRequest):
    """
    Search for companies by ticker, CIK, or name.
    First checks local database, then queries EODHD/SEC EDGAR if not found.
    """
    settings = get_settings()
    query = request.query.strip().upper()
    
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
                return supabase.table("companies").select("*").ilike("name", f"%{query}%").execute()

            response = await asyncio.to_thread(run_supabase_query)
            
            if response and response.data:
                companies = [Company(**company) for company in response.data]
                if companies:
                    print(f"Found {len(companies)} companies in Supabase")
                    return CompanyLookupResponse(companies=companies)
        
        except Exception as e:
            print(f"Database search error (skipping): {e}")
    else:
        print("Supabase not configured, skipping database search")
    
    # If not found in database, search EDGAR
    try:
        edgar_companies = await search_company_by_ticker_or_cik(query)
        
        if not edgar_companies:
            raise HTTPException(status_code=404, detail="Company not found")
        
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
                    fallback_company = Company(
                        id=company_id,
                        ticker=ticker,
                        name=company_data.get("name", query),
                        cik=company_data.get("cik"),
                        exchange=company_data.get("exchange"),
                        industry=company_data.get("industry"),
                        sector=company_data.get("sector"),
                        country=company_data.get("country", "US"),
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
                fallback_company = Company(
                    id=company_id,
                    ticker=ticker,
                    name=company_data.get("name", query),
                    cik=company_data.get("cik"),
                    exchange=company_data.get("exchange"),
                    industry=company_data.get("industry"),
                    sector=company_data.get("sector"),
                    country=company_data.get("country", "US"),
                    created_at=now,
                    updated_at=now,
                )
                fallback_companies[str(company_id)] = fallback_company.model_dump()
                save_fallback_companies()
                saved_companies.append(fallback_company)
                continue
        
        return CompanyLookupResponse(companies=saved_companies)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching for company: {str(e)}")


@router.get("/{company_id}", response_model=Company)
async def get_company(company_id: str, ticker: str | None = None):
    """Get company details by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        cached = fallback_companies.get(str(company_id))
        if cached:
            return Company(**cached)
        if ticker:
            return await _hydrate_company_from_ticker(str(company_id), ticker)
        raise HTTPException(status_code=404, detail="Company not available without Supabase configuration")

    supabase = get_supabase_client()

    try:
        response = supabase.table("companies").select("*").eq("id", company_id).execute()

        if not response.data:
            cached = fallback_companies.get(str(company_id))
            if cached:
                return Company(**cached)
            if ticker:
                return await _hydrate_company_from_ticker(str(company_id), ticker)
            raise HTTPException(status_code=404, detail="Company not found")

        return Company(**response.data[0])

    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            cached = fallback_companies.get(str(company_id))
            if cached:
                return Company(**cached)
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

        return [Company(**company) for company in response.data]

    except Exception as e:
        if is_supabase_table_missing_error(e):
            return [Company(**data) for data in fallback_companies.values()]
        raise HTTPException(status_code=500, detail=f"Error listing companies: {str(e)}")
