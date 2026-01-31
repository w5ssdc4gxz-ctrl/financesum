"""Deterministic regex-based KPI extraction fallback.

This module provides a reliable fallback when AI-based extraction fails.
It uses regex patterns to find common operational KPI patterns in filing text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .types import SpotlightKpiCandidate


# Flexible connector pattern to handle various formats:
# "GMV of $X", "GMV: $X", "GMV reached $X", "GMV was $X", "GMV totaled $X", etc.
_CONNECTOR = r"(?:\s+(?:of|at|is|was|were|reached|totaled|totalled|hit|grew\s+to|increased\s+to|stood\s+at)|\s*[:\-])?(?:\s+approximately|\s+about|\s+roughly)?\s*"

# Operational KPI patterns organized by industry/type
# Each pattern: (regex, kpi_name_template, unit, priority)
# Higher priority = more company-specific

_KPI_PATTERNS: List[Tuple[re.Pattern[str], str, str, int]] = [
    # =========================================================================
    # USER/SUBSCRIBER METRICS (highest priority - most company-specific)
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:monthly\s+active\s+users?|MAUs?)",
            re.IGNORECASE,
        ),
        "Monthly Active Users (MAUs)",
        "users",
        100,
    ),
    (
        re.compile(
            r"(?:monthly\s+active\s+users?|MAUs?)\s*(?:of|:)?\s*"
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Monthly Active Users (MAUs)",
        "users",
        100,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:daily\s+active\s+users?|DAUs?)",
            re.IGNORECASE,
        ),
        "Daily Active Users (DAUs)",
        "users",
        100,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"paid\s+(?:subscribers?|memberships?|members?)",
            re.IGNORECASE,
        ),
        "Paid Subscribers",
        "subscribers",
        95,
    ),
    (
        re.compile(
            r"paid\s+(?:subscribers?|memberships?|members?)" + _CONNECTOR +
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Paid Subscribers",
        "subscribers",
        95,
    ),
    (
        re.compile(
            r"(?:global\s+)?(?:paid\s+)?subscribers?" + _CONNECTOR +
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Subscribers",
        "subscribers",
        80,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:total\s+)?subscribers?",
            re.IGNORECASE,
        ),
        "Subscribers",
        "subscribers",
        80,
    ),
    
    # =========================================================================
    # TRANSACTION/VOLUME METRICS
    # =========================================================================
    (
        re.compile(
            r"(?:gross\s+merchandise\s+volume(?:\s*\(GMV\))?|GMV)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Gross Merchandise Volume (GMV)",
        "$",
        90,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:in\s+)?(?:gross\s+merchandise\s+volume|GMV)",
            re.IGNORECASE,
        ),
        "Gross Merchandise Volume (GMV)",
        "$",
        90,
    ),
    (
        re.compile(
            r"(?:total\s+payment\s+volume(?:\s*\(TPV\))?|TPV)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Total Payment Volume (TPV)",
        "$",
        90,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:in\s+)?(?:total\s+payment\s+volume|TPV)",
            re.IGNORECASE,
        ),
        "Total Payment Volume (TPV)",
        "$",
        90,
    ),
    (
        re.compile(
            r"(?:backlog|order\s+backlog)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Backlog",
        "$",
        86,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:in\s+)?(?:backlog|order\s+backlog)",
            re.IGNORECASE,
        ),
        "Backlog",
        "$",
        86,
    ),
    (
        re.compile(
            r"(?:remaining\s+performance\s+obligations(?:\s*\(RPO\))?|RPO)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Remaining Performance Obligations (RPO)",
        "$",
        84,
    ),
    (
        re.compile(
            r"(?:bookings|net\s+bookings|gross\s+bookings)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Bookings",
        "$",
        83,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:in\s+)?(?:bookings|net\s+bookings|gross\s+bookings)",
            re.IGNORECASE,
        ),
        "Bookings",
        "$",
        83,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:orders?|transactions?)",
            re.IGNORECASE,
        ),
        "Orders",
        "orders",
        75,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"trips?",
            re.IGNORECASE,
        ),
        "Trips",
        "trips",
        85,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"rides?",
            re.IGNORECASE,
        ),
        "Rides",
        "rides",
        85,
    ),
    
    # =========================================================================
    # SaaS METRICS
    # =========================================================================
    (
        re.compile(
            r"(?:annual\s+recurring\s+revenue(?:\s*\(ARR\))?|ARR)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Annual Recurring Revenue (ARR)",
        "$",
        88,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:in\s+)?(?:annual\s+recurring\s+revenue|ARR)",
            re.IGNORECASE,
        ),
        "Annual Recurring Revenue (ARR)",
        "$",
        88,
    ),
    (
        re.compile(
            r"(?:net\s+revenue\s+retention(?:\s*\(NRR\))?|NRR|net\s+dollar\s+retention(?:\s*\(NDR\))?|NDR)" + _CONNECTOR +
            r"(?P<value>\d{1,3}(?:\.\d+)?)\s*%",
            re.IGNORECASE,
        ),
        "Net Revenue Retention (NRR)",
        "%",
        85,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:\.\d+)?)\s*%\s*"
            r"(?:net\s+revenue\s+retention|NRR|net\s+dollar\s+retention)",
            re.IGNORECASE,
        ),
        "Net Revenue Retention (NRR)",
        "%",
        85,
    ),
    
    # =========================================================================
    # MANUFACTURING/DELIVERY METRICS
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B|thousand|K)?\s*"
            r"(?:vehicles?\s+)?delivered",
            re.IGNORECASE,
        ),
        "Vehicles Delivered",
        "units",
        90,
    ),
    (
        re.compile(
            r"delivered\s+(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B|thousand|K)?\s*"
            r"vehicles?",
            re.IGNORECASE,
        ),
        "Vehicles Delivered",
        "units",
        90,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B|thousand|K)?\s*"
            r"units?\s+(?:shipped|sold)",
            re.IGNORECASE,
        ),
        "Units Shipped",
        "units",
        80,
    ),
    (
        re.compile(
            r"shipped\s+(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B|thousand|K)?\s*"
            r"units?",
            re.IGNORECASE,
        ),
        "Units Shipped",
        "units",
        80,
    ),
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B|thousand|K)?\s*"
            r"systems?\s+(?:shipped|sold|installed)",
            re.IGNORECASE,
        ),
        "Systems Shipped",
        "systems",
        85,
    ),
    
    # =========================================================================
    # RETAIL METRICS
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*)\s*"
            r"(?:stores?|locations?|restaurants?|outlets?)",
            re.IGNORECASE,
        ),
        "Store Count",
        "stores",
        70,
    ),
    (
        re.compile(
            r"(?:same[- ]store\s+sales?|comparable\s+sales?|comp\s+sales?)\s*"
            r"(?:grew|increased|up|rose)?\s*(?:by)?\s*"
            r"(?P<value>\d{1,3}(?:\.\d+)?)\s*%",
            re.IGNORECASE,
        ),
        "Same-Store Sales Growth",
        "%",
        85,
    ),
    (
        re.compile(
            r"(?P<value>-?\d{1,3}(?:\.\d+)?)\s*%\s*"
            r"(?:same[- ]store\s+sales?|comparable\s+sales?|comp\s+sales?)",
            re.IGNORECASE,
        ),
        "Same-Store Sales Growth",
        "%",
        85,
    ),
    
    # =========================================================================
    # STREAMING/ENGAGEMENT METRICS
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:hours?\s+)?(?:viewed|watched|streamed)",
            re.IGNORECASE,
        ),
        "Hours Viewed",
        "hours",
        80,
    ),
    (
        re.compile(
            r"(?:watch\s+time|viewing\s+hours?|streaming\s+hours?)\s*(?:of|:)?\s*"
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*hours?",
            re.IGNORECASE,
        ),
        "Watch Hours",
        "hours",
        80,
    ),
    
    # =========================================================================
    # ASSET MANAGEMENT METRICS
    # =========================================================================
    (
        re.compile(
            r"(?:assets?\s+under\s+management(?:\s*\(AUM\))?|AUM)" + _CONNECTOR +
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|trillion|M|B|T)?",
            re.IGNORECASE,
        ),
        "Assets Under Management (AUM)",
        "$",
        88,
    ),
    (
        re.compile(
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|trillion|M|B|T)?\s*"
            r"(?:in\s+)?(?:assets?\s+under\s+management|AUM)",
            re.IGNORECASE,
        ),
        "Assets Under Management (AUM)",
        "$",
        88,
    ),
    
    # =========================================================================
    # TRAVEL/HOSPITALITY METRICS
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"(?:room\s+nights?|bookings?)",
            re.IGNORECASE,
        ),
        "Room Nights",
        "nights",
        80,
    ),
    (
        re.compile(
            r"(?:RevPAR|revenue\s+per\s+available\s+room)\s*(?:of|:)?\s*"
            r"\$?\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "RevPAR",
        "$",
        82,
    ),
    
    # =========================================================================
    # ACTIVE ACCOUNTS/CUSTOMERS (lower priority - more generic)
    # =========================================================================
    (
        re.compile(
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?\s*"
            r"active\s+(?:customers?|accounts?|merchants?)",
            re.IGNORECASE,
        ),
        "Active Customers",
        "customers",
        70,
    ),
    (
        re.compile(
            r"active\s+(?:customers?|accounts?|merchants?)\s*(?:of|:)?\s*"
            r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<scale>million|billion|M|B)?",
            re.IGNORECASE,
        ),
        "Active Customers",
        "customers",
        70,
    ),
]


def _parse_scale(scale: Optional[str]) -> float:
    """Convert scale string to multiplier."""
    if not scale:
        return 1.0
    s = scale.lower().strip()
    if s in ("b", "billion"):
        return 1_000_000_000.0
    if s in ("m", "million"):
        return 1_000_000.0
    if s in ("k", "thousand"):
        return 1_000.0
    if s in ("t", "trillion"):
        return 1_000_000_000_000.0
    return 1.0


def _parse_value(raw: str, scale: Optional[str]) -> Optional[float]:
    """Parse a number string with optional scale."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        base = float(cleaned)
        return base * _parse_scale(scale)
    except (ValueError, TypeError):
        return None


