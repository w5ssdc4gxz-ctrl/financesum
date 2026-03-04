"""
prompt_builder.py — Integration layer between the existing filings pipeline
and the new prompt_pack.

This module bridges the data structures in ``filings.py`` (preferences,
financial statements, company dicts, filing dicts) to the clean
``PromptContext`` consumed by ``prompt_pack.py``.

Public API
----------
calculate_section_budgets(target_length, *, section_weights, include_health_rating)
    → Dict[str, int]
    Mathematical budget allocator that sums to *exactly* ``target_length``.
    Foundation for the ±10-word controller (Task #4).

build_filing_summary_prompt(preferences, financial_data, filing_text, dossier, **kw)
    → str
    One-call convenience function for the pipeline.  Creates a PromptContext,
    fills it from standard backend data, computes section budgets, and returns
    the assembled prompt.

preferences_to_prompt_context(preferences, *, company, filing, ...)
    → PromptContext
    Lower-level adapter when callers need a PromptContext for two-pass or
    custom flows.

parse_narrative_summary(raw_text)
    → Dict[str, str]
    Parses LLM output into 7 narrative sections by ``##`` headers.
    Returns canonical keys (``executive_summary``, etc.) plus backward-compatible
    legacy aliases (``tldr``, ``thesis``, ``risks``, ``catalysts``, ``kpis``)
    so existing callers keep working.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.prompt_pack import (
    PromptContext,
    SECTION_ORDER,
    build_single_pass_prompt,
    build_outline_prompt,
    build_expansion_prompt,
    score_to_band,
    parse_narrative_summary,  # re-exported for convenience
)
from app.services.summary_budget_controller import (
    DEFAULT_SECTION_WEIGHTS_WITH_HEALTH,
    calculate_section_word_budgets as canonical_calculate_section_word_budgets,
    compute_depth_plan,
    compute_scale_factor,
    get_default_section_weights,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default section weights — mirrors SECTION_PROPORTIONAL_WEIGHTS from filings.py
# ---------------------------------------------------------------------------

DEFAULT_SECTION_WEIGHTS: Dict[str, int] = dict(DEFAULT_SECTION_WEIGHTS_WITH_HEALTH)

# Key Metrics is a fixed-format data block.  Past a certain total length,
# it should be capped so the extra words flow to narrative sections.
KEY_METRICS_CAP_THRESHOLD: int = 1000  # total words above which we cap Key Metrics
KEY_METRICS_CAP_WORDS: int = 220       # hard cap for Key Metrics in long memos
KEY_METRICS_MAX_WORDS: int = 500       # absolute max for Key Metrics

# Word-count constraints
TARGET_LENGTH_MIN_WORDS: int = 1
TARGET_LENGTH_MAX_WORDS: int = 5000

# Persona ID ↔ Name mapping (kept in sync with filings.py)
PERSONA_ID_TO_NAME: Dict[str, str] = {
    "buffett": "Warren Buffett",
    "munger": "Charlie Munger",
    "graham": "Benjamin Graham",
    "lynch": "Peter Lynch",
    "dalio": "Ray Dalio",
    "wood": "Cathie Wood",
    "greenblatt": "Joel Greenblatt",
    "bogle": "John Bogle",
    "marks": "Howard Marks",
    "ackman": "Bill Ackman",
}
PERSONA_NAME_TO_ID: Dict[str, str] = {
    name.lower(): pid for pid, name in PERSONA_ID_TO_NAME.items()
}


# ---------------------------------------------------------------------------
# Section budget calculator
# ---------------------------------------------------------------------------

def calculate_section_budgets(
    target_length: int,
    *,
    section_weights: Optional[Dict[str, int]] = None,
    include_health_rating: bool = True,
) -> Dict[str, int]:
    """Calculate per-section *body* word budgets that sum to exactly ``target_length``.

    Parameters
    ----------
    target_length:
        Total word budget for the entire summary.
    section_weights:
        Proportional weights per section (e.g. {"Executive Summary": 18, …}).
        Values are relative; they don't need to sum to 100.  Falls back to
        ``DEFAULT_SECTION_WEIGHTS`` when None.
    include_health_rating:
        When False, the Financial Health Rating section is excluded and its
        weight is redistributed.

    Returns
    -------
    Dict mapping section name → exact integer word budget.  The values
    always sum to exactly ``target_length`` (no drift).

    Notes
    -----
    This function is the mathematical foundation for the ±10-word controller.
    It guarantees:
      • All budgets are ≥ 0.
      • ``sum(budgets.values()) == target_length`` (exact, via largest-remainder).
      • Key Metrics is capped for long targets so narrative sections get depth.
      • Heading title words are NOT subtracted here — callers should account for
        them separately if needed.
    """
    weights = (
        dict(section_weights)
        if isinstance(section_weights, dict) and section_weights
        else get_default_section_weights(include_health_rating)
    )
    return canonical_calculate_section_word_budgets(
        target_length,
        include_health_rating=include_health_rating,
        weight_overrides=weights,
    )


# ---------------------------------------------------------------------------
# Preference / context adapters
# ---------------------------------------------------------------------------

def _resolve_persona(
    persona_id: Optional[str] = None,
    investor_focus: Optional[str] = None,
) -> Tuple[Optional[str], bool]:
    """Resolve persona name and whether a persona was requested.

    Returns (persona_name_or_None, persona_requested_bool).
    """
    pid = (persona_id or "").strip().lower()
    persona_name = PERSONA_ID_TO_NAME.get(pid)

    # Fall back to extracting persona from investor_focus text
    if not persona_name and investor_focus:
        role_match = re.search(r"Role:\s*([^.]+)\.", investor_focus, re.IGNORECASE)
        if role_match:
            candidate = role_match.group(1).strip()
            if candidate.lower() in PERSONA_NAME_TO_ID:
                persona_name = candidate

    persona_requested = bool(pid or persona_name)
    return persona_name, persona_requested


def _clamp_target_length(value: Optional[int]) -> Optional[int]:
    """Clamp target length into the valid [1, 5000] range."""
    if value is None:
        return None
    try:
        return max(TARGET_LENGTH_MIN_WORDS, min(TARGET_LENGTH_MAX_WORDS, int(value)))
    except (TypeError, ValueError):
        return None


def _format_dollar(value: Optional[float]) -> Optional[str]:
    """Format a dollar amount into a human-readable abbreviated string."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1_000_000_000:
        return f"{sign}${abs_v / 1_000_000_000:.1f}B"
    if abs_v >= 1_000_000:
        return f"{sign}${abs_v / 1_000_000:.1f}M"
    if abs_v >= 1_000:
        return f"{sign}${abs_v / 1_000:.1f}K"
    return f"{sign}${abs_v:,.0f}"


