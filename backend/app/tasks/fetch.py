"""Celery tasks for fetching financial data from EODHD API."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import get_settings
from app.models.database import get_supabase_client
from app.services.eodhd_client import get_eodhd_client
from app.tasks.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)


def _normalize_filing_types(filing_types: Optional[List[str]]) -> Set[str]:
    if not filing_types:
        return set()
    return {ft.upper() for ft in filing_types if isinstance(ft, str)}


def _calculate_cutoff(max_history_years: Optional[int]) -> Optional[date]:
    if not max_history_years or max_history_years <= 0:
        return None
    return datetime.utcnow().date() - timedelta(days=365 * max_history_years)


def _is_before_cutoff(date_str: str, cutoff: Optional[date]) -> bool:
    if not cutoff:
        return False
    try:
        parsed = datetime.fromisoformat(str(date_str)).date()
    except ValueError:
        return False
    return parsed < cutoff


def _store_filing_and_statements(
    supabase,
    *,
    company_id: str,
    ticker: str,
    filing_type: str,
    date_str: str,
    income_statement: Dict[str, Any],
    balance_statement: Dict[str, Any],
    cashflow_statement: Dict[str, Any],
    source_url: str,
) -> Tuple[int, int]:
    try:
        # Check if filing exists
        existing = (
            supabase.table("filings")
            .select("id")
            .eq("company_id", company_id)
            .eq("filing_type", filing_type)
            .eq("filing_date", date_str)
            .execute()
        )
        
        filing_id = None
        if existing.data:
            filing_id = existing.data[0]["id"]
            # Check if statements exist for this filing
            existing_statements = (
                supabase.table("financial_statements")
                .select("id")
                .eq("filing_id", filing_id)
                .execute()
            )
            if existing_statements.data:
                return 0, 1  # Fully exists, skip
            
            # Orphan filing found (filing exists but no statements), proceed to insert statements
            logger.info(f"Found orphan filing {filing_id} for {ticker} {date_str}, attempting recovery.")
        else:
            # Create new filing
            filing_data = {
                "company_id": company_id,
                "filing_type": filing_type,
                "filing_date": date_str,
                "period_end": date_str,
                "url": source_url,
                "raw_file_path": f"eodhd_{ticker}_{filing_type.replace('-', '')}_{date_str}",
                "status": "parsed",
            }
            filing_response = supabase.table("filings").insert(filing_data).execute()
            if not filing_response.data:
                return 0, 0
            filing_id = filing_response.data[0]["id"]

        # Insert financial statements
        financial_statement_data = {
            "filing_id": filing_id,
            "period_start": date_str,
            "period_end": date_str,
            "currency": "USD",
            "statements": {
                "income_statement": income_statement,
                "balance_sheet": balance_statement,
                "cash_flow": cashflow_statement,
            },
        }
        
        try:
            supabase.table("financial_statements").insert(financial_statement_data).execute()
            return 1, 0
        except Exception as stmt_exc:
            # If we just created this filing and statement insert failed, delete the filing to avoid orphan
            if not existing.data:
                logger.warning(f"Failed to insert statements for new filing {filing_id}, rolling back filing.")
                supabase.table("filings").delete().eq("id", filing_id).execute()
            raise stmt_exc

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Error processing %s statement on %s for %s: %s", filing_type, date_str, ticker, exc
        )
        return 0, 0


def _ingest_frequency(
    supabase,
    financial_data: Dict[str, Any],
    *,
    company_id: str,
    ticker: str,
    frequency_key: str,
    filing_type: str,
    cutoff: Optional[date],
) -> Tuple[int, int]:
    income_parent = financial_data.get("income_statement") or {}
    balance_parent = financial_data.get("balance_sheet") or {}
    cashflow_parent = financial_data.get("cash_flow") or {}

    income_statements: Dict[str, Any] = income_parent.get(frequency_key, {}) or {}
    balance_statements: Dict[str, Any] = balance_parent.get(frequency_key, {}) or {}
    cashflow_statements: Dict[str, Any] = cashflow_parent.get(frequency_key, {}) or {}

    if not income_statements:
        return 0

    saved = 0
    duplicates = 0
    source_url = f"https://eodhd.com/api/fundamentals/{ticker}.US"
    for date_str, statement in income_statements.items():
        if _is_before_cutoff(date_str, cutoff):
            continue
        new_saved, skipped = _store_filing_and_statements(
            supabase,
            company_id=company_id,
            ticker=ticker,
            filing_type=filing_type,
            date_str=date_str,
            income_statement=statement,
            balance_statement=balance_statements.get(date_str, {}),
            cashflow_statement=cashflow_statements.get(date_str, {}),
            source_url=source_url,
        )
        saved += new_saved
        duplicates += skipped
    return saved, duplicates


def _run_eodhd_ingestion(
    *,
    company_id: str,
    ticker: str,
    filing_types: Optional[List[str]],
    max_history_years: Optional[int],
) -> Tuple[int, int]:
    supabase = get_supabase_client()
    financial_data = get_eodhd_client().get_financial_statements(ticker, exchange="US")
    cutoff = _calculate_cutoff(max_history_years)
    normalized_types = _normalize_filing_types(filing_types)
    include_all = not normalized_types

    saved = 0
    duplicates = 0
    if include_all or "10-Q" in normalized_types:
        new_saved, new_duplicates = _ingest_frequency(
            supabase,
            financial_data,
            company_id=company_id,
            ticker=ticker,
            frequency_key="quarterly",
            filing_type="10-Q",
            cutoff=cutoff,
        )
        saved += new_saved
        duplicates += new_duplicates
    if include_all or "10-K" in normalized_types:
        new_saved, new_duplicates = _ingest_frequency(
            supabase,
            financial_data,
            company_id=company_id,
            ticker=ticker,
            frequency_key="yearly",
            filing_type="10-K",
            cutoff=cutoff,
        )
        saved += new_saved
        duplicates += new_duplicates
    return saved, duplicates


def run_fetch_filings_inline(
    *,
    company_id: str,
    ticker: str,
    cik: Optional[str],
    filing_types: Optional[List[str]],
    max_history_years: Optional[int],
) -> Dict[str, Any]:
    """
    Execute the filings fetch synchronously as a fallback when Celery/Redis is unavailable.
    """
    saved_count, duplicate_count = _run_eodhd_ingestion(
        company_id=company_id,
        ticker=ticker,
        filing_types=filing_types,
        max_history_years=max_history_years,
    )
    return {
        "status": "completed",
        "message": (
            f"Inline filing fetch completed. {saved_count} new financial statements were processed "
            f"and {duplicate_count} duplicates were skipped."
        ),
        "filings_count": saved_count,
        "duplicates_skipped": duplicate_count,
        "source": "inline_sec_fallback",
    }


@celery_app.task(bind=True)
def fetch_filings_task(
    self,
    company_id: str,
    ticker: str,
    cik: str,
    filing_types: List[str],
    max_history_years: int,
):
    """
    Background task to fetch financial data from EODHD API.

    Args:
        self: Celery task instance
        company_id: Company UUID
        ticker: Stock ticker
        cik: Company CIK (not used with EODHD, but kept for compatibility)
        filing_types: List of filing types (quarterly/yearly)
        max_history_years: Maximum years of history
    """
    supabase = get_supabase_client()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"progress": 10, "status": "Fetching financial data from EODHD..."},
        )
        financial_data = get_eodhd_client().get_financial_statements(ticker, exchange="US")

        self.update_state(
            state="PROGRESS",
            meta={"progress": 40, "status": "Processing financial statements..."},
        )
        cutoff = _calculate_cutoff(max_history_years)
        normalized_types = _normalize_filing_types(filing_types)
        include_all = not normalized_types
        saved_count = 0
        duplicate_count = 0

        if include_all or "10-Q" in normalized_types:
            new_saved, skipped = _ingest_frequency(
                supabase,
                financial_data,
                company_id=company_id,
                ticker=ticker,
                frequency_key="quarterly",
                filing_type="10-Q",
                cutoff=cutoff,
            )
            saved_count += new_saved
            duplicate_count += skipped

        if include_all or "10-K" in normalized_types:
            self.update_state(
                state="PROGRESS",
                meta={"progress": 70, "status": "Processing annual reports..."},
            )
            new_saved, skipped = _ingest_frequency(
                supabase,
                financial_data,
                company_id=company_id,
                ticker=ticker,
                frequency_key="yearly",
                filing_type="10-K",
                cutoff=cutoff,
            )
            saved_count += new_saved
            duplicate_count += skipped

        supabase.table("task_status").update({"status": "completed", "progress": 100}).eq(
            "task_id", self.request.id
        ).execute()

        return {
            "status": "completed",
            "message": (
                f"Successfully fetched and processed {saved_count} financial statements "
                f"from SEC filings (skipped {duplicate_count} duplicates)."
            ),
            "filings_count": saved_count,
            "duplicates_skipped": duplicate_count,
        }

    except Exception as e:  # noqa: BLE001
        supabase.table("task_status").update(
            {"status": "failed", "error_message": str(e)}
        ).eq("task_id", self.request.id).execute()
        raise
