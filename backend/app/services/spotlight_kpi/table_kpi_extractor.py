"""Table-based KPI extraction for SEC filings.

This module extracts operating KPIs from tabular structures in filings,
particularly "Key Metrics" or "Operating Metrics" tables that companies
often include in MD&A sections.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .types import SpotlightKpiCandidate


_KPI_TABLE_HEADERS = (
    "key metrics",
    "operating metrics",
    "key performance indicators",
    "selected operating data",
    "supplemental data",
    "non-gaap measures",
    "business metrics",
    "operating statistics",
    "key operating data",
    "quarterly metrics",
    "operating highlights",
)

_KPI_ROW_PATTERNS: List[Tuple[re.Pattern[str], str, str, int]] = [
    (re.compile(r"^monthly\s+active\s+users?(?:\s*\(MAU\))?", re.IGNORECASE), "Monthly Active Users (MAUs)", "users", 100),
    (re.compile(r"^daily\s+active\s+users?(?:\s*\(DAU\))?", re.IGNORECASE), "Daily Active Users (DAUs)", "users", 100),
    (re.compile(r"^subscribers?(?:\s+\(.*?\))?$", re.IGNORECASE), "Subscribers", "subscribers", 90),
    (re.compile(r"^paid\s+(?:subscribers?|members?)", re.IGNORECASE), "Paid Subscribers", "subscribers", 95),
    (re.compile(r"^gross\s+merchandise\s+volume(?:\s*\(GMV\))?", re.IGNORECASE), "Gross Merchandise Volume (GMV)", "$", 92),
    (re.compile(r"^total\s+payment\s+volume(?:\s*\(TPV\))?", re.IGNORECASE), "Total Payment Volume (TPV)", "$", 92),
    (re.compile(r"^annual\s+recurring\s+revenue(?:\s*\(ARR\))?", re.IGNORECASE), "Annual Recurring Revenue (ARR)", "$", 90),
    (re.compile(r"^monthly\s+recurring\s+revenue(?:\s*\(MRR\))?", re.IGNORECASE), "Monthly Recurring Revenue (MRR)", "$", 88),
    (re.compile(r"^net\s+(?:revenue|dollar)\s+retention(?:\s*\(N[RD]R\))?", re.IGNORECASE), "Net Revenue Retention (NRR)", "%", 88),
    (re.compile(r"^bookings?(?:\s+\(.*?\))?$", re.IGNORECASE), "Bookings", "$", 85),
    (re.compile(r"^gross\s+bookings?", re.IGNORECASE), "Gross Bookings", "$", 86),
    (re.compile(r"^(?:remaining\s+)?performance\s+obligations?(?:\s*\(RPO\))?", re.IGNORECASE), "Remaining Performance Obligations (RPO)", "$", 84),
    (re.compile(r"^backlog", re.IGNORECASE), "Backlog", "$", 85),
    (re.compile(r"^assets?\s+under\s+management(?:\s*\(AUM\))?", re.IGNORECASE), "Assets Under Management (AUM)", "$", 90),
    (re.compile(r"^vehicles?\s+delivered", re.IGNORECASE), "Vehicles Delivered", "units", 92),
    (re.compile(r"^units?\s+(?:shipped|sold|delivered)", re.IGNORECASE), "Units Shipped", "units", 85),
    (re.compile(r"^orders?(?:\s+\(.*?\))?$", re.IGNORECASE), "Orders", "orders", 80),
    (re.compile(r"^transactions?", re.IGNORECASE), "Transactions", "transactions", 78),
    (re.compile(r"^trips?(?:\s+\(.*?\))?$", re.IGNORECASE), "Trips", "trips", 88),
    (re.compile(r"^rides?(?:\s+\(.*?\))?$", re.IGNORECASE), "Rides", "rides", 88),
    (re.compile(r"^(?:store|location|restaurant)\s+count", re.IGNORECASE), "Store Count", "stores", 82),
    (re.compile(r"^(?:number\s+of\s+)?stores?(?:\s+at\s+(?:period\s+)?end)?", re.IGNORECASE), "Store Count", "stores", 80),
    (re.compile(r"^active\s+(?:customers?|accounts?|merchants?)", re.IGNORECASE), "Active Customers", "customers", 78),
    (re.compile(r"^(?:average\s+)?revenue\s+per\s+user(?:\s*\(ARPU\))?", re.IGNORECASE), "Average Revenue Per User (ARPU)", "$", 85),
    (re.compile(r"^take\s+rate", re.IGNORECASE), "Take Rate", "%", 88),
    (re.compile(r"^churn(?:\s+rate)?", re.IGNORECASE), "Churn Rate", "%", 85),
    (re.compile(r"^(?:customer\s+)?retention(?:\s+rate)?", re.IGNORECASE), "Retention Rate", "%", 84),
    (re.compile(r"^occupancy(?:\s+rate)?", re.IGNORECASE), "Occupancy Rate", "%", 85),
    (re.compile(r"^load\s+factor", re.IGNORECASE), "Load Factor", "%", 85),
    (re.compile(r"^RevPAR", re.IGNORECASE), "RevPAR", "$", 84),
    (re.compile(r"^same[- ]store\s+sales", re.IGNORECASE), "Same-Store Sales", "%", 86),
    (re.compile(r"^comparable\s+(?:store\s+)?sales", re.IGNORECASE), "Comparable Sales", "%", 86),
    (re.compile(r"^gross\s+written\s+premiums?(?:\s*\(GWP\))?", re.IGNORECASE), "Gross Written Premiums (GWP)", "$", 88),
    (re.compile(r"^combined\s+ratio", re.IGNORECASE), "Combined Ratio", "%", 86),
    (re.compile(r"^(?:medical\s+)?loss\s+ratio", re.IGNORECASE), "Loss Ratio", "%", 84),
    (re.compile(r"^policies?\s+in\s+force", re.IGNORECASE), "Policies in Force", "policies", 85),
    (re.compile(r"^wireless\s+(?:subscribers?|customers?)", re.IGNORECASE), "Wireless Subscribers", "subscribers", 88),
    (re.compile(r"^(?:registered\s+)?players?", re.IGNORECASE), "Registered Players", "players", 82),
    (re.compile(r"^(?:loan\s+)?originations?", re.IGNORECASE), "Loan Originations", "$", 85),
    (re.compile(r"^net\s+interest\s+margin(?:\s*\(NIM\))?", re.IGNORECASE), "Net Interest Margin (NIM)", "%", 84),
    (re.compile(r"^(?:registered\s+)?users?(?:\s+\(.*?\))?$", re.IGNORECASE), "Registered Users", "users", 70),
    (re.compile(r"^(?:total\s+)?deposits?", re.IGNORECASE), "Total Deposits", "$", 78),
    (re.compile(r"^(?:funds?\s+from\s+operations?|FFO)", re.IGNORECASE), "Funds from Operations (FFO)", "$", 86),
]


def _parse_table_value(cell: str) -> Optional[float]:
    if not cell:
        return None
    
    cleaned = cell.strip()
    if not cleaned or cleaned in ("-", "—", "N/A", "n/a", "NM", "nm"):
        return None
    
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    
    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace("$", "")
    cleaned = cleaned.replace("%", "")
    cleaned = cleaned.strip()
    
    multiplier = 1.0
    lower = cleaned.lower()
    if lower.endswith("b") or lower.endswith("bn") or "billion" in lower:
        multiplier = 1_000_000_000.0
        cleaned = re.sub(r"(?i)\s*(b|bn|billion)s?$", "", cleaned)
    elif lower.endswith("m") or lower.endswith("mn") or "million" in lower:
        multiplier = 1_000_000.0
        cleaned = re.sub(r"(?i)\s*(m|mn|million)s?$", "", cleaned)
    elif lower.endswith("k") or "thousand" in lower:
        multiplier = 1_000.0
        cleaned = re.sub(r"(?i)\s*(k|thousand)s?$", "", cleaned)
    elif lower.endswith("t") or "trillion" in lower:
        multiplier = 1_000_000_000_000.0
        cleaned = re.sub(r"(?i)\s*(t|trillion)s?$", "", cleaned)
    
    try:
        value = float(cleaned.strip()) * multiplier
        return -value if is_negative else value
    except ValueError:
        return None


def _is_kpi_table_header(text: str) -> bool:
    if not text:
        return False
    lower = text.lower().strip()
    return any(header in lower for header in _KPI_TABLE_HEADERS)


def _extract_kpis_from_table_rows(
    rows: List[List[str]],
    *,
    raw_lines: Optional[List[str]] = None,
    table_context: str = "",
) -> List[Tuple[str, str, str, float, int]]:
    results: List[Tuple[str, str, str, float, int]] = []
    
    if not rows or len(rows) < 2:
        return results
    
    header_row_idx = 0
    for idx, row in enumerate(rows[:3]):
        row_text = " ".join(str(c) for c in row if c).lower()
        if any(p in row_text for p in ("period", "quarter", "year", "q1", "q2", "q3", "q4", "fy", "ytd", "20")):
            header_row_idx = idx
            break
    
    for row_idx, row in enumerate(rows):
        if row_idx <= header_row_idx:
            continue
        if not row or len(row) < 2:
            continue
        
        label = str(row[0] or "").strip()
        if not label or len(label) < 2:
            continue
        raw_quote = ""
        if raw_lines and row_idx < len(raw_lines):
            raw_quote = str(raw_lines[row_idx] or "").strip()
        if not raw_quote:
            raw_quote = " ".join(str(c or "") for c in row if c).strip()
        
        for pattern, kpi_name, unit, priority in _KPI_ROW_PATTERNS:
            if pattern.match(label):
                for cell_idx in range(1, len(row)):
                    value = _parse_table_value(str(row[cell_idx] or ""))
                    if value is not None:
                        results.append((kpi_name, unit, raw_quote, value, priority))
                        break
                break
    
    return results


def extract_kpis_from_text_tables(
    text: str,
    company_name: str,
    max_results: int = 5,
) -> List[SpotlightKpiCandidate]:
    if not text:
        return []
    
    candidates: List[Tuple[int, SpotlightKpiCandidate]] = []
    seen_names: set[str] = set()
    
    lines = text.split("\n")
    
    in_metrics_section = False
    section_start = 0
    section_lines: List[str] = []
    
    for idx, line in enumerate(lines):
        if _is_kpi_table_header(line):
            in_metrics_section = True
            section_start = idx
            section_lines = []
            continue
        
        if in_metrics_section:
            section_lines.append(line)
            
            if len(section_lines) > 50:
                in_metrics_section = False
                section_lines = []
            elif line.strip() and not any(c.isalnum() for c in line):
                in_metrics_section = False
                _process_section(section_lines, candidates, seen_names, company_name)
                section_lines = []
    
    if section_lines:
        _process_section(section_lines, candidates, seen_names, company_name)
    
    _process_inline_metrics(lines, candidates, seen_names, company_name)
    
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in candidates[:max_results]]


def _process_section(
    lines: List[str],
    candidates: List[Tuple[int, SpotlightKpiCandidate]],
    seen_names: set[str],
    company_name: str,
) -> None:
    rows: List[List[str]] = []
    raw_lines: List[str] = []
    for line in lines:
        raw_line = str(line or "").strip()
        if not raw_line:
            continue
        if "\t" in raw_line:
            cells = raw_line.split("\t")
        else:
            cells = re.split(r"\s{2,}", raw_line)
        if cells:
            rows.append([c.strip() for c in cells])
            raw_lines.append(raw_line)
    
    if not rows:
        return
    
    kpis = _extract_kpis_from_table_rows(rows, raw_lines=raw_lines)
    for kpi_name, unit, quote, value, priority in kpis:
        name_key = kpi_name.lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)

        quote = re.sub(r"\s+", " ", (quote or "").strip())
        if len(quote) > 260:
            quote = quote[:260].rstrip()
        
        candidate: SpotlightKpiCandidate = {
            "name": kpi_name,
            "value": float(value),
            "unit": unit,
            "prior_value": None,
            "chart_type": "metric",
            "description": f"Extracted from key metrics table in {company_name} filing",
            "source_quote": quote,
            "why_company_specific": "Found in company's key metrics/operating data table",
            "evidence": [{"page": 1, "quote": quote, "type": "value"}],
            "confidence": 0.78,
            "ban_flags": ["table_extraction"],
        }
        candidates.append((priority, candidate))


def _process_inline_metrics(
    lines: List[str],
    candidates: List[Tuple[int, SpotlightKpiCandidate]],
    seen_names: set[str],
    company_name: str,
) -> None:
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 10:
            continue
        
        for pattern, kpi_name, unit, priority in _KPI_ROW_PATTERNS:
            if priority < 85:
                continue
            
            match = pattern.search(stripped)
            if not match:
                continue
            
            name_key = kpi_name.lower()
            if name_key in seen_names:
                continue
            
            number_match = re.search(
                r"(?:of|:|was|is|were|reached|totaled|at)\s*\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
                r"(million|billion|thousand|M|B|K)?",
                stripped,
                re.IGNORECASE,
            )
            
            if not number_match:
                number_match = re.search(
                    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|thousand|M|B|K)?",
                    stripped,
                    re.IGNORECASE,
                )
            
            if not number_match:
                continue
            
            value = _parse_table_value(number_match.group(0))
            if value is None:
                continue
            
            seen_names.add(name_key)
            
            quote = stripped[:220].rstrip() if len(stripped) > 220 else stripped
            
            candidate: SpotlightKpiCandidate = {
                "name": kpi_name,
                "value": float(value),
                "unit": unit,
                "prior_value": None,
                "chart_type": "metric",
                "description": None,
                "source_quote": quote,
                "why_company_specific": "Operating metric mentioned in filing text",
                "evidence": [{"page": 1, "quote": quote, "type": "value"}],
                "confidence": 0.72,
                "ban_flags": ["inline_table_extraction"],
            }
            candidates.append((priority - 5, candidate))
            break
