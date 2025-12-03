"""Financial ratio calculation service."""
from typing import Dict, Optional, Any
import math


class RatioCalculator:
    """Calculate financial ratios from financial statements."""
    
    def __init__(self, financial_data: Dict[str, Any]):
        """
        Initialize calculator with financial data.
        
        Args:
            financial_data: Dictionary containing income_statement, balance_sheet, cash_flow
        """
        self.income_statement = financial_data.get("income_statement", {})
        self.balance_sheet = financial_data.get("balance_sheet", {})
        self.cash_flow = financial_data.get("cash_flow", {})
        self.ratios = {}
    
    def _get_value(self, statement: Dict, key: str, period: str = "latest") -> Optional[float]:
        """
        Get a value from a statement.
        
        Args:
            statement: Statement dictionary
            key: Line item key
            period: Period label or "latest"
        
        Returns:
            Value or None
        """
        if key not in statement:
            return None
        
        values = statement[key]
        
        if not isinstance(values, dict):
            return values if isinstance(values, (int, float)) else None
        
        if period == "latest" and values:
            # Get the most recent period
            return list(values.values())[0]
        
        return values.get(period)
    
    def _get_latest_value(self, statement: Dict, key: str) -> Optional[float]:
        """Get the latest value for a line item."""
        return self._get_value(statement, key, "latest")
    
    def _get_prior_value(self, statement: Dict, key: str) -> Optional[float]:
        """Get the prior period value for a line item."""
        if key not in statement:
            return None
        
        values = statement[key]
        
        if not isinstance(values, dict) or len(values) < 2:
            return None
        
        # Get second most recent value
        return list(values.values())[1] if len(values) > 1 else None
    
    def calculate_all(self) -> Dict[str, Optional[float]]:
        """
        Calculate all financial ratios.
        
        Returns:
            Dictionary of ratio name to value
        """
        # Profitability ratios
        self.ratios["revenue_growth_yoy"] = self.calculate_revenue_growth()
        self.ratios["gross_margin"] = self.calculate_gross_margin()
        self.ratios["operating_margin"] = self.calculate_operating_margin()
        self.ratios["net_margin"] = self.calculate_net_margin()
        self.ratios["roa"] = self.calculate_roa()
        self.ratios["roe"] = self.calculate_roe()
        
        # Liquidity ratios
        self.ratios["current_ratio"] = self.calculate_current_ratio()
        self.ratios["quick_ratio"] = self.calculate_quick_ratio()
        self.ratios["dso"] = self.calculate_dso()
        self.ratios["inventory_turnover"] = self.calculate_inventory_turnover()
        
        # Leverage ratios
        self.ratios["debt_to_equity"] = self.calculate_debt_to_equity()
        self.ratios["net_debt_to_ebitda"] = self.calculate_net_debt_to_ebitda()
        self.ratios["interest_coverage"] = self.calculate_interest_coverage()
        
        # Cash flow ratios
        self.ratios["fcf"] = self.calculate_fcf()
        self.ratios["fcf_margin"] = self.calculate_fcf_margin()
        
        # Distress indicator
        self.ratios["altman_z_score"] = self.calculate_altman_z_score()
        
        return self.ratios
    
    # Profitability Ratios
    
    def calculate_revenue_growth(self) -> Optional[float]:
        """Revenue Growth (YoY) = (Revenue_current - Revenue_prior) / Revenue_prior"""
        revenue_current = self._get_latest_value(self.income_statement, "revenue")
        revenue_prior = self._get_prior_value(self.income_statement, "revenue")
        
        if revenue_current is None or revenue_prior is None or revenue_prior == 0:
            return None
        
        return (revenue_current - revenue_prior) / revenue_prior
    
    def calculate_gross_margin(self) -> Optional[float]:
        """Gross Margin = Gross_Profit / Revenue"""
        gross_profit = self._get_latest_value(self.income_statement, "gross_profit")
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        if gross_profit is None or revenue is None or revenue == 0:
            return None
        
        return gross_profit / revenue
    
    def calculate_operating_margin(self) -> Optional[float]:
        """Operating Margin = Operating_Income / Revenue"""
        operating_income = self._get_latest_value(self.income_statement, "operating_income")
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        if operating_income is None or revenue is None or revenue == 0:
            return None
        
        return operating_income / revenue
    
    def calculate_net_margin(self) -> Optional[float]:
        """Net Margin = Net_Income / Revenue"""
        net_income = self._get_latest_value(self.income_statement, "net_income")
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        if net_income is None or revenue is None or revenue == 0:
            return None
        
        return net_income / revenue
    
    def calculate_roa(self) -> Optional[float]:
        """Return on Assets = Net_Income / Average_Total_Assets"""
        net_income = self._get_latest_value(self.income_statement, "net_income")
        total_assets_current = self._get_latest_value(self.balance_sheet, "total_assets")
        total_assets_prior = self._get_prior_value(self.balance_sheet, "total_assets")
        
        if net_income is None or total_assets_current is None:
            return None
        
        # Use average assets if prior period available
        if total_assets_prior is not None:
            avg_assets = (total_assets_current + total_assets_prior) / 2
        else:
            avg_assets = total_assets_current
        
        if avg_assets == 0:
            return None
        
        return net_income / avg_assets
    
    def calculate_roe(self) -> Optional[float]:
        """Return on Equity = Net_Income / Average_Shareholders_Equity"""
        net_income = self._get_latest_value(self.income_statement, "net_income")
        equity_current = self._get_latest_value(self.balance_sheet, "total_equity")
        equity_prior = self._get_prior_value(self.balance_sheet, "total_equity")
        
        if net_income is None or equity_current is None:
            return None
        
        # Use average equity if prior period available
        if equity_prior is not None:
            avg_equity = (equity_current + equity_prior) / 2
        else:
            avg_equity = equity_current
        
        if avg_equity == 0:
            return None
        
        return net_income / avg_equity
    
    # Liquidity Ratios
    
    def calculate_current_ratio(self) -> Optional[float]:
        """Current Ratio = Current_Assets / Current_Liabilities"""
        current_assets = self._get_latest_value(self.balance_sheet, "current_assets")
        current_liabilities = self._get_latest_value(self.balance_sheet, "current_liabilities")
        
        if current_assets is None or current_liabilities is None or current_liabilities == 0:
            return None
        
        return current_assets / current_liabilities
    
    def calculate_quick_ratio(self) -> Optional[float]:
        """Quick Ratio = (Current_Assets - Inventories) / Current_Liabilities"""
        current_assets = self._get_latest_value(self.balance_sheet, "current_assets")
        inventories = self._get_latest_value(self.balance_sheet, "inventories") or 0
        current_liabilities = self._get_latest_value(self.balance_sheet, "current_liabilities")
        
        if current_assets is None or current_liabilities is None or current_liabilities == 0:
            return None
        
        return (current_assets - inventories) / current_liabilities
    
    def calculate_dso(self, days: int = 365) -> Optional[float]:
        """Days Sales Outstanding = (Accounts_Receivable / Revenue) * Days"""
        accounts_receivable = self._get_latest_value(self.balance_sheet, "accounts_receivable")
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        if accounts_receivable is None or revenue is None or revenue == 0:
            return None
        
        return (accounts_receivable / revenue) * days
    
    def calculate_inventory_turnover(self) -> Optional[float]:
        """Inventory Turnover = Cost_of_Goods_Sold / Average_Inventory"""
        cogs = self._get_latest_value(self.income_statement, "cost_of_revenue")
        inventory_current = self._get_latest_value(self.balance_sheet, "inventories")
        inventory_prior = self._get_prior_value(self.balance_sheet, "inventories")
        
        if cogs is None or inventory_current is None:
            return None
        
        # Use average inventory if prior period available
        if inventory_prior is not None:
            avg_inventory = (inventory_current + inventory_prior) / 2
        else:
            avg_inventory = inventory_current
        
        if avg_inventory == 0:
            return None
        
        return cogs / avg_inventory
    
    # Leverage Ratios
    
    def calculate_debt_to_equity(self) -> Optional[float]:
        """Debt-to-Equity = Total_Debt / Total_Equity"""
        short_term_debt = self._get_latest_value(self.balance_sheet, "short_term_debt") or 0
        long_term_debt = self._get_latest_value(self.balance_sheet, "long_term_debt") or 0
        total_equity = self._get_latest_value(self.balance_sheet, "total_equity")
        
        total_debt = short_term_debt + long_term_debt
        
        if total_equity is None or total_equity == 0:
            return None
        
        return total_debt / total_equity
    
    def calculate_net_debt_to_ebitda(self) -> Optional[float]:
        """Net Debt / EBITDA = (Short_Term_Debt + Long_Term_Debt - Cash) / EBITDA"""
        short_term_debt = self._get_latest_value(self.balance_sheet, "short_term_debt") or 0
        long_term_debt = self._get_latest_value(self.balance_sheet, "long_term_debt") or 0
        cash = self._get_latest_value(self.balance_sheet, "cash") or 0
        ebitda = self._get_latest_value(self.income_statement, "ebitda")
        
        # If EBITDA not directly available, calculate from operating income
        if ebitda is None:
            operating_income = self._get_latest_value(self.income_statement, "operating_income")
            # Note: Depreciation & Amortization would need to be extracted separately
            # For now, we'll return None if EBITDA is not directly available
            if operating_income is None:
                return None
            ebitda = operating_income  # Approximation
        
        net_debt = short_term_debt + long_term_debt - cash
        
        if ebitda == 0:
            return None
        
        return net_debt / ebitda
    
    def calculate_interest_coverage(self) -> Optional[float]:
        """Interest Coverage = EBIT / Interest_Expense"""
        ebit = self._get_latest_value(self.income_statement, "operating_income")
        interest_expense = self._get_latest_value(self.income_statement, "interest_expense")
        
        if ebit is None or interest_expense is None or interest_expense == 0:
            return None
        
        return ebit / interest_expense
    
    # Cash Flow Ratios
    
    def calculate_fcf(self) -> Optional[float]:
        """Free Cash Flow = Cash_From_Operations - Capital_Expenditures"""
        operating_cash_flow = self._get_latest_value(self.cash_flow, "operating_cash_flow")
        capex = self._get_latest_value(self.cash_flow, "capital_expenditures") or 0
        
        if operating_cash_flow is None:
            return None
        
        return operating_cash_flow - abs(capex)
    
    def calculate_fcf_margin(self) -> Optional[float]:
        """FCF Margin = FCF / Revenue"""
        fcf = self.calculate_fcf()
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        if fcf is None or revenue is None or revenue == 0:
            return None
        
        return fcf / revenue
    
    # Distress Indicator
    
    def calculate_altman_z_score(self) -> Optional[float]:
        """
        Altman Z-Score = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MVE/TL) + 1.0*(Sales/TA)
        
        Note: MVE (Market Value Equity) requires market data, which may not be available.
        We'll use book value of equity as a fallback.
        """
        # Get balance sheet items
        current_assets = self._get_latest_value(self.balance_sheet, "current_assets")
        current_liabilities = self._get_latest_value(self.balance_sheet, "current_liabilities")
        total_assets = self._get_latest_value(self.balance_sheet, "total_assets")
        retained_earnings = self._get_latest_value(self.balance_sheet, "retained_earnings")
        total_liabilities = self._get_latest_value(self.balance_sheet, "total_liabilities")
        total_equity = self._get_latest_value(self.balance_sheet, "total_equity")
        
        # Get income statement items
        ebit = self._get_latest_value(self.income_statement, "operating_income")
        revenue = self._get_latest_value(self.income_statement, "revenue")
        
        # Check if we have minimum required data
        if None in [current_assets, current_liabilities, total_assets, ebit, revenue]:
            return None
        
        if total_assets == 0:
            return None
        
        # Calculate components
        working_capital = current_assets - current_liabilities
        x1 = 1.2 * (working_capital / total_assets)
        
        # Retained earnings (may not be available)
        if retained_earnings is not None:
            x2 = 1.4 * (retained_earnings / total_assets)
        else:
            x2 = 0
        
        x3 = 3.3 * (ebit / total_assets)
        
        # Market value of equity (use book value as fallback)
        if total_equity is not None and total_liabilities is not None and total_liabilities != 0:
            x4 = 0.6 * (total_equity / total_liabilities)
        else:
            x4 = 0
        
        x5 = 1.0 * (revenue / total_assets)
        
        z_score = x1 + x2 + x3 + x4 + x5
        
        return z_score


def calculate_ratios(financial_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Calculate all financial ratios from financial data.
    
    Args:
        financial_data: Dictionary containing financial statements
    
    Returns:
        Dictionary of calculated ratios
    """
    calculator = RatioCalculator(financial_data)
    return calculator.calculate_all()

