def _build_financial_snapshot_str(statements: Optional[Dict[str, Any]]) -> str:
    """Build a compact financial snapshot from statement data."""
    if not statements or not isinstance(statements, dict):
        return ""

    data = statements.get("statements") or statements

    income = data.get("income_statement") or {}
    balance = data.get("balance_sheet") or {}
    cash_flow_stmt = data.get("cash_flow") or {}

    def _latest(d: Any) -> Optional[float]:
        """Extract latest numeric from a statement line item."""
        if d is None:
            return None
        if isinstance(d, (int, float)):
            return float(d)
        if isinstance(d, dict):
            for val in d.values():
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        if isinstance(d, list) and d:
            try:
                return float(d[0])
            except (TypeError, ValueError, IndexError):
                pass
        try:
            return float(d)
        except (TypeError, ValueError):
            return None

    revenue = _latest(income.get("totalRevenue") or income.get("Revenue"))
    op_income = _latest(
        income.get("OperatingIncomeLoss") or income.get("OperatingIncome")
    )
    net_income = _latest(income.get("NetIncomeLoss") or income.get("NetIncome"))
    op_cf = _latest(cash_flow_stmt.get("NetCashProvidedByUsedInOperatingActivities"))
    capex = _latest(cash_flow_stmt.get("PaymentsToAcquirePropertyPlantAndEquipment"))
    fcf = (op_cf - capex) if op_cf is not None and capex is not None else None
    total_assets = _latest(balance.get("TotalAssets"))
    total_liabilities = _latest(balance.get("TotalLiabilities"))
    cash = _latest(
        balance.get("CashAndCashEquivalentsAtCarryingValue")
        or balance.get("CashAndCashEquivalents")
    )

    lines: List[str] = []
    for label, val in [
        ("Revenue", _format_dollar(revenue)),
        ("Operating Income", _format_dollar(op_income)),
        ("Net Income", _format_dollar(net_income)),
        ("Operating Cash Flow", _format_dollar(op_cf)),
        ("Free Cash Flow", _format_dollar(fcf)),
        ("Total Assets", _format_dollar(total_assets)),
        ("Total Liabilities", _format_dollar(total_liabilities)),
        ("Cash & Equivalents", _format_dollar(cash)),
    ]:
        if val:
            lines.append(f"- {label}: {val}")

    return "\n".join(lines)


