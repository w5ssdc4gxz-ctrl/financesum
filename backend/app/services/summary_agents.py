"""Three-agent summary pipeline for filing summaries.

Replaces the monolithic single-LLM-call generation with a focused pipeline:

    Agent 1 — Company Intelligence Agent
        Identifies what makes a company unique + which KPIs matter.
        Output is cached (30-day TTL) in the existing company_research_cache table.

    Agent 2 — Filing Analysis Agent
        Reads the filing with company context and extracts company-specific
        insights, KPI findings, management quotes, and an evidence map.
        NOT cached (per-filing).

    Agent 3 — Summary Composition Agent
        Writes the final memo using curated insights from Agents 1 & 2.
        Uses the EXACT word target (no overshoot).

Public API
----------
run_summary_agent_pipeline(...)  → PipelineResult
    The pipeline orchestrator called by filings.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.prompt_pack import (
    ANTI_BOREDOM_RULES,
    QUOTE_BEHAVIOR_SPEC,
    SECTION_ORDER,
    SECTION_TEMPLATES,
)
from app.services.summary_budget_controller import (
    CANONICAL_SECTION_ORDER,
    compute_depth_plan,
    compute_scale_factor,
    describe_paragraph_range,
    describe_sentence_range,
    get_closing_takeaway_shape,
    get_financial_health_shape,
    get_risk_factors_shape,
    risk_budget_target_count,
    section_budget_tolerance_words,
    total_word_tolerance_words,
)
from app.services.summary_post_processor import PostProcessResult, post_process_summary
from app.services.word_surgery import count_words

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache constants (for Agent 1)
# ---------------------------------------------------------------------------
INTELLIGENCE_CACHE_TABLE = "company_research_cache"
INTELLIGENCE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


# ---------------------------------------------------------------------------
# Data models — Agent 1 output
# ---------------------------------------------------------------------------


@dataclass
class KPIDescriptor:
    """A company-specific Key Performance Indicator descriptor."""

    name: str  # e.g. "Paid Subscribers"
    why_it_matters: str  # 1-sentence explanation
    filing_search_terms: List[str]  # Terms to find in filing text
    metric_type: str = "currency"  # "count"|"currency"|"percentage"|"ratio"


@dataclass
class CompanyIntelligenceProfile:
    """Structured output from Agent 1 — Company Intelligence Agent."""

    business_identity: str  # What they do, how they make money
    competitive_moat: str  # Specific competitive advantage
    primary_kpis: List[KPIDescriptor]  # 3-5 company-specific KPIs
    key_competitors: List[str]  # 2-4 competitor names
    competitive_dynamics: str  # 1-2 sentences
    investor_focus_areas: List[str]  # 3-5 analytical questions
    industry_kpi_norms: str  # What "good" looks like
    raw_brief: str  # Backward-compatible flat text
    from_cache: bool = False


# ---------------------------------------------------------------------------
# Data models — Agent 2 output
# ---------------------------------------------------------------------------


@dataclass
class KPIFinding:
    """An actual KPI value found in the filing."""

    kpi_name: str
    current_value: str
    prior_value: Optional[str] = None
    change: Optional[str] = None
    change_direction: Optional[str] = None  # "improved"|"declined"|"stable"
    insight: str = ""
    source_quote: str = ""


@dataclass
class ManagementQuote:
    """A verbatim quote extracted from the filing."""

    quote: str
    attribution: str  # "CEO", "CFO", "Management"
    topic: str  # e.g. "growth strategy"
    suggested_section: str  # Which memo section it fits


@dataclass
class CompanyRisk:
    """A company-specific risk with mechanism and early warning."""

    risk_name: str
    mechanism: str
    early_warning: str
    evidence_from_filing: str


@dataclass
class FilingAnalysis:
    """Structured output from Agent 2 — Filing Analysis Agent."""

    central_tension: str  # ONE strategic question
    tension_evidence: str  # 2-3 supporting sentences
    kpi_findings: List[KPIFinding]
    period_specific_insights: List[str]  # 3-5 unique facts
    management_quotes: List[ManagementQuote]
    management_strategy_summary: str
    company_specific_risks: List[CompanyRisk]
    evidence_map: Dict[str, List[str]]  # section_name → data points


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Full result from the 3-agent pipeline."""

    summary_text: str
    company_intelligence: CompanyIntelligenceProfile
    filing_analysis: FilingAnalysis
    agent_timings: Dict[str, float] = field(default_factory=dict)
    total_llm_calls: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class SummarySectionBalanceError(Exception):
    """Raised when Continuous V2 cannot satisfy section-balance requirements."""

    def __init__(self, detail: Dict[str, Any]):
        self.detail = detail
        super().__init__(str(detail.get("detail") or "Summary section balance failed."))


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 1 — Company Intelligence Agent
# ═══════════════════════════════════════════════════════════════════════════


AGENT_1_SYSTEM_PROMPT = (
    "You are a senior equity research analyst specializing in company-specific "
    "KPI identification. Your job is to determine what makes a company unique "
    "and which metrics investors MUST track to understand its business.\n\n"
    "You MUST output valid JSON matching the schema provided. No markdown, "
    "no commentary outside the JSON."
)

AGENT_1_USER_PROMPT_TEMPLATE = """\
Analyze {company_name} ({ticker}) in the {sector_industry} sector.

This company filed a {filing_type}. I need you to identify:

1. BUSINESS IDENTITY: What does {company_name} actually do? How do they make money?
   Be specific — not "technology company" but "subscription streaming service that
   monetizes through tiered monthly memberships across 190+ countries."

2. PRIMARY KPIs (CRITICAL — most important part):
   Identify exactly 3-5 Key Performance Indicators that are SPECIFIC to
   {company_name}'s business model. These should be the metrics that professional
   analysts covering this stock actually track.

   Rules for KPI selection:
   - NEVER include generic financial metrics (Revenue, Net Income, EPS, Operating
     Margin) — those apply to ALL companies. Only business-model-specific metrics.
   - For a streaming company: subscribers, ARM, churn, content spend
   - For a bank: NIM, loan growth, deposit growth, credit quality, CET1 ratio
   - For a SaaS company: ARR, NDR, CAC payback, rule of 40
   - For a retailer: same-store sales, store count, sales per sq ft
   - For an insurer: combined ratio, premium growth, loss ratio
   - For a pharma company: pipeline candidates, approval milestones, patent cliffs
   - For each KPI, explain WHY it matters and include search terms to find it in filings

3. COMPETITIVE MOAT: What is {company_name}'s actual competitive advantage?
   Not "strong brand" — something specific like "largest content library with $17B
   annual content spend creating a scale barrier."

4. KEY COMPETITORS: Name 2-4 actual competitors.

5. INVESTOR FOCUS AREAS: What should an investment memo emphasize? List 3-5 specific
   analytical questions.

6. INDUSTRY KPI NORMS: What does "good" look like for the primary KPIs in this industry?

{existing_context}

Output your analysis as JSON matching this exact schema:
{{
  "business_identity": "string — 2-3 sentences",
  "competitive_moat": "string — 1-2 specific sentences",
  "primary_kpis": [
    {{
      "name": "string — metric name",
      "why_it_matters": "string — 1 sentence",
      "filing_search_terms": ["term1", "term2"],
      "metric_type": "count|currency|percentage|ratio"
    }}
  ],
  "key_competitors": ["competitor1", "competitor2"],
  "competitive_dynamics": "string — 1-2 sentences",
  "investor_focus_areas": ["area1", "area2", "area3"],
  "industry_kpi_norms": "string — 2-3 sentences"
}}"""


