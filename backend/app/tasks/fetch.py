"""Celery tasks for fetching financial data from EODHD API."""
import os
import json
from typing import List
from app.tasks.celery_app import celery_app
from app.models.database import get_supabase_client
from app.services.eodhd_client import get_eodhd_client, normalize_eodhd_to_internal_format
from app.config import get_settings

settings = get_settings()


@celery_app.task(bind=True)
def fetch_filings_task(
    self,
    company_id: str,
    ticker: str,
    cik: str,
    filing_types: List[str],
    max_history_years: int
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
        # Update task status
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'Fetching financial data from EODHD...'})
        
        # Get financial data from EODHD
        eodhd_client = get_eodhd_client()
        financial_data = eodhd_client.get_financial_statements(ticker, exchange="US")
        
        self.update_state(state='PROGRESS', meta={'progress': 40, 'status': 'Processing financial statements...'})
        
        # Process quarterly and yearly data
        saved_count = 0
        
        # Create "filings" for quarterly reports
        quarterly_income = financial_data.get("income_statement", {}).get("quarterly", {})
        for date, statement in quarterly_income.items():
            try:
                # Check if already exists
                existing = supabase.table("filings")\
                    .select("id")\
                    .eq("company_id", company_id)\
                    .eq("filing_type", "10-Q")\
                    .eq("filing_date", date)\
                    .execute()
                
                if existing.data:
                    continue
                
                # Save filing metadata
                filing_data = {
                    "company_id": company_id,
                    "filing_type": "10-Q",
                    "filing_date": date,
                    "period_end": date,
                    "url": f"https://eodhd.com/api/fundamentals/{ticker}.US",
                    "raw_file_path": f"eodhd_{ticker}_10Q_{date}",
                    "status": "parsed"  # EODHD data is already parsed
                }
                
                filing_response = supabase.table("filings").insert(filing_data).execute()
                
                if filing_response.data:
                    filing_id = filing_response.data[0]["id"]
                    
                    # Store the structured financial data
                    # Get balance sheet and cash flow for this period
                    balance = financial_data.get("balance_sheet", {}).get("quarterly", {}).get(date, {})
                    cashflow = financial_data.get("cash_flow", {}).get("quarterly", {}).get(date, {})
                    
                    financial_statement_data = {
                        "filing_id": filing_id,
                        "period_start": date,
                        "period_end": date,
                        "currency": "USD",
                        "statements": {
                            "income_statement": statement,
                            "balance_sheet": balance,
                            "cash_flow": cashflow
                        }
                    }
                    
                    supabase.table("financial_statements").insert(financial_statement_data).execute()
                    saved_count += 1
            
            except Exception as e:
                print(f"Error processing quarterly statement {date}: {e}")
                continue
        
        self.update_state(state='PROGRESS', meta={'progress': 70, 'status': 'Processing annual reports...'})
        
        # Create "filings" for annual reports  
        yearly_income = financial_data.get("income_statement", {}).get("yearly", {})
        for date, statement in yearly_income.items():
            try:
                # Check if already exists
                existing = supabase.table("filings")\
                    .select("id")\
                    .eq("company_id", company_id)\
                    .eq("filing_type", "10-K")\
                    .eq("filing_date", date)\
                    .execute()
                
                if existing.data:
                    continue
                
                # Save filing metadata
                filing_data = {
                    "company_id": company_id,
                    "filing_type": "10-K",
                    "filing_date": date,
                    "period_end": date,
                    "url": f"https://eodhd.com/api/fundamentals/{ticker}.US",
                    "raw_file_path": f"eodhd_{ticker}_10K_{date}",
                    "status": "parsed"  # EODHD data is already parsed
                }
                
                filing_response = supabase.table("filings").insert(filing_data).execute()
                
                if filing_response.data:
                    filing_id = filing_response.data[0]["id"]
                    
                    # Store the structured financial data
                    balance = financial_data.get("balance_sheet", {}).get("yearly", {}).get(date, {})
                    cashflow = financial_data.get("cash_flow", {}).get("yearly", {}).get(date, {})
                    
                    financial_statement_data = {
                        "filing_id": filing_id,
                        "period_start": date,
                        "period_end": date,
                        "currency": "USD",
                        "statements": {
                            "income_statement": statement,
                            "balance_sheet": balance,
                            "cash_flow": cashflow
                        }
                    }
                    
                    supabase.table("financial_statements").insert(financial_statement_data).execute()
                    saved_count += 1
            
            except Exception as e:
                print(f"Error processing annual statement {date}: {e}")
                continue
        
        # Update task status
        supabase.table("task_status")\
            .update({"status": "completed", "progress": 100})\
            .eq("task_id", self.request.id)\
            .execute()
        
        return {
            'status': 'completed',
            'message': f'Successfully fetched and processed {saved_count} financial statements from EODHD',
            'filings_count': saved_count
        }
    
    except Exception as e:
        # Update task status
        supabase.table("task_status")\
            .update({
                "status": "failed",
                "error_message": str(e)
            })\
            .eq("task_id", self.request.id)\
            .execute()
        
        raise