def _build_metrics_lines(
    calculated_metrics: Optional[Dict[str, Any]],
) -> str:
    """Build key metrics lines from pre-calculated metrics dict."""
    if not calculated_metrics:
        return ""

    lines: List[str] = []

    def _add_pct(label: str, key: str) -> None:
        val = calculated_metrics.get(key)
        if val is not None:
            try:
                lines.append(f"- {label}: {float(val):.1f}%")
            except (TypeError, ValueError):
                pass

    def _add_ratio(label: str, key: str, decimals: int = 2) -> None:
        val = calculated_metrics.get(key)
        if val is not None:
            try:
                lines.append(f"- {label}: {float(val):.{decimals}f}x")
            except (TypeError, ValueError):
                pass

    def _add_dollar(label: str, key: str) -> None:
        val = calculated_metrics.get(key)
        if val is not None:
            formatted = _format_dollar(float(val))
            if formatted:
                lines.append(f"- {label}: {formatted}")

    _add_pct("Gross Margin", "gross_margin")
    _add_pct("Operating Margin", "operating_margin")
    _add_pct("Net Margin", "net_margin")
    _add_pct("FCF Margin", "fcf_margin")
    _add_pct("Revenue Growth YoY", "revenue_growth_yoy")
    _add_ratio("Current Ratio", "current_ratio")
    _add_ratio("Debt-to-Equity", "debt_to_equity")
    _add_ratio("Interest Coverage", "interest_coverage")
    _add_dollar("Revenue", "revenue")
    _add_dollar("Operating Cash Flow", "operating_cash_flow")
    _add_dollar("Free Cash Flow", "free_cash_flow")

    return "\n".join(lines)


