from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Protocol, TypedDict


class TokenBudgetLike(Protocol):
    remaining_tokens: int
    total_tokens: int

    def can_afford(self, prompt: str, expected_output_tokens: int) -> bool: ...
    def charge(self, prompt: str, output: str) -> int: ...


ChartType = Literal["bar", "trend", "gauge", "metric", "donut", "waterfall", "comparison", "breakdown"]


class SpotlightKpiEvidence(TypedDict, total=False):
    page: int
    quote: str
    type: Literal["definition", "value", "context"]


class SpotlightKpiCandidate(TypedDict, total=False):
    name: str
    value: float
    unit: Optional[str]
    prior_value: Optional[float]
    chart_type: Optional[str]
    description: Optional[str]
    source_quote: str

    # Optional richer fields the model may return (ignored if absent)
    period_label: Optional[str]
    prior_period_label: Optional[str]
    segments: Optional[List[Dict[str, Any]]]
    history: Optional[List[Dict[str, Any]]]

    # Model self-ratings (0..100)
    representativeness_score: Optional[int]
    company_specificity_score: Optional[int]
    verifiability_score: Optional[int]
    ban_flags: Optional[List[str]]

    # Evidence-backed pipeline (2-call) extra fields (frontend may ignore)
    why_company_specific: Optional[str]
    how_calculated_or_defined: Optional[str]
    most_recent_value: Optional[str]
    period: Optional[str]
    confidence: Optional[float]
    evidence: Optional[List[SpotlightKpiEvidence]]


class SpotlightKpiExtraction(TypedDict, total=False):
    candidates: List[SpotlightKpiCandidate]
