"""EODHD API client for fetching fundamental data."""
import requests
import time
from typing import Dict, Optional, Any
from urllib.parse import quote
from app.config import get_settings
from app.services.country_resolver import normalize_country


settings = get_settings()


class EODHDAccessError(Exception):
    """Raised when EODHD rejects a request due to auth or plan issues."""


class EODHDClientError(Exception):
    """Raised for unexpected EODHD client failures."""


class EODHDClient:
    """Client for EODHD Fundamentals API."""
    
    BASE_URL = "https://eodhd.com/api"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize EODHD client.
        
        Args:
            api_key: EODHD API key (optional, uses config if not provided)
        """
        self.api_key = api_key or settings.eodhd_api_key
        if not self.api_key:
            raise ValueError("EODHD API key is required")
    
    def get_fundamentals(
        self,
        symbol: str,
        exchange: str = "US",
        filter_param: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get fundamental data for a company.
        
        Args:
            symbol: Stock symbol (e.g., 'AAPL')
            exchange: Exchange code (e.g., 'US' for NASDAQ/NYSE)
            filter_param: Optional filter for specific sections
                         (e.g., 'Financials::Balance_Sheet::quarterly')
        
        Returns:
            Dictionary with fundamental data
        """
        ticker = f"{symbol}.{exchange}"
        url = f"{self.BASE_URL}/fundamentals/{ticker}"
        
        params = {
            "api_token": self.api_key,
            "fmt": "json"
        }
        
        if filter_param:
            params["filter"] = filter_param
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                raise ValueError(f"Symbol {ticker} not found") from exc
            if status_code == 403:
                demo_hint = ""
                if (self.api_key or "").lower() == "demo":
                    demo_hint = " The demo API token only supports a handful of tickers; set EODHD_API_KEY to your paid token."
                raise EODHDAccessError(f"EODHD rejected the request (HTTP 403).{demo_hint}") from exc
            raise EODHDClientError(f"EODHD request failed with HTTP {status_code or 'unknown'}") from exc
        except Exception as exc:
            raise EODHDClientError(f"Error fetching fundamentals: {exc}") from exc
    
    def get_financial_statements(
        self,
        symbol: str,
        exchange: str = "US"
    ) -> Dict[str, Any]:
        """
        Get complete financial statements (Income Statement, Balance Sheet, Cash Flow).
        
        Args:
            symbol: Stock symbol
            exchange: Exchange code
        
        Returns:
            Dictionary with financial statements
        """
        data = self.get_fundamentals(symbol, exchange)
        
        return {
            "general": data.get("General", {}),
            "highlights": data.get("Highlights", {}),
            "income_statement": {
                "quarterly": data.get("Financials", {}).get("Income_Statement", {}).get("quarterly", {}),
                "yearly": data.get("Financials", {}).get("Income_Statement", {}).get("yearly", {})
            },
            "balance_sheet": {
                "quarterly": data.get("Financials", {}).get("Balance_Sheet", {}).get("quarterly", {}),
                "yearly": data.get("Financials", {}).get("Balance_Sheet", {}).get("yearly", {})
            },
            "cash_flow": {
                "quarterly": data.get("Financials", {}).get("Cash_Flow", {}).get("quarterly", {}),
                "yearly": data.get("Financials", {}).get("Cash_Flow", {}).get("yearly", {})
            },
            "earnings": data.get("Earnings", {}),
        }
    
    def get_company_info(
        self,
        symbol: str,
        exchange: str = "US"
    ) -> Dict[str, Any]:
        """
        Get general company information.
        
        Args:
            symbol: Stock symbol
            exchange: Exchange code
        
        Returns:
            Dictionary with company info
        """
        data = self.get_fundamentals(symbol, exchange, filter_param="General")
        return data.get("General", {})
    
    def search_symbol(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Search for a symbol by company name or ticker.
        
        Args:
            query: Search query (ticker or company name)
        
        Returns:
            Company information if found
        """
        # Try as direct ticker first
        query_upper = query.upper()
        
        # Common US exchanges to try
        exchanges = ["US", "NASDAQ", "NYSE"]
        
        for exchange in exchanges:
            try:
                info = self.get_company_info(query_upper, exchange)
                if info:
                    country = extract_country_from_eodhd(info)
                    return {
                        "ticker": info.get("Code"),
                        "name": info.get("Name"),
                        "exchange": info.get("Exchange"),
                        "cik": info.get("CIK"),
                        "sector": info.get("Sector"),
                        "industry": info.get("Industry"),
                        "country": country,
                    }
            except:
                continue
        
        return None

    def search_symbols(self, query: str, limit: int = 10) -> list[Dict[str, Any]]:
        """
        Search for symbols by company name or ticker using EODHD's search endpoint.

        Returns a best-effort list of results with at least `ticker`, `name`, and `exchange`.
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        url = f"{self.BASE_URL}/search/{quote(cleaned)}"
        params = {
            "api_token": self.api_key,
            "fmt": "json",
        }

        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 403:
                demo_hint = ""
                if (self.api_key or "").lower() == "demo":
                    demo_hint = " The demo API token may not support full symbol search; set EODHD_API_KEY to a paid token."
                raise EODHDAccessError(f"EODHD rejected the search request (HTTP 403).{demo_hint}") from exc
            raise EODHDClientError(f"EODHD search request failed with HTTP {status_code or 'unknown'}") from exc
        except Exception as exc:
            raise EODHDClientError(f"Error searching symbols: {exc}") from exc

        if not isinstance(payload, list):
            return []

        results: list[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            ticker = item.get("Code") or item.get("code") or item.get("ticker") or item.get("symbol")
            name = item.get("Name") or item.get("name") or item.get("title")
            exchange = item.get("Exchange") or item.get("exchange") or item.get("exch")
            country = item.get("Country") or item.get("country")

            if not ticker or not name:
                continue

            results.append(
                {
                    "ticker": str(ticker).strip().upper(),
                    "name": str(name).strip(),
                    "exchange": str(exchange).strip().upper() if exchange else None,
                    "country": normalize_country(country) or country,
                    "type": item.get("Type") or item.get("type"),
                }
            )

            if len(results) >= max(1, int(limit or 10)):
                break

        return results


def normalize_eodhd_to_internal_format(eodhd_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize EODHD data format to our internal financial data format.
    
    Args:
        eodhd_data: Data from EODHD API
    
    Returns:
        Normalized data compatible with ratio calculator
    """
    # Extract latest quarterly and yearly data
    income_quarterly = eodhd_data.get("income_statement", {}).get("quarterly", {})
    balance_quarterly = eodhd_data.get("balance_sheet", {}).get("quarterly", {})
    cashflow_quarterly = eodhd_data.get("cash_flow", {}).get("quarterly", {})
    
    # Get most recent periods (EODHD returns dates as keys)
    def get_latest_values(data: Dict, field: str, num_periods: int = 2) -> Dict:
        """Extract latest N periods for a field."""
        if not data:
            return {}
        
        # Sort by date descending
        sorted_dates = sorted(data.keys(), reverse=True)[:num_periods]
        
        result = {}
        for i, date in enumerate(sorted_dates):
            if field in data[date]:
                value = data[date].get(field)
                if value:
                    try:
                        result[date] = float(value)
                    except (ValueError, TypeError):
                        pass
        
        return result
    
    # Normalize to our format
    normalized = {
        "income_statement": {
            # Revenue
            "revenue": get_latest_values(income_quarterly, "totalRevenue"),
            "cost_of_revenue": get_latest_values(income_quarterly, "costOfRevenue"),
            "gross_profit": get_latest_values(income_quarterly, "grossProfit"),
            "operating_income": get_latest_values(income_quarterly, "operatingIncome"),
            "net_income": get_latest_values(income_quarterly, "netIncome"),
            "ebitda": get_latest_values(income_quarterly, "ebitda"),
            "interest_expense": get_latest_values(income_quarterly, "interestExpense"),
            "operating_expenses": get_latest_values(income_quarterly, "totalOperatingExpenses"),
        },
        "balance_sheet": {
            "total_assets": get_latest_values(balance_quarterly, "totalAssets"),
            "current_assets": get_latest_values(balance_quarterly, "totalCurrentAssets"),
            "cash": get_latest_values(balance_quarterly, "cash"),
            "accounts_receivable": get_latest_values(balance_quarterly, "netReceivables"),
            "inventories": get_latest_values(balance_quarterly, "inventory"),
            "total_liabilities": get_latest_values(balance_quarterly, "totalLiab"),
            "current_liabilities": get_latest_values(balance_quarterly, "totalCurrentLiabilities"),
            "short_term_debt": get_latest_values(balance_quarterly, "shortTermDebt"),
            "long_term_debt": get_latest_values(balance_quarterly, "longTermDebt"),
            "total_equity": get_latest_values(balance_quarterly, "totalStockholderEquity"),
            "retained_earnings": get_latest_values(balance_quarterly, "retainedEarnings"),
        },
        "cash_flow": {
            "operating_cash_flow": get_latest_values(cashflow_quarterly, "totalCashFromOperatingActivities"),
            "capital_expenditures": get_latest_values(cashflow_quarterly, "capitalExpenditures"),
            "investing_cash_flow": get_latest_values(cashflow_quarterly, "totalCashflowsFromInvestingActivities"),
            "financing_cash_flow": get_latest_values(cashflow_quarterly, "totalCashFromFinancingActivities"),
        }
    }
    
    return normalized


def get_eodhd_client() -> EODHDClient:
    """Get EODHD client instance."""
    return EODHDClient()


US_EQUIVALENTS = {
    "US",
    "USA",
    "UNITED STATES",
    "UNITED STATES OF AMERICA",
    "UNITEDSTATES",
    "UNITEDSTATESOFAMERICA",
}


def _normalize_country_value(value: Optional[str]) -> Optional[str]:
    """Return a trimmed country string or None."""
    if not value:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _is_us_country(value: Optional[str]) -> bool:
    normalized = (_normalize_country_value(value) or "").replace(".", "").replace(" ", "").upper()
    return normalized in US_EQUIVALENTS


def extract_country_from_eodhd(info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not info:
        return None

    country_fields = [
        "CountryName",
        "Country",
        "CountryISO",
        "CountryISO3",
        "CountryISOAlpha3",
    ]

    candidates: list[str] = []
    for field in country_fields:
        value = _normalize_country_value(info.get(field))
        if not value:
            continue
        candidates.append(value.upper() if "ISO" in field.upper() else value)

    # ISIN prefixes are ISO2 country codes (often a strong domicile hint).
    isin = _normalize_country_value(info.get("ISIN"))
    if isin:
        cleaned_isin = str(isin).strip().upper()
        if len(cleaned_isin) >= 2:
            prefix = cleaned_isin[:2]
            if prefix.isalpha():
                candidates.append(prefix)

    address = info.get("AddressData") or info.get("Address") or {}
    if isinstance(address, dict):
        address_country = _normalize_country_value(address.get("Country"))
        if address_country:
            candidates.append(address_country)

    normalized_candidates: list[str] = []
    for raw in candidates:
        normalized = normalize_country(raw) or raw
        if normalized and normalized not in normalized_candidates:
            normalized_candidates.append(normalized)

    if not normalized_candidates:
        return None

    # Prefer a non-US value if available (ADR / US listings can otherwise look like US).
    for candidate in normalized_candidates:
        if not _is_us_country(candidate):
            return candidate

    return normalized_candidates[0]


def should_hydrate_country(country: Optional[str]) -> bool:
    """Return True when country is missing or looks like an unresolved US placeholder."""
    return _normalize_country_value(country) is None or _is_us_country(country)


def hydrate_country_with_eodhd(ticker: str, exchange: Optional[str] = None) -> Optional[str]:
    if not ticker:
        return None
    try:
        client = EODHDClient()
        exchange_value = (exchange or "US").strip().upper()
        if exchange_value in {"NASDAQ", "NYSE", "AMEX", "ARCA", "NMS", "NYQ"}:
            exchange_value = "US"
        info = client.get_company_info(ticker, exchange_value)
        return extract_country_from_eodhd(info)
    except Exception as exc:  # noqa: BLE001
        print(f"Country hydration failed for {ticker}: {exc}")
        return None


def hydrate_country_with_retry(
    ticker: str,
    exchange: Optional[str] = None,
    max_retries: int = 2,
    base_delay: float = 0.5
) -> Optional[str]:
    """
    Hydrate country with exponential backoff retry.

    Args:
        ticker: Stock ticker symbol
        exchange: Exchange code (defaults to "US")
        max_retries: Maximum retry attempts (default 2)
        base_delay: Base delay in seconds for exponential backoff

    Returns:
        Country string or None if all attempts fail
    """
    if not ticker:
        return None

    for attempt in range(max_retries + 1):
        result = hydrate_country_with_eodhd(ticker, exchange)
        if result:
            return result

        if attempt < max_retries:
            delay = base_delay * (2 ** attempt)  # 0.5s, 1s, 2s
            time.sleep(delay)

    return None