def extract_kpis_with_regex(
    text: str,
    company_name: str,
    max_results: int = 5,
) -> List[SpotlightKpiCandidate]:
    """Extract KPIs using regex patterns.
    
    This is a deterministic fallback when AI extraction fails.
    Returns candidates sorted by priority (most company-specific first).
    """
    if not text:
        return []
    
    candidates: List[Tuple[int, SpotlightKpiCandidate]] = []
    seen_names: set[str] = set()
    
    # Search entire text
    for pattern, name_template, unit, priority in _KPI_PATTERNS:
        for match in pattern.finditer(text):
            # Extract value
            value_raw = match.group("value") if "value" in match.groupdict() else None
            scale = match.group("scale") if "scale" in match.groupdict() else None
            
            value = _parse_value(value_raw, scale)
            if value is None:
                continue
            
            # Skip if we already have this KPI type
            name_key = name_template.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            
            # Extract context around match for source quote
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            excerpt = text[start:end].strip()
            # Clean up excerpt
            excerpt = re.sub(r"\s+", " ", excerpt)
            if len(excerpt) > 200:
                excerpt = excerpt[:200] + "..."
            
            candidate: SpotlightKpiCandidate = {
                "name": name_template,
                "value": value,
                "unit": unit,
                "prior_value": None,
                "chart_type": "metric",
                "description": f"Extracted via pattern matching from {company_name} filing",
                "source_quote": excerpt,
                "representativeness_score": min(100, priority),
                "company_specificity_score": min(100, priority),
                "verifiability_score": 70,  # Lower confidence for regex extraction
                "ban_flags": ["regex_fallback"],
            }
            
            candidates.append((priority, candidate))
    
    # Sort by priority (highest first) and return top N
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in candidates[:max_results]]


