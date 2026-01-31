from __future__ import annotations

from typing import Any, Dict, List, Optional

from .text_pipeline import extract_company_specific_spotlight_kpi_from_text
from .types import TokenBudgetLike


def extract_spotlight_kpis_via_gemini_flash(
    gemini_client: Any,
    *,
    company_name: str,
    context_text: str,
    summary_snippet: str = "",
    candidate_quotes: Optional[List[str]] = None,
    token_budget: Optional[TokenBudgetLike] = None,
    max_candidates: int = 6,
    max_output_tokens: int = 900,
    max_attempts: int = 6,
) -> List[Dict[str, Any]]:
    """Extract a single Spotlight KPI via a hardened multi-pass pipeline.

    Note: this is a text-only pipeline. When PDF bytes are available, prefer
    `extract_company_specific_spotlight_kpi_from_pdf` from `pdf_pipeline.py`.

    Returns a list with a single KPI dict (best-first). If extraction fails, returns [].
    """
    _ = summary_snippet, candidate_quotes, token_budget, max_candidates, max_output_tokens, max_attempts
    if not gemini_client or not company_name or not (context_text or "").strip():
        return []

    kpi, _debug = extract_company_specific_spotlight_kpi_from_text(
        gemini_client,
        context_text=context_text,
        company_name=company_name,
    )
    return [dict(kpi)] if isinstance(kpi, dict) else []
