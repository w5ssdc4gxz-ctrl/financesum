"""SEC EDGAR filing fetcher service - Enhanced with EODHD."""
import requests
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.config import get_settings
from app.services.eodhd_client import EODHDClient

settings = get_settings()


async def search_company_by_ticker_or_cik(query: str) -> List[Dict]:
    """
    Search for company by ticker or CIK using EODHD API (enhanced) and SEC EDGAR.
    Returns list of company data dictionaries.
    """
    companies = []
    
    # Try EODHD first (faster and has more metadata)
    try:
        if settings.eodhd_api_key:
            eodhd_client = EODHDClient()
            company_info = eodhd_client.search_symbol(query)
            
            if company_info:
                return [{
                    "ticker": company_info["ticker"],
                    "cik": company_info.get("cik"),
                    "name": company_info["name"],
                    "exchange": company_info["exchange"],
                    "sector": company_info.get("sector"),
                    "industry": company_info.get("industry"),
                    "country": company_info.get("country", "USA")
                }]
    except Exception as e:
        print(f"EODHD search error (falling back to EDGAR): {e}")
    
    # Fallback to SEC EDGAR (if EODHD not available)
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }
    
    try:
        response = requests.get(tickers_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        companies_data = response.json()
        
        # Convert to list and search
        query_upper = query.upper()
        
        for key, company in companies_data.items():
            ticker = company.get("ticker", "").upper()
            cik = str(company.get("cik_str", "")).zfill(10)
            title = company.get("title", "")
            
            # Match by ticker or CIK
            if query_upper == ticker or query.zfill(10) == cik or query_upper in title.upper():
                companies.append({
                    "ticker": ticker,
                    "cik": cik,
                    "name": title,
                    "exchange": "US"
                })
                
                # If exact ticker match, return immediately
                if query_upper == ticker:
                    return [companies[-1]]
        
        return companies[:10]  # Limit to top 10 results
    
    except Exception as e:
        print(f"Error searching EDGAR: {e}")

    # Final fallback: Yahoo Finance public search API
    try:
        yahoo_url = "https://query2.finance.yahoo.com/v1/finance/search"
        yahoo_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FinanceSum/1.0; +https://financesum.local)",
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "quotesCount": 10,
            "newsCount": 0,
        }

        response = requests.get(yahoo_url, headers=yahoo_headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        quotes = data.get("quotes", [])
        for quote in quotes:
            quote_type = quote.get("quoteType")
            if quote_type not in {"EQUITY", "ETF"}:
                continue

            ticker = quote.get("symbol", "").upper()
            if not ticker:
                continue

            companies.append({
                "ticker": ticker,
                "cik": quote.get("cik") or quote.get("symbol"),
                "name": quote.get("longname") or quote.get("shortname") or ticker,
                "exchange": quote.get("exchDisp") or quote.get("exchange") or "US",
                "sector": quote.get("sectorDisp") or quote.get("sector"),
                "industry": quote.get("industryDisp") or quote.get("industry"),
                "country": quote.get("region") or "US",
            })

        if companies:
            return companies[:10]

    except Exception as e:
        print(f"Error searching Yahoo Finance: {e}")

    return companies


def get_company_filings(
    cik: str,
    filing_types: Optional[List[str]] = None,
    max_results: int = 100
) -> List[Dict]:
    """
    Get filings for a company from SEC EDGAR.
    
    Args:
        cik: Company CIK (will be zero-padded to 10 digits)
        filing_types: List of filing types to filter (e.g., ['10-K', '10-Q'])
        max_results: Maximum number of filings to return
    
    Returns:
        List of filing metadata dictionaries
    """
    cik_padded = str(cik).zfill(10)
    
    # SEC EDGAR Submissions API
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov"
    }
    
    try:
        response = requests.get(submissions_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        recent_filings = data.get("filings", {}).get("recent", {})
        
        filings = []
        
        # Get arrays of filing data
        accession_numbers = recent_filings.get("accessionNumber", [])
        filing_dates = recent_filings.get("filingDate", [])
        report_dates = recent_filings.get("reportDate", [])
        forms = recent_filings.get("form", [])
        primary_docs = recent_filings.get("primaryDocument", [])
        
        for i in range(len(forms)):
            form_type = forms[i]
            
            # Filter by filing type if specified
            if filing_types and form_type not in filing_types:
                continue
            
            accession = accession_numbers[i].replace("-", "")
            filing_date = filing_dates[i]
            report_date = report_dates[i] if i < len(report_dates) else None
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            
            # Construct document URL
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_doc}"
            
            filings.append({
                "filing_type": form_type,
                "filing_date": filing_date,
                "period_end": report_date,
                "url": doc_url,
                "accession_number": accession_numbers[i]
            })
            
            if len(filings) >= max_results:
                break
        
        return filings
    
    except Exception as e:
        print(f"Error fetching filings: {e}")
        return []


def download_filing(url: str, output_path: str) -> bool:
    """
    Download a filing from SEC EDGAR.
    
    Args:
        url: URL of the filing
        output_path: Local path to save the file
    
    Returns:
        True if successful, False otherwise
    """
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        return True
    
    except Exception as e:
        print(f"Error downloading filing: {e}")
        return False