def _intelligence_cache_key(
    company_name: str, ticker: str, filing_type: str
) -> str:
    """Cache key includes filing_type since 10-K vs 10-Q may surface different KPIs."""
    raw = (
        f"{(company_name or '').strip().lower()}"
        f"|{(ticker or '').strip().upper()}"
        f"|{(filing_type or '').strip().upper()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read_intelligence_cache(
    cache_key: str,
) -> Optional[CompanyIntelligenceProfile]:
    """Read cached intelligence profile from Supabase. Returns None on miss."""
    try:
        from app.models.database import get_supabase_client

        client = get_supabase_client()
    except Exception:
        return None

    try:
        response = (
            client.table(INTELLIGENCE_CACHE_TABLE)
            .select("intelligence_json, created_at")
            .eq("cache_key", cache_key)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None

        row = response.data[0]
        intelligence_json = row.get("intelligence_json")
        if not intelligence_json or not isinstance(intelligence_json, dict):
            return None

        # Check TTL
        created_at_str = row.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(
                    str(created_at_str).replace("Z", "+00:00")
                )
                age = (datetime.now(timezone.utc) - created_at).total_seconds()
                if age > INTELLIGENCE_CACHE_TTL_SECONDS:
                    return None
            except Exception:
                pass

        return _parse_intelligence_profile(intelligence_json, from_cache=True)

    except Exception as exc:
        logger.debug("Intelligence cache read error: %s", exc)
        return None


def _write_intelligence_cache(
    cache_key: str,
    company_name: str,
    ticker: str,
    filing_type: str,
    profile: CompanyIntelligenceProfile,
) -> None:
    """Write intelligence profile to Supabase cache (best-effort)."""
    try:
        from app.models.database import get_supabase_client

        client = get_supabase_client()
    except Exception:
        return

    try:
        intelligence_dict = _profile_to_dict(profile)
        row = {
            "cache_key": cache_key,
            "company_name": (company_name or "").strip(),
            "ticker": (ticker or "").strip().upper(),
            "dossier_text": profile.raw_brief,  # backward compat
            "intelligence_json": intelligence_dict,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(INTELLIGENCE_CACHE_TABLE).upsert(
            row, on_conflict="cache_key"
        ).execute()
    except Exception as exc:
        logger.debug("Intelligence cache write error: %s", exc)


def _parse_intelligence_profile(
    data: Dict[str, Any], *, from_cache: bool = False
) -> CompanyIntelligenceProfile:
    """Parse a dict (from JSON) into a CompanyIntelligenceProfile."""
    kpis = []
    for kpi_data in data.get("primary_kpis") or []:
        if not isinstance(kpi_data, dict):
            continue
        kpis.append(
            KPIDescriptor(
                name=str(kpi_data.get("name") or ""),
                why_it_matters=str(kpi_data.get("why_it_matters") or ""),
                filing_search_terms=kpi_data.get("filing_search_terms") or [],
                metric_type=str(kpi_data.get("metric_type") or "currency"),
            )
        )

    return CompanyIntelligenceProfile(
        business_identity=str(data.get("business_identity") or ""),
        competitive_moat=str(data.get("competitive_moat") or ""),
        primary_kpis=kpis,
        key_competitors=data.get("key_competitors") or [],
        competitive_dynamics=str(data.get("competitive_dynamics") or ""),
        investor_focus_areas=data.get("investor_focus_areas") or [],
        industry_kpi_norms=str(data.get("industry_kpi_norms") or ""),
        raw_brief=str(data.get("raw_brief") or ""),
        from_cache=from_cache,
    )


def _profile_to_dict(profile: CompanyIntelligenceProfile) -> Dict[str, Any]:
    """Serialize a CompanyIntelligenceProfile to a JSON-safe dict."""
    return {
        "business_identity": profile.business_identity,
        "competitive_moat": profile.competitive_moat,
        "primary_kpis": [
            {
                "name": kpi.name,
                "why_it_matters": kpi.why_it_matters,
                "filing_search_terms": kpi.filing_search_terms,
                "metric_type": kpi.metric_type,
            }
            for kpi in profile.primary_kpis
        ],
        "key_competitors": profile.key_competitors,
        "competitive_dynamics": profile.competitive_dynamics,
        "investor_focus_areas": profile.investor_focus_areas,
        "industry_kpi_norms": profile.industry_kpi_norms,
        "raw_brief": profile.raw_brief,
    }


def _build_raw_brief(profile: CompanyIntelligenceProfile) -> str:
    """Build a flat-text brief from a profile for backward compatibility."""
    parts = []
    if profile.business_identity:
        parts.append(f"BUSINESS: {profile.business_identity}")
    if profile.competitive_moat:
        parts.append(f"MOAT: {profile.competitive_moat}")
    if profile.primary_kpis:
        kpi_lines = ", ".join(k.name for k in profile.primary_kpis)
        parts.append(f"KEY KPIs: {kpi_lines}")
    if profile.key_competitors:
        parts.append(f"COMPETITORS: {', '.join(profile.key_competitors)}")
    if profile.competitive_dynamics:
        parts.append(f"DYNAMICS: {profile.competitive_dynamics}")
    if profile.investor_focus_areas:
        focus = "; ".join(profile.investor_focus_areas)
        parts.append(f"FOCUS: {focus}")
    if profile.industry_kpi_norms:
        parts.append(f"NORMS: {profile.industry_kpi_norms}")
    return "\n".join(parts)


def _run_agent_1(
    *,
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    filing_type: str,
    filing_date: str = "",
    openai_client: Any,
) -> CompanyIntelligenceProfile:
    """Run Agent 1 — Company Intelligence Agent.

    Checks cache first.  On miss, uses GPT-5.2 with web search to generate
    a structured intelligence profile.
    """
    # 1. Check cache
    cache_key = _intelligence_cache_key(company_name, ticker, filing_type)
    cached = _read_intelligence_cache(cache_key)
    if cached:
        logger.info(
            "Agent 1 cache HIT for %s (%s) — skipping LLM call",
            company_name,
            ticker,
        )
        return cached

    # 2. Generate via LLM
    logger.info(
        "Agent 1 cache MISS for %s (%s) — running Company Intelligence Agent",
        company_name,
        ticker,
    )

    sector_industry = f"{sector}/{industry}" if sector or industry else "Unknown"
    existing_context = ""

    prompt = AGENT_1_USER_PROMPT_TEMPLATE.format(
        company_name=company_name,
        ticker=ticker,
        sector_industry=sector_industry,
        filing_type=filing_type or "SEC filing",
        existing_context=existing_context,
    )

    try:
        # PRIMARY: Responses API with web search (time-aware)
        raw_json = openai_client.research_company_intelligence_with_web(
            prompt=prompt,
            system_message=AGENT_1_SYSTEM_PROMPT,
            filing_date=filing_date,
            timeout_seconds=30.0,
        )
        # FALLBACK: Chat Completions without web search
        if not raw_json or not isinstance(raw_json, dict):
            logger.info(
                "Agent 1 web search returned no valid JSON for %s, falling back to chat",
                company_name,
            )
            raw_json = openai_client.research_company_intelligence(
                prompt=prompt,
                system_message=AGENT_1_SYSTEM_PROMPT,
                timeout_seconds=25.0,
            )

        if not raw_json or not isinstance(raw_json, dict):
            logger.warning("Agent 1 returned invalid JSON for %s", company_name)
            return _build_fallback_profile(
                company_name, ticker, sector, industry, openai_client
            )

        profile = _parse_intelligence_profile(raw_json, from_cache=False)

        # Build a human-readable brief for backward compat
        if not profile.raw_brief:
            profile.raw_brief = _build_raw_brief(profile)

        # 3. Cache the result
        _write_intelligence_cache(
            cache_key, company_name, ticker, filing_type, profile
        )

        return profile

    except Exception as exc:
        logger.warning(
            "Agent 1 failed for %s (%s): %s. Using fallback.",
            company_name,
            ticker,
            exc,
        )
        return _build_fallback_profile(
            company_name, ticker, sector, industry, openai_client
        )


def _build_fallback_profile(
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    openai_client: Any,
) -> CompanyIntelligenceProfile:
    """Build a minimal profile using the old research_company_background fallback."""
    brief = ""
    try:
        brief = openai_client.research_company_background(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
        )
    except Exception:
        pass

    return CompanyIntelligenceProfile(
        business_identity=f"{company_name} operates in the {sector}/{industry} sector.",
        competitive_moat="",
        primary_kpis=[],
        key_competitors=[],
        competitive_dynamics="",
        investor_focus_areas=[],
        industry_kpi_norms="",
        raw_brief=brief or "",
        from_cache=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 2 — Filing Analysis Agent
# ═══════════════════════════════════════════════════════════════════════════


AGENT_2_SYSTEM_PROMPT = (
    "You are a senior equity research analyst performing deep filing analysis. "
    "You have been given a company intelligence profile that identifies what "
    "metrics and dynamics matter most for this specific company.\n\n"
    "You also have real-time context about what was happening with the company "
    "around the filing date. Use this to ground your analysis in the correct "
    "time period.\n\n"
    "Your job is to read the filing and extract ONLY the information that is "
    "relevant to THIS company's specific business model and KPIs.\n\n"
    "DO NOT extract generic financial metrics (Revenue, Net Income, EPS) unless "
    "they are directly relevant to the company-specific thesis. Focus on the "
    "PRIMARY KPIs identified in the intelligence profile.\n\n"
    "MANAGEMENT QUOTES ARE MANDATORY: Extract at least 3 verbatim quotes from "
    "the filing text. Each must be ≤25 words and reveal strategy, outlook, or "
    "risk acknowledgment.\n\n"
    "RISKS MUST BE COMPANY-SPECIFIC. BANNED generic risks: 'macroeconomic "
    "uncertainty', 'competitive pressure', 'regulatory risk', 'margin "
    "compression' — name the SPECIFIC factor, competitor, or regulation.\n\n"
    "You MUST output valid JSON matching the schema provided."
)

AGENT_2_USER_PROMPT_TEMPLATE = """\
Analyze the {filing_type} filing for {company_name} ({filing_period}).

=== COMPANY INTELLIGENCE (from prior research) ===
Business: {business_identity}
Competitive Moat: {competitive_moat}

PRIMARY KPIs TO FIND (these are the metrics that matter for {company_name}):
{formatted_kpi_list}

Investor Focus Areas:
{formatted_focus_areas}

=== FILING TEXT ===
{context_excerpt}

Filing Date: {filing_date}

=== TIME-AWARE COMPANY CONTEXT (around {filing_date}) ===
{current_company_context}

CRITICAL: This filing is from {filing_date}. Your analysis MUST be grounded in
this time period. Do NOT reference events that occurred after this date.

=== MD&A EXCERPT ===
{mda_excerpt}

=== RISK FACTORS EXCERPT ===
{risk_factors_excerpt}

=== COMPANY-SPECIFIC KPI DATA (pre-extracted from filing) ===
{company_kpi_context}

=== FINANCIAL DATA ===
{financial_snapshot}
{metrics_lines}

=== PERIOD COMPARISON DATA ===
{prior_period_delta}

=== FILING LANGUAGE SNIPPETS (available for verbatim quotes) ===
{filing_language_snippets}

YOUR TASK:
1. CENTRAL TENSION: Identify the ONE strategic question this filing answers for
   {company_name}. This must be specific to the company (not "Will revenue grow?"
   but something like "Can {company_name}'s [specific initiative] sustain [specific
   metric] improvement while managing [specific challenge]?").
   Ground it in the company intelligence above.

2. KPI FINDINGS: For each PRIMARY KPI listed above, find its current value, prior
   period value, and the change. If a KPI is not mentioned in the filing, skip it
   — do NOT invent data. For each found KPI, write ONE sentence explaining why
   the change matters for the thesis.

3. PERIOD-SPECIFIC INSIGHTS: What is UNIQUE about this specific filing period?
   What happened that wouldn't appear in any other quarter/year? List 3-5
   specific facts.

4. MANAGEMENT QUOTES: Find 3-6 high-signal verbatim quotes from the filing text.
   Each must be ≤25 words, reveal strategy/outlook/risk, and appear in the provided
   filing text. Tag each with which memo section it best fits.

5. COMPANY-SPECIFIC RISKS: Identify 2-3 risks specific to {company_name}'s
   situation (not generic like "macroeconomic uncertainty"). For each, explain
   the mechanism and an early-warning signal.

6. EVIDENCE MAP: For each memo section (Executive Summary, Financial Performance,
   MD&A, Risk Factors, Closing Takeaway), list the 2-3 most important data points
   or insights to include. This ensures the writer knows which evidence goes where.

Output as JSON matching this schema:
{{
  "central_tension": "string",
  "tension_evidence": "string — 2-3 sentences",
  "kpi_findings": [
    {{
      "kpi_name": "string",
      "current_value": "string",
      "prior_value": "string or null",
      "change": "string or null",
      "change_direction": "improved|declined|stable",
      "insight": "string — 1 sentence",
      "source_quote": "string"
    }}
  ],
  "period_specific_insights": ["string", "string", "string"],
  "management_quotes": [
    {{
      "quote": "string — verbatim ≤25 words",
      "attribution": "string",
      "topic": "string",
      "suggested_section": "string"
    }}
  ],
  "management_strategy_summary": "string — 2-3 sentences",
  "company_specific_risks": [
    {{
      "risk_name": "string",
      "mechanism": "string",
      "early_warning": "string",
      "evidence_from_filing": "string"
    }}
  ],
  "evidence_map": {{
    "Executive Summary": ["string", "string"],
    "Financial Performance": ["string", "string"],
    "Management Discussion & Analysis": ["string", "string"],
    "Risk Factors": ["string", "string"],
    "Closing Takeaway": ["string", "string"]
  }}
}}"""


def _format_kpi_list(profile: CompanyIntelligenceProfile) -> str:
    """Format the KPI list for Agent 2's prompt."""
    if not profile.primary_kpis:
        return "(No company-specific KPIs identified — use standard financial metrics)"
    lines = []
    for i, kpi in enumerate(profile.primary_kpis, 1):
        search = ", ".join(kpi.filing_search_terms) if kpi.filing_search_terms else ""
        lines.append(
            f"{i}. {kpi.name} ({kpi.metric_type})\n"
            f"   Why: {kpi.why_it_matters}\n"
            f"   Search for: {search}"
        )
    return "\n".join(lines)


def _format_focus_areas(profile: CompanyIntelligenceProfile) -> str:
    """Format investor focus areas for Agent 2's prompt."""
    if not profile.investor_focus_areas:
        return "(Use standard financial analysis focus areas)"
    return "\n".join(f"- {area}" for area in profile.investor_focus_areas)


def _parse_filing_analysis(data: Dict[str, Any]) -> FilingAnalysis:
    """Parse a dict (from JSON) into a FilingAnalysis."""
    kpi_findings = []
    for kf in data.get("kpi_findings") or []:
        if not isinstance(kf, dict):
            continue
        kpi_findings.append(
            KPIFinding(
                kpi_name=str(kf.get("kpi_name") or ""),
                current_value=str(kf.get("current_value") or ""),
                prior_value=kf.get("prior_value"),
                change=kf.get("change"),
                change_direction=kf.get("change_direction"),
                insight=str(kf.get("insight") or ""),
                source_quote=str(kf.get("source_quote") or ""),
            )
        )

    quotes = []
    for q in data.get("management_quotes") or []:
        if not isinstance(q, dict):
            continue
        quotes.append(
            ManagementQuote(
                quote=str(q.get("quote") or ""),
                attribution=str(q.get("attribution") or ""),
                topic=str(q.get("topic") or ""),
                suggested_section=str(q.get("suggested_section") or ""),
            )
        )

    risks = []
    for r in data.get("company_specific_risks") or []:
        if not isinstance(r, dict):
            continue
        risks.append(
            CompanyRisk(
                risk_name=str(r.get("risk_name") or ""),
                mechanism=str(r.get("mechanism") or ""),
                early_warning=str(r.get("early_warning") or ""),
                evidence_from_filing=str(r.get("evidence_from_filing") or ""),
            )
        )

    evidence_map_raw = data.get("evidence_map") or {}
    evidence_map: Dict[str, List[str]] = {}
    for section, items in evidence_map_raw.items():
        if isinstance(items, list):
            evidence_map[str(section)] = [str(i) for i in items]

    return FilingAnalysis(
        central_tension=str(data.get("central_tension") or ""),
        tension_evidence=str(data.get("tension_evidence") or ""),
        kpi_findings=kpi_findings,
        period_specific_insights=[
            str(s) for s in (data.get("period_specific_insights") or [])
        ],
        management_quotes=quotes,
        management_strategy_summary=str(
            data.get("management_strategy_summary") or ""
        ),
        company_specific_risks=risks,
        evidence_map=evidence_map,
    )


def _run_agent_2(
    *,
    company_intelligence: CompanyIntelligenceProfile,
    company_name: str,
    ticker: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    context_excerpt: str,
    mda_excerpt: str,
    risk_factors_excerpt: str,
    company_kpi_context: str,
    financial_snapshot: str,
    metrics_lines: str,
    prior_period_delta_block: str,
    filing_language_snippets: str,
    openai_client: Any,
) -> FilingAnalysis:
    """Run Agent 2 — Filing Analysis Agent."""
    # Fetch time-aware company context (non-blocking)
    current_context = ""
    if filing_date:
        try:
            current_context = openai_client.research_company_current_context(
                company_name=company_name,
                ticker=ticker,
                filing_date=filing_date,
                filing_type=filing_type,
                timeout_seconds=18.0,
            )
        except Exception:
            pass

    prompt = AGENT_2_USER_PROMPT_TEMPLATE.format(
        filing_type=filing_type or "SEC filing",
        company_name=company_name,
        filing_period=filing_period or "",
        business_identity=company_intelligence.business_identity,
        competitive_moat=company_intelligence.competitive_moat,
        formatted_kpi_list=_format_kpi_list(company_intelligence),
        formatted_focus_areas=_format_focus_areas(company_intelligence),
        context_excerpt=context_excerpt or "(No filing text available)",
        filing_date=filing_date or "(Not available)",
        current_company_context=current_context or "(No additional context available)",
        mda_excerpt=mda_excerpt or "(No MD&A excerpt available)",
        risk_factors_excerpt=risk_factors_excerpt or "(No risk factors available)",
        company_kpi_context=company_kpi_context or "(None)",
        financial_snapshot=financial_snapshot or "(None)",
        metrics_lines=metrics_lines or "(None)",
        prior_period_delta=prior_period_delta_block or "(None)",
        filing_language_snippets=filing_language_snippets or "(None)",
    )

    try:
        raw_json = openai_client.analyze_filing_with_context(
            prompt=prompt,
            system_message=AGENT_2_SYSTEM_PROMPT,
            timeout_seconds=40.0,
        )

        if not raw_json or not isinstance(raw_json, dict):
            logger.warning("Agent 2 returned invalid JSON for %s", company_name)
            return _build_fallback_analysis(company_name)

        return _parse_filing_analysis(raw_json)

    except Exception as exc:
        logger.warning("Agent 2 failed for %s: %s. Using fallback.", company_name, exc)
        return _build_fallback_analysis(company_name)


def _build_fallback_analysis(company_name: str) -> FilingAnalysis:
    """Build a minimal fallback analysis when Agent 2 fails."""
    return FilingAnalysis(
        central_tension=f"The key question for {company_name} this period.",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[],
        evidence_map={},
    )


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 3 — Summary Composition Agent
# ═══════════════════════════════════════════════════════════════════════════


def _build_agent_3_system_prompt(
    *,
    company_name: str,
    target_length: Optional[int],
    persona_name: Optional[str],
    persona_requested: bool,
) -> str:
    """Build the system prompt for Agent 3."""
    identity = (
        f"You are a senior equity research analyst writing an institutional "
        f"investment memo for portfolio managers."
    )
    if persona_name:
        identity += (
            f" You are filtering the analysis through the priorities of "
            f"{persona_name}, but you must NOT mimic catchphrases or produce "
            f"self-referential manifesto language."
        )

    word_count_block = ""
    if target_length:
        tolerance = total_word_tolerance_words(target_length)
        lower = max(1, int(target_length) - int(tolerance))
        upper = int(target_length) + int(tolerance)
        word_count_block = (
            f"\n\nCRITICAL WORD COUNT RULES:\n"
            f"- Total word count MUST be between {lower} and {upper} words.\n"
            f"- Section budgets are BODY-word budgets only; headings do not count.\n"
            f"- Each section has a specific budget. Follow it without front-loading early sections.\n"
            f"- If exact target match would force repetition or filler, stay inside the allowed band and preserve quality.\n"
            f"- Never pad. Under-target output must add new analysis or compress expectations."
        )

    return (
        f"{identity}\n\n"
        f"You are writing the final investment memo. You have been given:\n"
        f"1. A company intelligence profile (what makes this company unique)\n"
        f"2. A filing analysis (the specific insights from this filing)\n"
        f"3. Exact section-by-section word budgets\n"
        f"{word_count_block}\n\n"
        f"CRITICAL CONTENT RULES:\n"
        f"- Use the COMPANY-SPECIFIC KPIs as your PRIMARY metrics, not generic financials.\n"
        f"- If the filing analysis provides a KPI finding, USE IT instead of generic Revenue/EPS.\n"
        f"- The central tension from the filing analysis IS your thesis — do not invent a different one.\n"
        f"- Management quotes are pre-verified from the filing — use them verbatim.\n"
        f"- Follow the evidence map: put the right data in the right section.\n"
        f"- MANDATORY: Include AT LEAST 3 management quotes across the memo.\n"
        f"- RISK FACTORS must describe company-specific threats with concrete mechanisms. "
        f"BANNED: 'macroeconomic uncertainty', 'competitive pressure' without naming the SPECIFIC factor.\n"
        f"- NUMBERS support the argument, not replace it. Lead every paragraph with a business insight."
    )


def _format_kpi_findings(analysis: FilingAnalysis) -> str:
    """Format KPI findings for Agent 3's prompt."""
    if not analysis.kpi_findings:
        return "(No company-specific KPI findings — use supplementary financial data)"
    lines = []
    for kf in analysis.kpi_findings:
        line = f"- {kf.kpi_name}: {kf.current_value}"
        if kf.prior_value:
            line += f" (prior: {kf.prior_value}"
            if kf.change:
                line += f", change: {kf.change}"
            line += ")"
        if kf.insight:
            line += f"\n  Insight: {kf.insight}"
        lines.append(line)
    return "\n".join(lines)


def _format_management_quotes(analysis: FilingAnalysis) -> str:
    """Format management quotes for Agent 3's prompt."""
    if not analysis.management_quotes:
        return "(No pre-verified quotes — paraphrase management with attribution)"
    lines = []
    for q in analysis.management_quotes:
        lines.append(
            f'- "{q.quote}" ({q.attribution}, re: {q.topic}) '
            f"→ best for: {q.suggested_section}"
        )
    return "\n".join(lines)


def _format_risks(analysis: FilingAnalysis) -> str:
    """Format company-specific risks for Agent 3's prompt."""
    if not analysis.company_specific_risks:
        return "(Use standard risk analysis from filing)"
    lines = []
    for r in analysis.company_specific_risks:
        lines.append(
            f"- {r.risk_name}: {r.mechanism}\n"
            f"  Early warning: {r.early_warning}\n"
            f"  Filing evidence: {r.evidence_from_filing}"
        )
    return "\n".join(lines)


def _format_evidence_map(analysis: FilingAnalysis) -> str:
    """Format the evidence map for Agent 3's prompt."""
    if not analysis.evidence_map:
        return "(No evidence map — use filing data for each section)"
    lines = []
    for section, items in analysis.evidence_map.items():
        items_text = "\n".join(f"  - {item}" for item in items)
        lines.append(f"{section}:\n{items_text}")
    return "\n".join(lines)


def _format_period_insights(analysis: FilingAnalysis) -> str:
    """Format period-specific insights for Agent 3's prompt."""
    if not analysis.period_specific_insights:
        return "(No period-specific insights identified)"
    return "\n".join(
        f"- {insight}" for insight in analysis.period_specific_insights
    )


def _normalize_section_body(section_name: str, raw_text: str) -> str:
    """Normalize a section body returned by the model."""
    text = str(raw_text or "").strip()
    if not text:
        return ""
    fenced = re.search(r"```(?:markdown)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    heading_pattern = re.compile(
        rf"^\s*#{{1,6}}\s*{re.escape(section_name)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    duplicate_title_pattern = re.compile(
        rf"^\s*{re.escape(section_name)}\s*:?\s*",
        re.IGNORECASE,
    )
    while True:
        prior = text
        text = heading_pattern.sub("", text).strip()
        text = duplicate_title_pattern.sub("", text, count=1).strip()
        if section_name != "Key Metrics":
            text = re.sub(r"^\s*#.+$", "", text, flags=re.MULTILINE).strip()
        if text == prior:
            break
    return text.strip()


def _depth_moves_for_section(section_name: str, depth_plan: Any) -> List[str]:
    moves: List[str] = []
    if section_name == "Financial Performance":
        if depth_plan.yoy_score >= 0.35:
            moves.append("Add a year-over-year comparison only if the filing evidence supports it.")
        if depth_plan.sequential_score >= 0.35:
            moves.append("Add a sequential comparison if it sharpens the current-period read.")
        if depth_plan.leverage_score >= 0.4:
            moves.append("Explain operating leverage through price, volume, mix, or cost absorption.")
        if depth_plan.example_score >= 0.45:
            moves.append("Use one concrete example or evidence anchor to prove the main driver.")
    elif section_name == "Management Discussion & Analysis":
        if depth_plan.leverage_score >= 0.4:
            moves.append("Connect management actions to operating leverage or margin durability.")
        if depth_plan.capital_allocation_score >= 0.4:
            moves.append("Add capital allocation implications only if filing evidence supports them.")
        if depth_plan.balance_sheet_score >= 0.4:
            moves.append("Mention balance-sheet flexibility only if it changes execution capacity.")
    elif section_name == "Risk Factors":
        if depth_plan.scenario_score >= 0.35:
            moves.append("Frame downside in scenario terms with a measurable trigger.")
        if depth_plan.cash_conversion_score >= 0.35:
            moves.append("Tie at least one risk to cash conversion, liquidity, or working-capital strain if supported.")
    elif section_name == "Closing Takeaway":
        if depth_plan.scenario_score >= 0.35:
            moves.append("Include one measurable trigger that would change the stance.")
        if depth_plan.capital_allocation_score >= 0.4:
            moves.append("Mention capital allocation only if it changes the underwriting conclusion.")
    elif section_name == "Financial Health Rating":
        if depth_plan.cash_conversion_score >= 0.35:
            moves.append("Explain how cash conversion influences the health verdict.")
        if depth_plan.balance_sheet_score >= 0.35:
            moves.append("Explain how balance-sheet flexibility caps downside.")
    else:
        if depth_plan.example_score >= 0.45:
            moves.append("Use one evidence-backed example if it adds new insight.")
    if not moves:
        moves.append("Focus on signal only. Compress secondary angles rather than adding filler.")
    return moves


def _section_budget_range(section_name: str, budget: int) -> tuple[int, int]:
    tolerance = section_budget_tolerance_words(section_name, int(budget or 0))
    return max(1, int(budget) - int(tolerance)), int(budget) + int(tolerance)


def _quotes_for_section(analysis: FilingAnalysis, section_name: str) -> List[ManagementQuote]:
    return [
        quote
        for quote in analysis.management_quotes
        if (quote.suggested_section or "").strip() == section_name
    ]


def _fallback_key_metrics_from_kpis(analysis: FilingAnalysis) -> str:
    lines: List[str] = []
    for finding in analysis.kpi_findings[:5]:
        label = (finding.kpi_name or "").strip()
        current = (finding.current_value or "").strip()
        if label and current:
            lines.append(f"→ {label}: {current}")
    return "\n".join(lines)


_END_PUNCT_RE = re.compile(r'[.!?](?:["\')\]]+)?$')
_RISK_ITEM_RE = re.compile(
    r"\*\*(?P<name>[^*:\n]{2,120}?):?\*\*\s*:?\s*(?P<body>.+?)(?=(?:\n\s*\*\*[^*]+?\*\*\s*:?)|\Z)",
    re.DOTALL,
)
_MECHANISM_RE = re.compile(
    r"\b(because|driven by|if|unless|leads to|results in|pressure|compress|dilute|erode|funding|liquidity|working capital|pricing|churn|renewal|mix shift|substitution|execution slip)\b",
    re.IGNORECASE,
)
_TRANSMISSION_RE = re.compile(
    r"\b(revenue|pricing|volume|mix|margin|gross margin|operating margin|cash flow|free cash flow|liquidity|refinancing|debt|balance sheet|working capital|capex|opex|demand|backlog|bookings)\b",
    re.IGNORECASE,
)
_EARLY_WARNING_RE = re.compile(
    r"\b(early[- ]warning|watch|signal|trigger|threshold|leading indicator|renewal|bookings|backlog|churn|pipeline|pricing|utilization|adoption|attrition|downtime|default|refinancing)\b",
    re.IGNORECASE,
)
_HEALTH_PROFITABILITY_RE = re.compile(
    r"\b(margin|profitability|cash conversion|free cash flow|operating cash flow|fcf|earnings quality|return on capital)\b",
    re.IGNORECASE,
)
_HEALTH_BALANCE_SHEET_RE = re.compile(
    r"\b(balance sheet|liquidity|cash|debt|funding|flexibility|leverage|coverage|refinancing|optionalit)\b",
    re.IGNORECASE,
)
_DANGLING_PATTERNS = (
    r",\s*$",
    r"\bbut\s*$",
    r"\band\s*$",
    r"\bor\s*$",
    r"\bthat\s*$",
    r"\bwith\s*$",
    r"\bwhich\s*$",
    r"\bwhere\s*$",
    r"\bif\s*$",
)
_STANCE_RE = re.compile(
    r"\b(buy|hold|sell|overweight|underweight|neutral|constructive|cautious|positive|negative|attractive|unattractive)\b",
    re.IGNORECASE,
)
_TRIGGER_RE = re.compile(
    r"\b(trigger|watch|must stay true|breaks the thesis|would change|needs to|if\b|unless\b)\b",
    re.IGNORECASE,
)


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()])


def _has_terminal_punctuation(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped) and bool(_END_PUNCT_RE.search(stripped))


def _has_dangling_ending(text: str) -> bool:
    stripped = str(text or "").rstrip()
    for pattern in _DANGLING_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    return False


def _section_word_distance(section_name: str, text: str, budget: int) -> int:
    actual_words = count_words(text)
    lower, upper = _section_budget_range(section_name, budget)
    if lower <= actual_words <= upper:
        return 0
    if actual_words < lower:
        return lower - actual_words
    return actual_words - upper


def _validate_health_local_contract(
    text: str,
    *,
    budget: int,
    health_score_data: Optional[Dict[str, Any]],
) -> List[str]:
    failures: List[str] = []
    shape = get_financial_health_shape(budget)
    score = health_score_data.get("overall_score") if health_score_data else None
    score_line_present = "/100" in str(text or "")
    if score is not None and not score_line_present:
        failures.append("Open with the exact pre-calculated health score line.")
    if not _HEALTH_PROFITABILITY_RE.search(text or ""):
        failures.append("Explain profitability quality or cash conversion.")
    if not _HEALTH_BALANCE_SHEET_RE.search(text or ""):
        failures.append("Explain balance-sheet flexibility or liquidity.")
    sentence_count = _sentence_count(text)
    if not (shape.min_sentences <= sentence_count <= shape.max_sentences):
        failures.append(
            "Use "
            + describe_sentence_range(shape.min_sentences, shape.max_sentences)
            + " for this Financial Health Rating budget."
        )
    return failures


def _validate_risk_local_contract(text: str, *, budget: int) -> List[str]:
    failures: List[str] = []
    shape = get_risk_factors_shape(budget)
    items = list(_RISK_ITEM_RE.finditer(text or ""))
    expected_count = int(shape.risk_count or 0)
    if len(items) != expected_count:
        failures.append(f"Write exactly {expected_count} structured risks.")
        return failures
    per_risk_target = max(18, budget // max(1, expected_count))
    per_risk_tolerance = max(8, int(round(per_risk_target * 0.12)))
    for item in items:
        risk_name = (item.group("name") or "").strip()
        body = (item.group("body") or "").strip()
        sentence_count = _sentence_count(body)
        if not (
            int(shape.per_risk_min_sentences or 2)
            <= sentence_count
            <= int(shape.per_risk_max_sentences or 3)
        ):
            failures.append(
                f"Risk '{risk_name}' must contain "
                + describe_sentence_range(
                    int(shape.per_risk_min_sentences or 2),
                    int(shape.per_risk_max_sentences or 3),
                )
                + "."
            )
        body_words = count_words(body)
        if body_words < max(18, per_risk_target - per_risk_tolerance):
            failures.append(
                f"Risk '{risk_name}' is too short for this section budget; expand it with company-specific analysis."
            )
        if not _MECHANISM_RE.search(body):
            failures.append(f"Risk '{risk_name}' needs a concrete mechanism.")
        if not _TRANSMISSION_RE.search(body):
            failures.append(f"Risk '{risk_name}' must explain the financial impact path.")
        if not _EARLY_WARNING_RE.search(body):
            failures.append(f"Risk '{risk_name}' must include an early-warning signal.")
    return failures


def _validate_closing_local_contract(text: str, *, budget: int) -> List[str]:
    failures: List[str] = []
    shape = get_closing_takeaway_shape(budget)
    sentence_count = _sentence_count(text)
    if not (shape.min_sentences <= sentence_count <= shape.max_sentences):
        failures.append(
            "Use "
            + describe_sentence_range(shape.min_sentences, shape.max_sentences)
            + " for this Closing Takeaway budget."
        )
    if len(_STANCE_RE.findall(text or "")) != 1:
        failures.append("State exactly one clear stance in the Closing Takeaway.")
    if budget < 120 and not _TRIGGER_RE.search(text or ""):
        failures.append("Include one measurable trigger or watch item.")
    if budget >= 120:
        lowered = str(text or "")
        if not re.search(
            r"\b(what must stay true|must stay true|as long as|while|so long as|holds?|stays?)\b",
            lowered,
            re.IGNORECASE,
        ):
            failures.append("Include a measurable 'what must stay true' trigger.")
        if not re.search(
            r"\b(what breaks the thesis|breaks the thesis|downgrade|downside|if .*below|if .*compress|deteriorat(?:e|es|ion))\b",
            lowered,
            re.IGNORECASE,
        ):
            failures.append("Include a measurable 'what breaks the thesis' trigger.")
    if _has_dangling_ending(text):
        failures.append("Close on a complete sentence with no dangling clause.")
    return failures


def _validate_section_local_contract(
    *,
    section_name: str,
    text: str,
    budget: int,
    health_score_data: Optional[Dict[str, Any]],
) -> List[str]:
    failures: List[str] = []
    body = str(text or "").strip()
    if not body:
        return ["Section body is empty."]
    if section_name == "Key Metrics":
        if "\n" not in body and "→" not in body and "->" not in body:
            return ["Return only deterministic metric lines for Key Metrics."]
        return []

    lower, upper = _section_budget_range(section_name, budget)
    actual_words = count_words(body)
    if actual_words < lower:
        failures.append(
            f"Section is underweight at {actual_words} words; required range is {lower}-{upper}."
        )
    elif actual_words > upper:
        failures.append(
            f"Section is overweight at {actual_words} words; required range is {lower}-{upper}."
        )
    if not _has_terminal_punctuation(body):
        failures.append("End the section with terminal punctuation.")
    if _has_dangling_ending(body):
        failures.append("End the section on a complete thought without a dangling clause.")
    if _sentence_count(body) < 2:
        failures.append("Write at least two full sentences.")

    if section_name == "Financial Health Rating":
        failures.extend(
            _validate_health_local_contract(
                body, budget=budget, health_score_data=health_score_data
            )
        )
    elif section_name == "Risk Factors":
        failures.extend(_validate_risk_local_contract(body, budget=budget))
    elif section_name == "Closing Takeaway":
        failures.extend(_validate_closing_local_contract(body, budget=budget))
    return failures


def generate_section_body_to_budget(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    budget: int,
    depth_plan: Any,
    prior_section_text: str,
    used_claims: List[str],
    openai_client: Any,
    failure_reason: str = "",
) -> str:
    if section_name == "Key Metrics":
        return _generate_section_body(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            openai_client=openai_client,
            failure_reason=failure_reason,
        )

    max_attempts = 4 if section_name == "Risk Factors" else 3
    best_body = ""
    best_rank: Optional[tuple[int, int, int, int]] = None
    next_failure_reason = failure_reason

    for attempt in range(1, max_attempts + 1):
        draft = _generate_section_body(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            openai_client=openai_client,
            failure_reason=next_failure_reason,
        )
        local_failures = _validate_section_local_contract(
            section_name=section_name,
            text=draft,
            budget=budget,
            health_score_data=health_score_data,
        )
        rank = (
            0 if not local_failures else 1,
            _section_word_distance(section_name, draft, budget),
            0 if _sentence_count(draft) >= 2 else 1,
            0 if _has_terminal_punctuation(draft) else 1,
        )
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_body = draft
        if not local_failures:
            return draft

        actual_words = count_words(draft)
        lower, upper = _section_budget_range(section_name, budget)
        shortfall = max(0, lower - actual_words)
        overage = max(0, actual_words - upper)
        next_failure_reason = (
            f"Attempt {attempt} failed local section validation.\n"
            f"- Actual word count: {actual_words}\n"
            f"- Required range: {lower}-{upper}\n"
            f"- Shortfall: {shortfall}\n"
            f"- Overage: {overage}\n"
            f"- Failures:\n"
            + "\n".join(f"  - {item}" for item in local_failures[:8])
        )

    return best_body


def _assemble_summary_from_sections(
    section_bodies: Dict[str, str],
    *,
    include_health_rating: bool,
) -> str:
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    parts: List[str] = []
    for section_name in ordered_sections:
        body = (section_bodies.get(section_name) or "").strip()
        if not body:
            continue
        parts.append(f"## {section_name}\n{body}")
    return "\n\n".join(parts).strip()


def _collect_section_word_counts(
    text: str,
    *,
    include_health_rating: bool,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    for section_name in ordered_sections:
        match = re.search(
            rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
            text or "",
            re.DOTALL,
        )
        counts[section_name] = count_words((match.group(1) if match else "").strip())
    return counts


def _build_section_prompt(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    budget: int,
    prior_section_text: str,
    used_claims: List[str],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    depth_plan: Any,
    failure_reason: str = "",
) -> str:
    lower, upper = _section_budget_range(section_name, budget)
    evidence_points = filing_analysis.evidence_map.get(section_name) or []
    quotes = _quotes_for_section(filing_analysis, section_name)
    quote_lines = "\n".join(
        f'- "{quote.quote}" ({quote.attribution}, re: {quote.topic})'
        for quote in quotes[:3]
    ) or "(No pre-assigned quotes. Paraphrase management only if grounded in the filing.)"
    evidence_block = "\n".join(f"- {item}" for item in evidence_points[:5]) or "- Use the strongest filing-grounded evidence available for this section."
    prior_block = prior_section_text.strip() or "(This is the first section or there is no prior section text.)"
    used_claims_block = "\n".join(f"- {claim}" for claim in used_claims[:5]) or "- No prior claims yet."
    depth_moves = "\n".join(f"- {move}" for move in _depth_moves_for_section(section_name, depth_plan))
    risk_target = risk_budget_target_count(budget) if section_name == "Risk Factors" else 0
    risk_lines = "\n".join(
        f"- {risk.risk_name}: {risk.mechanism} Early warning: {risk.early_warning}"
        for risk in filing_analysis.company_specific_risks[:3]
    ) or "- Use only company-specific underwriting risks from the filing."
    health_block = ""
    if section_name == "Financial Health Rating" and health_score_data:
        score = health_score_data.get("overall_score")
        band = health_score_data.get("score_band")
        if score is not None:
            health_block = (
                f"\nPRE-CALCULATED HEALTH SCORE:\n"
                f"- Use this exact opening: {float(score):.0f}/100 - {band or ''}.\n"
                f"- Do not compute a different score.\n"
            )

    key_metrics_instruction = ""
    if section_name == "Key Metrics":
        key_metrics_instruction = (
            "\nReturn only arrow-format metric lines. No prose paragraphs. "
            "Do not add analysis, bullets, or a heading."
        )
    section_contract = ""
    if section_name == "Financial Health Rating":
        shape = get_financial_health_shape(budget)
        section_contract = (
            "FINANCIAL HEALTH RATING CONTRACT:\n"
            f"- Write {describe_sentence_range(shape.min_sentences, shape.max_sentences)} across "
            f"{describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs)}.\n"
            "- Start with the exact pre-calculated score line.\n"
            "- Explain profitability quality, cash conversion quality, balance-sheet flexibility, and the main downside limiter.\n"
            "- End by bridging into Executive Summary.\n"
            "- Do not stop after the opening score line.\n\n"
        )
    elif section_name == "Risk Factors":
        shape = get_risk_factors_shape(budget)
        per_risk_target = max(18, budget // max(1, risk_target))
        per_risk_tolerance = max(8, int(round(per_risk_target * 0.12)))
        section_contract = (
            f"RISK FACTORS CONTRACT:\n"
            f"- Write exactly {int(shape.risk_count or risk_target)} risks.\n"
            f"- Each risk should land around {per_risk_target} words (allowed variance ±{per_risk_tolerance}).\n"
            f"- Format each as **Risk Name:** followed by "
            f"{describe_sentence_range(int(shape.per_risk_min_sentences or 2), int(shape.per_risk_max_sentences or 3))}.\n"
            f"- Each risk must include mechanism, financial impact pathway, and an early-warning signal.\n"
            f"- If the budget is large, expand each risk with company-specific narrative mass instead of compressing it into one mini-paragraph.\n"
            f"- No merged risks. No generic macro filler.\n"
            f"- Candidate company-specific risks:\n{risk_lines}\n\n"
        )
    elif section_name == "Closing Takeaway":
        shape = get_closing_takeaway_shape(budget)
        section_contract = (
            "CLOSING TAKEAWAY CONTRACT:\n"
            f"- Write {describe_sentence_range(shape.min_sentences, shape.max_sentences)} across "
            f"{describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs, short=True)}.\n"
            "- State exactly one stance.\n"
        )
        if budget < 120:
            section_contract += "- Include one measurable trigger.\n\n"
        else:
            section_contract += (
                "- Include the underwriting conclusion, one 'what must stay true' trigger, one 'what breaks the thesis' trigger, "
                "and one implication for capital allocation, cash generation, or valuation support.\n\n"
            )

    return (
        f"Write ONLY the body of the '{section_name}' section for {company_name}.\n\n"
        f"BODY WORD BUDGET:\n"
        f"- Target {budget} body words.\n"
        f"- Allowed range {lower}-{upper} body words.\n"
        f"- Count body words only; do not include a heading.\n"
        f"- Never pad with filler or repetition.\n"
        f"- Never repeat the section title inside the body.\n"
        f"- Never add extra sections or inline headers.\n"
        f"- End on complete sentences only; no dangling clauses or mid-thought cutoffs.\n"
        f"{key_metrics_instruction}\n\n"
        f"CENTRAL TENSION:\n{filing_analysis.central_tension}\n\n"
        f"PRIOR SECTION BRIDGE CONTEXT:\n{prior_block}\n\n"
        f"USED CLAIMS TO AVOID RESTATING:\n{used_claims_block}\n\n"
        f"SECTION EVIDENCE TO PRIORITIZE:\n{evidence_block}\n\n"
        f"AVAILABLE QUOTES:\n{quote_lines}\n\n"
        f"DEPTH MOVES:\n{depth_moves}\n"
        f"{health_block}\n"
        f"COMPANY CONTEXT:\n"
        f"- Business: {company_intelligence.business_identity or '(Not available)'}\n"
        f"- Moat: {company_intelligence.competitive_moat or '(Not available)'}\n"
        f"- Management strategy summary: {filing_analysis.management_strategy_summary or '(Not available)'}\n"
        f"- Financial snapshot: {financial_snapshot or '(Not available)'}\n"
        f"- Metrics block available elsewhere: {'yes' if metrics_lines.strip() else 'no'}\n"
        f"- Filing target length: {target_length or 0}\n\n"
        + section_contract
        + (
            f"KEY METRICS BLOCK TO COPY FROM:\n{metrics_lines.strip() or _fallback_key_metrics_from_kpis(filing_analysis) or '(No deterministic metrics available)'}\n\n"
            if section_name == "Key Metrics"
            else ""
        )
        + (
            f"REPAIR INSTRUCTION:\n{failure_reason}\n"
            f"Introduce new analytical content or compress; do not pad and do not restate earlier sections.\n\n"
            if failure_reason
            else ""
        )
        + "Return only the section body."
    )


def _generate_section_body(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    budget: int,
    depth_plan: Any,
    prior_section_text: str,
    used_claims: List[str],
    openai_client: Any,
    failure_reason: str = "",
) -> str:
    if section_name == "Key Metrics":
        body = (metrics_lines or "").strip() or _fallback_key_metrics_from_kpis(filing_analysis)
        return _normalize_section_body(section_name, body)

    prompt = _build_section_prompt(
        section_name=section_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        company_name=company_name,
        target_length=target_length,
        budget=budget,
        prior_section_text=prior_section_text,
        used_claims=used_claims,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        health_score_data=health_score_data,
        depth_plan=depth_plan,
        failure_reason=failure_reason,
    )
    raw = openai_client.compose_summary(
        prompt=prompt,
        system_message=(
            f"Write the {section_name} section body only. "
            "Use complete sentences, no headings, no bullets in narrative sections, and no padding."
        ),
        max_output_tokens=min(2500, max(300, int(budget * 3))),
        temperature=0.35,
        timeout_seconds=45.0,
    )
    return _normalize_section_body(section_name, raw)


def _build_per_section_instructions(
    *,
    analysis: FilingAnalysis,
    intelligence: CompanyIntelligenceProfile,
    section_budgets: Dict[str, int],
    include_health_rating: bool,
    health_score_data: Optional[Dict[str, Any]],
    persona_name: Optional[str],
    persona_requested: bool,
    company_name: str,
    industry: str,
) -> str:
    """Build section-by-section instructions with company-specific evidence."""
    sections_list = list(SECTION_ORDER)
    if not include_health_rating:
        sections_list = [s for s in sections_list if s != "Financial Health Rating"]

    instructions = []
    for section_name in sections_list:
        template = SECTION_TEMPLATES.get(section_name)
        if not template:
            continue

        budget = section_budgets.get(section_name, 0)
        budget_line = f"Target: ~{budget} words." if budget > 0 else ""

        # Evidence from Agent 2's evidence map
        evidence = analysis.evidence_map.get(section_name, [])
        evidence_block = (
            "\n".join(f"  - {e}" for e in evidence) if evidence else "  (use filing data)"
        )

        # Quotes assigned to this section
        section_quotes = [
            q
            for q in analysis.management_quotes
            if q.suggested_section == section_name
        ]
        quotes_block = (
            "\n".join(
                f'  - "{q.quote}" ({q.attribution}, re: {q.topic})'
                for q in section_quotes
            )
            if section_quotes
            else "  (no pre-assigned quotes)"
        )

        # Health-specific instructions
        health_block = ""
        if section_name == "Financial Health Rating" and health_score_data:
            score = health_score_data.get("overall_score")
            band = health_score_data.get("score_band")
            if score is not None:
                health_block = (
                    f"\nPRE-CALCULATED SCORE: {score}/100 — {band or ''}\n"
                    f"Use this exact score. Do NOT compute your own.\n"
                )

        # Persona instruction for Closing Takeaway
        persona_block = ""
        if section_name == "Closing Takeaway":
            if persona_name:
                persona_block = (
                    f"\nWrite with subtle {persona_name}-aligned framing for "
                    f"{company_name}. Avoid imitation catchphrases."
                )
            elif persona_requested:
                persona_block = (
                    "\nWrite in first person using the selected persona lens."
                )

        do_rules = ", ".join(template.do_rules[:3])
        dont_rules = ", ".join(template.dont_rules[:3])

        instructions.append(
            f"## {section_name} ({budget_line})\n"
            f"{template.system_guidance}\n"
            f"{health_block}"
            f"\nKey evidence for this section:\n{evidence_block}\n"
            f"\nAvailable quotes:\n{quotes_block}\n"
            f"{persona_block}\n"
            f"DO: {do_rules}\n"
            f"DON'T: {dont_rules}\n"
            f"Max numeric density: {template.max_numeric_density} per 100 words.\n"
            f"Transition OUT: {template.transition_out}"
        )

    return "\n\n".join(instructions)


AGENT_3_USER_PROMPT_TEMPLATE = """\
Write the investment memo for {company_name} ({filing_type}, {filing_period}).

=== COMPANY INTELLIGENCE ===
Business: {business_identity}
Competitive Moat: {competitive_moat}
Industry: {industry}

PRIMARY KPIs FOR {company_name} (USE THESE, not generic metrics):
{formatted_kpi_findings}

=== FILING ANALYSIS ===
Central Tension: {central_tension}
Supporting Evidence: {tension_evidence}

Period-Specific Insights (unique to this filing):
{formatted_period_insights}

Management Strategy: {management_strategy_summary}

MANDATORY Management Quotes (you MUST use at least 3):
{formatted_management_quotes}

Company-Specific Risks:
{formatted_risks}

=== EVIDENCE MAP (which data goes where) ===
{formatted_evidence_map}

=== SUPPLEMENTARY FINANCIAL DATA ===
{financial_snapshot}

=== PRE-FORMATTED KEY METRICS (copy these EXACT lines into Key Metrics section) ===
{metrics_lines}
NOTE: These are ready to use. Copy them directly as → MetricName: Value lines.
Do NOT rewrite them as prose paragraphs.

{health_score_block}

=== SECTION STRUCTURE AND BUDGETS ===
Write these sections in this exact order using ## headers:

{per_section_instructions}

=== WORD COUNT CONTRACT ===
{word_count_contract}

=== FORMAT REQUIREMENTS ===
- Use ## headers for each section in the specified order
- Use billions as "$X.XB", millions as "$X.XM"
- Specify fiscal period with figures (FY24, Q3 FY25)
- Every sentence must end with a complete thought
- Do not echo these instructions

{persona_instruction}

{anti_boredom_rules}
{quote_behavior_spec}

FINAL QUALITY CONTRACT:
- Every section must advance the central tension: "{central_tension}"
- If a paragraph does not move the argument forward, cut it.
- Closing Takeaway must include a clear BUY/HOLD/SELL stance and ONE measurable trigger.
- The memo should read like a single coherent argument, not independent section reports.
"""


def _build_word_count_contract(
    target_length: Optional[int], section_budgets: Dict[str, int]
) -> str:
    """Build the word count contract block for Agent 3."""
    if not target_length:
        return "Keep concise; prioritize substance over length."

    tolerance = total_word_tolerance_words(target_length)
    lower = max(1, int(target_length) - int(tolerance))
    upper = int(target_length) + int(tolerance)

    budget_lines = "\n".join(
        (
            f"- {section}: target {words} body words "
            f"(allowed range {_section_budget_range(section, words)[0]}-{_section_budget_range(section, words)[1]})"
        )
        for section, words in section_budgets.items()
        if words > 0
    )

    return (
        f"Total: approximately {target_length} words (allowed range {lower}-{upper}).\n"
        f"Section budgets:\n{budget_lines}\n\n"
        f"Count section BODY words only; markdown headings do not count. "
        f"Stay within the band without padding or repetition."
    )


def _build_persona_instruction(
    persona_name: Optional[str], persona_requested: bool, company_name: str
) -> str:
    """Build persona instruction for Agent 3."""
    if persona_requested and persona_name:
        return (
            f"Write with subtle {persona_name}-aligned framing for "
            f"{company_name}. Avoid imitation catchphrases, role-play "
            f"theatrics, and investor name-dropping."
        )
    if persona_requested:
        return "Write in first person using the selected persona lens, but keep the voice institutional."
    return f"Write in neutral third-person analyst voice focused on {company_name}."


def _run_agent_3(
    *,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    include_health_rating: bool,
    section_budgets: Dict[str, int],
    persona_name: Optional[str],
    persona_requested: bool,
    investor_focus: Optional[str],
    industry: str,
    openai_client: Any,
) -> Tuple[str, int, PostProcessResult]:
    """Run Agent 3 — section-by-section summary composition."""
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    depth_plan = compute_depth_plan(compute_scale_factor(target_length or 300))
    section_bodies: Dict[str, str] = {}

    try:
        for section_name in ordered_sections:
            budget = int(section_budgets.get(section_name, 0) or 0)
            if budget <= 0:
                continue
            section_bodies[section_name] = generate_section_body_to_budget(
                section_name=section_name,
                company_intelligence=company_intelligence,
                filing_analysis=filing_analysis,
                company_name=company_name,
                target_length=target_length,
                financial_snapshot=financial_snapshot,
                metrics_lines=metrics_lines,
                health_score_data=health_score_data,
                budget=budget,
                depth_plan=depth_plan,
                prior_section_text=(
                    section_bodies.get(ordered_sections[ordered_sections.index(section_name) - 1], "")
                    if ordered_sections.index(section_name) > 0
                    else ""
                ),
                used_claims=[
                    body.split(". ")[0].strip()
                    for name, body in section_bodies.items()
                    if name != "Key Metrics" and body.strip()
                ],
                openai_client=openai_client,
            )
    except Exception as exc:
        logger.error("Agent 3 failed for %s: %s", company_name, exc)
        raise

    summary_text = _assemble_summary_from_sections(
        section_bodies,
        include_health_rating=include_health_rating,
    )

    section_retry_counts: Dict[str, int] = {}

    def _regenerate_section(
        *,
        section_name: str,
        budget: int,
        failure_reason: str,
        prior_section_text: str,
        existing_section_text: str,
        used_claims: List[str],
    ) -> str:
        retry_count = int(section_retry_counts.get(section_name, 0) or 0)
        if retry_count >= 3:
            return existing_section_text
        section_retry_counts[section_name] = retry_count + 1
        return generate_section_body_to_budget(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            openai_client=openai_client,
            failure_reason=failure_reason,
        )

    if not target_length:
        empty_report = PostProcessResult(text=summary_text, passed=True, retries=0)
        return summary_text, 0, empty_report

    processed = post_process_summary(
        summary_text,
        target_words=int(target_length),
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        risk_factors_excerpt=" ".join(
            risk.evidence_from_filing for risk in filing_analysis.company_specific_risks
        ),
        max_retries=12,
        max_retries_per_section=3,
        regenerate_section_fn=_regenerate_section,
    )
    final_text = processed.text or summary_text
    if not processed.passed:
        final_counts = {
            section_name: count_words(section_bodies.get(section_name, ""))
            for section_name in ordered_sections
            if section_name in section_budgets
        }
        validation_report = processed.validation_report
        raise SummarySectionBalanceError(
            {
                "detail": "Continuous V2 summary failed section-balance validation after bounded retries.",
                "failure_code": "SUMMARY_SECTION_BALANCE_FAILED",
                "target_length": int(target_length or 0),
                "section_word_budgets": dict(section_budgets or {}),
                "section_word_counts": (
                    {
                        section_name: count_words(
                            re.search(
                                rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
                                final_text,
                                re.DOTALL,
                            ).group(1)
                        )
                        for section_name in ordered_sections
                        if re.search(
                            rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
                            final_text,
                            re.DOTALL,
                        )
                    }
                    if final_text
                    else final_counts
                ),
                "section_failures": [
                    {
                        "section_name": failure.section_name,
                        "code": failure.code,
                        "message": failure.message,
                        "severity": float(failure.severity or 0.0),
                    }
                    for failure in list((validation_report.section_failures if validation_report else []) or [])
                ],
                "global_failures": list((validation_report.global_failures if validation_report else []) or []),
                "repair_attempts": int(processed.retries or 0),
            }
        )
    return final_text, int(processed.retries or 0), processed


# ═══════════════════════════════════════════════════════════════════════════
# Word Count Compliance — surgical rewrite loop
# ═══════════════════════════════════════════════════════════════════════════


def _count_words_simple(text: str) -> int:
    """Simple word counter: split on whitespace and count."""
    if not text:
        return 0
    return len(text.split())


def _surgical_word_count_rewrite(
    *,
    summary_text: str,
    target_length: int,
    openai_client: Any,
    max_attempts: int = 2,
) -> str:
    """Surgically adjust word count to be within ±10 of target.

    If the summary is already within tolerance, returns it unchanged.
    Makes up to ``max_attempts`` tries, keeping the best result.
    On any exception, returns the original text unchanged.
    """
    import re as _re

    best_text = summary_text
    best_distance = abs(_count_words_simple(summary_text) - target_length)

    for attempt in range(max_attempts):
        current_wc = _count_words_simple(best_text)
        delta = current_wc - target_length

        # Already within tolerance
        if abs(delta) <= 10:
            return best_text

        try:
            if delta > 0:
                # OVER target — need to cut words
                rewrite_prompt = (
                    f"The following investment memo is {current_wc} words but must be "
                    f"EXACTLY {target_length} words (±10). It is OVER by {delta} words.\n\n"
                    f"CUT exactly {abs(delta)} words by:\n"
                    f"- Removing redundant adjectives and filler phrases\n"
                    f"- Tightening verbose sentences\n"
                    f"- Removing low-value qualifiers\n\n"
                    f"PRESERVE EXACTLY:\n"
                    f"- All ## section headers\n"
                    f"- All quoted text (in quotation marks)\n"
                    f"- All numeric data and dollar figures\n"
                    f"- All → arrow-format lines in Key Metrics\n"
                    f"- The BUY/HOLD/SELL verdict\n\n"
                    f"Return ONLY the revised memo with no commentary.\n\n"
                    f"---\n{best_text}"
                )
            else:
                # UNDER target — need to add words
                rewrite_prompt = (
                    f"The following investment memo is {current_wc} words but must be "
                    f"EXACTLY {target_length} words (±10). It is UNDER by {abs(delta)} words.\n\n"
                    f"ADD exactly {abs(delta)} words by:\n"
                    f"- Adding 'so what' interpretation sentences after key data points\n"
                    f"- Extending causal reasoning chains\n"
                    f"- Adding forward-looking implications\n"
                    f"- Distributing additions across multiple sections\n\n"
                    f"PRESERVE EXACTLY:\n"
                    f"- All ## section headers\n"
                    f"- All quoted text (in quotation marks)\n"
                    f"- All numeric data and dollar figures\n"
                    f"- All → arrow-format lines in Key Metrics\n"
                    f"- The BUY/HOLD/SELL verdict\n\n"
                    f"Do NOT add generic filler or repeated framework sentences.\n"
                    f"Return ONLY the revised memo with no commentary.\n\n"
                    f"---\n{best_text}"
                )

            system_msg = (
                "You are a precision editor. Your ONLY job is to adjust the word count "
                "of an investment memo to hit an exact target. Make minimal changes. "
                "Preserve all structure, data, quotes, and analytical substance."
            )

            result = openai_client.compose_summary(
                prompt=rewrite_prompt,
                system_message=system_msg,
                temperature=0.2,
                timeout_seconds=45.0,
            )

            if not result or not result.strip():
                continue

            new_wc = _count_words_simple(result)
            new_distance = abs(new_wc - target_length)

            # Accept if closer to target
            if new_distance < best_distance:
                best_text = result
                best_distance = new_distance

        except Exception as exc:
            logger.warning(
                "Surgical word count rewrite attempt %d failed: %s", attempt + 1, exc
            )
            continue

    return best_text


# ═══════════════════════════════════════════════════════════════════════════
# Key Metrics Validation
# ═══════════════════════════════════════════════════════════════════════════


def _validate_key_metrics_section(summary_text: str, metrics_lines: str) -> str:
    """Validate and fix the Key Metrics section to ensure arrow-format lines.

    If >50% of content lines are prose (not matching arrow format), replaces
    the entire section body with the raw metrics_lines data.
    Otherwise, keeps valid arrow lines and drops prose lines.
    """
    import re as _re

    if not summary_text:
        return summary_text

    # Find the Key Metrics section
    pattern = _re.compile(
        r'(##\s*Key\s*Metrics[^\n]*\n)(.*?)(?=\n##\s|\Z)',
        _re.DOTALL | _re.IGNORECASE,
    )
    match = pattern.search(summary_text)
    if not match:
        return summary_text

    header = match.group(1)
    body = match.group(2).strip()

    if not body:
        return summary_text

    # Parse lines and validate
    arrow_pattern = _re.compile(r'^→\s*[^:]+:\s*[\$\d\-\+\(]')
    lines = [line for line in body.split('\n') if line.strip()]

    if not lines:
        return summary_text

    valid_lines = []
    prose_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if arrow_pattern.match(stripped):
            valid_lines.append(stripped)
        else:
            prose_count += 1

    total_content_lines = len(valid_lines) + prose_count

    if total_content_lines == 0:
        return summary_text

    # If >50% prose, replace entire section with raw metrics
    if prose_count > total_content_lines * 0.5:
        if metrics_lines and metrics_lines.strip() and metrics_lines.strip() != "(None)":
            replacement_body = metrics_lines.strip()
        elif valid_lines:
            replacement_body = '\n'.join(valid_lines)
        else:
            return summary_text
    else:
        # Keep only valid arrow lines
        if valid_lines:
            replacement_body = '\n'.join(valid_lines)
        else:
            return summary_text

    # Replace in summary
    new_section = f"{header}\n{replacement_body}\n"
    return summary_text[:match.start()] + new_section + summary_text[match.end():]


def run_summary_agent_pipeline(
    *,
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    target_length: Optional[int],
    context_excerpt: str,
    mda_excerpt: str,
    risk_factors_excerpt: str,
    company_kpi_context: str,
    financial_snapshot: str,
    metrics_lines: str,
    prior_period_delta_block: str,
    filing_language_snippets: str,
    calculated_metrics: Dict[str, Any],
    health_score_data: Optional[Dict[str, Any]],
    include_health_rating: bool,
    section_budgets: Dict[str, int],
    preferences: Any,
    persona_name: Optional[str],
    persona_requested: bool,
    investor_focus: Optional[str],
    openai_client: Any,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> PipelineResult:
    """Orchestrate the 3-agent pipeline for filing summary generation.

    This is the main entry point called by ``generate_filing_summary()`` in
    filings.py when the agent pipeline feature flag is enabled.

    Parameters match the data already prepared by filings.py's generation flow.
    """
    timings: Dict[str, float] = {}
    total_calls = 0

    # ─── Agent 1: Company Intelligence ────────────────────────
    if progress_callback:
        progress_callback("Researching company intelligence...", 50)

    t0 = time.monotonic()
    company_intelligence = _run_agent_1(
        company_name=company_name,
        ticker=ticker,
        sector=sector,
        industry=industry,
        filing_type=filing_type,
        filing_date=filing_date,
        openai_client=openai_client,
    )
    timings["agent_1"] = time.monotonic() - t0
    if not company_intelligence.from_cache:
        total_calls += 1

    # ─── Agent 2: Filing Analysis ─────────────────────────────
    if progress_callback:
        progress_callback("Analyzing filing with company context...", 65)

    t0 = time.monotonic()
    filing_analysis = _run_agent_2(
        company_intelligence=company_intelligence,
        company_name=company_name,
        ticker=ticker,
        filing_type=filing_type,
        filing_period=filing_period,
        filing_date=filing_date,
        context_excerpt=context_excerpt,
        mda_excerpt=mda_excerpt,
        risk_factors_excerpt=risk_factors_excerpt,
        company_kpi_context=company_kpi_context,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        prior_period_delta_block=prior_period_delta_block,
        filing_language_snippets=filing_language_snippets,
        openai_client=openai_client,
    )
    timings["agent_2"] = time.monotonic() - t0
    total_calls += 1

    # ─── Agent 3: Summary Composition ─────────────────────────
    if progress_callback:
        progress_callback("Composing investment memo...", 80)

    t0 = time.monotonic()
    summary_text, repair_attempts, agent_3_post_process = _run_agent_3(
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        company_name=company_name,
        filing_type=filing_type,
        filing_period=filing_period,
        filing_date=filing_date,
        target_length=target_length,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        health_score_data=health_score_data,
        include_health_rating=include_health_rating,
        section_budgets=section_budgets,
        persona_name=persona_name,
        persona_requested=persona_requested,
        investor_focus=investor_focus,
        industry=industry,
        openai_client=openai_client,
    )
    timings["agent_3"] = time.monotonic() - t0
    total_calls += len(
        [
            section_name
            for section_name in SECTION_ORDER
            if section_name != "Key Metrics"
            and (include_health_rating or section_name != "Financial Health Rating")
        ]
    )

    # ─── Post-processing: Key Metrics Validation ──────────────
    if summary_text:
        summary_text = _validate_key_metrics_section(summary_text, metrics_lines)

    logger.info(
        "Agent pipeline complete for %s: calls=%d, timings=%s",
        company_name,
        total_calls,
        {k: f"{v:.1f}s" for k, v in timings.items()},
    )

    section_word_counts = _collect_section_word_counts(
        summary_text,
        include_health_rating=include_health_rating,
    )

    return PipelineResult(
        summary_text=summary_text,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        agent_timings=timings,
        total_llm_calls=total_calls,
        metadata={
            "scale_factor": compute_scale_factor(target_length or 300),
            "depth_plan": (
                compute_depth_plan(compute_scale_factor(target_length or 300)).__dict__
            ),
            "key_metrics_budget": int(section_budgets.get("Key Metrics", 0) or 0),
            "narrative_budgets": {
                key: value
                for key, value in section_budgets.items()
                if key != "Key Metrics"
            },
            "repair_attempts": int(repair_attempts or 0),
            "risk_count": int(risk_budget_target_count(section_budgets.get("Risk Factors", 0) or 0)),
            "section_word_counts": section_word_counts,
            "section_word_ranges": {
                key: {
                    "lower": _section_budget_range(key, value)[0],
                    "upper": _section_budget_range(key, value)[1],
                }
                for key, value in (section_budgets or {}).items()
                if int(value or 0) > 0
            },
            "section_validation_passed": bool(
                agent_3_post_process.validation_report.passed
                if agent_3_post_process.validation_report
                else True
            ),
            "section_validation_failures": [
                {
                    "section_name": failure.section_name,
                    "code": failure.code,
                    "message": failure.message,
                    "severity": float(failure.severity or 0.0),
                }
                for failure in list(
                    (agent_3_post_process.validation_report.section_failures if agent_3_post_process.validation_report else [])
                    or []
                )
            ],
            "pipeline_mode": "continuous_v2_sectioned",
            "used_padding": False,
        },
    )