def _extract_filing_quotes(
    text: str,
    max_quotes: int = 10,
    min_words: int = 6,
    max_words: int = 35,
) -> str:
    """Extract quotable filing-language snippets from filing text."""
    if not text:
        return ""

    out: List[str] = []
    seen: set = set()

    for match in re.finditer(r'["\u201c]([^"\u201d\n]{20,240})["\u201d]', text):
        candidate = " ".join((match.group(1) or "").split()).strip()
        if not candidate:
            continue
        words = candidate.split()
        if len(words) < min_words or len(words) > max_words:
            continue
        norm = re.sub(r"[^a-z0-9 ]+", "", candidate.lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(f'- "{candidate}"')
        if len(out) >= max_quotes:
            break

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Core adapter: preferences → PromptContext
# ---------------------------------------------------------------------------

def preferences_to_prompt_context(
    *,
    # User preferences (from FilingSummaryPreferences)
    mode: str = "default",
    investor_focus: Optional[str] = None,
    persona_id: Optional[str] = None,
    focus_areas: Optional[List[str]] = None,
    tone: Optional[str] = None,
    detail_level: Optional[str] = None,
    output_style: Optional[str] = None,
    target_length: Optional[int] = None,
    complexity: str = "intermediate",
    section_weight_overrides: Optional[Dict[str, int]] = None,
    include_health_rating: bool = True,
    # Company data
    company_name: str = "Unknown",
    company: Optional[Dict[str, Any]] = None,
    # Filing data
    filing: Optional[Dict[str, Any]] = None,
    # Financial data
    statements: Optional[Dict[str, Any]] = None,
    calculated_metrics: Optional[Dict[str, Any]] = None,
    # Pre-calculated health score
    health_score_data: Optional[Dict[str, Any]] = None,
    # Filing text excerpts
    context_excerpt: str = "",
    risk_factors_excerpt: str = "",
    mda_excerpt: str = "",
    filing_language_snippets: str = "",
    prior_period_delta_block: str = "",
    # Company research dossier
    company_research_brief: str = "",
    # Agent pipeline context (optional)
    company_intelligence: Optional[Dict[str, Any]] = None,
    filing_analysis: Optional[Dict[str, Any]] = None,
) -> PromptContext:
    """Translate existing backend data structures into a ``PromptContext``.

    This function is the primary bridge between the ``filings.py`` data-prep
    pipeline and the new ``prompt_pack.py`` templates.
    """
    company = company or {}
    filing = filing or {}

    # Resolve persona
    persona_name, persona_requested = _resolve_persona(persona_id, investor_focus)

    # Clamp target length
    clamped_length = _clamp_target_length(target_length)

    # Calculate section budgets
    weights = section_weight_overrides or DEFAULT_SECTION_WEIGHTS
    section_budgets: Dict[str, int] = {}
    if clamped_length and clamped_length > 0:
        section_budgets = calculate_section_budgets(
            clamped_length,
            section_weights=weights,
            include_health_rating=include_health_rating,
        )

    # Build financial data strings
    financial_snapshot = _build_financial_snapshot_str(statements)
    metrics_lines = _build_metrics_lines(calculated_metrics)

    # Resolve health score
    health_score: Optional[float] = None
    health_band = ""
    health_drivers = ""
    if health_score_data:
        health_score = health_score_data.get("overall_score")
        health_band = health_score_data.get("score_band") or score_to_band(health_score)
        # Build a concise drivers string from component scores
        components = health_score_data.get("components") or {}
        if components:
            driver_parts = []
            for comp_name, comp_score in sorted(
                components.items(), key=lambda x: x[1], reverse=True
            ):
                driver_parts.append(f"{comp_name}: {comp_score:.0f}")
            health_drivers = ", ".join(driver_parts[:4])

    # Extract quote snippets if not already provided
    if not filing_language_snippets and context_excerpt:
        filing_language_snippets = _extract_filing_quotes(context_excerpt)

    scale_factor = compute_scale_factor(clamped_length) if clamped_length else 0.5

    return PromptContext(
        company_name=company_name or company.get("name") or company.get("ticker") or "Unknown",
        filing_type=filing.get("filing_type") or "",
        filing_period=filing.get("filing_period") or "",
        filing_date=filing.get("filing_date") or "",
        industry=company.get("industry") or "Not specified",
        business_model=company.get("business_model") or "Not specified",
        key_segments=company.get("key_segments") or "Not specified",
        ticker=company.get("ticker") or "",
        exchange=company.get("exchange") or "",
        sector=company.get("sector") or "",
        country=company.get("country") or "",
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        prior_period_delta=prior_period_delta_block,
        context_excerpt=context_excerpt,
        mda_excerpt=mda_excerpt,
        risk_factors_excerpt=risk_factors_excerpt,
        filing_language_snippets=filing_language_snippets,
        health_score=health_score,
        health_band=health_band,
        health_drivers=health_drivers,
        company_research_brief=company_research_brief,
        tone=tone or "objective",
        detail_level=detail_level or "comprehensive",
        output_style=output_style or "paragraph",
        complexity=complexity,
        target_length=clamped_length,
        investor_focus=investor_focus or "",
        persona_name=persona_name,
        persona_requested=persona_requested,
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        scale_factor=scale_factor,
        depth_plan=compute_depth_plan(scale_factor),
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
    )


# ---------------------------------------------------------------------------
# Convenience: one-call prompt builder for the pipeline
# ---------------------------------------------------------------------------

def build_filing_summary_prompt(
    preferences: Optional[Dict[str, Any]] = None,
    financial_data: Optional[Dict[str, Any]] = None,
    filing_text: str = "",
    dossier: Optional[Dict[str, Any]] = None,
    *,
    # Direct overrides (take precedence over preferences dict)
    company: Optional[Dict[str, Any]] = None,
    filing: Optional[Dict[str, Any]] = None,
    statements: Optional[Dict[str, Any]] = None,
    calculated_metrics: Optional[Dict[str, Any]] = None,
    health_score_data: Optional[Dict[str, Any]] = None,
    context_excerpt: Optional[str] = None,
    risk_factors_excerpt: str = "",
    mda_excerpt: str = "",
    filing_language_snippets: str = "",
    prior_period_delta_block: str = "",
    company_name: Optional[str] = None,
) -> str:
    """Build a complete filing summary prompt from standard backend data.

    This is the recommended entry point for the pipeline.  It:
    1. Extracts preferences from the dict (or uses defaults)
    2. Builds a ``PromptContext`` via ``preferences_to_prompt_context``
    3. Computes section budgets automatically
    4. Returns the assembled single-pass prompt string

    Parameters
    ----------
    preferences:
        Dict with keys matching ``FilingSummaryPreferences`` fields:
        mode, investor_focus, persona_id, focus_areas, tone, detail_level,
        output_style, target_length, complexity, section_weight_overrides,
        include_key_takeaways, health_rating.
    financial_data:
        Raw financial statements dict (``{"statements": {...}}``) from
        Supabase or fallback cache.
    filing_text:
        The filing narrative text (full or excerpted).
    dossier:
        Company research dossier dict with ``"brief"`` key containing
        the research brief text.
    """
    prefs = preferences or {}
    dossier = dossier or {}
    company = company or {}
    filing = filing or {}

    # Extract health rating preferences
    health_pref = prefs.get("health_rating") or {}
    if hasattr(health_pref, "model_dump"):
        try:
            health_pref = health_pref.model_dump(exclude_none=True)
        except TypeError:
            health_pref = health_pref.model_dump()
    include_health = bool(health_pref.get("enabled", False))

    # Use filing_text as context_excerpt if not provided separately
    resolved_context = context_excerpt if context_excerpt is not None else filing_text

    # Resolve company name
    resolved_name = (
        company_name
        or company.get("name")
        or company.get("ticker")
        or "Unknown"
    )

    ctx = preferences_to_prompt_context(
        mode=prefs.get("mode", "default"),
        investor_focus=prefs.get("investor_focus"),
        persona_id=prefs.get("persona_id"),
        focus_areas=prefs.get("focus_areas"),
        tone=prefs.get("tone"),
        detail_level=prefs.get("detail_level"),
        output_style=prefs.get("output_style"),
        target_length=prefs.get("target_length"),
        complexity=prefs.get("complexity", "intermediate"),
        section_weight_overrides=prefs.get("section_weight_overrides"),
        include_health_rating=include_health,
        company_name=resolved_name,
        company=company,
        filing=filing,
        statements=financial_data or statements,
        calculated_metrics=calculated_metrics,
        health_score_data=health_score_data,
        context_excerpt=resolved_context,
        risk_factors_excerpt=risk_factors_excerpt,
        mda_excerpt=mda_excerpt,
        filing_language_snippets=filing_language_snippets,
        prior_period_delta_block=prior_period_delta_block,
        company_research_brief=dossier.get("brief") or "",
    )

    return build_single_pass_prompt(ctx)


# ---------------------------------------------------------------------------
# Two-pass convenience functions
# ---------------------------------------------------------------------------

def build_outline_for_filing(
    preferences: Optional[Dict[str, Any]] = None,
    financial_data: Optional[Dict[str, Any]] = None,
    filing_text: str = "",
    dossier: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Tuple[str, PromptContext]:
    """Build the outline prompt (Pass 1) and return both the prompt and context.

    Callers use the context object to call ``build_expansion_for_filing``
    after receiving the outline from the LLM.
    """
    prefs = preferences or {}
    dossier = dossier or {}
    company = kwargs.get("company") or {}
    filing = kwargs.get("filing") or {}

    health_pref = prefs.get("health_rating") or {}
    if hasattr(health_pref, "model_dump"):
        try:
            health_pref = health_pref.model_dump(exclude_none=True)
        except TypeError:
            health_pref = health_pref.model_dump()
    include_health = bool(health_pref.get("enabled", False))

    resolved_context = kwargs.get("context_excerpt") or filing_text
    resolved_name = (
        kwargs.get("company_name")
        or company.get("name")
        or company.get("ticker")
        or "Unknown"
    )

    ctx = preferences_to_prompt_context(
        mode=prefs.get("mode", "default"),
        investor_focus=prefs.get("investor_focus"),
        persona_id=prefs.get("persona_id"),
        focus_areas=prefs.get("focus_areas"),
        tone=prefs.get("tone"),
        detail_level=prefs.get("detail_level"),
        output_style=prefs.get("output_style"),
        target_length=prefs.get("target_length"),
        complexity=prefs.get("complexity", "intermediate"),
        section_weight_overrides=prefs.get("section_weight_overrides"),
        include_health_rating=include_health,
        company_name=resolved_name,
        company=company,
        filing=filing,
        statements=financial_data or kwargs.get("statements"),
        calculated_metrics=kwargs.get("calculated_metrics"),
        health_score_data=kwargs.get("health_score_data"),
        context_excerpt=resolved_context,
        risk_factors_excerpt=kwargs.get("risk_factors_excerpt", ""),
        mda_excerpt=kwargs.get("mda_excerpt", ""),
        filing_language_snippets=kwargs.get("filing_language_snippets", ""),
        prior_period_delta_block=kwargs.get("prior_period_delta_block", ""),
        company_research_brief=dossier.get("brief") or "",
    )

    outline_prompt = build_outline_prompt(ctx)
    return outline_prompt, ctx


def build_expansion_for_filing(
    ctx: PromptContext,
    outline: str,
) -> str:
    """Build the expansion prompt (Pass 2) from a context and LLM-generated outline."""
    return build_expansion_prompt(ctx, outline)


# ---------------------------------------------------------------------------
# Response parser — legacy-alias wrapper
# ---------------------------------------------------------------------------
#
# The canonical parser lives in prompt_pack.parse_narrative_summary() and
# returns keys matching SECTION_ORDER (e.g. "Executive Summary").
#
# This module re-exports it (imported above) AND provides a legacy-alias
# wrapper for callers that still use the old key names (tldr, thesis, etc.).
# ---------------------------------------------------------------------------

# Backward-compatible legacy key aliases.
# Maps canonical SECTION_ORDER name → list of old keys to populate.
_LEGACY_ALIASES: Dict[str, List[str]] = {
    "Financial Health Rating": ["financial_health_rating", "tldr", "health"],
    "Executive Summary": ["executive_summary", "thesis"],
    "Financial Performance": ["financial_performance", "performance"],
    "Management Discussion & Analysis": ["management_discussion_analysis", "mda", "strategic_initiatives"],
    "Risk Factors": ["risk_factors", "risks"],
    "Key Metrics": ["key_metrics", "kpis"],
    "Closing Takeaway": ["closing_takeaway", "closing", "catalysts", "investment_recommendation"],
}


def parse_narrative_summary_with_legacy_keys(raw_text: str) -> Dict[str, str]:
    """Parse LLM output and return a dict with BOTH canonical and legacy keys.

    Wraps ``prompt_pack.parse_narrative_summary`` (canonical section-name keys)
    and adds backward-compatible aliases (``tldr``, ``thesis``, ``risks``,
    ``catalysts``, ``kpis``, ``mda``, ``closing``, ``performance``, ``health``,
    snake_case variants, etc.) so that existing callers keep working.

    Also adds ``full_summary`` with the raw text.

    Parameters
    ----------
    raw_text : str
        Raw markdown output from the LLM.

    Returns
    -------
    Dict[str, str]
        Section key → section body text.  Contains:
        - Canonical keys from SECTION_ORDER (e.g. ``"Executive Summary"``)
        - snake_case keys (e.g. ``"executive_summary"``)
        - Legacy aliases (e.g. ``"thesis"``, ``"tldr"``, ``"risks"``)
        - ``"full_summary"`` and ``"_raw"`` with the complete text
    """
    # Get canonical parse from prompt_pack
    canonical = parse_narrative_summary(raw_text)

    # Start with canonical result (includes _raw and all SECTION_ORDER keys)
    result = dict(canonical)

    # Add full_summary alias
    result["full_summary"] = result.get("_raw", (raw_text or "").strip())

    # Populate legacy aliases
    for section_name, aliases in _LEGACY_ALIASES.items():
        body = result.get(section_name, "")
        for alias in aliases:
            if alias not in result or not result[alias]:
                result[alias] = body

    # Ensure all expected legacy keys exist (even if empty)
    for aliases in _LEGACY_ALIASES.values():
        for alias in aliases:
            result.setdefault(alias, "")
    result.setdefault("tldr", "")
    result.setdefault("thesis", "")
    result.setdefault("risks", "")
    result.setdefault("catalysts", "")
    result.setdefault("kpis", "")

    return result
