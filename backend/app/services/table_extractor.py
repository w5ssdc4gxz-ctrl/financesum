"""Financial table extraction and normalization service."""
import re
from typing import Dict, List, Optional, Any
from datetime import datetime


class FinancialTableExtractor:
    """Extract and normalize financial tables."""
    
    # Canonical line item mappings
    LINE_ITEM_MAPPINGS = {
        # Income Statement
        "revenue": [
            "total revenue", "revenues", "net revenues", "total net revenues",
            "sales", "net sales", "total sales"
        ],
        "cost_of_revenue": [
            "cost of revenue", "cost of revenues", "cost of sales",
            "cost of goods sold", "cogs"
        ],
        "gross_profit": [
            "gross profit", "gross income"
        ],
        "operating_expenses": [
            "operating expenses", "total operating expenses",
            "operating costs and expenses"
        ],
        "operating_income": [
            "operating income", "income from operations",
            "operating profit", "ebit"
        ],
        "net_income": [
            "net income", "net earnings", "net profit",
            "net income attributable to", "income from continuing operations"
        ],
        "ebitda": [
            "ebitda", "earnings before interest"
        ],
        "interest_expense": [
            "interest expense", "interest cost", "finance costs"
        ],
        
        # Balance Sheet
        "total_assets": [
            "total assets"
        ],
        "current_assets": [
            "total current assets", "current assets"
        ],
        "cash": [
            "cash and cash equivalents", "cash", "cash & equivalents"
        ],
        "accounts_receivable": [
            "accounts receivable", "trade receivables", "receivables"
        ],
        "inventories": [
            "inventories", "inventory"
        ],
        "total_liabilities": [
            "total liabilities"
        ],
        "current_liabilities": [
            "total current liabilities", "current liabilities"
        ],
        "accounts_payable": [
            "accounts payable", "trade payables"
        ],
        "short_term_debt": [
            "short-term debt", "short term debt", "current portion of long-term debt"
        ],
        "long_term_debt": [
            "long-term debt", "long term debt"
        ],
        "total_equity": [
            "total equity", "shareholders' equity", "stockholders' equity",
            "total shareholders' equity", "total stockholders' equity"
        ],
        "retained_earnings": [
            "retained earnings", "accumulated earnings"
        ],
        
        # Cash Flow Statement
        "operating_cash_flow": [
            "cash from operating activities", "net cash provided by operating activities",
            "operating cash flow", "cash flow from operations"
        ],
        "investing_cash_flow": [
            "cash from investing activities", "net cash used in investing activities"
        ],
        "financing_cash_flow": [
            "cash from financing activities", "net cash used in financing activities"
        ],
        "capital_expenditures": [
            "capital expenditures", "capex", "purchases of property and equipment",
            "additions to property, plant and equipment"
        ],
        "free_cash_flow": [
            "free cash flow", "fcf"
        ]
    }
    
    def __init__(self):
        """Initialize extractor."""
        pass
    
    def extract_financial_statements(
        self,
        tables: List[List[List[str]]]
    ) -> Dict[str, Any]:
        """
        Extract financial statements from table data.
        
        Args:
            tables: List of tables (each table is a list of rows)
        
        Returns:
            Dictionary with normalized financial data
        """
        financial_data = {
            "income_statement": {},
            "balance_sheet": {},
            "cash_flow": {}
        }
        
        for table in tables:
            # Analyze table to determine statement type
            statement_type = self._identify_statement_type(table)
            
            if statement_type:
                # Extract line items
                line_items = self._extract_line_items(table)
                financial_data[statement_type].update(line_items)
        
        return financial_data
    
    def _identify_statement_type(self, table: List[List[str]]) -> Optional[str]:
        """
        Identify what type of financial statement a table represents.
        
        Args:
            table: Table data
        
        Returns:
            Statement type: 'income_statement', 'balance_sheet', 'cash_flow', or None
        """
        # Convert table to lowercase text
        table_text = " ".join([" ".join(row) for row in table]).lower()
        
        # Check for statement type indicators
        if any(keyword in table_text for keyword in ["revenue", "gross profit", "operating income", "net income"]):
            return "income_statement"
        elif any(keyword in table_text for keyword in ["total assets", "liabilities", "shareholders' equity"]):
            return "balance_sheet"
        elif any(keyword in table_text for keyword in ["operating activities", "investing activities", "financing activities"]):
            return "cash_flow"
        
        return None
    
    def _extract_line_items(self, table: List[List[str]]) -> Dict[str, Any]:
        """
        Extract and map line items from a table to canonical names.
        
        Args:
            table: Table data
        
        Returns:
            Dictionary mapping canonical names to values
        """
        line_items = {}
        
        if not table or len(table) < 2:
            return line_items
        
        # Try to identify header row (usually first row with dates/periods)
        header_row = table[0]
        periods = self._extract_periods(header_row)
        
        # Process each data row
        for row in table[1:]:
            if not row:
                continue
            
            # First cell is usually the line item label
            label = row[0].lower().strip() if row else ""
            
            # Match to canonical name
            canonical_name = self._match_line_item(label)
            
            if canonical_name:
                # Extract values for each period
                values = {}
                for i, period in enumerate(periods):
                    if i + 1 < len(row):
                        value = self._parse_value(row[i + 1])
                        if value is not None:
                            values[period] = value
                
                if values:
                    line_items[canonical_name] = values
        
        return line_items
    
    def _extract_periods(self, header_row: List[str]) -> List[str]:
        """
        Extract period/date labels from header row.
        
        Args:
            header_row: Header row data
        
        Returns:
            List of period labels
        """
        periods = []
        
        for cell in header_row[1:]:  # Skip first cell (usually label column)
            # Look for date patterns
            cell_clean = cell.strip()
            
            # Try to parse as date
            date_patterns = [
                r"\d{1,2}/\d{1,2}/\d{2,4}",  # MM/DD/YYYY
                r"\w+ \d{1,2},? \d{4}",       # Month DD, YYYY
                r"\d{4}",                      # YYYY
                r"Q[1-4] \d{4}"                # Q1 2024
            ]
            
            for pattern in date_patterns:
                if re.search(pattern, cell_clean):
                    periods.append(cell_clean)
                    break
            else:
                # If no date pattern, use as-is if not empty
                if cell_clean:
                    periods.append(cell_clean)
        
        return periods
    
    def _match_line_item(self, label: str) -> Optional[str]:
        """
        Match a line item label to canonical name.
        
        Args:
            label: Line item label
        
        Returns:
            Canonical name or None
        """
        label_clean = label.lower().strip()
        
        # Remove common prefixes/suffixes
        label_clean = re.sub(r"^\s*[\$\(\)]+\s*", "", label_clean)
        label_clean = re.sub(r"\s*[\$\(\)]+\s*$", "", label_clean)
        
        # Try exact or partial match
        for canonical_name, aliases in self.LINE_ITEM_MAPPINGS.items():
            for alias in aliases:
                if alias in label_clean or label_clean in alias:
                    return canonical_name
        
        return None
    
    def _parse_value(self, value_str: str) -> Optional[float]:
        """
        Parse a numeric value from string.
        
        Args:
            value_str: String containing numeric value
        
        Returns:
            Numeric value or None
        """
        if not value_str or not isinstance(value_str, str):
            return None
        
        # Clean the string
        value_clean = value_str.strip()
        
        # Remove common formatting
        value_clean = value_clean.replace(",", "")
        value_clean = value_clean.replace("$", "")
        value_clean = value_clean.replace("(", "-")
        value_clean = value_clean.replace(")", "")
        
        # Try to parse
        try:
            value = float(value_clean)
            return value
        except (ValueError, AttributeError):
            return None


def extract_financial_data(tables: List[List[List[str]]]) -> Dict[str, Any]:
    """
    Extract and normalize financial data from tables.
    
    Args:
        tables: List of tables extracted from PDF
    
    Returns:
        Normalized financial data
    """
    extractor = FinancialTableExtractor()
    return extractor.extract_financial_statements(tables)