def extract_kpis_with_regex_by_page(
    page_texts: List[str],
    company_name: str,
    max_results: int = 5,
) -> List[SpotlightKpiCandidate]:
    """Extract KPIs using regex patterns, preserving page evidence.

    This is intended for PDF workflows where we have page-scoped extracted text and
    need a verifiable (page, quote) evidence item.
    """
    if not page_texts:
        return []

    def _build_quote(page_text: str, match: re.Match[str], *, max_chars: int = 260) -> str:
        if not page_text:
            return ""
        match_start = int(match.start())
        match_end = int(match.end())
        if match_start < 0 or match_end <= match_start:
            raw = str(match.group(0) or "")
            return re.sub(r"\s+", " ", raw).strip()

        # Keep the full match while providing limited surrounding context.
        match_center = int((match_start + match_end) / 2)
        start = max(0, match_center - int(max_chars / 2))
        end = min(len(page_text), start + max_chars)
        if end < match_end:
            end = min(len(page_text), match_end)
            start = max(0, end - max_chars)
        excerpt = page_text[start:end].strip()
        # Collapse whitespace for readability; verification normalizes whitespace too.
        return re.sub(r"\s+", " ", excerpt).strip()

    candidates: List[Tuple[int, SpotlightKpiCandidate]] = []
    seen_names: set[str] = set()

    for page_idx, page_text in enumerate(page_texts):
        text = page_text or ""
        if not text.strip():
            continue
        page_num = page_idx + 1  # 1-indexed for UI/evidence

        for pattern, name_template, unit, priority in _KPI_PATTERNS:
            for match in pattern.finditer(text):
                value_raw = match.group("value") if "value" in match.groupdict() else None
                scale = match.group("scale") if "scale" in match.groupdict() else None

                value = _parse_value(value_raw, scale)
                if value is None:
                    continue

                name_key = name_template.lower()
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)

                quote = _build_quote(text, match)
                if not quote:
                    continue

                most_recent_value = (value_raw or "").strip()
                if scale and str(scale).strip():
                    most_recent_value = f"{most_recent_value} {str(scale).strip()}"
                if unit == "%":
                    most_recent_value = f"{most_recent_value}%".strip()
                elif unit == "$" and most_recent_value and not most_recent_value.startswith("$"):
                    most_recent_value = f"${most_recent_value}"

                candidate: SpotlightKpiCandidate = {
                    "name": name_template,
                    "value": float(value),
                    "unit": unit,
                    "prior_value": None,
                    "chart_type": "metric",
                    "description": None,
                    "source_quote": f"[p. {page_num}] {quote}",
                    "why_company_specific": "Disclosed as an operating metric in the company's filing.",
                    "how_calculated_or_defined": None,
                    "most_recent_value": most_recent_value or None,
                    "period": None,
                    "confidence": 0.72,
                    "evidence": [{"page": page_num, "quote": quote, "type": "value"}],
                    "ban_flags": ["regex_page_fallback"],
                }

                candidates.append((priority, candidate))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in candidates[:max_results]]


def extract_single_best_kpi_with_regex(
    text: str,
    company_name: str,
) -> Optional[SpotlightKpiCandidate]:
    """Extract the single best KPI using regex patterns.
    
    Returns the highest-priority match or None.
    """
    candidates = extract_kpis_with_regex(text, company_name, max_results=1)
    return candidates[0] if candidates else None
