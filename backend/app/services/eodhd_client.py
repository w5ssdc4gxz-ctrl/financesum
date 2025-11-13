"""EODHD API client for fetching fundamental data."""
import requests
from typing import Dict, Optional, Any
from app.config import get_settings

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
                    return {
                        "ticker": info.get("Code"),
                        "name": info.get("Name"),
                        "exchange": info.get("Exchange"),
                        "cik": info.get("CIK"),
                        "sector": info.get("Sector"),
                        "industry": info.get("Industry"),
                        "country": info.get("CountryName", "USA")
                    }
            except:
                continue
        
        return None


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











