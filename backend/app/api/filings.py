"""Filings API endpoints."""

import os
import json
import io
import hashlib
import logging
import random
import re
import string
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable, Literal, Set
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from app.models.database import get_supabase_client
from app.models.schemas import (
    Filing,
    FilingsFetchRequest,
    FilingsFetchResponse,
    FilingSummaryPreferences,
)
from app.api.auth import CurrentUser, get_current_user
from app.tasks.fetch import fetch_filings_task, run_fetch_filings_inline
from app.config import get_settings
from app.api.companies import _supabase_configured
from app.services.country_resolver import (
    infer_country_from_company_name,
    infer_country_from_exchange,
    infer_country_from_ticker,
    normalize_country,
)
from app.services.eodhd_client import (
    get_eodhd_client,
    EODHDAccessError,
    EODHDClientError,
    hydrate_country_with_eodhd,
    should_hydrate_country,
)
from app.services.edgar_fetcher import (
    download_filing,
    get_company_filings,
    resolve_country_from_sec_submission,
    search_company_by_ticker_or_cik,
)
from app.services.yahoo_finance import resolve_country_from_yahoo_asset_profile
from app.services.local_cache import (
    fallback_companies,
    fallback_filings,
    fallback_filings_by_id,
    fallback_financial_statements,
    fallback_filing_summaries,
    fallback_task_status,
    save_fallback_companies,
    progress_cache,
)
from app.services.summary_activity import record_summary_generated_event
from app.services.billing_usage import get_summary_usage_status
from app.services.summary_export import build_summary_docx, build_summary_pdf
from app.services.gemini_client import get_gemini_client, generate_growth_assessment
from app.services.gemini_exceptions import (
    GeminiRateLimitError,
    GeminiAPIError,
    GeminiTimeoutError,
)
from app.services.health_scorer import calculate_health_score
from app.services.sample_data import sample_filings_by_ticker
from app.utils.supabase_errors import is_supabase_table_missing_error

router = APIRouter()
logger = logging.getLogger(__name__)

# Gemini 2.0 Flash Lite supports up to ~1M tokens. Cap context to keep requests fast.
MAX_GEMINI_CONTEXT_CHARS = 200_000
# Summary quality can degrade quickly when we fall back to deterministic padding.
# Give the model an extra attempt to satisfy section minimums + length constraints.
MAX_SUMMARY_ATTEMPTS = 3
# Allow a couple of rewrite passes so we hit the strict word band
# without relying on low-quality deterministic padding.
MAX_REWRITE_ATTEMPTS = 3
SUMMARY_TOTAL_TIMEOUT_SECONDS = 120  # Hard cap per-request generation time

# ---------------------------------------------------------------------------
# Cost / token budget guardrails
# ---------------------------------------------------------------------------
# Pro plan economics: $10 / 100 summaries => $0.10 budget per summary.
#
# Gemini pricing is typically quoted per 1K tokens. To keep a hard upper bound
# on spend per summary, we estimate tokens from characters using a conservative
# heuristic (1 token ~= 4 chars). This is not perfect tokenization, but it is
# reliable enough for defensive budgeting.
#
# Configure via env if you want to tweak without code changes:
#   - GEMINI_COST_PER_SUMMARY_USD (default 0.10)
#   - GEMINI_COST_PER_1K_TOKENS_USD (default 0.002)
#   - GEMINI_MAX_OUTPUT_TOKENS (default 9000)
#   - GEMINI_SUMMARY_TOKEN_RESERVE (default 0)

DEFAULT_SUMMARY_BUDGET_USD = 0.10
DEFAULT_GEMINI_COST_PER_1K_TOKENS_USD = 0.002
DEFAULT_GEMINI_MAX_OUTPUT_TOKENS = 9000
DEFAULT_SUMMARY_TOKEN_RESERVE = 0
CHARS_PER_TOKEN_ESTIMATE = 4


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class TokenBudget:
    """Best-effort token budget tracker (defensive cost control).

    We track remaining tokens across multiple Gemini calls in a single summary
    request. When the budget is insufficient, we skip additional LLM rewrites
    and fall back to deterministic trimming/padding.
    """

    total_tokens: int
    remaining_tokens: int

    def estimate_tokens(self, text: Optional[str]) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / CHARS_PER_TOKEN_ESTIMATE))

    def can_afford(self, prompt: str, expected_output_tokens: int) -> bool:
        if self.remaining_tokens <= 0:
            return False
        prompt_tokens = self.estimate_tokens(prompt)
        return (prompt_tokens + max(0, int(expected_output_tokens))) <= self.remaining_tokens

    def charge(self, prompt: str, output: str) -> int:
        used = self.estimate_tokens(prompt) + self.estimate_tokens(output)
        self.remaining_tokens = max(0, self.remaining_tokens - used)
        return used


def _summary_token_budget() -> TokenBudget:
    budget_usd = _float_env("GEMINI_COST_PER_SUMMARY_USD", DEFAULT_SUMMARY_BUDGET_USD)
    cost_per_1k = _float_env(
        "GEMINI_COST_PER_1K_TOKENS_USD", DEFAULT_GEMINI_COST_PER_1K_TOKENS_USD
    )
    reserve = _int_env("GEMINI_SUMMARY_TOKEN_RESERVE", DEFAULT_SUMMARY_TOKEN_RESERVE)

    max_tokens = 0
    if budget_usd > 0 and cost_per_1k > 0:
        max_tokens = int((budget_usd / cost_per_1k) * 1000)
        max_tokens = max(0, max_tokens - max(0, reserve))
    return TokenBudget(total_tokens=max_tokens, remaining_tokens=max_tokens)


def _summary_max_output_tokens() -> int:
    return _int_env("GEMINI_MAX_OUTPUT_TOKENS", DEFAULT_GEMINI_MAX_OUTPUT_TOKENS)


def _strip_large_context_block(prompt: str) -> str:
    """Remove the large filing CONTEXT block from a prompt.

    Used for rewrite attempts to avoid re-sending the full filing text, keeping
    token usage bounded while preserving the instruction scaffold.
    """

    if not prompt:
        return prompt

    pattern = re.compile(
        r"(\n\s*CONTEXT:\s*\n)(.*?)(\n\s*FINANCIAL SNAPSHOT\b)",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(
        r"\1[CONTEXT OMITTED FOR REWRITE TO SAVE TOKENS]\3",
        prompt,
        count=1,
    )


def _truncate_prompt_to_token_budget(
    prompt: str,
    *,
    max_prompt_chars: int,
    budget_note: str = "",
) -> str:
    """Truncate the CONTEXT block so the full prompt fits inside max_prompt_chars."""
    if not prompt or max_prompt_chars <= 0:
        return prompt
    if len(prompt) <= max_prompt_chars:
        return prompt

    pattern = re.compile(
        r"(\n\s*CONTEXT:\s*\n)(.*?)(\n\s*FINANCIAL SNAPSHOT\b)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(prompt)
    if not match:
        return prompt[:max_prompt_chars]

    prefix = prompt[: match.start(2)]
    suffix = prompt[match.end(2) :]
    context = match.group(2)

    allowance = max(0, max_prompt_chars - len(prefix) - len(suffix) - len(budget_note))
    truncated_context = context[:allowance]
    return prefix + truncated_context + budget_note + suffix

DETAIL_LEVEL_PROMPTS: Dict[str, str] = {
    "snapshot": "Keep analysis concise (1–2 short paragraphs) and only cite headline metrics that prove the main point.",
    "balanced": "Provide balanced coverage with equal weight on growth, profitability, balance sheet, and guidance.",
    "deep dive": "Offer exhaustive commentary with supporting data points for every section, including subtle nuances from management commentary.",
}

OUTPUT_STYLE_PROMPTS: Dict[str, str] = {
    "narrative": (
        "Write in cohesive, human-sounding paragraphs with strong topic sentences and transitions. "
        "Aim for a clear narrative arc (setup → evidence → tension → resolution) so the memo reads smoothly end-to-end. "
        "Use one bridge sentence at the end of each narrative section to foreshadow the next. "
        "Keep numbers purposeful: outside of the required data blocks/labels, use only 1–2 anchor figures per section and never stack metrics."
    ),
    "bullets": "Favor bullet lists and short sentences. Each bullet should start with a bolded label followed by insights.",
    "mixed": "Open each section with a short paragraph, then follow with a bulleted list of the most actionable takeaways.",
}

COMPLEXITY_LEVEL_PROMPTS: Dict[str, str] = {
    "simple": "Use plain English and avoid jargon. Explain financial concepts simply.",
    "intermediate": "Use standard financial analysis language.",
    "expert": "Use sophisticated financial terminology. Assume the reader is an expert investor.",
}

DEFAULT_HEALTH_RATING_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "framework": "value_investor_default",
    "primary_factor_weighting": "profitability_margins",
    "risk_tolerance": "moderately_conservative",
    "analysis_depth": "key_financial_items",
    "display_style": "score_plus_grade",
}

HEALTH_FRAMEWORK_PROMPTS: Dict[str, str] = {
    "value_investor_default": "Value Investor Default – prioritize cash flow durability, balance sheet strength, and downside protection.",
    "quality_moat_focus": "Quality & Moat Focus – emphasize ROIC consistency, competitive advantage, and earnings stability.",
    "financial_resilience": "Financial Resilience – stress-test liquidity, leverage, refinancing risk, and debt schedules.",
    "growth_sustainability": "Growth Sustainability – evaluate margin expansion, reinvestment efficiency, and the long-term growth path.",
    "user_defined_mix": "User-Defined Mix – treat profitability, risk, liquidity, growth, and efficiency with equal importance.",
}

HEALTH_WEIGHTING_PROMPTS: Dict[str, str] = {
    "profitability_margins": "Profitability & Margins should be the dominant factor.",
    "cash_flow_conversion": "Cash Flow & Conversion Quality should drive most of the score.",
    "balance_sheet_strength": "Balance Sheet Strength & Leverage must weigh most heavily.",
    "liquidity_near_term_risk": "Liquidity & Near-Term Risk factors outrank other drivers.",
    "execution_competitiveness": "Execution & Competitive Position carry the greatest weight.",
}

HEALTH_RISK_PROMPTS: Dict[str, str] = {
    "very_conservative": "Be very conservative and penalize even subtle weaknesses.",
    "moderately_conservative": "Apply a moderately conservative, value-investor style penalty for risks.",
    "balanced": "Use a balanced, neutral tolerance for risks and positives.",
    "moderately_lenient": "Be moderately lenient, highlighting strengths unless risks are severe.",
    "very_lenient": "Be very lenient and focus on upside even if notable risks exist.",
}

HEALTH_ANALYSIS_DEPTH_PROMPTS: Dict[str, str] = {
    "headline_only": "Limit diligence to headline red flags that management highlighted.",
    "key_financial_items": "Inspect key financial statement items – margins, cash flow, debt, and working capital.",
    "full_footnote_review": "Extend analysis through footnotes, including leases, covenants, and adjustments.",
    "accounting_integrity": "Perform an accounting integrity pass focusing on non-GAAP, one-offs, and earnings quality.",
    "forensic_deep_dive": "Run a forensic-style deep dive, hunting for aggressive accounting, accrual spikes, or anomalies.",
}

HEALTH_DISPLAY_PROMPTS: Dict[str, str] = {
    "score_only": "Present only the 0–100 score.",
    "score_plus_grade": "Present the 0–100 score plus the band label (Very Healthy/Healthy/Watch/At Risk).",
    "score_plus_traffic_light": "Present the 0–100 score plus a traffic light (Green/Yellow/Red) indicator.",
    "score_plus_pillars": "Present the 0–100 score plus a four-pillar breakdown (Profitability | Risk | Liquidity | Growth).",
    "score_with_narrative": "Present the 0–100 score alongside a short narrative paragraph explaining the result.",
}


def _clamp_target_length(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(10, min(5000, value))


def _extract_persona_name(investor_focus: Optional[str]) -> Optional[str]:
    """Extract the persona name from the investor_focus prompt text.

    The investor_focus typically contains text like:
    "Role: Howard Marks. Personality: Calm, cycle-aware..."
    or
    "Role: Warren Buffett. Personality: Folksy clarity..."

    Returns the persona name (e.g., "Howard Marks", "Warren Buffett") or None if not found.
    """
    if not investor_focus:
        return None

    # Try to extract from "Role: [Name]." pattern
    role_match = re.search(r"Role:\s*([^.]+)\.", investor_focus, re.IGNORECASE)
    if role_match:
        return role_match.group(1).strip()

    # Try to extract from "As [Name]," pattern
    as_match = re.search(r"As\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", investor_focus)
    if as_match:
        return as_match.group(1).strip()

    # Common persona names to look for
    persona_names = [
        "Warren Buffett",
        "Charlie Munger",
        "Benjamin Graham",
        "Peter Lynch",
        "Ray Dalio",
        "Cathie Wood",
        "Joel Greenblatt",
        "John Bogle",
        "Howard Marks",
        "Bill Ackman",
    ]

    for name in persona_names:
        if name.lower() in investor_focus.lower():
            return name

    return None


def _build_closing_takeaway_description(
    persona_name: Optional[str],
    company_name: str,
    *,
    target_length: Optional[int] = None,
    persona_requested: bool = False,
    budget_words: Optional[int] = None,
    budget_tolerance: int = 10,
) -> Tuple[str, str]:
    """Build a dynamic Closing Takeaway section description based on the selected persona.

    If a persona is selected, the instructions are specifically tailored to that persona's voice.
    If no persona is selected, generic instructions are provided.
    """
    title = "Closing Takeaway"

    if budget_words and budget_words > 0:
        # Budget is for the section body (heading excluded) and must follow the
        # fixed proportional distribution.
        length_guidance = (
            f"Target ~{int(budget_words)} words (±{int(budget_tolerance)}) in the Closing Takeaway body."
        )
    elif target_length and target_length < 350:
        length_guidance = "3-4 COMPLETE sentences (~45-70 words)."
    elif target_length and target_length < 500:
        length_guidance = "4-5 COMPLETE sentences (~70-95 words)."
    else:
        length_guidance = "4-6 COMPLETE sentences (~80-110 words)."

    common_requirements = (
        "Include a substantive Closing Takeaway. Keep this balanced with other sections.\n"
        f"{length_guidance} This is your FINAL INVESTMENT VERDICT.\n"
        "Write as ONE cohesive paragraph (avoid one-sentence throwaways).\n"
        "Do NOT introduce brand-new facts here; synthesize the narrative built in prior sections so the verdict feels inevitable.\n"
        "Anchor the verdict with 1-2 concrete metrics or operating drivers.\n"
        "FORBIDDEN: single-sentence or one-line closings.\n\n"
        "=== SUPPORTING ELEMENTS (BRIEF) ===\n"
        "- Quality assessment: High-quality, average, or poor business (1 sentence)\n"
        "- Key driver: The #1 factor behind your decision (1 sentence)\n"
        "- Trigger: What would change the stance (1 sentence)\n\n"
    )

    persona_verdict_requirement = (
        "=== REQUIRED VERDICT SENTENCE (CANNOT BE OMITTED) ===\n"
        "Your FIRST or LAST sentence MUST state a clear action (BUY/HOLD/SELL) in first person and mention the company.\n"
        "Do NOT use a fixed template; vary phrasing and sentence openings across summaries.\n"
        "Avoid stock openers like 'Where are we in the cycle?' unless uniquely relevant; if you mention the cycle, phrase it differently.\n"
        "Examples (choose a style; do NOT copy verbatim):\n"
        "- 'For my own portfolio, I'd HOLD [Company] at this valuation.'\n"
        "- 'If I had to act today, I'd BUY [Company] because [reason].'\n"
        "- 'My stance is SELL on [Company] until [condition].'\n"
        "VERIFICATION: Does your Closing Takeaway include a first-person BUY/HOLD/SELL sentence? If NO, add one.\n\n"
    )

    objective_verdict_requirement = (
        "=== REQUIRED RECOMMENDATION (CANNOT BE OMITTED) ===\n"
        "Your FIRST or LAST sentence MUST state a clear recommendation (Buy/Hold/Sell) in third person.\n"
        "Do NOT use a fixed template; vary phrasing and sentence openings across summaries.\n"
        "Examples (choose a style; do NOT copy verbatim):\n"
        "- 'A Hold rating appears warranted at current levels.'\n"
        "- 'The appropriate stance is Buy given [reason].'\n"
        "- 'A Sell recommendation is justified until [condition].'\n"
        "VERIFICATION: Does your Closing Takeaway include an explicit Buy/Hold/Sell recommendation? If NO, add one.\n\n"
    )

    completion_requirements = "\nEnsure sentences are complete and punctuated. Avoid trailing off or ellipses.\n"

    if persona_requested and persona_name and persona_name in PERSONA_CLOSING_INSTRUCTIONS:
        # Persona-specific instructions
        persona_instructions = PERSONA_CLOSING_INSTRUCTIONS[persona_name]
        description = (
            common_requirements
            + persona_verdict_requirement
            + f"=== YOU ARE {persona_name.upper()} - WRITE EXACTLY AS THEY WOULD ===\n"
            f"This Closing Takeaway MUST sound like {persona_name} personally wrote it about {company_name}.\n"
            f"Use FIRST PERSON voice throughout ('I', 'my view', 'I would').\n\n"
            f"{persona_instructions}\n"
            f"\nDO NOT write a generic analyst conclusion. Sound EXACTLY like {persona_name}.\n"
            f"The reader should immediately recognize this as {persona_name}'s voice.\n"
            + completion_requirements
            + f"This section MUST provide CLOSURE as {persona_name} giving their final verdict on {company_name}."
        )
    elif persona_requested:
        # Generic persona instructions (user provided a custom persona prompt)
        description = (
            common_requirements
            + persona_verdict_requirement
            + "=== CUSTOM PERSONA MODE (USER-PROVIDED) ===\n"
            "CRITICAL: The user provided a custom investor persona/viewpoint above. You MUST adopt it fully.\n\n"
            "REQUIRED:\n"
            "- Write in FIRST PERSON throughout ('I', 'my view', 'I would').\n"
            "- Anchor the recommendation in 1-2 specific metrics or operating drivers from this memo.\n"
            "- End with a clear first-person BUY/HOLD/SELL sentence that mentions the company.\n\n"
            "FORBIDDEN:\n"
            "- Do NOT suddenly switch to third-person research tone.\n"
            "- Do NOT imitate a famous investor unless the user explicitly asked for that voice.\n"
            + completion_requirements
            + f"This section MUST provide CLOSURE as the selected persona giving a final verdict on {company_name}."
        )
    else:
        # Generic instructions (no persona selected) - HIGH QUALITY OBJECTIVE ANALYSIS
        description = (
            common_requirements
            + objective_verdict_requirement
            + "=== OBJECTIVE ANALYST MODE (NO PERSONA) ===\n"
            "CRITICAL: You are a NEUTRAL PROFESSIONAL ANALYST. You must NOT adopt any persona.\n\n"
            "FORBIDDEN - DO NOT USE:\n"
            "- First person language ('I', 'my view', 'I would', 'I believe', 'my conviction')\n"
            "- Any famous investor's voice or catchphrases (no 'wonderful business', 'moat', 'invert', etc.)\n"
            "- Persona-specific language patterns from Buffett, Munger, Graham, Lynch, Ackman, or any other investor\n"
            "- Folksy analogies or colorful investor expressions\n\n"
            "REQUIRED - USE THIS APPROACH:\n"
            "- Third-person objective language ('The analysis suggests...', 'The data indicates...', 'This company...')\n"
            "- Focus on quantitative metrics: revenue growth %, margins, ROE, debt ratios, valuation multiples\n"
            "- Professional, neutral tone like a research analyst report\n"
            "- Evidence-based conclusions tied to specific financial data\n\n"
            "Provide a balanced, professional conclusion that:\n"
            "- Summarizes the investment case objectively using third-person language\n"
            "- States a clear recommendation (Buy/Hold/Sell) with supporting metrics\n"
            "- Identifies key risks and opportunities based on financial data\n"
            "- Suggests specific metrics to monitor going forward\n"
            + completion_requirements
            + f"This section MUST provide CLOSURE for the analysis of {company_name} using NEUTRAL, OBJECTIVE language."
        )

    return (title, description)


# Persona-specific closing templates for dynamic generation
# CRITICAL: All personas MUST end with a first-person buy/hold/sell recommendation (wording should vary).
PERSONA_CLOSING_INSTRUCTIONS = {
    "Warren Buffett": (
        "As Warren Buffett, your closing MUST:\n"
        "- Use phrases like 'wonderful business', 'moat', 'owner earnings', 'circle of competence'\n"
        "- Reference whether you'd 'hold for decades'\n"
        "- Assess the moat (wide/narrow/non-existent)\n"
        "- Use folksy language and analogies\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be a first-person BUY/HOLD/SELL recommendation that mentions the company (vary wording; do not always use 'I personally would').\n"
        "EXAMPLE: 'This is a wonderful business with a wide moat built on [specific advantage]. "
        "The economics are durable, and I would be comfortable holding for decades. "
        "At current prices, Mr. Market is offering a fair deal for patient capital. For my own portfolio, I'd buy and hold for the long term.'"
    ),
    "Charlie Munger": (
        "As Charlie Munger, your closing MUST:\n"
        "- Use inversion: 'What would make this a terrible investment?'\n"
        "- Discuss incentives alignment\n"
        "- Be blunt and pithy\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a blunt first-person BUY/HOLD/SELL stance (vary wording; do not always use 'I personally would').\n"
        "EXAMPLE: 'Inverting the question: what would make this a disaster? [Answer]. "
        "The incentives are properly aligned. The economics make sense. If I had to act today, I'd buy.'"
    ),
    "Benjamin Graham": (
        "As Benjamin Graham, your closing MUST:\n"
        "- Reference 'margin of safety' explicitly\n"
        "- Discuss intrinsic value vs market price\n"
        "- Use 'intelligent investor' language\n"
        "- Be quantitative and methodical\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a clear, first-person BUY/HOLD/SELL stance (wording should vary).\n"
        "EXAMPLE: 'The margin of safety at current prices is [adequate/insufficient]. "
        "For the intelligent investor, this represents [investment/speculation]. "
        "The balance sheet strength [supports/undermines] the thesis. I'd hold until a wider margin of safety appears.'"
    ),
    "Peter Lynch": (
        "As Peter Lynch, your closing MUST:\n"
        "- Tell 'the story' in simple terms\n"
        "- Reference PEG ratio if applicable\n"
        "- Classify as stalwart/fast grower/turnaround/cyclical\n"
        "- Be enthusiastic if bullish\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a first-person BUY/HOLD/SELL stance with Lynch-like energy (wording should vary).\n"
        "EXAMPLE: 'Here's the story: [simple explanation]. The PEG of [X] says this is [cheap/fair/expensive]. "
        "This is a [category] that I would [verdict]. You don't need an MBA to understand this one. I'd buy it and put it away.'"
    ),
    "Ray Dalio": (
        "As Ray Dalio, your closing MUST:\n"
        "- Reference 'where we are in the cycle'\n"
        "- Discuss risk parity considerations\n"
        "- Mention correlation to macro factors\n"
        "- Use systems thinking language\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a first-person BUY/HOLD/SELL stance plus sizing rationale (wording should vary).\n"
        "EXAMPLE: 'At this point in the cycle, [assessment]. The risk parity consideration suggests [sizing]. "
        "Understanding the machine, I'd hold in moderate size given the current cycle position.'"
    ),
    "Cathie Wood": (
        "As Cathie Wood, your closing MUST:\n"
        "- Reference 'disruptive innovation'\n"
        "- Mention Wright's Law or S-curves if relevant\n"
        "- Give a 5-year or 2030 vision\n"
        "- Express high conviction in innovation\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a first-person BUY/HOLD/SELL stance with conviction (wording should vary).\n"
        "EXAMPLE: 'The disruptive innovation potential here is [assessment]. "
        "Wright's Law suggests costs will [trajectory]. By 2030, [vision]. I'd buy with high conviction for the next 5 years.'"
    ),
    "Joel Greenblatt": (
        "As Joel Greenblatt, your closing MUST:\n"
        "- Reference return on capital and earnings yield\n"
        "- Give a clear Magic Formula verdict: Is it GOOD (high ROC), CHEAP (high earnings yield), or BOTH? The Magic Formula works best when a stock is BOTH good AND cheap.\n"
        "- Be quantitative and direct - cite specific numbers\n"
        "- Assess if this is a 'clean situation' or if there are complications\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY - FINAL SENTENCE):\n"
        "  Your VERY LAST sentence MUST be a first-person recommendation that includes BUY/HOLD/SELL/PASS and mentions the company.\n"
        "  Use one of these styles (do NOT copy verbatim):\n"
        "  * 'My call: HOLD [Company] (but don't add).'\n"
        "  * 'On the Magic Formula, I'd BUY [Company] at this price.'\n"
        "  * 'I'd PASS on [Company] until the screen improves.'\n"
        "EXAMPLE: 'Return on capital is 25%, earnings yield is 8%. By the Magic Formula, this is a good business at a fair price - but not clearly cheap. "
        "The cash generation is strong, but leverage concerns limit the margin of safety. My call: HOLD UBER, but don't add until it screens cheaper.'"
    ),
    "John Bogle": (
        "As John Bogle, your closing MUST:\n"
        "- Reference 'stay the course' and 'costs matter'\n"
        "- Compare individual stock to index fund approach\n"
        "- Use 'haystack vs needle' analogy\n"
        "- Be humble and prudent\n"
        "- STATE A CLEAR VERDICT: Even as an index advocate, give your assessment\n"
        "- Include what would change your view (e.g., 'valuation, competitive threats')\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a clear, humble first-person BUY/HOLD/SELL recommendation (wording should vary).\n"
        "EXAMPLE: 'This is a fine business with exceptional profitability. But why own one needle when you can own the haystack? "
        "Costs matter, and 90% of active managers fail. If valuation became more attractive, I might reconsider. For those who insist on individual stocks, I'd hold this one—but I'd still prefer the index fund.'"
    ),
    "Howard Marks": (
        "As Howard Marks, your closing MUST:\n"
        "- Reference 'second-level thinking'\n"
        "- Discuss 'where we are in the cycle' and 'the pendulum'\n"
        "- Assess risk-reward asymmetry\n"
        "- Consider 'what's priced in'\n"
        "- STATE A CLEAR VERDICT: BUY, HOLD, SELL, or WAIT with your reasoning\n"
        "- Include what would change your view (cycle shift, valuation change)\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a first-person BUY/HOLD/SELL/WAIT stance (wording should vary).\n"
        "EXAMPLE: 'Cycle check: optimism is elevated but not extreme. Second-level thinking suggests the market is not fully pricing in competitive risks. "
        "The risk-reward asymmetry favors caution. If it were my money, I'd hold and wait for better asymmetry.'"
    ),
    "Bill Ackman": (
        "As Bill Ackman, your closing MUST:\n"
        "- Assess if business is 'simple, predictable, free-cash-flow generative'\n"
        "- Identify 'the catalyst' for value creation\n"
        "- State what 'management MUST' do\n"
        "- Express conviction level\n"
        "- STATE A CLEAR VERDICT: BUY, HOLD, or SELL with conviction level\n"
        "- Include what would change your view (catalyst, management action)\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): End with a first-person BUY/HOLD/SELL stance with conviction (wording should vary).\n"
        "EXAMPLE: 'This is simple, predictable, and free-cash-flow generative—exactly what I look for. "
        "The catalyst for value creation is clear. Management MUST maintain discipline. I'd buy with high conviction at these levels.'"
    ),
}


def _count_words(text: str) -> int:
    """Approximate MS Word-style counting by using whitespace tokens and stripping punctuation."""
    if not text:
        return 0
    punct = string.punctuation + "\u201c\u201d\u2018\u2019\u2014\u2013\u2026"
    count = 0
    for raw_token in text.split():
        token = raw_token.strip(punct)
        if token:
            count += 1
    return count


_MICRO_TRIM_SKIP_SECTIONS = {
    "key metrics",
    "key data appendix",
    "financial health rating",
    "closing takeaway",
}


def _micro_trim_filler_words(text: str, max_remove: int) -> Tuple[str, int]:
    """Remove low-information filler words to shave tiny overages without breaking sentences.

    This is intentionally conservative and only used when we're *barely* outside the
    user's strict word band (e.g., off by 1-3 words). It avoids heavy truncation that
    could drop entire sentences/sections.

    Returns (new_text, removed_count).
    """
    if not text or max_remove <= 0:
        return text, 0

    # Only remove words that rarely change meaning in finance memos.
    # Keep this list small + boring to avoid semantic drift.
    removable_words = [
        "overall",
        "notably",
        "generally",
        "typically",
        "largely",
        "mainly",
        "primarily",
        "effectively",
        "essentially",
        "basically",
        "relatively",
        "somewhat",
        "quite",
        "very",
        "really",
        "currently",
        "significant",
        "substantial",
        "materially",
    ]

    heading_re = re.compile(r"^\s*##\s*(.+?)\s*$")
    current_section: Optional[str] = None
    removed = 0
    out_lines: List[str] = []

    # Common discourse markers that can be removed *with their trailing comma*.
    # (Each removes exactly 1 word.)
    discourse_re = re.compile(
        r"\b(?:Overall|Notably|Importantly)\s*,\s+", re.IGNORECASE
    )
    phrase_trim_rules: List[Tuple[re.Pattern[str], int]] = [
        (re.compile(r"\bright\s+now\s+", re.IGNORECASE), 2),
        (re.compile(r"\bat\s+this\s+juncture\s+", re.IGNORECASE), 3),
    ]

    def _cleanup_spaces(s: str) -> str:
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\s+([,.;:!?])", r"\1", s)
        return s

    for line in text.splitlines():
        m = heading_re.match(line)
        if m:
            current_section = " ".join(m.group(1).lower().split())
            out_lines.append(line)
            continue

        if removed >= max_remove:
            out_lines.append(line)
            continue

        if current_section and any(
            current_section.startswith(skip) for skip in _MICRO_TRIM_SKIP_SECTIONS
        ):
            out_lines.append(line)
            continue

        # Skip strict metric lines (arrow format) even outside Key Metrics.
        if line.lstrip().startswith("→"):
            out_lines.append(line)
            continue

        working = line
        # 1) Remove discourse markers like "Overall," first.
        if removed < max_remove:
            working, n = discourse_re.subn("", working, count=1)
            if n:
                removed += 1

        # 1b) Remove a couple of common filler phrases (multi-word, counted precisely).
        if removed < max_remove:
            for rule_re, rule_words in phrase_trim_rules:
                if removed + rule_words > max_remove:
                    continue
                working, n = rule_re.subn("", working, count=1)
                if n:
                    removed += rule_words
                    break

        # 2) Remove standalone filler words.
        # Remove one word per pass to keep edits minimal.
        for w in removable_words:
            if removed >= max_remove:
                break
            pattern = re.compile(rf"\b{re.escape(w)}\b\s+", re.IGNORECASE)
            working, n = pattern.subn("", working, count=1)
            if n:
                removed += 1

        out_lines.append(_cleanup_spaces(working))

    return "\n".join(out_lines), removed


def _enforce_whitespace_word_band(
    text: str,
    target_length: int,
    tolerance: int = 10,
    *,
    allow_padding: bool = True,
    dedupe: bool = True,
) -> str:
    """Enforce the word band using user-visible whitespace token counting.

    This enforces BOTH:
    - raw whitespace token count (`len(text.split())`, which counts markdown markers like `##`)
    - punctuation-stripped count (`_count_words`, closer to MS Word)

    Reason: the codebase and UI/tests use both styles in different places, so we
    keep the output safely inside the band under either interpretation.
    """
    if not text or target_length is None:
        return text

    lower = target_length - tolerance
    upper = target_length + tolerance

    def _strip_markdown_list_tokens(value: str) -> str:
        """Reduce whitespace-only markdown tokens without changing semantic words.

        These tokens (e.g., leading '-' bullets) inflate `len(text.split())` but are
        ignored by `_count_words()` and can make the dual-band constraint unsatisfiable.
        """
        if not value:
            return value

        out_lines: List[str] = []
        changed = False
        for raw in value.splitlines():
            line = raw.rstrip()
            # Drop standalone list-marker lines.
            if re.match(r"^\s*[-*•]\s*$", line):
                changed = True
                continue
            # Strip leading list markers (keep the content).
            stripped = re.sub(r"^(\s*)[-*•]\s+", r"\1", line)
            if stripped != line:
                changed = True
            out_lines.append(stripped)

        if not changed:
            return value
        return "\n".join(out_lines).strip()

    for _ in range(5):
        split_count = len(text.split())
        stripped_count = _count_words(text)

        if lower <= split_count <= upper and lower <= stripped_count <= upper:
            if dedupe:
                cleaned = _dedupe_consecutive_sentences(text)
                cleaned = _deduplicate_sentences(cleaned)
                if cleaned != text:
                    text = cleaned
                    continue
            return text

        if split_count > upper or stripped_count > upper:
            # If whitespace tokens are dominating the overage, try removing purely
            # presentational markdown tokens first (no semantic loss).
            if split_count > upper and (split_count - stripped_count) > tolerance:
                cleaned = _strip_markdown_list_tokens(text)
                if cleaned and cleaned != text and len(cleaned.split()) < split_count:
                    text = cleaned
                    continue

            excess = max(split_count - upper, stripped_count - upper)

            # For tiny overages, prefer micro-trimming filler words over dropping sentences.
            if excess <= 15:
                micro, removed = _micro_trim_filler_words(text, excess)
                if removed > 0 and (
                    len(micro.split()) < split_count
                    or _count_words(micro) < stripped_count
                ):
                    text = micro
                    continue

            # Fall back: reduce the backend word count by the same excess.
            target_words = max(lower, _count_words(text) - excess)
            text = _trim_preserving_headings(text, target_words)
            continue

        # Under target by whitespace count.
        deficit = max(lower - split_count, lower - stripped_count)
        if not allow_padding:
            return text
        text = _distribute_padding_across_sections(text, deficit)

    # Final safety: deterministic clamp if the iterative pass didn't converge.
    final_split = len(text.split())
    final_stripped = _count_words(text)
    if lower <= final_split <= upper and lower <= final_stripped <= upper:
        return text

    # Fallback: clamp and then re-check BOTH counting methods. `_clamp_to_band()` enforces
    # the MS-Word-style count only; we need to guarantee the UI-visible whitespace count
    # stays inside the same band as well.
    text = _clamp_to_band(text, lower, upper, allow_padding=allow_padding)

    for _ in range(8):
        split_count = len(text.split())
        stripped_count = _count_words(text)
        if lower <= split_count <= upper and lower <= stripped_count <= upper:
            return text

        if split_count > upper or stripped_count > upper:
            if split_count > upper and (split_count - stripped_count) > tolerance:
                cleaned = _strip_markdown_list_tokens(text)
                if cleaned and cleaned != text and len(cleaned.split()) < split_count:
                    text = cleaned
                    continue

            # Whitespace tokens can exceed the stripped count due to markdown markers
            # (e.g., leading '##'). Compute the effective upper bound for stripped words
            # that will still satisfy the whitespace band.
            delta = max(0, split_count - stripped_count)
            effective_upper = max(lower, upper - delta)

            excess = max(split_count - upper, stripped_count - upper, 1)
            target_words = max(lower, min(effective_upper, stripped_count - excess))
            text = _trim_preserving_headings(text, target_words)
            continue

        # Under target: pad (deterministically) into sections.
        deficit = max(lower - split_count, lower - stripped_count)
        if deficit <= 0:
            return text
        if not allow_padding:
            return text
        text = _distribute_padding_across_sections(text, deficit)

    return text


def _ensure_final_strict_word_band(
    text: str,
    target_length: int,
    *,
    include_health_rating: bool,
    tolerance: int = 10,
) -> str:
    """Final, idempotent guard to ensure the strict total word band after all other processing."""
    if not text or target_length is None:
        return text

    lower = int(target_length) - int(tolerance)
    upper = int(target_length) + int(tolerance)
    split_count = len((text or "").split())
    stripped_count = _count_words(text or "")
    if lower <= split_count <= upper and lower <= stripped_count <= upper:
        return text

    enforced = _enforce_whitespace_word_band(
        text, int(target_length), tolerance=int(tolerance), allow_padding=True
    )
    return _enforce_section_order(
        enforced, include_health_rating=include_health_rating
    )


def _call_gemini_client(
    gemini_client,
    prompt: str,
    *,
    allow_stream: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    stage_name: str = "Generating",
    expected_tokens: int = 4000,
) -> str:
    """
    Generate text using the Gemini client, gracefully falling back when streaming helpers
    are unavailable (e.g., in tests that mock only the underlying model).
    """
    if allow_stream and hasattr(gemini_client, "stream_generate_content"):
        try:
            return gemini_client.stream_generate_content(
                prompt,
                progress_callback=progress_callback,
                stage_name=stage_name,
                expected_tokens=expected_tokens,
            )
        except ValueError as exc:
            if "request_options" in str(exc) and hasattr(
                gemini_client, "force_http_fallback"
            ):
                gemini_client.force_http_fallback = True
                try:
                    return gemini_client.stream_generate_content(
                        prompt,
                        progress_callback=progress_callback,
                        stage_name=stage_name,
                        expected_tokens=expected_tokens,
                    )
                except Exception:
                    logger.warning(
                        "Streaming failed after forcing HTTP fallback; using non-stream generation."
                    )
            else:
                logger.warning(
                    "Streaming generation failed with ValueError (%s); using non-stream generation.",
                    exc,
                )
        except Exception as exc:
            logger.warning(
                "Streaming generation unavailable (%s); using non-stream generation.",
                exc,
            )

    generator = None
    if hasattr(gemini_client, "generate_content"):
        generator = gemini_client.generate_content
    elif getattr(gemini_client, "model", None) and hasattr(
        gemini_client.model, "generate_content"
    ):
        generator = gemini_client.model.generate_content

    if not generator:
        raise AttributeError("Gemini client does not expose generate_content")

    response = generator(prompt)
    return getattr(response, "text", response)


def _ensure_gemini_client_interface(gemini_client):
    """
    Ensure the provided gemini_client exposes a stream-compatible interface.
    Adds a shim when only a bare generate_content/model.generate_content exists (e.g., test doubles).
    """
    if hasattr(gemini_client, "stream_generate_content"):
        return gemini_client

    generator = None
    if hasattr(gemini_client, "generate_content"):
        generator = gemini_client.generate_content
    elif getattr(gemini_client, "model", None) and hasattr(
        gemini_client.model, "generate_content"
    ):
        generator = gemini_client.model.generate_content

    if generator:

        def _shim(prompt: str, **kwargs):
            response = generator(prompt)
            return getattr(response, "text", response)

        setattr(gemini_client, "stream_generate_content", _shim)
        return gemini_client

    raise AttributeError("Gemini client does not expose a generation method")


def _fix_inline_section_headers(text: str) -> str:
    """Fix section headers that appear inline with content instead of on their own lines.

    This handles patterns like:
    "...business. ## Executive Summary As Bill Ackman, I seek..."

    And converts them to:
    "...business.

    ## Executive Summary

    As Bill Ackman, I seek..."
    """
    if not text:
        return text

    # List of all section headers that should be on their own lines
    section_headers = [
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Management Discussion and Analysis",
        "Risk Factors",
        "Competitive Landscape",
        "Strategic Initiatives & Capital Allocation",
        "Strategic Initiatives and Capital Allocation",
        "Key Metrics",
        "Key Data Appendix",
        "Closing Takeaway",
    ]

    result = text

    # UNIVERSAL PATTERN: First, ensure ANY ## header has proper newlines before it
    # This catches cases where headers appear inline regardless of surrounding text
    # Pattern: any character (not newline) followed by space(s) and ##
    result = re.sub(r"([^\n])\s*(##\s*)", r"\1\n\n\2", result)

    # Also ensure newlines after headers before content
    result = re.sub(r"(##\s*[^\n]+)\n([^\n#])", r"\1\n\n\2", result)

    for header in section_headers:
        # Pattern 1: Header appears after punctuation on same line (now redundant but kept for robustness)
        # e.g., "...business. ## Executive Summary As Bill..."
        pattern1 = re.compile(
            rf"([.!?])\s*(?:##?\s*)?({re.escape(header)})\s+(\S)", re.IGNORECASE
        )
        result = pattern1.sub(
            lambda m: f"{m.group(1)}\n\n## {header}\n\n{m.group(3)}", result
        )

        # Pattern 2: Header appears mid-sentence without punctuation
        # e.g., "some text ## Executive Summary more text"
        # Only add period if the character before isn't already punctuation
        # IMPORTANT: Only treat this as an *inline* header when it's on the SAME
        # line. If we allow \s+ here, we may match across newlines and accidentally
        # add periods to the end of the previous section (e.g., Key Metrics rows).
        pattern2 = re.compile(
            rf"([^.!?\s\n])[ \t]+(?:##?\s*)({re.escape(header)})[ \t]+(\S)",
            re.IGNORECASE,
        )
        result = pattern2.sub(
            lambda m: f"{m.group(1)}.\n\n## {header}\n\n{m.group(3)}", result
        )

        # Pattern 3: Header at very start of text without ##
        pattern3 = re.compile(
            rf"^(?:##?\s*)?({re.escape(header)})\s*\n?", re.IGNORECASE | re.MULTILINE
        )
        if re.match(pattern3, result):
            result = pattern3.sub(f"## {header}\n\n", result, count=1)

        # Pattern 4: Header without ## prefix appearing after newline
        # e.g., "\nExecutive Summary\n" should become "\n## Executive Summary\n"
        pattern4 = re.compile(rf"\n({re.escape(header)})\s*\n", re.IGNORECASE)
        result = pattern4.sub(f"\n\n## {header}\n\n", result)

    # Clean up excessive newlines (more than 3 consecutive)
    result = re.sub(r"\n{4,}", "\n\n\n", result)

    # Ensure ## headers are properly formatted (normalize # count)
    result = re.sub(r"(\n|^)#{1,6}\s+", r"\1## ", result)

    # Clean up double periods that might have been introduced
    result = re.sub(r"\.{2,}", ".", result)

    # Final pass: ensure every ## header has a blank line before it
    result = re.sub(r"([^\n])\n(## )", r"\1\n\n\2", result)

    # And a blank line after header lines (header line = starts with ## and ends at newline)
    result = re.sub(r"(## [^\n]+)\n([^\n])", r"\1\n\n\2", result)

    return result


def _remove_filler_phrases(text: str) -> str:
    """
    Remove filler phrases that slip through LLM generation.
    Safety net for phrases that should never appear in output.
    """
    if not text:
        return text

    # Comprehensive filler patterns (regex to catch variations)
    # NOTE: Prefer whitespace-tolerant patterns for short slogans because the model
    # can introduce line breaks / non-breaking spaces between words.
    filler_patterns = [
        r"Additional detail covers?\s+[^.]*\.",
        r"Capital allocation remarks?\s+[^.]*\.",
        r"Further notes? address(?:es)?\s+[^.]*\.",
        r"Risk coverage includes?\s+[^.]*\.",
        r"The analysis (?:also )?outlines?\s+[^.]*\.",
        r"Valuation context compares?\s+[^.]*\.",
        r"Management discussion covers?\s+[^.]*\.",
        r"Strategic initiatives include\s+[^.]*\.",
        r"Add liquidity and leverage observations[^.]*\.",
        r"Tie capital deployment[^.]*\.",
        r"Clarify risk scenarios[^.]*\.",
        r"Expand margin and cash conversion commentary[^.]*\.",
        # Imperative "monitor/track/watch" filler often added to hit word counts
        r"(?:Additionally,?\s*)?\bMonitor\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bTrack\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bWatch\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bAssess\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bReview\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bCompare\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bConsider\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bEvaluate\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bTest\b[^.]*\.",
        r"(?:Additionally,?\s*)?\bBenchmark\b[^.]*\.",
        # Legacy padding slogans (users perceive these as low-quality/random filler)
        r"\bEarnings[\s\u00A0]+quality[\s\u00A0]+is[\s\u00A0]+the[\s\u00A0]+key[\s\u00A0]+question\.",
        r"\bDurability[\s\u00A0]+matters[\s\u00A0]+more[\s\u00A0]+than[\s\u00A0]+optics\.",
        r"\bFocus[\s\u00A0]+on[\s\u00A0]+what[\s\u00A0]+is[\s\u00A0]+repeatable\.",
        r"\bCash[\s\u00A0]+flow[\s\u00A0]+anchors[\s\u00A0]+the[\s\u00A0]+thesis\.",
        r"\bMargins[\s\u00A0]+must[\s\u00A0]+hold[\s\u00A0]+through[\s\u00A0]+competition\.",
        r"\bLeverage[\s\u00A0]+shapes[\s\u00A0]+downside[\s\u00A0]+risk\.",
        r"\bScale[\s\u00A0]+must[\s\u00A0]+translate[\s\u00A0]+to[\s\u00A0]+profit\.",
        r"\bUnit[\s\u00A0]+economics[\s\u00A0]+should[\s\u00A0]+improve[\s\u00A0]+with[\s\u00A0]+scale\.",
        r"\bValuation[\s\u00A0]+should[\s\u00A0]+match[\s\u00A0]+durability\.",
        # Match "One-off" across hyphen variants (ASCII hyphen, non-breaking hyphen, en/em dashes)
        r"\bOne[-\u2010\u2011\u2013\u2014]?off gains should be discounted\.",
        # Catch partial sentences
        r"Additional detail covers?\.?\s*$",
        r"Further notes? address\.?\s*$",
        r"Risk coverage includes?\.?\s*$",
        r"Add liquidity and leverage observations\.?\s*$",
        r"Tie capital deployment\.?\s*$",
        r"Clarify risk scenarios\.?\s*$",
    ]

    result = text
    removed_count = 0

    for pattern in filler_patterns:
        matches = re.findall(pattern, result, re.IGNORECASE | re.MULTILINE)
        if matches:
            removed_count += len(matches)
            result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.MULTILINE)

    if removed_count > 0:
        logger.info(f"Post-processing removed {removed_count} filler phrase(s)")

    # Clean up double spaces or hanging punctuation.
    # IMPORTANT: Do NOT collapse newlines (markdown structure). Only collapse spaces/tabs.
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"[ \t]+\.", ".", result)
    result = re.sub(r"\.\s*\.", ".", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result


_GENERIC_HEURISTIC_SENTENCES = [
    # These phrases are commonly produced as standalone “finance advice” one-liners.
    # When they appear as isolated paragraphs, users perceive them as low-quality and
    # disconnected from the section narrative.
    "If reported EPS beats but cash lags, the gap often sits in working capital, deferred revenue, or other timing items that unwind over quarters.",
    "Margin durability depends on pricing power and disciplined reinvestment.",
    "Working-capital swings can overstate performance when demand is softening.",
    "Capex intensity matters because it determines how much revenue converts to cash.",
    "Leverage matters most when growth slows and refinancing windows tighten.",
    "A durable moat often appears in stable returns on invested capital and pricing discipline, while erosion shows up first in rising customer-acquisition intensity.",
    "Acquisition-driven growth needs an integration lens; synergy targets are real only when they translate into margin and cash conversion, not pro forma adjustments.",
    "It is worth separating organic operating momentum from accounting noise, especially when stock-based compensation, FX, or restructuring items swing earnings.",
    "The main bear path is margin pressure plus higher reinvestment, flattening free cash flow even if revenue holds up.",
    "Margin resilience hinges on mix and pricing, so product and region shifts can matter more than headline growth when competition heats up.",
    "What matters is whether operating leverage shows up in both margins and cash, not just adjusted metrics or one-time items.",
    "Capital allocation that compounds value usually looks like reinvesting where returns are clear and keeping dilution and acquisition premiums contained.",
]


def _remove_generic_heuristic_paragraphs(text: str) -> str:
    """Remove generic heuristic sentences that read like disconnected filler.

    These phrases are common “finance advice” boilerplate. If they appear as
    standalone one-liners (or as tacked-on sentences), users perceive them as
    low-quality and disconnected from the section narrative.
    """
    if not text:
        return text

    def _norm_sentence(s: str) -> str:
        s = (s or "").replace("\u00A0", " ")
        s = " ".join(s.split())
        return s.lower().rstrip(".!?")

    banned = {_norm_sentence(s) for s in _GENERIC_HEURISTIC_SENTENCES if s}
    if not banned:
        return text

    heading_regex = re.compile(r"^\s*##\s+.+", re.MULTILINE)
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []
    preamble: List[str] = []
    first_heading_seen = False

    for line in (text or "").splitlines():
        if heading_regex.match(line):
            if not first_heading_seen and buffer:
                preamble = buffer[:]
                buffer = []
            first_heading_seen = True
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).rstrip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)
        else:
            preamble.append(line)

    preamble_text = "\n".join(preamble).rstrip()
    if current_heading is not None:
        sections.append((current_heading, "\n".join(buffer).rstrip()))

    if not sections:
        return text

    target_sections = {
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Closing Takeaway",
    }

    def _is_structured_paragraph(paragraph: str) -> bool:
        stripped = (paragraph or "").lstrip()
        return bool(
            stripped.startswith(("→", "- ", "* ", "• "))
            or stripped.startswith("**")
        )

    rebuilt_sections: List[str] = []
    for heading, body in sections:
        section_name = _standard_section_name_from_heading(heading)
        raw_body = (body or "").strip()

        if section_name not in target_sections or not raw_body:
            section_text = f"{heading}\n\n{raw_body}".strip() if raw_body else heading.strip()
            rebuilt_sections.append(section_text)
            continue

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw_body) if p.strip()]
        kept: List[str] = []
        for paragraph in paragraphs:
            if _is_structured_paragraph(paragraph):
                kept.append(paragraph)
                continue
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip()]
            norms = [_norm_sentence(s) for s in sentences if s.strip()]
            if not norms:
                kept.append(paragraph)
                continue

            # Drop banned heuristic sentences even when they are appended to
            # an otherwise good paragraph. If the paragraph becomes empty,
            # drop the paragraph entirely.
            filtered: List[str] = [
                s for s, n in zip(sentences, norms) if n and n not in banned
            ]
            if not filtered:
                continue
            kept.append(" ".join(filtered).strip())

        rebuilt_body = "\n\n".join(kept).strip()
        section_text = f"{heading}\n\n{rebuilt_body}".strip() if rebuilt_body else heading.strip()
        rebuilt_sections.append(section_text)

    rebuilt = "\n\n".join([s for s in ([preamble_text] if preamble_text else []) + rebuilt_sections if s]).strip()
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    return rebuilt


def _fix_trailing_ellipsis(text: str) -> str:
    """Fix sentences that trail off with ellipsis (...) or incomplete phrases.

    This function finds sentences ending with '...' or incomplete trailing patterns and either:
    1. Completes them with contextually appropriate endings, or
    2. Truncates to the last complete sentence in that paragraph

    Handles both line-ending ellipsis and mid-paragraph ellipsis.
    """
    if not text:
        return text

    # Comprehensive trailing patterns and their completions
    # Patterns work on both explicit "..." and incomplete trailing phrases
    ellipsis_fixes = [
        # ===== PERSONA-SPECIFIC PATTERNS (Howard Marks, Warren Buffett, etc.) =====
        (
            r"I would\s*\.{2,}\s*$",
            "I would proceed with caution given current valuations.",
        ),
        (r"I would\s*$", "I would proceed with caution given current valuations."),
        (
            r"I need to\s*\.{2,}",
            "I need to see clearer evidence before committing capital.",
        ),
        (r"I believe\s*\.{2,}", "I believe caution is warranted at current levels."),
        (
            r"I am concerned about\s*\.{2,}",
            "I am concerned about the sustainability of current trends.",
        ),
        (
            r"Given my focus on\s*\.{2,}",
            "Given my focus on risk-reward asymmetry, I remain cautious.",
        ),
        (r"I prefer to\s*\.{2,}", "I prefer to wait for a more favorable entry point."),
        # ===== EXECUTIVE SUMMARY / CLOSING PATTERNS =====
        (
            r"sustainability and the\s*\.{2,}",
            "sustainability and the long-term durability of these exceptional margins.",
        ),
        (
            r"sustainability and the\s*$",
            "sustainability and the long-term durability of these exceptional margins.",
        ),
        (r"and the\s*\.{2,}\s*$", "and the implications for long-term value creation."),
        (r"and the\s*$", "and the implications for long-term value creation."),
        (
            r"but the current\s*\.{2,}",
            "but the current valuation leaves limited margin of safety.",
        ),
        (
            r"raises concerns about\s*\.{2,}",
            "raises concerns about the sustainability of exceptional results.",
        ),
        # ===== MD&A / MANAGEMENT PATTERNS =====
        (
            r"uncertainties in global\s*\.{2,}",
            "uncertainties in the global supply chain and macroeconomic environment.",
        ),
        (
            r"uncertainties in global\s*$",
            "uncertainties in the global supply chain and macroeconomic environment.",
        ),
        (r"in global\s*\.{2,}", "in global markets and supply chains."),
        (r"in global\s*$", "in global markets and supply chains."),
        (
            r"supply chain complexities\s*\.{2,}",
            "supply chain complexities that require ongoing attention.",
        ),
        (
            r"strategic agility\s*\.{2,}",
            "strategic agility to maintain market leadership.",
        ),
        # ===== RISK FACTOR PATTERNS =====
        (
            r"a geopolitical\s*\.{2,}",
            "a geopolitical risk that warrants close monitoring.",
        ),
        (r"a geopolitical\s*$", "a geopolitical risk that warrants close monitoring."),
        (
            r", a geopolitical\s*\.{2,}",
            ", a geopolitical concern that warrants attention.",
        ),
        (r", a geopolitical\s*$", ", a geopolitical concern that warrants attention."),
        (
            r"in a key market\s*\.{2,}",
            "in a key market that could materially impact results.",
        ),
        (
            r"in a key market\s*$",
            "in a key market that could materially impact results.",
        ),
        (
            r"materially affecting\s*\.{2,}",
            "materially affecting the company's financial performance.",
        ),
        (
            r"geopolitical instability\s*\.{2,}",
            "geopolitical instability that could disrupt operations.",
        ),
        (
            r"capacity constraints\s*\.{2,}",
            "capacity constraints that could limit production.",
        ),
        # ===== COMPETITIVE LANDSCAPE PATTERNS =====
        (r"NVIDIA's\s*\.{2,}", "NVIDIA's competitive positioning and pricing power."),
        (r"NVIDIA's\s*$", "NVIDIA's competitive positioning and pricing power."),
        (
            r"reliance on NVIDIA's\s*\.{2,}",
            "reliance on NVIDIA's chips and potentially developing alternatives.",
        ),
        (
            r"reliance on NVIDIA's\s*$",
            "reliance on NVIDIA's chips and potentially developing alternatives.",
        ),
        (
            r"competitive\s+strategies\s*\.{2,}",
            "competitive strategies and market positioning.",
        ),
        (
            r"competitive\s+strategies\s*$",
            "competitive strategies and market positioning.",
        ),
        (
            r"potentially reducing their\s*\.{2,}",
            "potentially reducing their dependency on external suppliers.",
        ),
        (
            r"potentially reducing their\s*$",
            "potentially reducing their dependency on external suppliers.",
        ),
        (
            r"hyperscalers like\s*\.{2,}",
            "hyperscalers like Google, Amazon, and Microsoft.",
        ),
        (r"eroding\s*\.{2,}", "eroding market share over time."),
        (
            r"concentration\s*\.{2,}",
            "concentration risk that investors should monitor.",
        ),
        # ===== STRATEGIC INITIATIVES PATTERNS =====
        (
            r"technological\s+advancements\s*\.{2,}",
            "technological advancements and market adoption milestones.",
        ),
        (
            r"technological\s+advancements\s*$",
            "technological advancements and market adoption milestones.",
        ),
        (
            r"product launches and\s*\.{2,}",
            "product launches and technological innovations.",
        ),
        (
            r"product launches and\s*$",
            "product launches and technological innovations.",
        ),
        (
            r"articulated, along with\s*\.{2,}",
            "articulated, along with clear performance metrics and timelines.",
        ),
        (
            r"articulated, along with\s*$",
            "articulated, along with clear performance metrics and timelines.",
        ),
        (r"value creation and\s*\.{2,}", "value creation and shareholder returns."),
        # ===== GENERIC TRAILING PATTERNS =====
        (r"global\s*\.{2,}", "global market dynamics and competitive pressures."),
        (r"reliance on\s*\.{2,}", "reliance on key suppliers and partners."),
        (r"securing and\s*\.{2,}", "securing and maintaining market position."),
        (r"potentially hindering\s*\.{2,}", "potentially hindering future growth."),
        (
            r"potentially eroding\s*\.{2,}",
            "potentially eroding competitive advantages.",
        ),
        (
            r"driven by the\s*\.{2,}",
            "driven by strong demand and operational execution.",
        ),
        (r"driven by\s*\.{2,}", "driven by favorable market conditions."),
        # ===== PATTERNS ENDING WITH PREPOSITIONS/ARTICLES =====
        (
            r",\s+but\s+the\s*\.{2,}\s*$",
            ", but the risks remain manageable for long-term investors.",
        ),
        (
            r",\s+but\s+the\s*$",
            ", but the risks remain manageable for long-term investors.",
        ),
        (r",\s+although\s+the\s*\.{2,}", ", although the outlook remains uncertain."),
        (r",\s+although\s+the\s*$", ", although the outlook remains uncertain."),
        (
            r",\s+while\s+the\s*\.{2,}",
            ", while the opportunity set remains compelling.",
        ),
        (r",\s+while\s+the\s*$", ", while the opportunity set remains compelling."),
        (
            r",\s+however\s+the\s*\.{2,}",
            ", however the valuation provides some cushion.",
        ),
        (r",\s+however\s+the\s*$", ", however the valuation provides some cushion."),
        # ===== INCOMPLETE ARTICLE/PREPOSITION ENDINGS =====
        (r"\bthe\s*\.{2,}\s*$", "the implications for investors."),
        (r"\ba\s*\.{2,}\s*$", "a material consideration for investors."),
        (r"\ban\s*\.{2,}\s*$", "an important factor to monitor."),
        (r"\bto\s*\.{2,}\s*$", "to monitor closely."),
        (r"\bof\s*\.{2,}\s*$", "of significant importance."),
        (r"\bfor\s*\.{2,}\s*$", "for careful consideration."),
        (r"\bwith\s*\.{2,}\s*$", "with appropriate risk management."),
        (r"\bin\s*\.{2,}\s*$", "in the current market environment."),
    ]

    result = text

    # Apply pattern-based fixes to the full text
    for pattern, replacement in ellipsis_fixes:
        result = re.sub(
            pattern, replacement, result, flags=re.IGNORECASE | re.MULTILINE
        )

    # Handle any remaining ellipsis by finding and fixing them
    # Split into paragraphs (double newline) to preserve structure
    paragraphs = re.split(r"(\n\n+)", result)
    fixed_paragraphs = []

    for para in paragraphs:
        # Skip paragraph separators
        if re.match(r"^\n+$", para):
            fixed_paragraphs.append(para)
            continue

        # Check for remaining ellipsis in this paragraph
        if re.search(r"\.{2,}", para):
            # Find all ellipsis positions and fix each
            while re.search(r"\.{2,}", para):
                match = re.search(r"\.{2,}", para)
                if not match:
                    break

                pos = match.start()
                # Get text before ellipsis
                before = para[:pos].rstrip()
                after = para[match.end() :].lstrip()

                # Find the last complete sentence before this point
                last_punct = max(
                    before.rfind(". "),
                    before.rfind("! "),
                    before.rfind("? "),
                    before.rfind(".\n"),
                )

                if last_punct > len(before) * 0.3:
                    # Truncate to last complete sentence
                    para = before[: last_punct + 1]
                    if after and not after.startswith("\n"):
                        para += " " + after
                    else:
                        para += after
                else:
                    # Add a contextual completion based on surrounding text
                    completion = _get_contextual_completion(before)
                    para = (
                        before
                        + completion
                        + (
                            " " + after
                            if after and not after.startswith("\n")
                            else after
                        )
                    )

        fixed_paragraphs.append(para)

    return "".join(fixed_paragraphs)


def _get_contextual_completion(text: str) -> str:
    """Generate a contextual completion for incomplete text based on keywords."""
    text_lower = text.lower()

    # Risk-related context
    if any(
        kw in text_lower
        for kw in ["risk", "concern", "threat", "vulnerable", "exposure"]
    ):
        return ", which warrants careful monitoring by investors."

    # Competition-related context
    if any(
        kw in text_lower for kw in ["compet", "rival", "market share", "amd", "intel"]
    ):
        return ", presenting ongoing competitive challenges."

    # Financial/valuation context
    if any(
        kw in text_lower
        for kw in ["margin", "profit", "revenue", "growth", "valuation"]
    ):
        return ", which impacts the investment thesis."

    # Management/strategy context
    if any(
        kw in text_lower for kw in ["management", "strategy", "initiative", "capital"]
    ):
        return ", requiring continued execution from management."

    # Geopolitical context
    if any(kw in text_lower for kw in ["geopolitical", "china", "taiwan", "export"]):
        return ", a factor that requires ongoing monitoring."

    # Supply chain context
    if any(
        kw in text_lower for kw in ["supply", "manufacturing", "tsmc", "production"]
    ):
        return ", impacting production capabilities."

    # Default completion
    return ", which warrants careful consideration."


def _fix_health_score_in_summary(
    summary_text: str,
    pre_calculated_score: Optional[float],
    pre_calculated_band: Optional[str],
) -> str:
    """
    Post-process the summary to fix any health score mismatch.

    The AI sometimes ignores the pre-calculated score instruction and generates
    its own score. This function finds and replaces incorrect scores in the
    Financial Health Rating section.
    """
    if not summary_text or pre_calculated_score is None or not pre_calculated_band:
        return summary_text

    # Pattern to match the Financial Health Rating section header and first line
    # Matches patterns like: "## Financial Health Rating" followed by score patterns
    fhr_section_pattern = re.compile(
        r"(##\s*Financial Health Rating\s*\n+"  # Section header
        r"[^#]*?)"  # Any content before the score
        r"(\d{1,3}(?:\.\d+)?/100\s*"  # The score (e.g., "1/100" or "62/100" or "51.1/100")
        r"(?:\([A-Z]{1,3}\)\s*)?"  # Optional grade/abbrev in parens
        r"-?\s*(?:Very Healthy|Healthy|Watch|At Risk)?)",  # Optional band
        re.IGNORECASE | re.DOTALL,
    )

    # Also match arrow-prefixed scores like "→\n1/100 - Watch"
    arrow_score_pattern = re.compile(
        r"(→\s*\n?\s*)"  # Arrow prefix
        r"(\d{1,3}(?:\.\d+)?/100\s*"  # Score
        r"(?:\([A-Z]{1,3}\)\s*)?"  # Optional grade/abbrev
        r"-?\s*(?:Very Healthy|Healthy|Watch|At Risk)?)",
        re.IGNORECASE,
    )

    # Format the correct score - numeric score + band label only (no letter grades/abbreviations).
    correct_score = f"{pre_calculated_score:.0f}/100 - {pre_calculated_band}"

    # Track if we made any fixes
    original_text = summary_text

    # First, try to fix scores in the Financial Health Rating section
    def replace_fhr_score(match):
        prefix = match.group(1)
        return prefix + correct_score

    summary_text = fhr_section_pattern.sub(replace_fhr_score, summary_text, count=1)

    # Also fix arrow-prefixed scores
    def replace_arrow_score(match):
        arrow = match.group(1)
        return arrow + correct_score

    summary_text = arrow_score_pattern.sub(replace_arrow_score, summary_text, count=1)

    # Log if we made a fix
    if summary_text != original_text:
        logger.info(
            f"Fixed health score mismatch: replaced AI-generated score with {pre_calculated_score:.1f}/100 - {pre_calculated_band}"
        )

    return summary_text


def _validate_complete_sentences(text: str) -> str:
    """Validate and fix incomplete sentences in the generated text.

    This function performs comprehensive cleanup:
    1. Removes sentences that end with incomplete numbers (e.g., "revenue of $3.")
    2. Removes sentences that trail off with no verb or context
    3. Fixes sentences that end with trailing prepositions/articles
    4. Ensures each paragraph ends with proper punctuation
    """
    if not text:
        return text

    lines = text.split("\n")
    validated_lines = []

    # Patterns that indicate incomplete sentences (more comprehensive)
    incomplete_patterns = [
        # Number without unit at end: "revenue of $3." or "cash flow of $13.47"
        r"\$\d+(?:\.\d+)?\.?\s*$",
        # Dangling dash / hyphen at end (e.g., "68/100 -" or "Risk -" )
        r"[-\u2013\u2014]\s*$",
        # Trailing "of" or "at" with nothing after (not followed by ellipsis)
        r"\s+(?:of|at|to|for|with)\s*[,]?\s*$",
        # Sentence ending with just comma or colon (not ellipsis)
        r"[,:]$",
        # Blank amount placeholders
        r"(?:of|at|to)\s*,",
        # Ends with articles without noun
        r"\s+(?:the|a|an)\s*$",
        # Common model cut-off: "..., a 1" (started a phrase like "a 10% margin" but got truncated)
        r"\b(?:a|an)\s+\d+\s*$",
        # Ends with conjunctions without completion
        r"\s+(?:and|but|or|while|although|however|which)\s*$",
        # Ends with possessive without noun (e.g., "NVIDIA's")
        r"[A-Za-z]+['']s\s*$",
        # Ends with "that" without clause
        r"\s+that\s*$",
        # Ends with incomplete comparisons
        r"\s+(?:than|as)\s*$",
    ]

    # Completions for various trailing patterns
    trailing_completions = {
        # Strip common truncations cleanly instead of appending generic filler
        r",?\s+\b(?:a|an)\s+\d+\s*$": ".",
        r"\s*[-\u2013\u2014]\s*$": ".",
        r"\s+the\s*$": " the implications for investors.",
        r"\s+a\s*$": " a key consideration.",
        r"\s+an\s*$": " an important factor.",
        r"\s+and\s*$": " and other relevant factors.",
        r"\s+but\s*$": " but caution is warranted.",
        r"\s+or\s*$": " or alternative approaches.",
        r"\s+while\s*$": " while maintaining focus on fundamentals.",
        r"\s+although\s*$": " although the outlook remains uncertain.",
        r"\s+however\s*$": " however the valuation provides some cushion.",
        r"\s+which\s*$": " which impacts the investment case.",
        r"\s+that\s*$": " that warrants attention.",
        r"[A-Za-z]+['']s\s*$": "'s strategic positioning.",
    }

    def _last_sentence_end_idx(line: str) -> int:
        """Return index of last true sentence-ending punctuation, ignoring decimals/abbreviations."""
        sentence_endings: List[int] = []
        for idx, char in enumerate(line or ""):
            if char not in ".!?":
                continue
            # Ignore decimal points in numbers like "$1.35" or "3.5%"
            if idx + 1 < len(line) and line[idx + 1].isdigit():
                continue
            # Ignore periods that are not followed by whitespace/end (abbrev/URL)
            if idx + 1 < len(line) and line[idx + 1] not in " \n\t\"'":
                continue
            sentence_endings.append(idx)
        return sentence_endings[-1] if sentence_endings else -1

    for line in lines:
        # Skip empty lines or section headers
        if not line.strip() or line.strip().startswith("#"):
            validated_lines.append(line)
            continue

        # Skip bullet points that might intentionally be brief
        if line.strip().startswith("- ") or line.strip().startswith("→"):
            validated_lines.append(line)
            continue
        # Skip structured labels that are not full sentences.
        if re.match(r"^\s*Health\s+Score\s+Drivers?\s*:?\s*$", line, re.IGNORECASE):
            validated_lines.append(line)
            continue

        original_line = line
        fixed = False

        # First, try to complete trailing patterns
        for pattern, completion in trailing_completions.items():
            if re.search(pattern, line, re.IGNORECASE):
                # Remove the trailing pattern and add completion
                line = re.sub(pattern, completion, line, flags=re.IGNORECASE)
                fixed = True
                break

        if fixed:
            validated_lines.append(line)
            continue

        # Check for incomplete sentence patterns
        is_incomplete = False
        for pattern in incomplete_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                is_incomplete = True
                # Try to fix by finding last complete sentence
                last_punct = _last_sentence_end_idx(line)
                if last_punct > len(line) * 0.5:  # Only cut if we keep at least 50%
                    line = line[: last_punct + 1]
                    is_incomplete = False
                elif last_punct > len(line) * 0.3:
                    # If we can keep at least 30%, cut and add a generic completion
                    line = line[: last_punct + 1]
                    is_incomplete = False
                break

        if not is_incomplete:
            validated_lines.append(line)
        else:
            # If still incomplete, try to add generic completion instead of dropping
            line_stripped = original_line.rstrip()
            if line_stripped and not line_stripped[-1] in ".!?":
                # Add a contextual ending
                if "risk" in line_stripped.lower():
                    validated_lines.append(
                        line_stripped + ", which warrants monitoring."
                    )
                elif "compet" in line_stripped.lower():
                    validated_lines.append(
                        line_stripped + ", presenting competitive challenges."
                    )
                elif (
                    "growth" in line_stripped.lower()
                    or "margin" in line_stripped.lower()
                ):
                    validated_lines.append(
                        line_stripped + ", impacting the investment thesis."
                    )
                else:
                    validated_lines.append(
                        line_stripped + ", which requires attention."
                    )
            else:
                validated_lines.append(original_line)

    return "\n".join(validated_lines)


def _truncate_text_to_word_limit(text: str, max_words: int) -> str:
    """Trim text so it contains at most `max_words` tokens while preserving complete sentences.

    CRITICAL: This function NEVER returns incomplete sentences. It will always
    cut back to the last complete sentence, even if that means going significantly
    under the word limit. Complete sentences are more important than hitting word count.
    """
    if max_words <= 0:
        return ""

    # IMPORTANT:
    # This truncator must be consistent with `_count_words()` (which the backend/UI/tests
    # use as an MS Word-style approximation). The prior implementation used `\b\w+\b`,
    # which splits tokens like "33.9%" into multiple "words" and can therefore truncate
    # numeric-heavy sections far more aggressively than intended.
    punct = string.punctuation + "\u201c\u201d\u2018\u2019\u2014\u2013\u2026"

    counted: List[re.Match[str]] = []
    for m in re.finditer(r"\S+", text or ""):
        raw = m.group(0)
        token = raw.strip(punct)
        if token:
            counted.append(m)

    if len(counted) <= max_words:
        return text.rstrip()

    # Initial hard cutoff at the end of the Nth counted token.
    cutoff_index = counted[max_words - 1].end()
    truncated = text[:cutoff_index].rstrip()

    # ALWAYS find the last complete sentence - don't allow incomplete sentences
    # Look for sentence-ending punctuation (.!?) that's NOT followed by a digit
    # (to avoid cutting after "$1." in "$1.2B")
    sentence_endings = []
    for i, char in enumerate(truncated):
        if char in ".!?":
            # Check it's not a decimal point (e.g., "$1.2B" or "3.5%")
            if i + 1 < len(truncated) and truncated[i + 1].isdigit():
                continue
            # Check it's not an abbreviation mid-sentence
            if i + 1 < len(truncated) and truncated[i + 1] not in " \n\t\"'":
                continue
            sentence_endings.append(i)

    if sentence_endings:
        # Use the last complete sentence
        last_sentence_end = sentence_endings[-1]
        result = truncated[: last_sentence_end + 1].rstrip()

        # Verify the result ends with proper punctuation
        if result and result[-1] in ".!?":
            return result

    # If we still can't find a good sentence ending, look in the ENTIRE text
    # for the last sentence ending before our word limit
    for i in range(len(truncated) - 1, -1, -1):
        if truncated[i] in ".!?":
            # Verify it's not a decimal
            if i + 1 < len(truncated) and truncated[i + 1].isdigit():
                continue
            return truncated[: i + 1].rstrip()

    # Absolute last resort: find ANY sentence ending in the original text
    # and cut there, even if it's much shorter
    for i in range(cutoff_index - 1, 0, -1):
        if text[i] in ".!?":
            if i + 1 < len(text) and text[i + 1].isdigit():
                continue
            result = text[: i + 1].rstrip()
            if result:
                return result

    # If there's truly no sentence ending (shouldn't happen), return what we have
    # but ensure it ends with a period
    if truncated and not truncated.rstrip().endswith((".", "!", "?")):
        truncated = truncated.rstrip() + "."
    return truncated


def _build_padding_block(
    required_words: int, *, exclude_norms: Optional[set[str]] = None
) -> str:
    """Deterministic, finance-relevant padding to safely reach strict word floors."""
    if required_words <= 0:
        return ""

    padding_sentences = _generate_padding_sentences(required_words, exclude_norms=exclude_norms)
    if not padding_sentences:
        return ""

    # Render as a clearly separated continuation block.
    # IMPORTANT: Do NOT use raw HTML comments as markers here — our markdown renderer
    # can surface them to end users (confusing). Keep this as plain text.
    padding_text = " ".join(padding_sentences).strip()
    return f"Key underwriting questions: {padding_text}".strip()


def _strip_length_padding_markers(text: str) -> str:
    """Remove legacy length-padding *marker* lines that may leak into user output.

    Older versions injected HTML comment markers like:
      <!--LENGTH_PADDING_START--> / <!--LENGTH_PADDING_END-->

    In some markdown renderers these show up as literal text, confusing users.
    This function strips ONLY the marker lines (not the padding content).
    """

    if not text:
        return text

    cleaned = re.sub(
        r"^\s*<!--\s*LENGTH_PADDING_START\s*-->\s*$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(
        r"^\s*<!--\s*LENGTH_PADDING_END\s*-->\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _dedupe_underwriting_payload_sentences(payload: str) -> str:
    """Deduplicate sentences inside a 'Key underwriting questions' payload.

    Padding/length enforcement can run multiple times; if we accidentally append the
    same template sentence more than once, this keeps the block clean.
    """

    if not payload:
        return ""

    payload = payload.replace("\u00A0", " ").strip()
    parts = re.split(r"(?<=[.!?])\s+", payload)
    seen: set[str] = set()
    unique: List[str] = []
    for part in parts:
        sent = (part or "").strip()
        if not sent:
            continue
        # Normalize for comparison.
        norm = " ".join(sent.lower().split())
        norm = norm.rstrip(".!?")
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(sent)
    return " ".join(unique).strip()


def _trim_underwriting_payload(payload: str, *, max_words: int) -> str:
    """Hard-cap an underwriting payload so it cannot dominate a section.

    We keep whole sentences where possible; if the first sentence alone exceeds the
    cap, we truncate by words and ensure terminal punctuation.
    """

    if not payload or max_words <= 0:
        return ""

    payload = payload.replace("\u00A0", " ").strip()
    if len(payload.split()) <= max_words:
        return payload

    parts = re.split(r"(?<=[.!?])\s+", payload)
    kept: List[str] = []
    total = 0
    for part in parts:
        sent = (part or "").strip()
        if not sent:
            continue
        wc = len(sent.split())
        if total == 0 and wc > max_words:
            words = sent.split()[:max_words]
            truncated = " ".join(words).rstrip(" ,;:")
            if truncated and not truncated.endswith((".", "!", "?")):
                truncated += "."
            return truncated
        if total + wc > max_words:
            break
        kept.append(sent)
        total += wc

    trimmed = " ".join(kept).strip()
    if trimmed and not trimmed.endswith((".", "!", "?")):
        trimmed += "."
    return trimmed


def _normalize_underwriting_questions_formatting(text: str) -> str:
    """Ensure 'Key underwriting questions:' is a separate paragraph.

    Without a blank line before it, markdown renders this line *inline* with the
    previous paragraph, which looks like random low-quality filler inside analysis.
    """

    if not text:
        return text

    result = text

    # If the label appears inline (mid-line), force it to start a new paragraph.
    result = re.sub(
        r"([^\n])\s*(Key\s+underwriting\s+questions\s*:)",
        r"\1\n\nKey underwriting questions:",
        result,
        flags=re.IGNORECASE,
    )

    # Normalize casing/spacing to a canonical prefix.
    result = re.sub(
        r"^\s*Key\s+underwriting\s+questions\s*:\s*",
        "Key underwriting questions: ",
        result,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # Ensure there is a blank line BEFORE the underwriting questions line.
    result = re.sub(
        r"([^\n])\n(Key underwriting questions:\s*)",
        r"\1\n\n\2",
        result,
        flags=re.IGNORECASE,
    )

    # Collapse excessive newlines.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _relocate_underwriting_questions_to_mdna(text: str) -> str:
    """Move any 'Key underwriting questions:' line(s) into the MD&A section.

    Rationale: underwriting-style prompts read like strategy/earnings-quality commentary.
    The user prefers Risk Factors to stay concise while MD&A carries more narrative weight.

    This function is idempotent.
    """

    if not text:
        return text

    prefix_re = re.compile(
        r"^\s*Key\s+underwriting\s+questions\s*:\s*(.*)$",
        re.IGNORECASE,
    )

    lines = text.splitlines()
    payloads: List[str] = []
    cleaned_lines: List[str] = []
    for line in lines:
        m = prefix_re.match(line)
        if m:
            payload = (m.group(1) or "").strip()
            if payload:
                payloads.append(payload)
            continue
        cleaned_lines.append(line)

    if not payloads:
        return text

    merged_payload = _dedupe_underwriting_payload_sentences(" ".join(payloads))
    # Keep this block short so it never becomes the dominant part of the memo.
    merged_payload = _trim_underwriting_payload(merged_payload, max_words=90)
    if not merged_payload:
        cleaned = "\n".join(cleaned_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    underwriting_line = f"Key underwriting questions: {merged_payload}".strip()

    # Insert into the MD&A section (if present), otherwise place it before Risk Factors,
    # then before Key Metrics, then at end as a last resort.
    is_heading = lambda l: bool(re.match(r"^\s*##\s+", l))
    mdna_idx: Optional[int] = None
    risk_idx: Optional[int] = None
    key_metrics_idx: Optional[int] = None

    for idx, line in enumerate(cleaned_lines):
        if mdna_idx is None and re.match(
            r"^\s*##\s*Management\s+Discussion\s*(?:&|and)\s*Analysis\b",
            line,
            re.IGNORECASE,
        ):
            mdna_idx = idx
        if risk_idx is None and re.match(r"^\s*##\s*Risk\s+Factors\b", line, re.IGNORECASE):
            risk_idx = idx
        if key_metrics_idx is None and re.match(
            r"^\s*##\s*(Key\s+Metrics|Key\s+Data\s+Appendix)\b",
            line,
            re.IGNORECASE,
        ):
            key_metrics_idx = idx

    if mdna_idx is None:
        insert_at = (
            risk_idx
            if risk_idx is not None
            else (key_metrics_idx if key_metrics_idx is not None else len(cleaned_lines))
        )
        # Ensure a clean paragraph break.
        while insert_at > 0 and cleaned_lines[insert_at - 1].strip() == "":
            insert_at -= 1
        insertion = ["", underwriting_line, ""]
        rebuilt_lines = cleaned_lines[:insert_at] + insertion + cleaned_lines[insert_at:]
        rebuilt = "\n".join(rebuilt_lines)
        rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
        return rebuilt.strip()

    # Find end of MD&A section.
    end_idx = len(cleaned_lines)
    for j in range(mdna_idx + 1, len(cleaned_lines)):
        if is_heading(cleaned_lines[j]):
            end_idx = j
            break

    # Insert before end_idx, trimming trailing blank lines inside the section.
    insert_at = end_idx
    while insert_at > mdna_idx + 1 and cleaned_lines[insert_at - 1].strip() == "":
        insert_at -= 1

    insertion = ["", underwriting_line, ""]
    rebuilt_lines = cleaned_lines[:insert_at] + insertion + cleaned_lines[insert_at:]
    rebuilt = "\n".join(rebuilt_lines)
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    return rebuilt.strip()


def _merge_underwriting_question_lines(text: str) -> str:
    """Coalesce repeated 'Key underwriting questions:' lines into a single line.

    Length enforcement can call padding multiple times. If we accidentally insert
    multiple underwriting-question lines, merge them so users see one clean block.
    """

    if not text:
        return text

    prefix_re = re.compile(
        r"^\s*Key\s+underwriting\s+questions\s*:\s*(.*)$",
        re.IGNORECASE,
    )

    merged_parts: List[str] = []
    out_lines: List[str] = []
    placeholder_idx: Optional[int] = None

    for line in text.splitlines():
        m = prefix_re.match(line)
        if m:
            content = (m.group(1) or "").strip()
            if content:
                merged_parts.append(content)
            if placeholder_idx is None:
                placeholder_idx = len(out_lines)
                out_lines.append("__UNDERWRITING_PLACEHOLDER__")
            # Skip subsequent underwriting lines.
            continue
        out_lines.append(line)

    if placeholder_idx is None:
        return text

    merged_text = " ".join(part for part in merged_parts if part).strip()
    merged_text = _dedupe_underwriting_payload_sentences(merged_text)
    out_lines[placeholder_idx] = (
        f"Key underwriting questions: {merged_text}" if merged_text else ""
    ).strip()

    cleaned = "\n".join(out_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _generate_padding_sentences(
    required_words: int,
    *,
    exclude_norms: Optional[set[str]] = None,
    section: Optional[str] = None,
    is_persona: bool = False,
    exclude_risk_names: Optional[set[str]] = None,
    max_words: Optional[int] = None,
) -> List[str]:
    """Generate concise, finance-relevant padding sentences to reach required word counts."""
    if required_words <= 0:
        return []

    if max_words is not None:
        max_words = int(max_words)
        if max_words <= 0:
            return []

    # IMPORTANT:
    # Padding is a LAST-RESORT safety net to satisfy strict word floors.
    # It must preserve narrative flow and match the document voice (persona vs neutral).
    #
    # Design goals:
    # - Avoid dumping standalone generic one-liners as separate paragraphs.
    # - Avoid repetitive, boilerplate “template” finance slogans users notice.
    # - Prefer section-aware connective sentences that can be appended seamlessly.

    canon_section = (section or "").strip().lower().replace("&", "and")
    canon_section = re.sub(r"\s+", " ", canon_section).strip()

    def _voice(persona: str, neutral: str) -> str:
        return persona if is_persona else neutral

    def _normalize_risk_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

    def _risk_name_from_template(sentence: str) -> Optional[str]:
        match = re.match(r"\*\*(.+?)\*\*\s*:", sentence or "")
        if match:
            return _normalize_risk_name(match.group(1))
        return None

    section_templates: Dict[str, List[str]] = {
        "financial health rating": [
            _voice(
                "I anchor the score on operating profitability and cash conversion because those signals tend to persist through a cycle.",
                "The score anchors on operating profitability and cash conversion because those signals tend to persist through a cycle.",
            ),
            _voice(
                "Liquidity is the margin of safety; it determines whether the company can absorb a shock without defensive financing.",
                "Liquidity is the margin of safety; it determines whether the company can absorb a shock without defensive financing.",
            ),
            _voice(
                "Leverage is the constraint: it can force bad decisions when demand softens or funding costs rise.",
                "Leverage is the constraint: it can force bad decisions when demand softens or funding costs rise.",
            ),
            _voice(
                "I look for balance-sheet room for error so the operating plan can survive a weaker tape.",
                "Balance-sheet room for error determines how much stress the operating plan can absorb.",
            ),
            _voice(
                "Free cash flow is the difference between optionality and dependence on external capital.",
                "Free cash flow is the difference between optionality and dependence on external capital.",
            ),
            _voice(
                "I treat clean conversion from operating income to free cash flow as the check on earnings quality.",
                "Clean conversion from operating income to free cash flow is the check on earnings quality.",
            ),
        ],
        "executive summary": [
            _voice(
                "I frame the debate around durability: can scale translate into structural operating leverage without relying on incentives that can be competed away?",
                "The debate is durability: can scale translate into structural operating leverage without relying on incentives that can be competed away?",
            ),
            _voice(
                "The cleanest check is cash conversion—when the operating story is real, it shows up as repeatable free cash flow after reinvestment.",
                "The cleanest check is cash conversion—when the operating story is real, it shows up as repeatable free cash flow after reinvestment.",
            ),
            _voice(
                "The bull case is an improving flywheel: better marketplace efficiency supports margin expansion without buying growth back through incentives.",
                "The bull case is an improving flywheel: better marketplace efficiency supports margin expansion without buying growth back through incentives.",
            ),
            _voice(
                "The bear case is a cost reset: regulation, labor dynamics, or competition can force higher variable costs that compress margins quickly.",
                "The bear case is a cost reset: regulation, labor dynamics, or competition can force higher variable costs that compress margins quickly.",
            ),
            _voice(
                "I’m less focused on a perfect quarter and more focused on the through-cycle algorithm: pricing, incentives, and retention should move in predictable ways.",
                "Focus less on a perfect quarter and more on the through-cycle algorithm: pricing, incentives, and retention should move in predictable ways.",
            ),
            _voice(
                "Balance-sheet flexibility matters because it decides whether management can invest through a slowdown instead of cutting defensively.",
                "Balance-sheet flexibility matters because it decides whether management can invest through a slowdown instead of cutting defensively.",
            ),
            _voice(
                "The thesis reads best when unit economics, margins, and cash flow all reconcile to the same story; divergence is where risk shows up first.",
                "The thesis reads best when unit economics, margins, and cash flow all reconcile to the same story; divergence is where risk shows up first.",
            ),
        ],
        "closing takeaway": [
            _voice(
                "I get more constructive when free cash flow confirms that margin gains are structural (not timing) and the balance sheet keeps optionality intact.",
                "Conviction improves when free cash flow confirms that margin gains are structural (not timing) and the balance sheet keeps optionality intact.",
            ),
            _voice(
                "I would revisit quickly if cash conversion weakens at the same time leverage rises, because those two moves together compress the margin for error.",
                "Revisit quickly if cash conversion weakens at the same time leverage rises, because those two moves together compress the margin for error.",
            ),
            _voice(
                "The durability question is straightforward: can margins hold through a softer demand tape without buying volume back with incentives?",
                "The durability question is straightforward: can margins hold through a softer demand tape without buying volume back with incentives?",
            ),
            _voice(
                "The most informative monitor is the bridge from operating margin to free cash flow after reinvestment; that is where durability shows up.",
                "The most informative monitor is the bridge from operating margin to free cash flow after reinvestment; that is where durability shows up.",
            ),
            _voice(
                "If profitability looks steady but cash conversion weakens for multiple quarters, I treat it as a valuation risk even if revenue remains resilient.",
                "If profitability looks steady but cash conversion weakens for multiple quarters, treat it as a valuation risk even if revenue remains resilient.",
            ),
            _voice(
                "Upside comes from structural operating leverage; downside comes from incentives, regulation, or competition resetting the cost base.",
                "Upside comes from structural operating leverage; downside comes from incentives, regulation, or competition resetting the cost base.",
            ),
            _voice(
                "What changes my view is evidence the company can self-fund growth while protecting cash conversion as the cycle softens and funding costs stay high.",
                "What changes the view is evidence the company can self-fund growth while protecting cash conversion as the cycle softens and funding costs stay high.",
            ),
            _voice(
                "A practical watch list keeps it simple: incentives, take rates, regulatory headlines, and balance-sheet flexibility.",
                "A practical watch list keeps it simple: incentives, take rates, regulatory headlines, and balance-sheet flexibility.",
            ),
            _voice("Net: stay cautious.", "Net: stay cautious."),
            _voice("Base case: hold.", "Base case: hold."),
            _voice("Watch cash conversion.", "Watch cash conversion."),
            _voice("Margin for error matters.", "Margin for error matters."),
            _voice("Risk stays two-sided.", "Risk stays two-sided."),
        ],
        "financial performance": [
            _voice(
                "I focus on the bridge from operating profit to free cash flow because that is where incentives, working-capital timing, and capex reveal earnings quality.",
                "The bridge from operating profit to free cash flow is where incentives, working-capital timing, and capex reveal earnings quality.",
            ),
            _voice(
                "When profitability improves, I separate pricing and mix from incentive cuts; one tends to persist, the other can reverse quickly in competition.",
                "When profitability improves, separate pricing and mix from incentive cuts; one tends to persist, the other can reverse quickly in competition.",
            ),
            _voice(
                "If cash conversion weakens while margins look fine, I treat it as a signal to investigate working-capital drift and the true cost of volume acquisition.",
                "If cash conversion weakens while margins look fine, treat it as a signal to investigate working-capital drift and the true cost of volume acquisition.",
            ),
            _voice(
                "A high free cash flow margin with low capex supports an asset-light model, but the durability still depends on variable-cost discipline.",
                "A high free cash flow margin with low capex supports an asset-light model, but durability still depends on variable-cost discipline.",
            ),
            _voice(
                "The most informative quarters are the ones where revenue, margins, and cash all tell the same story; divergence is where sustainability questions start.",
                "The most informative quarters are the ones where revenue, margins, and cash all tell the same story; divergence is where sustainability questions start.",
            ),
        ],
        "management discussion and analysis": [
            _voice(
                "I want reinvestment to earn its keep: hiring, R&D, and marketing should map to a clear payback period and show up as operating leverage over time.",
                "Reinvestment should earn its keep: hiring, R&D, and marketing should map to a clear payback period and show up as operating leverage over time.",
            ),
            _voice(
                "The cleanest management narratives reconcile to the financials: operating margin and cash flow should explain the strategy without leaning on recurring adjustments.",
                "The cleanest management narratives reconcile to the financials: operating margin and cash flow should explain the strategy without leaning on recurring adjustments.",
            ),
            _voice(
                "Capital allocation is a durability signal when growth is self-funded and the balance sheet stays flexible; it is a warning sign when it relies on leverage or dilution.",
                "Capital allocation is a durability signal when growth is self-funded and the balance sheet stays flexible; it is a warning sign when it relies on leverage or dilution.",
            ),
            _voice(
                "If buybacks are part of the plan, I want them funded by free cash flow after core reinvestment, not by shrinking the cash cushion.",
                "If buybacks are part of the plan, they should be funded by free cash flow after core reinvestment, not by shrinking the cash cushion.",
            ),
            _voice(
                "Stock-based compensation is economically real even when it is non-cash; dilution cadence should be evaluated alongside free cash flow generation.",
                "Stock-based compensation is economically real even when it is non-cash; dilution cadence should be evaluated alongside free cash flow generation.",
            ),
        ],
        "risk factors": [
            _voice(
                "**Execution Risk**: If reinvestment outpaces revenue, margins can reset lower.",
                "**Execution Risk**: If reinvestment outpaces revenue, margins can reset lower.",
            ),
            _voice(
                "**Competitive Pricing Risk**: Higher incentives can pressure take rates and margins.",
                "**Competitive Pricing Risk**: Higher incentives can pressure take rates and margins.",
            ),
            _voice(
                "**Regulatory Platform Risk**: Policy shifts can reduce monetization or raise compliance costs.",
                "**Regulatory Platform Risk**: Policy shifts can reduce monetization or raise compliance costs.",
            ),
            _voice(
                "**Technology Platform Risk**: Outages or fraud can damage trust and retention.",
                "**Technology Platform Risk**: Outages or fraud can damage trust and retention.",
            ),
            _voice(
                "**Cyclicality Demand Risk**: A slowdown can reduce volumes and utilization.",
                "**Cyclicality Demand Risk**: A slowdown can reduce volumes and utilization.",
            ),
            _voice(
                "**Legal Litigation Risk**: Claims and settlements can add volatility.",
                "**Legal Litigation Risk**: Claims and settlements can add volatility.",
            ),
            _voice(
                "**Earnings Quality Risk**: I worry that reported profitability can look steadier than the cash profile when working capital or capex timing shifts. If cash conversion weakens for multiple quarters, valuation support can erode even if revenue holds up.",
                "**Earnings Quality Risk**: Reported profitability can look steadier than the cash profile when working capital or capex timing shifts. If cash conversion weakens for multiple quarters, valuation support can erode even if revenue holds up.",
            ),
            _voice(
                "**Margin Compression Risk**: I worry that competitive intensity forces higher incentives or reinvestment, limiting operating leverage. If growth slows at the same time, small cost inflation can translate into outsized profit pressure.",
                "**Margin Compression Risk**: Competitive intensity can force higher incentives or reinvestment, limiting operating leverage. If growth slows at the same time, small cost inflation can translate into outsized profit pressure.",
            ),
            _voice(
                "**Balance Sheet Risk**: Leverage or maturities can reduce strategic flexibility.",
                "**Balance Sheet Risk**: Leverage or maturities can reduce strategic flexibility.",
            ),
        ],
    }

    fallback_templates = [
        _voice(
            "For me, the thread to pull is durability: repeatable cash conversion is more informative than a single strong quarter.",
            "The thread to pull is durability: repeatable cash conversion is more informative than a single strong quarter.",
        ),
        _voice(
            "I prefer to underwrite the business off operating profitability and cash, because below-the-line items can be noisy quarter to quarter.",
            "The business is best underwritten off operating profitability and cash, because below-the-line items can be noisy quarter to quarter.",
        ),
        _voice(
            "The risk-reward tends to shift when cash conversion and balance-sheet flexibility move in the same direction as margins.",
            "The risk-reward tends to shift when cash conversion and balance-sheet flexibility move in the same direction as margins.",
        ),
    ]

    templates: List[str] = []
    if canon_section in section_templates:
        templates.extend(section_templates[canon_section])
    else:
        templates.extend(fallback_templates)

    def _norm_sentence(s: str) -> str:
        s = (s or "").replace("\u00A0", " ")
        s = " ".join(s.lower().split())
        return s.rstrip(".!?")

    excluded = {(_norm_sentence(s)) for s in (exclude_norms or set()) if s}

    risk_name_blacklist = {
        _normalize_risk_name(name)
        for name in (exclude_risk_names or set())
        if name
    }

    candidates: List[Tuple[int, str]] = []
    for t in templates:
        if excluded and _norm_sentence(t) in excluded:
            continue
        if canon_section == "risk factors" and risk_name_blacklist:
            risk_name = _risk_name_from_template(t)
            if risk_name and risk_name in risk_name_blacklist:
                continue
        wc = _count_words(t)
        if wc > 0:
            candidates.append((wc, t))

    if not candidates:
        # If the section already contains all of its padding templates, broaden to the
        # generic pool first to avoid inserting duplicate sentences into the same memo.
        if canon_section in section_templates:
            for t in fallback_templates:
                if excluded and _norm_sentence(t) in excluded:
                    continue
                wc = _count_words(t)
                if wc > 0:
                    candidates.append((wc, t))

        # If everything was still excluded, fall back to allowing repeats so we can
        # satisfy strict length bands.
        if not candidates:
            candidates = []
            for t in templates:
                wc = _count_words(t)
                if wc > 0:
                    candidates.append((wc, t))
    candidates.sort(key=lambda x: x[0])

    sentences: List[str] = []
    remaining = int(required_words)
    budget_remaining = int(max_words) if max_words is not None else None
    used: set[str] = set()

    def _stable_pick(options: List[Tuple[int, str]], key: str) -> Tuple[int, str]:
        if len(options) == 1:
            return options[0]
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % len(options)
        return options[idx]

    # Greedy within bounds:
    # - Prefer the smallest sentence that covers the remaining deficit.
    # - If none can cover, take the longest sentence that still fits to make progress.
    while remaining > 0 and candidates:
        if budget_remaining is not None and budget_remaining <= 0:
            break

        available = [(wc, t) for wc, t in candidates if t not in used]
        if budget_remaining is not None:
            available = [(wc, t) for wc, t in available if wc <= budget_remaining]
        if not available:
            break

        cover = [(wc, t) for wc, t in available if wc >= remaining]
        if cover:
            min_wc = min(wc for wc, _t in cover)
            same_size = [(wc, t) for wc, t in cover if wc == min_wc]
            wc, sentence = _stable_pick(
                same_size,
                f"{canon_section}:{is_persona}:{remaining}:{budget_remaining}:{len(used)}",
            )
        else:
            max_wc = max(wc for wc, _t in available)
            same_size = [(wc, t) for wc, t in available if wc == max_wc]
            wc, sentence = _stable_pick(
                same_size,
                f"{canon_section}:{is_persona}:longest:{budget_remaining}:{len(used)}",
            )

        sentences.append(sentence)
        used.add(sentence)
        remaining -= wc
        if budget_remaining is not None:
            budget_remaining -= wc

    return sentences


def _distribute_padding_across_sections(summary_text: str, required_words: int) -> str:
    """Spread deterministic padding across the *shortest* narrative sections.

    Goal: hit strict word bands without dumping all added words into one section.
    """
    if required_words <= 0 or not summary_text:
        return summary_text

    # Strip legacy markers and normalize any underwriting blocks so we don't amplify
    # formatting issues during padding.
    summary_text = _strip_length_padding_markers(summary_text)
    summary_text = _normalize_underwriting_questions_formatting(summary_text)
    summary_text = _merge_underwriting_question_lines(summary_text)
    summary_text = _relocate_underwriting_questions_to_mdna(summary_text)

    # Normalize obvious inline header issues BEFORE we try to locate target sections.
    summary_text = _fix_inline_section_headers(summary_text)

    # Be permissive about spacing after ## to avoid missing headings like "##Executive Summary".
    heading_regex = re.compile(r"^\s*##\s*.+")
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []

    for line in summary_text.splitlines():
        if heading_regex.match(line):
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).strip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(buffer).strip()))

    def _canon_heading(heading_line: str) -> str:
        title = re.sub(r"^\s*##\s*", "", heading_line).strip().lower()
        title = title.replace("&", "and")
        title = re.sub(r"[^a-z0-9\s]", " ", title)
        return " ".join(title.split())

    if not sections:
        # Fall back: no headings; append padding as a final paragraph.
        base = summary_text.rstrip()
        if base and not base.endswith((".", "!", "?")):
            base += "."
        is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", summary_text))
        padding_text = " ".join(
            _generate_padding_sentences(
                required_words, section=None, is_persona=is_persona
            )
        ).strip()
        return f"{base}\n\n{padding_text}".strip() if padding_text else base

    # Candidate sections to pad (exclude Health/Key Metrics/Closing).
    #
    # IMPORTANT: If we exclude Executive Summary / Risk Factors, the padding step becomes
    # a dumping ground for Financial Performance / MD&A, which makes the memo feel
    # lopsided (users perceive this as "80% MD&A/Financials").
    candidate_order = {
        "risk factors": 0,
        "executive summary": 1,
        "financial performance": 2,
        "management discussion and analysis": 3,
    }

    candidates: List[Tuple[int, int, int]] = []  # (word_count, tie_break, section_idx)
    for idx, (heading, body) in enumerate(sections):
        canon = _canon_heading(heading)
        for key, tie_break in candidate_order.items():
            if canon.startswith(key):
                candidates.append((len((body or "").split()), tie_break, idx))
                break

    if not candidates:
        base = summary_text.rstrip()
        if base and not base.endswith((".", "!", "?")):
            base += "."
        is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", summary_text))
        padding_text = " ".join(
            _generate_padding_sentences(
                required_words, section=None, is_persona=is_persona
            )
        ).strip()
        return f"{base}\n\n{padding_text}".strip() if padding_text else base

    candidates.sort()

    # Touch more than one section for non-trivial deficits to prevent any single
    # section from becoming the "dumping ground".
    n_targets = min(len(candidates), max(1, min(4, 1 + required_words // 20)))
    target_section_idxs = [idx for _wc, _tb, idx in candidates[:n_targets]]

    base_alloc = required_words // n_targets
    remainder = required_words % n_targets
    allocations = [base_alloc + (1 if i < remainder else 0) for i in range(n_targets)]

    # Seed exclusions with existing sentences so repeated padding passes don't
    # re-insert the same templates (a major perceived-quality regression).
    used_sentences: set[str] = set()
    for line in summary_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("→") or stripped.startswith("- "):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", stripped):
            sent = (sent or "").strip()
            if len(sent.split()) < 5:
                continue
            used_sentences.add(sent)

    is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", summary_text))

    def _extract_risk_names(body: str) -> set[str]:
        names: set[str] = set()
        for match in re.finditer(r"\*\*(.+?)\*\*\s*:", body or ""):
            name = re.sub(r"[^a-z0-9]+", " ", match.group(1).lower()).strip()
            if name:
                names.add(name)
        return names

    def _append_padding_into_body(body: str, pad_text: str, section_name: str) -> str:
        body = (body or "").strip()
        pad_text = (pad_text or "").strip()
        if not pad_text:
            return body
        if not body:
            return pad_text
        if section_name == "Risk Factors":
            # Preserve the expected risk-item formatting by keeping a paragraph break.
            return f"{body}\n\n{pad_text}".strip()
        cleaned = body.rstrip()
        # Avoid ending on a dangling dash from earlier formatting mistakes.
        cleaned = re.sub(r"[-\u2013\u2014]+\s*$", "", cleaned).rstrip()
        if cleaned and not cleaned.endswith((".", "!", "?")):
            cleaned += "."
        return f"{cleaned} {pad_text}".strip()

    for alloc, target_idx in zip(allocations, target_section_idxs):
        if alloc <= 0:
            continue
        heading, body = sections[target_idx]
        canon_title = _standard_section_name_from_heading(heading)
        risk_name_exclusions = (
            _extract_risk_names(body) if canon_title == "Risk Factors" else None
        )
        pad_sentences = _generate_padding_sentences(
            alloc,
            exclude_norms=used_sentences,
            section=canon_title,
            is_persona=is_persona,
            exclude_risk_names=risk_name_exclusions,
        )
        if not pad_sentences:
            continue
        used_sentences.update(pad_sentences)
        pad_text = " ".join(pad_sentences).strip()
        if not pad_text:
            continue
        body = _append_padding_into_body(body, pad_text, canon_title)
        sections[target_idx] = (heading, body.strip())

    rebuilt_sections: List[str] = []
    for heading, body in sections:
        section_text = f"{heading}\n{(body or '').strip()}".strip()
        rebuilt_sections.append(section_text)

    rebuilt = "\n\n".join(rebuilt_sections).strip()
    rebuilt = _normalize_underwriting_questions_formatting(rebuilt)
    rebuilt = _merge_underwriting_question_lines(rebuilt)
    rebuilt = _relocate_underwriting_questions_to_mdna(rebuilt)
    return rebuilt


def _clamp_to_band(
    text: str, lower: int, upper: int, *, allow_padding: bool = True
) -> str:
    """Deterministically adjust text to land within [lower, upper] words.

    If allow_padding is False, the function will not add template padding when the text is short;
    it will simply return the current text (after any trimming) to preserve narrative quality.
    """
    if not text:
        return text

    for _ in range(3):
        words = _count_words(text)
        if lower <= words <= upper:
            return text
        if words < lower:
            if not allow_padding:
                return text
            text = _distribute_padding_across_sections(text, lower - words)
        else:
            text = _trim_preserving_headings(text, upper)

    # Final safety: truncate to upper, then pad back to lower if needed
    text = _truncate_text_to_word_limit(text, upper)
    words = _count_words(text)
    if words < lower and allow_padding:
        text = _distribute_padding_across_sections(text, lower - words)
    return text


def _enforce_section_order(text: str, include_health_rating: bool = True) -> str:
    """
    Reorder sections into the canonical sequence to improve flow.
    Order: Health (optional) → Executive Summary → Financial Performance → MD&A →
    Risk Factors → Key Metrics → Closing Takeaway.
    """
    if not text:
        return text

    pattern = re.compile(r"^\s*##\s*(.+)", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return text

    sections = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        title = match.group(1).strip()
        body = text[start:end].strip()
        sections.append((title, body))

    def _canonical(title: str) -> str:
        t = title.lower()
        t = t.replace("&", "and")
        t = re.sub(r"[^a-z\s]", "", t)
        return " ".join(t.split())

    order = [
        "financial health rating" if include_health_rating else None,
        "executive summary",
        "financial performance",
        "management discussion and analysis",
        "risk factors",
        "key metrics",
        "closing takeaway",
    ]
    order = [o for o in order if o]

    buckets = {o: [] for o in order}
    leftovers = []

    for title, body in sections:
        canon = _canonical(title)
        if canon.startswith("key data appendix"):
            canon = "key metrics"
            title = "Key Metrics"
        if canon.startswith("strategic initiatives"):
            # Fold this content into MD&A rather than leaving an extra section
            mdna_key = "management discussion and analysis"
            if buckets.get(mdna_key):
                prev_title, prev_body = buckets[mdna_key][-1]
                buckets[mdna_key][-1] = (prev_title, f"{prev_body}\n\n{body}".strip())
            else:
                buckets[mdna_key].append(("Management Discussion & Analysis", body))
            continue
        matched = False
        for o in order:
            if canon.startswith(o):
                buckets[o].append((title, body))
                matched = True
                break
        if not matched:
            leftovers.append((title, body))

    rebuilt = []
    for o in order:
        for title, body in buckets.get(o, []):
            rebuilt.append(f"## {title}\n{body}".strip())
    rebuilt.extend([f"## {title}\n{body}".strip() for title, body in leftovers])

    return "\n\n".join([block for block in rebuilt if block.strip()])


def _deduplicate_sentences(text: str) -> str:
    """
    Remove duplicate sentences from the text to improve flow.
    Preserves the first occurrence of each sentence and removes subsequent duplicates.
    """
    if not text:
        return text

    def _is_orphan_fragment(sentence: str) -> bool:
        s = (sentence or "").strip()
        if not s:
            return True
        words = s.split()
        if len(words) <= 1:
            return True
        if len(words) < 5 and any(ch.isdigit() for ch in s):
            return True
        lowered = s.lower()
        if lowered.startswith("the full footnote"):
            return True
        if lowered.startswith("when we ") and len(words) < 8:
            return True
        if re.search(r"\bto[.!?]?$", lowered) and len(words) < 12:
            return True
        return False

    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "as",
        "at",
        "by",
        "from",
        "into",
        "that",
        "this",
        "it",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "we",
        "i",
        "my",
        "our",
        "your",
    }

    def _content_signature(sentence: str) -> str:
        lowered = (sentence or "").lower()
        tokens = re.split(r"[^a-z0-9]+", lowered)
        tokens = [t for t in tokens if t and t not in stopwords and len(t) >= 3]
        # Use a sorted, de-duplicated signature so minor rephrasings collapse.
        uniq = sorted(set(tokens))
        return " ".join(uniq[:18])

    # Split into lines to preserve structure
    lines = text.splitlines()
    seen_sentences: set = set()
    seen_signatures: set = set()
    result_lines: List[str] = []

    for line in lines:
        # Preserve headings and empty lines
        if not line.strip() or line.strip().startswith("#"):
            result_lines.append(line)
            continue

        # Split line into sentences
        # Match sentences ending with . ! or ? followed by space or end
        sentences = re.split(r"(?<=[.!?])\s+", line.strip())
        unique_sentences: List[str] = []

        for sentence in sentences:
            sentence = (sentence or "").strip()
            if not sentence:
                continue
            if _is_orphan_fragment(sentence):
                continue
            # Normalize for comparison (lowercase, strip extra whitespace)
            normalized = " ".join(sentence.lower().split())
            normalized = normalized.strip(" \t\"'“”‘’")
            normalized = normalized.rstrip(".!?")
            # Strip common discourse openers that tend to get repeated as filler.
            normalized = re.sub(
                r"^(?:from my perspective|in my view|for me|my take is|i think|i believe|my stance is|the key monitor is|what changes my view is)\s*[:,;-]?\s+",
                "",
                normalized,
            )
            # Treat the deterministic padding label as non-substantive so repeated
            # underwriting-question sentences can be deduplicated cleanly.
            normalized = re.sub(
                r"^key\s+underwriting\s+questions\s*:\s*",
                "",
                normalized,
            )
            signature = _content_signature(normalized)
            if normalized and normalized not in seen_sentences and signature not in seen_signatures:
                seen_sentences.add(normalized)
                if signature:
                    seen_signatures.add(signature)
                unique_sentences.append(sentence)

        if unique_sentences:
            result_lines.append(" ".join(unique_sentences))
        elif line.strip():
            # If all sentences were duplicates, add empty line to preserve structure
            pass

    return "\n".join(result_lines)


def _trim_appendix_preserving_rows(body: str, max_words: int) -> str:
    """Trim Key Metrics/Appendix body by removing rows from the bottom to avoid partial bullets."""
    lines = body.splitlines()
    trimmed: List[str] = []
    words = 0
    for line in lines:
        line_words = _count_words(line)
        if words + line_words > max_words:
            break
        trimmed.append(line)
        words += line_words
    return "\n".join(trimmed).strip()


def _canonicalize_section_title(title: str) -> str:
    """Normalize section titles for matching regardless of punctuation/casing."""

    if not title:
        return ""
    cleaned = title.lower().replace("&", "and")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return " ".join(cleaned.split()).strip()


def _standard_section_name_from_heading(heading_line: str) -> str:
    """Map a markdown heading line (e.g. '## Key Data Appendix') to our canonical section names."""

    title = re.sub(r"^\s*##\s*", "", heading_line or "").strip()
    canon = _canonicalize_section_title(title)

    if canon.startswith("key data appendix") or canon.startswith("key metrics"):
        return "Key Metrics"
    if canon.startswith("management discussion"):
        return "Management Discussion & Analysis"
    if canon.startswith("strategic initiatives"):
        return "Management Discussion & Analysis"
    if canon.startswith("financial health rating"):
        return "Financial Health Rating"
    if canon.startswith("executive summary"):
        return "Executive Summary"
    if canon.startswith("financial performance"):
        return "Financial Performance"
    if canon.startswith("risk factors"):
        return "Risk Factors"
    if canon.startswith("closing takeaway"):
        return "Closing Takeaway"
    # Unknown/extra section
    return title or "Section"


def _trim_preserving_headings(text: str, max_words: int) -> str:
    """
    Deterministically trim the memo while keeping every section heading present.
    This avoids chopping off the Key Metrics section or other trailing sections.
    """
    heading_regex = re.compile(r"^\s*##\s+.+")
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []
    preamble: List[str] = []
    first_heading_seen = False

    for line in text.splitlines():
        if heading_regex.match(line):
            if not first_heading_seen and buffer:
                preamble = buffer[:]
                buffer = []
            first_heading_seen = True
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).strip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)
        else:
            # Capture lines before the first heading as preamble
            preamble.append(line)

    preamble_text = "\n".join(preamble).strip()

    if current_heading is not None:
        section_body = "\n".join(buffer).strip()
        sections.append((current_heading, section_body))

    if not sections:
        return _truncate_text_to_word_limit(text, max_words)

    # Account for preamble + heading titles when allocating body word budgets.
    # (UI/tests count title tokens too.)
    preamble_words = _count_words(preamble_text)
    heading_title_words = sum(
        _count_words(re.sub(r"^\s*##\s*", "", h).strip()) for h, _ in sections
    )
    available_body_words = max(0, max_words - preamble_words - heading_title_words)

    if available_body_words <= 0:
        # Edge case: extremely small max_words. Keep a truncated preamble only.
        return _truncate_text_to_word_limit(preamble_text or text, max_words)

    section_keys = [_standard_section_name_from_heading(h) for h, _ in sections]
    current_body_counts = [_count_words(body) for _, body in sections]

    # Scale per-section minimums if the user requested an unusually short memo.
    # CRITICAL: Protect the "bookends" (Risk Factors + Closing Takeaway) from collapsing
    # into one-liners during deterministic trimming.
    include_health_rating = "Financial Health Rating" in section_keys
    target_mins = _calculate_section_min_words_for_target(
        available_body_words, include_health_rating=include_health_rating
    )
    default_min = max(1, min(10, available_body_words // max(1, len(sections))))
    scaled_mins = [
        int(target_mins.get(key, default_min)) for key in section_keys
    ]

    overflow = sum(scaled_mins) - available_body_words
    if overflow > 0:
        # Prefer shrinking unknown/extra sections first.
        for idx in reversed(range(len(scaled_mins))):
            if overflow <= 0:
                break
            key = section_keys[idx]
            if key in target_mins:
                continue
            reducible = scaled_mins[idx] - 1
            if reducible <= 0:
                continue
            delta = min(reducible, overflow)
            scaled_mins[idx] -= delta
            overflow -= delta
    if overflow > 0:
        # Last resort: scale all mins proportionally while keeping at least 1 word.
        total = sum(scaled_mins) or 1
        scale = available_body_words / total
        scaled_mins = [max(1, int(m * scale)) for m in scaled_mins]
        drift = available_body_words - sum(scaled_mins)
        if drift != 0 and scaled_mins:
            step = 1 if drift > 0 else -1
            remaining = abs(drift)
            idx = 0
            while remaining > 0 and idx < 10_000:
                i = idx % len(scaled_mins)
                next_val = scaled_mins[i] + step
                if next_val >= 1:
                    scaled_mins[i] = next_val
                    remaining -= 1
                idx += 1

    # Allocate target body words per section using the same proportional weights we
    # communicate to the model. This prevents the trim step from crushing Executive
    # Summary / Financial Performance (a common quality regression).
    weights = [SECTION_PROPORTIONAL_WEIGHTS.get(key, 10) for key in section_keys]
    weight_sum = sum(weights) or len(weights)

    exacts = [w * available_body_words / weight_sum for w in weights]
    floors = [int(x) for x in exacts]
    remainders = [x - int(x) for x in exacts]

    remaining = available_body_words - sum(floors)
    if remaining > 0:
        for idx in sorted(range(len(floors)), key=lambda i: remainders[i], reverse=True)[:remaining]:
            floors[idx] += 1

    targets = [max(floors[i], scaled_mins[i]) for i in range(len(floors))]
    overflow = sum(targets) - available_body_words
    if overflow > 0:
        # Reduce from sections with the most slack above their scaled minimum.
        slack = [targets[i] - scaled_mins[i] for i in range(len(targets))]
        for idx in sorted(range(len(targets)), key=lambda i: slack[i], reverse=True):
            if overflow <= 0:
                break
            reducible = targets[idx] - scaled_mins[idx]
            if reducible <= 0:
                continue
            delta = min(reducible, overflow)
            targets[idx] -= delta
            overflow -= delta

    # Convert targets into per-section allowed word counts (can't exceed existing body).
    allocations = [min(current_body_counts[i], targets[i]) for i in range(len(sections))]

    # If some sections are shorter than their target, redistribute the remaining
    # budget to other sections that still have content above their allocation.
    remaining_budget = available_body_words - sum(allocations)
    if remaining_budget > 0:
        expandable = [
            i for i in range(len(sections)) if current_body_counts[i] > allocations[i]
        ]
        # Prefer allocating back to higher-weight sections first.
        expandable.sort(key=lambda i: weights[i], reverse=True)
        while remaining_budget > 0 and expandable:
            progressed = False
            for idx in expandable:
                if remaining_budget <= 0:
                    break
                if allocations[idx] < current_body_counts[idx]:
                    allocations[idx] += 1
                    remaining_budget -= 1
                    progressed = True
            if not progressed:
                break

    trimmed_sections: List[str] = []
    for idx, (heading, body) in enumerate(sections):
        allowed = allocations[idx]
        if allowed <= 0:
            continue
        if "key metrics" in section_keys[idx].lower() or "key data appendix" in section_keys[idx].lower():
            trimmed_body = _trim_appendix_preserving_rows(body, allowed)
        else:
            trimmed_body = _truncate_text_to_word_limit(body, allowed)
        trimmed_sections.append(f"{heading}\n{trimmed_body}".rstrip())

    rebuilt = []
    if preamble_text:
        rebuilt.append(preamble_text)
    rebuilt.extend(trimmed_sections)
    return "\n\n".join(rebuilt).strip()


def _compress_summary_to_length(
    gemini_client,
    summary_text: str,
    max_words: int,
    target_length: int,
    tolerance: int,
    *,
    token_budget: Optional[TokenBudget] = None,
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Ask the model to compress the memo to fit within the band without truncating sections.
    Returns (compressed_text, word_count) or (None, None) on failure.
    """
    compress_prompt = (
        f"The following investor memo must be compressed to land BETWEEN {target_length - tolerance} "
        f"and {target_length + tolerance} words. It currently exceeds {max_words} words.\n\n"
        "RULES:\n"
        "- KEEP every section heading and bullet; do NOT drop sections (including Risk Factors/Appendix).\n"
        "- Shorten sentences, merge overlapping points, and remove redundancy instead of cutting sections.\n"
        "- Preserve key metrics, conclusions, and investor-lens framing.\n"
        "- Maintain markdown headings (## Section).\n"
        "- End with an accurate control line: 'WORD COUNT: ###'.\n\n"
        "MEMO TO COMPRESS:\n"
        f"{summary_text}"
    )
    if token_budget and not token_budget.can_afford(compress_prompt, max_output_tokens):
        logger.warning(
            "Skipping compression rewrite due to token budget (remaining=%s tokens)",
            token_budget.remaining_tokens,
        )
        return None, None

    raw_text = _call_gemini_client(gemini_client, compress_prompt)
    if token_budget:
        token_budget.charge(compress_prompt, raw_text)
    compressed_text, reported = _extract_word_count_control(raw_text)
    if not compressed_text:
        return None, None
    words = _count_words(compressed_text)
    return compressed_text, words


def _finalize_length_band(
    summary_text: str, target_length: int, tolerance: int = 10
) -> str:
    """
    Hard guardrail to guarantee the final text lands within the requested band,
    even if the model repeatedly ignores instructions.
    """
    if not summary_text or target_length is None:
        return summary_text

    # First, remove any duplicate sentences to improve flow
    summary_text = _deduplicate_sentences(summary_text)

    lower = target_length - tolerance
    upper = target_length + tolerance
    word_count = _count_words(summary_text)

    if lower <= word_count <= upper:
        return summary_text

    # Over target: trim deterministically while keeping headings present
    if word_count > upper:
        trimmed = _trim_preserving_headings(summary_text, upper)
        trimmed_words = _count_words(trimmed)
        # If trimming drops below the lower bound, pad back up to the lower bound
        if trimmed_words < lower:
            deficit = lower - trimmed_words
            trimmed = _distribute_padding_across_sections(trimmed, deficit)
            trimmed_words = _count_words(trimmed)
        if trimmed_words > upper:
            trimmed = _truncate_text_to_word_limit(trimmed, upper)
        if trimmed and not trimmed.rstrip().endswith((".", "!", "?")):
            trimmed = trimmed.rstrip() + "."
        return trimmed

    # Under target: append additional content seamlessly (no label)
    deficit = lower - word_count
    padded = _distribute_padding_across_sections(summary_text, deficit)
    padded_words = _count_words(padded)
    if padded_words > upper:
        padded = _trim_preserving_headings(padded, upper)
        padded_words = _count_words(padded)

    # Final safety check to keep result inside the band
    if padded_words > upper:
        padded = _truncate_text_to_word_limit(padded, upper)
        padded_words = _count_words(padded)
    elif padded_words < lower:
        shortfall = lower - padded_words
        padded = _distribute_padding_across_sections(padded, shortfall)
        padded_words = _count_words(padded)
        if padded_words > upper:
            padded = _truncate_text_to_word_limit(padded, upper)
    return _clamp_to_band(padded, lower, upper)


def _force_final_band(
    summary_text: str,
    target_length: int,
    tolerance: int = 10,
    *,
    allow_padding: bool = True,
) -> str:
    """
    Absolutely enforce the target band with deterministic padding/trim, even if prior steps failed.
    """
    if not summary_text or target_length is None:
        return summary_text

    lower = target_length - tolerance
    upper = target_length + tolerance

    for _ in range(3):
        words = _count_words(summary_text)
        if lower <= words <= upper:
            return summary_text
        if words > upper:
            summary_text = _trim_preserving_headings(summary_text, upper)
            continue
        deficit = lower - words
        if not allow_padding:
            break
        summary_text = _distribute_padding_across_sections(summary_text, deficit)

    # Final safety net
    final_words = _count_words(summary_text)
    if final_words > upper:
        trimmed = _trim_preserving_headings(summary_text, upper)
        trimmed_words = _count_words(trimmed)
        if trimmed_words < lower and allow_padding:
            deficit = lower - trimmed_words
            trimmed = _distribute_padding_across_sections(trimmed, deficit)
            trimmed_words = _count_words(trimmed)
        if lower <= trimmed_words <= upper:
            summary_text = trimmed
        else:
            # If still over, hard truncate to upper as last resort
            summary_text = _truncate_text_to_word_limit(trimmed, upper)
    elif final_words < lower and allow_padding:
        # If still under, distribute a final round of padding away from the Closing Takeaway
        summary_text = _distribute_padding_across_sections(
            summary_text, lower - final_words
        )
    return _clamp_to_band(summary_text, lower, upper, allow_padding=allow_padding)


def _needs_length_retry(
    text: str, target_length: int, cached_count: Optional[int] = None
) -> Tuple[bool, int, int]:
    """Return tuple indicating if retry needed, actual count, tolerance band size."""
    words = cached_count if cached_count is not None else _count_words(text)
    tolerance = 10  # Strict tolerance as requested by user
    lower = target_length - tolerance
    upper = target_length + tolerance
    if lower <= words <= upper:
        return False, words, tolerance
    return True, words, tolerance


def _rewrite_summary_to_length(
    gemini_client,
    summary_text: str,
    target_length: int,
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
    current_words: Optional[int] = None,
    *,
    token_budget: Optional[TokenBudget] = None,
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
) -> Tuple[str, Tuple[int, int]]:
    """
    Ask the model to rewrite an existing draft so it fits within the required length band
    while keeping every section intact. Returns the new draft and its (word_count, tolerance).
    """
    tolerance = 10
    lower = target_length - tolerance
    upper = target_length + tolerance
    corrections: List[str] = []
    working_draft = summary_text
    latest_words = (
        current_words if current_words is not None else _count_words(working_draft)
    )
    best_valid_draft = working_draft
    best_stats: Tuple[int, int] = (latest_words, tolerance)

    def _build_prompt() -> str:
        diff = latest_words - target_length
        abs_diff = abs(diff)
        has_health = bool(
            re.search(r"financial health rating", working_draft, re.IGNORECASE)
        )
        health_cut_line = (
            "   - Financial Health Rating: ~10% of cuts\n" if has_health else ""
        )

        preserve_titles: List[str] = []
        for title, _min_words in SUMMARY_SECTION_REQUIREMENTS:
            if re.search(
                rf"^\s*##?\s*{re.escape(title)}\b",
                working_draft,
                re.IGNORECASE | re.MULTILINE,
            ):
                preserve_titles.append(title)
        if not preserve_titles:
            preserve_titles = [
                title
                for title, _ in SUMMARY_SECTION_REQUIREMENTS
                if title != "Financial Health Rating" or has_health
            ]
        preserve_clause = ", ".join(preserve_titles)

        if latest_words > upper:
            direction_instruction = (
                f"You are {abs_diff} words OVER the limit. \n"
                "ACTION: CONDENSE the text PROPORTIONALLY across ALL sections. \n"
                f"1. CUT approximately {int(abs_diff * 1.2)} words total.\n"
                "2. PROPORTIONAL CUTS - reduce EACH section by a similar percentage:\n"
                f"{health_cut_line}"
                "   - Executive Summary: ~15% of cuts\n"
                "   - Financial Performance: ~20% of cuts\n"
                "   - Management Discussion & Analysis: ~20% of cuts\n"
                "   - Risk Factors: ~15% of cuts\n"
                "   - Key Metrics: ~10% of cuts\n"
                "   - Closing Takeaway: ~10% of cuts (KEEP A COMPLETE VERDICT; do not collapse to a one-liner)\n"
                "3. DO NOT take all cuts from one section (especially Closing Takeaway).\n"
                "4. Remove adjectives, adverbs, and filler words. Merge sentences.\n"
                "5. Keep 'Key Metrics' compact but complete.\n"
                "6. DO NOT append any new summary. Just condense existing sections."
            )
        elif latest_words < lower:
            words_needed = lower - latest_words
            direction_instruction = (
                f"You are {abs_diff} words SHORT of the MINIMUM requirement ({lower} words). \n"
                f"ACTION: EXPAND the content NOW. You MUST add AT LEAST {int(words_needed * 1.3)} words.\n\n"
                f"MANDATORY EXPANSION (add exactly these words per section):\n"
                f"- Financial Health Rating: Add {max(2, int(words_needed * 0.10))} words (link score to 1-2 key drivers)\n"
                f"- Executive Summary: Add {max(3, int(words_needed * 0.15))} words (thesis + swing factor)\n"
                f"- Financial Performance: Add {max(5, int(words_needed * 0.20))} words (margin bridge, cash conversion, YoY context)\n"
                f"- Management Discussion & Analysis: Add {max(5, int(words_needed * 0.20))} words (strategy, capital allocation, execution)\n"
                f"- Risk Factors: Add {max(4, int(words_needed * 0.15))} words (only the most material, filing-grounded risks)\n"
                f"- Key Metrics: Add {max(2, int(words_needed * 0.10))} words by adding MORE arrow-line metric rows (NO prose paragraphs)\n"
                f"- Closing Takeaway: Add {max(3, int(words_needed * 0.10))} words (clear verdict + what changes the view)\n\n"
                f"DO NOT use generic filler sentences. Add SUBSTANTIVE analysis with specific data points.\n"
                f"You MUST reach at least {lower} words. Count your words before finishing."
            )
        else:
            direction_instruction = "Ensure you stay within the target range."

        prompt = (
            f"You previously drafted an equity research memo containing {latest_words} words, which is outside the "
            f"required range of {lower}–{upper} words (target {target_length}). \n\n"
            f"{direction_instruction}\n\n"
            "Rewrite the entire memo so it fits the range while preserving every section and investor-specific instruction."
            "\n\nMANDATORY REQUIREMENTS (IN PRIORITY ORDER):\n"
            "1. SENTENCE COMPLETION IS HIGHEST PRIORITY - NEVER cut off mid-sentence, even to meet word count.\n"
            f"2. Keep all existing section headings ({preserve_clause}) unless they were absent in the draft. Do NOT drop sections to save space.\n"
            "3. Retain the key figures, personas, and conclusions.\n"
            "4. EVERY sentence MUST end with proper punctuation. No cutting off with 'and the...', 'which is...', or incomplete numbers like '$1.'.\n"
            "5. ENSURE THE OUTPUT IS COMPLETE. Do not cut off the last section or the Closing Takeaway.\n"
            "6. After rewriting, append a final line formatted exactly as `WORD COUNT: ###` (replace ### with the true count)."
            f"\n\nLENGTH TARGET:\nAim for {lower}–{upper} words. You MUST land inside this range. "
            f"If finishing a sentence would push you out of range, tighten earlier sentences to compensate. "
            f"Incomplete sentences are NOT allowed."
        )
        if corrections:
            prompt += "\n\nADDITIONAL CORRECTIONS:\n" + "\n".join(corrections)
        prompt += "\n\nPREVIOUS DRAFT:\n" + working_draft
        return prompt

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        # Add delay between rewrite attempts to space out API calls
        # This helps avoid rapid-fire requests that trigger rate limits
        if attempt > 1:
            delay_seconds = 2 ** (attempt - 1)  # Exponential: 2s, 4s, 8s...
            delay_seconds = min(delay_seconds, 5)  # Cap at 5 seconds
            logger.info(
                f"Waiting {delay_seconds}s before rewrite attempt {attempt}/{MAX_REWRITE_ATTEMPTS}"
            )
            time.sleep(delay_seconds)

        prompt = _build_prompt()
        if token_budget and not token_budget.can_afford(prompt, max_output_tokens):
            logger.warning(
                "Skipping rewrite attempt due to token budget (remaining=%s tokens)",
                token_budget.remaining_tokens,
            )
            break

        raw_text = _call_gemini_client(gemini_client, prompt)
        if token_budget:
            token_budget.charge(prompt, raw_text)
        new_text, reported_count = _extract_word_count_control(raw_text)
        if not new_text.strip():
            corrections.append(
                "OUTPUT ISSUE: Draft was empty. Provide the full memo with all sections."
            )
            continue

        working_draft = new_text
        latest_words = _count_words(working_draft)
        within_band = lower <= latest_words <= upper

        if reported_count is None:
            corrections.append(
                "QUALITY CORRECTION: Append the control line `WORD COUNT: ###` exactly once at the end after recounting."
            )
            continue
        if latest_words != reported_count:
            corrections.append(
                f"QUALITY CORRECTION: Control line reported {reported_count} words but the memo contains {latest_words}. "
                "Recount accurately and update the memo."
            )
            continue

        issue_message = None
        if quality_validators:
            for validator in quality_validators:
                issue_message = validator(working_draft)
                if issue_message:
                    break

        if issue_message is None:
            best_valid_draft = working_draft
            best_stats = (latest_words, tolerance)

        if within_band and not issue_message:
            return working_draft, (latest_words, tolerance)

        if not within_band:
            corrections.append(
                f"LENGTH CORRECTION #{attempt}: Draft contains {latest_words} words but must land between {lower} and {upper}. "
                "Condense prose without deleting mandated sections or metrics."
            )
        if issue_message:
            corrections.append(
                f"QUALITY CORRECTION #{attempt}: {issue_message} Rewrite the memo while keeping every prior requirement."
            )

    return best_valid_draft, best_stats


def _enforce_length_constraints(
    summary_text: str,
    target_length: int,
    gemini_client,
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
    last_word_stats: Optional[Tuple[int, int]],
    *,
    token_budget: Optional[TokenBudget] = None,
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
) -> str:
    """
    Ensure the final memo fits inside the required length band using rewrite attempts before trimming.
    """
    if not summary_text:
        return summary_text

    if last_word_stats:
        actual_words, tolerance = last_word_stats
    else:
        _, actual_words, tolerance = _needs_length_retry(summary_text, target_length)
    lower = target_length - tolerance
    upper = target_length + tolerance

    if lower <= actual_words <= upper:
        return summary_text

    rewritten_text, rewrite_stats = _rewrite_summary_to_length(
        gemini_client,
        summary_text,
        target_length,
        quality_validators,
        current_words=actual_words,
        token_budget=token_budget,
        max_output_tokens=max_output_tokens,
    )
    summary_text = rewritten_text
    actual_words, tolerance = rewrite_stats
    lower = target_length - tolerance
    upper = target_length + tolerance

    if lower <= actual_words <= upper:
        return summary_text

    if actual_words > upper:
        overage = actual_words - upper
        # If we're massively over the target, skip an extra model call and trim deterministically first.
        if overage > max(200, int(target_length * 0.35)):
            logger.warning(
                "Summary is far above target range (over by %s words). Applying deterministic trim before any further rewrites.",
                overage,
            )
            summary_text = _trim_preserving_headings(summary_text, upper)
        else:
            logger.warning(
                "Summary remained above target range after rewrite fallback (got %s words; target %s±%s). Attempting compression rewrite.",
                actual_words,
                target_length,
                tolerance,
            )
            compressed, compressed_words = _compress_summary_to_length(
                gemini_client,
                summary_text,
                upper,
                target_length,
                tolerance,
                token_budget=token_budget,
                max_output_tokens=max_output_tokens,
            )
            if compressed and compressed_words and lower <= compressed_words <= upper:
                logger.info(
                    "Compression succeeded: %s words (target %s±%s)",
                    compressed_words,
                    target_length,
                    tolerance,
                )
                return _finalize_length_band(compressed, target_length, tolerance)

            logger.warning(
                "Compression rewrite failed or still out of band. Applying final deterministic clamp.",
            )
            summary_text = _trim_preserving_headings(summary_text, upper)

        actual_words = _count_words(summary_text)
        if lower <= actual_words <= upper:
            return summary_text

    # If still under length, force one more aggressive expansion
    if actual_words < lower:
        shortfall = lower - actual_words

        # IMPORTANT:
        # Deterministic padding is a LAST resort. If we're materially short, we must
        # expand the *real sections* (Exec Summary / Financial Performance / Risk Factors)
        # via a rewrite, otherwise the memo becomes lopsided (e.g. all length stuffed
        # into MD&A) and users perceive it as filler.
        # For very small shortfalls, deterministic padding is acceptable as a last resort.
        # (We keep the padding templates intentionally neutral to avoid out-of-context lines.)
        SMALL_PADDING_THRESHOLD = 25

        if shortfall <= SMALL_PADDING_THRESHOLD:
            padded = _distribute_padding_across_sections(summary_text, shortfall)
            padded_words = _count_words(padded)
            if lower <= padded_words <= upper:
                return _finalize_length_band(padded, target_length, tolerance)
            summary_text = padded
            actual_words = padded_words
            shortfall = lower - actual_words

        if shortfall > 0:
            logger.warning(
                "Summary is critically short (%s words; minimum %s). Forcing emergency expansion.",
                actual_words,
                lower,
            )
            emergency_prompt = (
                f"The following summary is {shortfall} words SHORT of the ABSOLUTE MINIMUM requirement of {lower} words.\n\n"
                f"You MUST expand this summary by adding AT LEAST {int(shortfall * 1.2)} words of substantive analysis.\n\n"
                "CRITICAL EXPANSION REQUIREMENTS:\n"
                "- Add detailed analysis to 'Financial Performance' (margins, cash flow quality, sustainability).\n"
                "- Expand 'Management Discussion & Analysis' with strategic insights and forward guidance.\n"
                "- Elaborate 'Risk Factors' with specific scenarios and quantified impact estimates.\n"
                "- Ensure any strategic initiatives or capital allocation context is integrated into 'Management Discussion & Analysis'.\n"
                "- Keep all existing sections intact. Only ADD content, do not remove anything.\n\n"
                "MANDATORY: Append a final line 'WORD COUNT: ###' with the actual count after expansion.\n\n"
                f"SUMMARY TO EXPAND:\n{summary_text}"
            )
            expected_out_tokens = min(max_output_tokens, max(800, int(shortfall * 4)))
            if token_budget and not token_budget.can_afford(
                emergency_prompt, expected_out_tokens
            ):
                logger.warning(
                    "Skipping emergency expansion due to token budget (remaining=%s tokens)",
                    token_budget.remaining_tokens,
                )
                return _finalize_length_band(summary_text, target_length, tolerance)

            raw_text = _call_gemini_client(
                gemini_client, emergency_prompt, expected_tokens=expected_out_tokens
            )
            if token_budget:
                token_budget.charge(emergency_prompt, raw_text)
            expanded_text, reported_count = _extract_word_count_control(raw_text)
            expanded_words = _count_words(expanded_text)

            if expanded_words >= lower:
                logger.info(
                    "Emergency expansion successful: %s words (minimum %s)",
                    expanded_words,
                    lower,
                )
                return _finalize_length_band(expanded_text, target_length, tolerance)
            else:
                logger.error(
                    "Emergency expansion failed. Returning original (%s words; minimum %s).",
                    actual_words,
                    lower,
                )

    logger.warning(
        "Summary remained outside target range after rewrites (got %s words; target %s±%s). Applying final clamp.",
        _count_words(summary_text),
        target_length,
        tolerance,
    )
    return _finalize_length_band(summary_text, target_length, tolerance)


def _generate_summary_with_length_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
) -> str:
    return _generate_summary_with_quality_control(
        gemini_client, base_prompt, target_length, None
    )


def _generate_summary_with_quality_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
    filing_id: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    *,
    token_budget: Optional[TokenBudget] = None,
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
) -> str:
    """
    Call Gemini up to MAX_SUMMARY_ATTEMPTS times, tightening instructions if word count or quality drifts.
    Uses streaming for real-time progress updates when filing_id is provided.
    """
    gemini_client = _ensure_gemini_client_interface(gemini_client)

    corrections: List[str] = []
    prompt = base_prompt
    previous_draft: Optional[str] = None
    summary_text: str = ""
    last_word_stats: Optional[Tuple[int, int]] = None  # (actual_words, tolerance)
    start_time = time.time()

    def _progress_callback(percentage: int, status: str):
        if filing_id:
            progress_cache[str(filing_id)] = status

    def _rebuild_prompt() -> str:
        correction_block = ("\n\n".join(corrections)) if corrections else ""
        previous_block = (
            f"\n\nPrevious draft (for reference, do not copy verbatim):\n{previous_draft}\n"
            if previous_draft
            else ""
        )
        combined = base_prompt
        # For rewrite attempts, do NOT resend the entire filing context. This keeps
        # token usage bounded and helps stay within per-summary cost budgets.
        if previous_draft:
            combined = _strip_large_context_block(combined)
        if correction_block:
            combined += "\n\n" + correction_block
        combined += previous_block
        combined += "\n\nRewrite the entire memo applying every instruction above."
        return combined

    for attempt in range(1, MAX_SUMMARY_ATTEMPTS + 1):
        if timeout_seconds and (time.time() - start_time) > timeout_seconds:
            raise TimeoutError(f"Summary generation exceeded {timeout_seconds} seconds")
        stage_label = "Generating Summary"
        if filing_id:
            # Keep the status user-facing and stable across retries; internal attempt counts are noise.
            progress_cache[str(filing_id)] = f"{stage_label}..."

        expected_out_tokens = (
            min(max_output_tokens, max(500, int(target_length * 2)))
            if target_length
            else min(max_output_tokens, 4000)
        )
        if token_budget and not token_budget.can_afford(prompt, expected_out_tokens):
            logger.warning(
                "Stopping summary attempts due to token budget (remaining=%s tokens)",
                token_budget.remaining_tokens,
            )
            break

        raw_text = _call_gemini_client(
            gemini_client,
            prompt,
            allow_stream=bool(filing_id),
            progress_callback=_progress_callback if filing_id else None,
            stage_name=stage_label if filing_id else "Generating",
            expected_tokens=expected_out_tokens,
        )
        if token_budget:
            token_budget.charge(prompt, raw_text)
        summary_text, reported_count = _extract_word_count_control(raw_text)
        previous_draft = summary_text

        needs_length_retry = False
        actual_words = None
        if target_length:
            actual_words = _count_words(summary_text)
            needs_length_retry, actual_words, tolerance = _needs_length_retry(
                summary_text, target_length, cached_count=actual_words
            )
            last_word_stats = (actual_words, tolerance)

        if target_length:
            if reported_count is None:
                corrections.append(
                    "QUALITY CORRECTION: You must append a final line formatted exactly as 'WORD COUNT: ###' (with the "
                    "actual number of words in the memo). Add this control line after recounting."
                )
                prompt = _rebuild_prompt()
                continue
            if actual_words is not None and reported_count != actual_words:
                corrections.append(
                    f"QUALITY CORRECTION: Your control line reported {reported_count} words but the memo contains "
                    f"{actual_words}. Recount accurately, adjust the memo to the required length, and update the control line."
                )
                prompt = _rebuild_prompt()
                continue

        if not needs_length_retry:
            issue_message = None
            if quality_validators:
                for validator in quality_validators:
                    issue_message = validator(summary_text)
                    if issue_message:
                        break
            if not issue_message:
                return summary_text

            corrections.append(
                f"QUALITY CORRECTION #{attempt}: {issue_message} Rewrite the entire memo while keeping all previous requirements intact."
            )
            prompt = _rebuild_prompt()
            continue

        prior_count = reported_count if reported_count is not None else actual_words
        diff = prior_count - target_length
        abs_diff = abs(diff)

        if prior_count > (target_length + tolerance):
            sentences_to_cut = max(1, int(abs_diff / 10))
            action = (
                f"CONDENSE the text immediately. You are {abs_diff} words OVER. "
                f"Remove approximately {sentences_to_cut} sentences of fluff or repetitive content. "
                "Merge short sentences. Do not lose key metrics, but be ruthless with adjectives. "
                "DO NOT add a summary at the end."
            )
        else:
            # Much more aggressive expansion with specific word targets
            min_words = target_length - tolerance
            action = (
                f"EXPAND the content immediately. You are {abs_diff} words SHORT of the MINIMUM ({min_words} words).\n"
                f"You MUST add AT LEAST {int(abs_diff * 1.2)} words total.\n\n"
                f"MANDATORY WORD ADDITIONS:\n"
                f"- Financial Performance: +{max(3, int(abs_diff * 0.30))} words (margin analysis, trend interpretation)\n"
                f"- Management Discussion & Analysis: +{max(2, int(abs_diff * 0.35))} words (strategy, capital allocation, execution risks)\n"
                f"- Risk Factors: +{max(2, int(abs_diff * 0.20))} words (specific 'if-then' scenarios)\n"
                f"- Executive Summary: +{max(1, int(abs_diff * 0.10))} words (conviction rationale and narrative)\n"
                f"DO NOT add narrative to Key Metrics; keep it a scannable data block.\n\n"
                f"COUNT your words before finishing. Target: {target_length} words (min: {min_words})."
            )

        corrections.append(
            f"LENGTH CORRECTION #{attempt}: Your last draft contained {prior_count} words. "
            f"REQUIRED RANGE: {target_length - tolerance} to {target_length + tolerance} words. "
            f"You are {abs_diff} words {'OVER' if diff > 0 else 'SHORT'}. \n"
            f"ACTION: {action}\n"
            "This is a STRICT requirement. Failure to meet this range will result in rejection."
        )
        prompt = _rebuild_prompt()

    # BULLETPROOF FINAL VALIDATION: Do not return anything under minimum word count
    if target_length and summary_text:
        final_word_count = _count_words(summary_text)
        tolerance = 10
        minimum_acceptable = target_length - tolerance

        if final_word_count < minimum_acceptable:
            logger.error(
                "CRITICAL: Summary is %s words, below minimum of %s. Forcing final expansion.",
                final_word_count,
                minimum_acceptable,
            )

            shortfall = minimum_acceptable - final_word_count

            # IMPORTANT:
            # Do NOT paper over large deficits with deterministic padding. That creates
            # visibly low-quality filler and can make one section (often MD&A) dominate.
            # Use a final expansion call when the shortfall is material.
            # For very small shortfalls, deterministic padding is acceptable as a last resort.
            # (We keep the padding templates intentionally neutral to avoid out-of-context lines.)
            SMALL_PADDING_THRESHOLD = 25

            if shortfall <= SMALL_PADDING_THRESHOLD:
                padded = _distribute_padding_across_sections(summary_text, shortfall)
                padded_count = _count_words(padded)
                if padded_count > final_word_count:
                    summary_text = padded
                    final_word_count = padded_count

                shortfall = max(0, minimum_acceptable - final_word_count)
                if shortfall == 0:
                    logger.info(
                        "Deterministic padding lifted summary to %s words (minimum %s).",
                        final_word_count,
                        minimum_acceptable,
                    )
                    return _finalize_length_band(summary_text, target_length, tolerance)

            # One final, extremely forceful expansion attempt
            final_expansion_prompt = (
                f"CRITICAL FAILURE: The summary you generated is {final_word_count} words. "
                f"The ABSOLUTE MINIMUM requirement is {minimum_acceptable} words. "
                f"You are {shortfall} words SHORT.\\n\\n"
                f"You MUST add EXACTLY {int(shortfall * 1.3)} words to meet the minimum.\\n\\n"
                "DO NOT rewrite. DO NOT condense. ONLY ADD content to these sections:\\n"
                "1. Financial Performance: Add 3-4 sentences analyzing margin sustainability and cash conversion quality.\\n"
                "2. Management Discussion & Analysis: Add 2-3 sentences on strategic priorities and competitive positioning.\\n"
                "3. Risk Factors: Add 2-3 sentences with specific 'if-then' scenario analysis.\\n\\n"
                "MANDATORY: Keep ALL existing content. Append 'WORD COUNT: ###' at the end.\\n\\n"
                f"SUMMARY TO EXPAND:\\n{summary_text}"
            )

            expected_out_tokens = min(max_output_tokens, max(800, int(shortfall * 4)))
            if token_budget and not token_budget.can_afford(
                final_expansion_prompt, expected_out_tokens
            ):
                logger.warning(
                    "Skipping final expansion due to token budget (remaining=%s tokens)",
                    token_budget.remaining_tokens,
                )
                return _finalize_length_band(summary_text, target_length, tolerance)

            raw_text = _call_gemini_client(
                gemini_client, final_expansion_prompt, expected_tokens=expected_out_tokens
            )
            if token_budget:
                token_budget.charge(final_expansion_prompt, raw_text)
            expanded_text, _ = _extract_word_count_control(raw_text)
            expanded_count = _count_words(expanded_text)

            if expanded_count >= minimum_acceptable:
                logger.info(
                    "Final expansion successful: %s words (minimum %s)",
                    expanded_count,
                    minimum_acceptable,
                )
                return expanded_text
            else:
                # Still too short - log and return the best we have
                logger.error(
                    "FAILED to meet minimum word count after all attempts. "
                    "Returning %s words (minimum %s).",
                    expanded_count
                    if expanded_count > final_word_count
                    else final_word_count,
                    minimum_acceptable,
                )
                return (
                    expanded_text if expanded_count > final_word_count else summary_text
                )

        # If over minimum, apply the usual length constraints
        summary_text = _enforce_length_constraints(
            summary_text,
            target_length,
            gemini_client,
            quality_validators,
            last_word_stats,
            token_budget=token_budget,
            max_output_tokens=max_output_tokens,
        )
        # Final deterministic guardrail to land in the strict band.
        # NOTE: Padding is only used if we're short; templates are intentionally neutral.
        summary_text = _force_final_band(summary_text, target_length, tolerance=10)

    return summary_text


MDNA_BANNED_PHRASES = [
    "not available",
    "not provided",
    "no insights",
    "no information",
    "cannot be gleaned",
    "cannot be inferred",
    "not included",
]


def _validate_mdna_section(text: str) -> Optional[str]:
    """Ensure Management Discussion section exists and has substance."""
    mdna_pattern = re.compile(
        r"(?:##+\s*)?Management Discussion(?:\s*&\s*Analysis)?[:\s]*(.*?)(?:\n(?:#|\w)|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = mdna_pattern.search(text)
    if not match:
        return (
            "You omitted a meaningful 'Management Discussion & Analysis' section. "
            "Add a dedicated subsection that discusses management's priorities, strategy, and outlook."
        )
    section_text = match.group(0).strip()
    lower_section = section_text.lower()
    if any(phrase in lower_section for phrase in MDNA_BANNED_PHRASES):
        return (
            "The 'Management Discussion & Analysis' section currently claims information is unavailable. "
            "Instead, synthesize management's likely commentary using the filing data and historical initiatives."
        )
    if len(section_text.split()) < 60:
        return (
            "The 'Management Discussion & Analysis' section is too brief. Expand it with concrete takeaways on strategy, "
            "competitive dynamics, capital deployment, and guidance signals."
        )
    return None


SUMMARY_SECTION_REQUIREMENTS: List[Tuple[str, int]] = [
    # Users strongly prefer a memo that feels balanced across headings (rather
    # than spending most of the budget in Financial Performance / MD&A).
    #
    # Keep total base mins aligned with the default target length (currently ~550 words)
    # so budgets are stable for the common path.
    ("Financial Health Rating", 70),
    ("Executive Summary", 95),  # Verdict + framing (keep crisp, avoid bloat)
    ("Financial Performance", 85),  # Depth on the numbers that matter
    (
        "Management Discussion & Analysis",
        95,
    ),  # Strategy + capital allocation without dominating total length
    ("Risk Factors", 85),  # Keep concrete risks; avoid generic one-liners
    ("Key Metrics", 30),
    ("Closing Takeaway", 90),  # Reasoned verdict and what changes the view
]
SUMMARY_SECTION_MIN_WORDS = {
    title: minimum for title, minimum in SUMMARY_SECTION_REQUIREMENTS
}

# Section proportional weights for distributing word budgets
# These represent relative importance/length of each section
# Sum = 100 (percentages)
# Executive Summary is the HERO section - the premium insight users pay for
SECTION_PROPORTIONAL_WEIGHTS: Dict[str, int] = {
    # CRITICAL PRODUCT REQUIREMENT:
    # The section-length distribution must stay FIXED regardless of the user's
    # requested total length.
    #
    # Target distribution (sum = 100):
    #   - Financial Health Rating: 10%
    #   - Executive Summary: 15%
    #   - Financial Performance: 20%
    #   - Management Discussion & Analysis: 20%
    #   - Risk Factors: 15%
    #   - Key Metrics: 10%
    #   - Closing Takeaway: 10%
    "Financial Health Rating": 10,
    "Executive Summary": 15,
    "Financial Performance": 20,
    "Management Discussion & Analysis": 20,
    "Risk Factors": 15,
    "Key Metrics": 10,
    "Closing Takeaway": 10,
}

# Key Metrics is a fixed-format, scannable data block. Past a certain length,
# scaling it with the full memo causes low-quality output (repeated "watch" lines).
# For long targets, cap Key Metrics and redistribute the remaining budget across
# narrative sections using their existing weights.
KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS = 1000
KEY_METRICS_FIXED_BUDGET_WORDS = 170
KEY_METRICS_MAX_WORDS = 190
KEY_METRICS_MAX_WATCH_ITEMS = 8


def _section_budget_tolerance_words(budget_words: int, *, max_tolerance: int = 10) -> int:
    """Compute a per-section word tolerance for enforcing the fixed distribution.

    A flat ±10 words is far too loose for short targets (e.g., a 20-word budget would
    allow a 0–40 word section). We scale tolerance to the section budget so the
    proportions stay stable across all target lengths.
    """

    max_tolerance = max(0, int(max_tolerance))
    budget_words = int(budget_words or 0)
    if budget_words <= 0:
        return max(4, max_tolerance) if max_tolerance else 4

    # ~6% of the section budget, bounded to avoid over-tightening and impossible
    # padding when the budget is small.
    scaled = max(4, int(round(budget_words * 0.06)))
    return min(max_tolerance, scaled) if max_tolerance else scaled


def _calculate_section_word_budgets(
    target_length: int,
    include_health_rating: bool = True,
) -> Dict[str, int]:
    """
    Calculate per-section *body* word budgets using a fixed percentage distribution.

    IMPORTANT: These budgets are for SECTION BODY WORDS (excluding the heading titles).
    This aligns with:
      - our validators (which count section bodies), and
      - trimming logic that subtracts heading-title words.

    The distribution MUST remain constant regardless of target_length.
    """
    if not target_length or target_length <= 0:
        return {}

    # Determine which sections to include.
    sections_to_use = list(SECTION_PROPORTIONAL_WEIGHTS.keys())
    if not include_health_rating:
        sections_to_use = [s for s in sections_to_use if s != "Financial Health Rating"]
    if not sections_to_use:
        return {}

    # Budgets are for SECTION BODY words (headings are counted separately in our
    # validators / trimming), so subtract heading-title words from the total.
    heading_words = sum(
        len(re.findall(r"\b\w+\b", section)) for section in sections_to_use
    )
    body_target = max(0, int(target_length) - int(heading_words))
    if body_target <= 0:
        # Degenerate case: user requested a length so small the headings alone don't fit.
        # Fall back to treating target_length as body budget so downstream logic doesn't
        # divide-by-zero.
        body_target = int(target_length)

    use_key_metrics_cap = (
        int(target_length) >= int(KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS)
        and "Key Metrics" in sections_to_use
    )
    fixed_key_metrics_budget = 0
    distribution_sections = sections_to_use[:]
    if use_key_metrics_cap:
        distribution_sections = [s for s in sections_to_use if s != "Key Metrics"]
        fixed_key_metrics_budget = min(int(KEY_METRICS_FIXED_BUDGET_WORDS), int(body_target))

    remaining_body_target = max(0, int(body_target) - int(fixed_key_metrics_budget))

    total_weight = sum(
        SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) for s in distribution_sections
    )
    if total_weight <= 0:
        total_weight = len(distribution_sections) if distribution_sections else 1

    exacts = {
        s: (SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) * remaining_body_target / total_weight)
        for s in distribution_sections
    }
    budgets: Dict[str, int] = {s: int(exacts[s]) for s in distribution_sections}
    remainders = {s: exacts[s] - budgets[s] for s in distribution_sections}

    drift = remaining_body_target - sum(budgets.values())
    if drift != 0:
        order = sorted(
            distribution_sections, key=lambda s: remainders.get(s, 0), reverse=True
        )
        step = 1 if drift > 0 else -1
        remaining = abs(drift)
        idx = 0
        while remaining > 0 and order and idx < 10_000:
            section = order[idx % len(order)]
            next_val = budgets.get(section, 0) + step
            if next_val >= 0:
                budgets[section] = next_val
                remaining -= 1
            idx += 1

    if use_key_metrics_cap:
        budgets["Key Metrics"] = int(fixed_key_metrics_budget)

    # Avoid 0-word sections when the target is extremely small.
    zeros = [s for s in sections_to_use if budgets.get(s, 0) <= 0]
    if zeros:
        for s in zeros:
            budgets[s] = 1
        # Re-balance to preserve exact sum.
        diff = sum(budgets.values()) - body_target
        if diff > 0:
            order = sorted(sections_to_use, key=lambda s: budgets.get(s, 0), reverse=True)
            idx = 0
            while diff > 0 and order and idx < 10_000:
                section = order[idx % len(order)]
                if budgets.get(section, 0) > 1:
                    budgets[section] -= 1
                    diff -= 1
                idx += 1

    return budgets


def _calculate_section_min_words_for_target(
    target_length: Optional[int],
    *,
    include_health_rating: bool,
) -> Dict[str, int]:
    """
    Compute target-aware per-section *minimum* word counts.

    Why: The backend supports short targets (frontend defaults to ~300 words). If we
    enforce static minimums (built for longer memos), generation becomes unsatisfiable
    and post-processing ends up trimming/padding in ways that drop sections (often
    Risk Factors) or create lopsided length distribution.
    """
    base_mins = dict(SUMMARY_SECTION_MIN_WORDS)
    if not include_health_rating:
        base_mins.pop("Financial Health Rating", None)

    if not target_length or target_length <= 0:
        return base_mins

    budgets = _calculate_section_word_budgets(
        target_length, include_health_rating=include_health_rating
    )
    if not budgets:
        return base_mins

    mins: Dict[str, int] = {}
    for section, raw_budget in budgets.items():
        budget = int(raw_budget or 0)
        if budget <= 0:
            continue

        base_min = int(base_mins.get(section, 25))

        # Target-aware minimums: keep sections from collapsing so the distribution
        # stays close to the fixed proportional budgets.
        min_floor = 1
        if section == "Key Metrics":
            # Key Metrics is a fixed-format data block; avoid forcing the model to bloat it
            # just to satisfy long-target distributions.
            if int(target_length) >= int(KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS):
                ratio_min = max(min_floor, min(base_min, budget))
            else:
                ratio_min = max(min_floor, int(budget * 0.70))
        elif section in {"Risk Factors", "Closing Takeaway"}:
            # These are the "bookends" users remember; keep them substantive.
            ratio_min = max(min_floor, int(budget * 0.75))
        elif section == "Financial Health Rating":
            ratio_min = max(min_floor, int(budget * 0.70))
        else:
            ratio_min = max(min_floor, int(budget * 0.70))

        if budget < base_min:
            # Never require a minimum larger than the budget.
            mins[section] = max(min_floor, min(budget, ratio_min))
        else:
            mins[section] = max(base_min, ratio_min)

    # Ensure every expected section has some minimum (fallback to base mins).
    for section, base_min in base_mins.items():
        mins.setdefault(section, int(base_min))

    return mins


def _format_section_word_budgets(
    target_length: int,
    include_health_rating: bool = True,
) -> str:
    """
    Format section word budgets as a readable instruction string.
    """
    budgets = _calculate_section_word_budgets(target_length, include_health_rating)

    sections_to_use = list(SECTION_PROPORTIONAL_WEIGHTS.keys())
    if not include_health_rating:
        sections_to_use = [s for s in sections_to_use if s != "Financial Health Rating"]
    heading_words = sum(len(re.findall(r"\b\w+\b", s)) for s in sections_to_use)

    lines = [
        "=== SECTION WORD BUDGETS (PROPORTIONAL DISTRIBUTION) ===",
        "CRITICAL: The narrative section-length distribution is FIXED. Maintain these proportions regardless of the user's target length.",
        f"Key Metrics is capped at ~{KEY_METRICS_FIXED_BUDGET_WORDS} words for long targets (>= {KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS} words); redistribute the remainder to narrative sections.",
        "Do NOT steal words from one section to inflate another.",
        "",
        "TARGET WORD ALLOCATION PER SECTION:",
    ]

    for section, budget in budgets.items():
        lines.append(f"  • {section}: ~{budget} words")

    total_budgeted = sum(budgets.values())
    lines.extend(
        [
            "",
            f"  TOTAL (SECTION BODIES ONLY): ~{total_budgeted} words",
            f"  NOTE: Section headings add ~{heading_words} words, so the full memo lands near the requested total.",
            "",
            "IMPORTANT:",
            "- Treat these as REQUIRED proportional budgets (minor variance is okay, but keep sections in the same shape)",
            "- If you need to CUT words: cut from EVERY section proportionally",
            "- If you need to ADD words: add to EVERY section proportionally",
            "- Keep Key Metrics as a scannable, arrow-line data block (no prose paragraphs)",
            "- Do NOT sacrifice one section to make room for another",
            "=== END SECTION WORD BUDGETS ===",
        ]
    )

    return "\n".join(lines)


def _enforce_section_budget_distribution(
    summary_text: str,
    *,
    target_length: int,
    include_health_rating: bool,
    metrics_lines: Optional[str] = None,
    section_tolerance: int = 10,
) -> str:
    """Deterministically nudge section lengths toward the fixed proportional budgets.

    This is the final safety net to keep the *actual* output distribution stable even
    after post-processing steps (filler removal, health-score injection, etc.).

    Rules:
    - Uses `_count_words()` (MS Word-style approximation used by the backend).
    - Ensures each section body is within ±section_tolerance of its computed budget.
    - Does NOT add extra headings or sections.
    - If `metrics_lines` is provided, Key Metrics is replaced with that deterministic block.
    """

    if not summary_text or not target_length:
        return summary_text

    # When we have deterministic Key Metrics (from extracted financials), do not pad it into
    # low-signal filler; keep it compact and shift length to narrative sections instead.
    key_metrics_capped = bool(metrics_lines) or (
        int(target_length) >= int(KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS)
    )

    budgets = _calculate_section_word_budgets(
        int(target_length), include_health_rating=include_health_rating
    )
    if not budgets:
        return summary_text

    heading_regex = re.compile(r"^\s*##\s+.+")
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []
    preamble: List[str] = []
    first_heading_seen = False

    for line in (summary_text or "").splitlines():
        if heading_regex.match(line):
            if not first_heading_seen and buffer:
                preamble = buffer[:]
                buffer = []
            first_heading_seen = True
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).strip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)
        else:
            preamble.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(buffer).strip()))

    if not sections:
        return summary_text

    # Merge duplicate canonical sections.
    merged: Dict[str, str] = {}
    for heading, body in sections:
        canon = _standard_section_name_from_heading(heading)
        if canon in merged:
            combined = (merged[canon] or "").strip()
            addition = (body or "").strip()
            merged[canon] = f"{combined}\n\n{addition}".strip() if addition else combined
        else:
            merged[canon] = (body or "").strip()

    # If we captured any preamble, fold it into Executive Summary so it doesn't steal
    # from the proportional budgets.
    preamble_text = "\n".join([ln for ln in preamble if ln.strip()]).strip()
    if preamble_text:
        exec_body = (merged.get("Executive Summary") or "").strip()
        merged["Executive Summary"] = f"{preamble_text}\n\n{exec_body}".strip() if exec_body else preamble_text

    # Replace Key Metrics with deterministic, non-hallucinated block when available.
    if metrics_lines:
        merged["Key Metrics"] = (metrics_lines or "").strip()

    def _normalize_key_metrics_for_word_band(body: str) -> str:
        """Reduce whitespace-token inflation in Key Metrics without losing content."""
        body = (body or "").replace("\u00A0", " ").strip()
        if not body:
            return body

        out_lines: List[str] = []
        for raw in body.splitlines():
            line = (raw or "").rstrip()
            # Pipes are punctuation-only tokens under whitespace counting; convert them to commas.
            line = re.sub(r"\s*\|\s*", ", ", line)
            # Leading '-' bullets inflate `len(text.split())` but don't count as words in `_count_words()`.
            line = re.sub(r"^\s*-\s+", "", line)
            # Prefer words over punctuation-only separators.
            line = line.replace(" + ", " and ")
            line = line.replace(" & ", " and ")
            out_lines.append(line)

        cleaned = "\n".join(out_lines).strip()
        cleaned = re.sub(r",\s*,+", ", ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    if "Key Metrics" in merged:
        merged["Key Metrics"] = _normalize_key_metrics_for_word_band(merged.get("Key Metrics") or "")
        if key_metrics_capped:
            merged["Key Metrics"] = _trim_appendix_preserving_rows(
                merged.get("Key Metrics") or "", int(KEY_METRICS_MAX_WORDS)
            )
            # Treat Key Metrics as a compact, fixed-content block; redistribute any budget
            # slack to narrative sections instead of padding Key Metrics into noisy lists.
            km_budget = int(budgets.get("Key Metrics") or 0)
            km_wc = _count_words(merged.get("Key Metrics") or "")
            delta = int(km_budget) - int(km_wc)
            if delta != 0:
                recipients = [s for s in budgets.keys() if s != "Key Metrics"]
                total_w = sum(SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) for s in recipients) or len(
                    recipients
                )
                magnitude = abs(int(delta))
                exacts = {
                    s: (SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) * magnitude / total_w)
                    for s in recipients
                }
                bump: Dict[str, int] = {s: int(exacts[s]) for s in recipients}
                remainders = {s: exacts[s] - bump[s] for s in recipients}
                drift = int(magnitude) - sum(bump.values())
                if drift:
                    order = sorted(recipients, key=lambda s: remainders.get(s, 0), reverse=True)
                    idx = 0
                    while drift > 0 and order and idx < 10_000:
                        section = order[idx % len(order)]
                        bump[section] = int(bump.get(section, 0)) + 1
                        drift -= 1
                        idx += 1

                for section, inc in bump.items():
                    current = int(budgets.get(section, 0) or 0)
                    budgets[section] = (
                        current + int(inc or 0)
                        if delta > 0
                        else max(1, current - int(inc or 0))
                    )

            budgets["Key Metrics"] = max(0, int(km_wc))

    is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", summary_text or ""))

    def _append_padding(body: str, pad_text: str, section_name: str) -> str:
        body = (body or "").strip()
        pad_text = (pad_text or "").strip()
        if not pad_text:
            return body
        if not body:
            return pad_text
        if section_name == "Risk Factors":
            return f"{body}\n\n{pad_text}".strip()
        cleaned = body.rstrip()
        cleaned = re.sub(r"[-\u2013\u2014]+\s*$", "", cleaned).rstrip()
        if cleaned and not cleaned.endswith((".", "!", "?")):
            cleaned += "."
        return f"{cleaned} {pad_text}".strip()

    def _trim_key_metrics_to(body: str, limit_words: int) -> str:
        return _trim_appendix_preserving_rows((body or "").strip(), limit_words)

    def _pad_key_metrics(body: str, add_words: int, *, upper_words: int) -> str:
        """Pad Key Metrics with additional arrow-line watch items (no prose)."""
        body = (body or "").strip()
        if add_words <= 0:
            return body

        upper_words = max(1, int(upper_words))
        target_words = min(upper_words, _count_words(body) + int(add_words))

        # Keep this list broad so we can pad Key Metrics budgets without inventing numbers.
        # These are intentionally written as "watch items" (not metrics) so they stay non-hallucinatory.
        watch_topics = [
            # Cash + earnings quality
            "cash conversion vs net income",
            "free cash flow conversion",
            "working-capital swings",
            "one-offs vs core earnings",
            "non-GAAP adjustments cadence",
            "cash taxes vs reported tax",
            "cash interest expense",
            # Margins + cost structure
            "margin trend vs pricing/mix",
            "gross margin trajectory",
            "operating margin trajectory",
            "pricing discipline",
            "input cost inflation",
            "opex discipline",
            # Capex + reinvestment
            "capex pacing",
            "capex intensity vs growth",
            "reinvestment intensity",
            "capex efficiency",
            # Balance sheet + liquidity
            "liquidity runway and maturities",
            "debt maturities profile",
            "refinancing rate sensitivity",
            "leverage and refinancing terms",
            "interest coverage and covenants",
            "covenant headroom",
            "lease obligations",
            # Working capital components
            "receivables turns",
            "inventory turns",
            "payables turns",
            "AR aging",
            "bad debt expense",
            # Capital allocation
            "share count dilution",
            "SBC and dilution cadence",
            "buyback pace vs free cash flow",
            "M&A spend vs organic reinvestment",
            # Demand + execution signals (generic)
            "backlog/bookings momentum",
            "order cancellations",
            "customer concentration risk",
            "supplier concentration risk",
            "segment mix shifts",
            "regional demand mix",
            "competitive intensity",
            # FX / macro transmission (keep generic)
            "FX and geographic mix",
            "FX hedging effectiveness",
        ]

        watch_templates = [f"→ Watch: {topic}" for topic in watch_topics]
        candidates: List[Tuple[int, str]] = [(_count_words(t), t) for t in watch_templates]
        candidates = [(wc, t) for wc, t in candidates if wc > 0 and t]
        candidates.sort(key=lambda x: x[0])

        used = set((body or "").splitlines())
        working = body

        # Add lines until we hit the target budget, staying <= upper_words.
        for _ in range(600):
            current = _count_words(working)
            if current >= target_words or current >= upper_words:
                break

            slack = max(0, upper_words - current)
            if slack <= 0:
                break

            chosen: Optional[str] = None
            # Prefer unused lines; pick the longest that fits the remaining slack.
            for wc, template in reversed(candidates):
                if wc <= slack and template not in used:
                    chosen = template
                    break
            # If we've exhausted unique lines, allow repeats as a last resort to
            # satisfy strict distribution budgets.
            if chosen is None:
                for wc, template in reversed(candidates):
                    if wc <= slack:
                        chosen = template
                        break
            if chosen is None:
                break

            working = (working + "\n" + chosen).strip() if working else chosen
            used.add(chosen)

        if _count_words(working) > upper_words:
            working = _trim_key_metrics_to(working, upper_words)
        return working.strip()

    def _seed_exclude_norms(body: str) -> set[str]:
        """Collect existing sentences so padding avoids duplicating model text."""
        norms: set[str] = set()
        for line in (body or "").splitlines():
            stripped = (line or "").strip()
            if not stripped:
                continue
            # Skip obvious non-prose lines.
            if stripped.startswith("#") or stripped.startswith("→"):
                continue
            # Keep risk paragraphs (they start with **name**: ...) but ignore bullet
            # markers that don't add meaning.
            if stripped in {"-", "*", "•"}:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", stripped):
                sent = (sent or "").strip()
                if len(sent.split()) < 5:
                    continue
                norms.add(sent)
        return norms

    def _extract_risk_names(body: str) -> set[str]:
        names: set[str] = set()
        for match in re.finditer(r"\*\*(.+?)\*\*\s*:", body or ""):
            name = re.sub(r"[^a-z0-9]+", " ", match.group(1).lower()).strip()
            if name:
                names.add(name)
        return names

    def _remove_last_sentence(body: str, *, min_words: int) -> str:
        """Remove exactly one trailing sentence (complete-thought safe).

        Returns the original body if we cannot remove a full sentence while staying
        above `min_words`.
        """
        body = (body or "").strip()
        if _count_words(body) <= min_words:
            return body

        endings: List[int] = []
        for i, char in enumerate(body):
            if char not in ".!?":
                continue
            # Skip decimals like 1.2
            if i + 1 < len(body) and body[i + 1].isdigit():
                continue
            # Skip abbreviations mid-token
            if i + 1 < len(body) and body[i + 1] not in " \n\t\"'":
                continue
            endings.append(i)

        # Need at least two sentence endings to remove *one* sentence.
        if len(endings) < 2:
            return body

        candidate = body[: endings[-2] + 1].rstrip()
        return candidate if _count_words(candidate) >= min_words else body

    def _trim_one_key_metrics_row(body: str, *, min_words: int) -> str:
        """Remove one trailing Key Metrics row (arrow-line safe)."""
        body = (body or "").strip()
        if _count_words(body) <= min_words:
            return body

        lines = [ln.rstrip() for ln in body.splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) <= 1:
            return body

        candidate = "\n".join(lines[:-1]).strip()
        return candidate if _count_words(candidate) >= min_words else body

    def _normalize_risk_factors_body(body: str, *, max_items: int = 10) -> str:
        """Normalize Risk Factors so each risk starts on its own paragraph and duplicates are removed."""
        body = (body or "").replace("\u00A0", " ").strip()
        if not body:
            return body

        preamble = ""
        first_header = re.search(r"\*\*[^*]{2,120}\*\*\s*:", body)
        if first_header and first_header.start() > 0:
            preamble = body[: first_header.start()].strip()
            body = body[first_header.start() :].strip()

        # If the model emitted multiple risks inline (e.g., "**A**: ... **B**: ..."),
        # force each risk header to start on its own line so the UI renders it cleanly.
        body = re.sub(r"\s+(?=\*\*[^*]{2,120}\*\*\s*:)", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

        lines = [ln.strip() for ln in body.splitlines() if (ln or "").strip()]
        items: List[Tuple[str, str]] = []
        buffer: List[str] = []
        current_title: Optional[str] = None

        def _flush() -> None:
            nonlocal buffer, current_title, items
            if not current_title:
                buffer = []
                return
            desc = " ".join(buffer).strip()
            desc = re.sub(r"\s+", " ", desc).strip()
            if desc:
                sentences = re.split(r"(?<=[.!?])\s+", desc)
                desc = " ".join([s for s in sentences[:3] if s]).strip()
                items.append((current_title, desc))
            buffer = []
            current_title = None

        for line in lines:
            match = re.match(r"^\*\*(.+?)\*\*\s*:\s*(.*)$", line)
            if match:
                _flush()
                current_title = match.group(1).strip()
                remainder = (match.group(2) or "").strip()
                if remainder:
                    buffer.append(remainder)
                continue

            if current_title:
                buffer.append(line)

        _flush()

        if not items:
            return body

        seen: set[str] = set()
        out: List[str] = []
        for title, desc in items:
            norm = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(f"**{title}**: {desc}".strip())
            if len(out) >= int(max_items):
                break

        blocks: List[str] = []
        if preamble:
            blocks.append(preamble)
        blocks.append("\n\n".join(out).strip())
        return "\n\n".join([b for b in blocks if b.strip()]).strip()

    # Adjust each canonical section into its budget band.
    for section_name, budget in budgets.items():
        # If the model omitted a canonical section, create an empty placeholder so
        # deterministic padding can still restore the fixed distribution.
        if section_name not in merged:
            merged[section_name] = ""
        budget = int(budget or 0)
        if budget <= 0:
            continue

        tol = _section_budget_tolerance_words(budget, max_tolerance=int(section_tolerance))
        lower = max(1, budget - tol)
        upper = budget + tol

        body = (merged.get(section_name) or "").strip()

        # Trim if overweight.
        wc = _count_words(body)
        if wc > upper:
            if section_name == "Key Metrics":
                cap = int(KEY_METRICS_MAX_WORDS) if key_metrics_capped else upper
                body = _trim_appendix_preserving_rows(body, min(upper, cap))
            else:
                body = _truncate_text_to_word_limit(body, upper)
            wc = _count_words(body)

        # Pad if underweight.
        if wc < lower:
            if section_name == "Key Metrics":
                # Only pad Key Metrics for shorter targets where the budget is small.
                # For long targets, padding becomes a repetitive "watch list" dump.
                if not key_metrics_capped:
                    body = _pad_key_metrics(body, lower - wc, upper_words=upper)
                    wc = _count_words(body)
            else:
                # IMPORTANT: Padding must be incremental so we don't overshoot the
                # section upper bound and then get truncated back to the original
                # text (no net progress).
                exclude_norms = _seed_exclude_norms(body)

                while wc < lower and wc < upper:
                    risk_name_exclusions = (
                        _extract_risk_names(body) if section_name == "Risk Factors" else None
                    )
                    slack = max(0, upper - wc)
                    needed = max(1, lower - wc)

                    request_candidates = [
                        min(needed, slack, 12),
                        min(needed, slack, 8),
                        min(needed, slack, 5),
                        1,
                    ]
                    request_candidates = [r for r in request_candidates if r > 0]
                    request_candidates = list(dict.fromkeys(request_candidates))

                    progressed = False
                    repeat_policy = (False,) if section_name == "Risk Factors" else (False, True)
                    for allow_repeats in repeat_policy:
                        for req in request_candidates:
                            pad_sentences = _generate_padding_sentences(
                                req,
                                exclude_norms=None if allow_repeats else exclude_norms,
                                section=section_name,
                                is_persona=is_persona,
                                exclude_risk_names=risk_name_exclusions,
                                max_words=slack,
                            )
                            if section_name == "Risk Factors":
                                pad_text = "\n".join([s for s in pad_sentences if (s or "").strip()]).strip()
                            else:
                                pad_text = " ".join(pad_sentences).strip()
                            candidate = _append_padding(body, pad_text, section_name)
                            if _count_words(candidate) > upper:
                                candidate = _truncate_text_to_word_limit(candidate, upper)
                            new_wc = _count_words(candidate)
                            if new_wc > wc:
                                body = candidate
                                wc = new_wc
                                exclude_norms = _seed_exclude_norms(body)
                                progressed = True
                                break
                        if progressed:
                            break

                    if not progressed:
                        break

        if section_name == "Risk Factors":
            body = _normalize_risk_factors_body(body, max_items=10)
        merged[section_name] = (body or "").strip()

    # Canonical order (no extra headings allowed here).
    order = [
        "Financial Health Rating" if include_health_rating else None,
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ]
    order = [o for o in order if o]

    overall_lower = int(target_length) - 10
    overall_upper = int(target_length) + 10

    def _section_bounds(name: str) -> Tuple[int, int]:
        budget = int(budgets.get(name, 0) or 0)
        if budget <= 0:
            return 1, max(2, int(section_tolerance))
        tol = _section_budget_tolerance_words(budget, max_tolerance=int(section_tolerance))
        return max(1, budget - tol), budget + tol

    def _rebuild_from_merged() -> str:
        blocks: List[str] = []
        for name in order:
            body = (merged.get(name) or "").strip()
            if not body:
                continue
            blocks.append(f"## {name}\n{body}".strip())
        return "\n\n".join(blocks).strip()

    # Global rebalance loop: keep BOTH overall word counts (whitespace + MS-word style)
    # inside the band while preserving per-section bounds.
    for _ in range(12):
        rebuilt = _rebuild_from_merged()
        split_count = len(rebuilt.split())
        stripped_count = _count_words(rebuilt)

        if (
            overall_lower <= split_count <= overall_upper
            and overall_lower <= stripped_count <= overall_upper
        ):
            break

        if split_count > overall_upper or stripped_count > overall_upper:
            excess = max(split_count - overall_upper, stripped_count - overall_upper)
            # Trim from sections with the most slack above their lower bound.
            trim_candidates: List[Tuple[int, str]] = []  # (slack, name)
            for name in order:
                body = (merged.get(name) or "").strip()
                if not body:
                    continue
                sec_lower, _sec_upper = _section_bounds(name)
                current_wc = _count_words(body)
                slack = current_wc - sec_lower
                if slack > 0:
                    trim_candidates.append((slack, name))

            trim_candidates.sort(reverse=True)

            for _slack, name in trim_candidates:
                if excess <= 0:
                    break
                body = (merged.get(name) or "").strip()
                if not body:
                    continue
                sec_lower, _sec_upper = _section_bounds(name)
                current_wc = _count_words(body)
                if current_wc <= sec_lower:
                    continue
                # For small overages, sentence-safe word targeting can undershoot
                # dramatically (e.g., dropping an entire paragraph). Prefer removing
                # ONE complete sentence/row at a time.
                if name == "Key Metrics":
                    new_body = _trim_one_key_metrics_row(body, min_words=sec_lower)
                else:
                    new_body = _remove_last_sentence(body, min_words=sec_lower)

                new_wc = _count_words(new_body)
                removed = max(0, current_wc - new_wc)
                if removed > 0:
                    merged[name] = new_body.strip()
                    excess = max(0, excess - removed)
            continue

        # Under target: pad into sections that still have room up to their upper bound.
        deficit = max(overall_lower - split_count, overall_lower - stripped_count)
        pad_candidates: List[Tuple[int, str]] = []  # (slack, name)
        for name in order:
            body = (merged.get(name) or "").strip()
            _sec_lower, sec_upper = _section_bounds(name)
            current_wc = _count_words(body)
            slack = max(0, sec_upper - current_wc)
            if slack > 0:
                pad_candidates.append((slack, name))

        pad_candidates.sort(reverse=True)

        for _slack, name in pad_candidates:
            if deficit <= 0:
                break
            body = (merged.get(name) or "").strip()
            sec_lower, sec_upper = _section_bounds(name)
            current_wc = _count_words(body)
            slack = max(0, sec_upper - current_wc)
            if slack <= 0:
                continue

            if name == "Key Metrics":
                if key_metrics_capped:
                    continue
                new_body = _pad_key_metrics(body, deficit, upper_words=sec_upper)
            else:
                # IMPORTANT:
                # `_generate_padding_sentences(required_words)` chooses the *shortest* template
                # that is >= required_words. If we request the full slack (often ~20 words),
                # the chosen sentence can overshoot the section upper bound and then get
                # truncated away entirely (no net progress). To guarantee progress, request
                # smaller chunks that fit inside the slack.
                request_candidates = [
                    min(deficit, slack, 12),
                    min(deficit, slack, 8),
                    min(deficit, slack, 5),
                    1,
                ]
                request_candidates = [r for r in request_candidates if r > 0]
                request_candidates = list(dict.fromkeys(request_candidates))

                new_body = body
                risk_name_exclusions = (
                    _extract_risk_names(body) if name == "Risk Factors" else None
                )
                exclude_norms = _seed_exclude_norms(body)
                for allow_repeats in (False, True):
                    for req in request_candidates:
                        pad_sentences = _generate_padding_sentences(
                            req,
                            exclude_norms=None if allow_repeats else exclude_norms,
                            section=name,
                            is_persona=is_persona,
                            exclude_risk_names=risk_name_exclusions,
                            max_words=slack,
                        )
                        pad_text = " ".join(pad_sentences).strip()
                        candidate = _append_padding(body, pad_text, name)
                        if _count_words(candidate) > sec_upper:
                            candidate = _truncate_text_to_word_limit(candidate, sec_upper)
                        if _count_words(candidate) > current_wc:
                            new_body = candidate
                            break
                    if _count_words(new_body) > current_wc:
                        break

            new_wc = _count_words(new_body)
            added = max(0, new_wc - current_wc)
            if added > 0:
                merged[name] = new_body.strip()
                deficit = max(0, deficit - added)

    rebuilt = _rebuild_from_merged().strip()

    # Final safety: if we're still over the strict band, prefer reducing punctuation-heavy
    # whitespace tokens (Key Metrics separators) BEFORE removing real words.
    final_split = len(rebuilt.split())
    final_stripped = _count_words(rebuilt)
    if final_split > overall_upper or final_stripped > overall_upper:
        if "Key Metrics" in merged:
            merged["Key Metrics"] = _normalize_key_metrics_for_word_band(
                merged.get("Key Metrics") or ""
            )
        rebuilt = _rebuild_from_merged().strip()
        final_split = len(rebuilt.split())
        final_stripped = _count_words(rebuilt)

    # If we're still over, trim within per-section lower bounds (sentence/row safe).
    if final_split > overall_upper or final_stripped > overall_upper:
        for _ in range(600):
            final_split = len(rebuilt.split())
            final_stripped = _count_words(rebuilt)
            if final_split <= overall_upper and final_stripped <= overall_upper:
                break

            excess = max(final_split - overall_upper, final_stripped - overall_upper)
            trimmed_any = False

            trim_candidates: List[Tuple[int, str]] = []  # (slack, name)
            for name in order:
                body = (merged.get(name) or "").strip()
                if not body:
                    continue
                sec_lower, _sec_upper = _section_bounds(name)
                current_wc = _count_words(body)
                slack = current_wc - sec_lower
                if slack > 0:
                    trim_candidates.append((slack, name))

            trim_candidates.sort(reverse=True)

            # For tiny overages, prefer micro-trimming single filler words rather than
            # removing whole sentences (which can undershoot section lower bounds).
            if excess <= 25:
                for slack, name in trim_candidates:
                    if excess <= 0:
                        break
                    body = (merged.get(name) or "").strip()
                    if not body:
                        continue
                    sec_lower, _sec_upper = _section_bounds(name)
                    current_wc = _count_words(body)
                    if current_wc <= sec_lower:
                        continue

                    attempt = min(int(excess), int(slack), 6)
                    if attempt <= 0:
                        continue

                    # Reuse the global micro-trimmer on a synthetic section so it doesn't
                    # accidentally skip based on the real section name.
                    trimmed_text, removed_words = _micro_trim_filler_words(
                        f"## Temp\n{body}", attempt
                    )
                    if not removed_words:
                        continue

                    new_body = "\n".join(trimmed_text.splitlines()[1:]).strip()
                    new_wc = _count_words(new_body)
                    if new_wc >= sec_lower and new_wc < current_wc:
                        merged[name] = new_body.strip()
                        excess = max(0, int(excess) - int(removed_words))
                        trimmed_any = True

                if trimmed_any:
                    rebuilt = _rebuild_from_merged().strip()
                    continue

            for _slack, name in trim_candidates:
                if excess <= 0:
                    break
                body = (merged.get(name) or "").strip()
                if not body:
                    continue
                sec_lower, _sec_upper = _section_bounds(name)
                current_wc = _count_words(body)
                if current_wc <= sec_lower:
                    continue

                if name == "Key Metrics":
                    new_body = _trim_one_key_metrics_row(body, min_words=sec_lower)
                else:
                    new_body = _remove_last_sentence(body, min_words=sec_lower)

                new_wc = _count_words(new_body)
                removed = max(0, current_wc - new_wc)
                if removed > 0:
                    merged[name] = new_body.strip()
                    excess = max(0, excess - removed)
                    trimmed_any = True

            rebuilt = _rebuild_from_merged().strip()
            if not trimmed_any:
                break
    return rebuilt.strip()


# Rating scale - using dashboard-aligned labels only (no letter grades per user decision)
# Scale: 0-49 = At Risk, 50-69 = Watch, 70-84 = Healthy, 85-100 = Very Healthy
# NO letter grades (A, B, C, D) - numeric score + descriptive label only
RATING_SCALE = [
    (85, "VH", "Very Healthy"),
    (70, "H", "Healthy"),
    (50, "W", "Watch"),
    (0, "AR", "At Risk"),
]


def _make_section_completeness_validator(
    include_health_rating: bool, target_length: Optional[int] = None
):
    required_titles: List[Tuple[str, int]] = [
        (title, minimum)
        for title, minimum in SUMMARY_SECTION_REQUIREMENTS
        if title != "Financial Health Rating"
    ]
    if include_health_rating:
        required_titles = SUMMARY_SECTION_REQUIREMENTS

    ordered_titles = [title for title, _ in required_titles]
    min_words_by_section: Dict[str, int] = {
        title: int(SUMMARY_SECTION_MIN_WORDS.get(title, 25)) for title in ordered_titles
    }
    if target_length and target_length > 0:
        scaled_mins = _calculate_section_min_words_for_target(
            target_length, include_health_rating=include_health_rating
        )
        min_words_by_section = {
            title: int(scaled_mins.get(title, min_words_by_section.get(title, 25)))
            for title in ordered_titles
        }

    def _validator(text: str) -> Optional[str]:
        lower_text = text.lower()
        search_start = 0
        for idx, title in enumerate(ordered_titles):
            target = title.lower()
            heading_token = f"## {target}"
            match_index = lower_text.find(heading_token, search_start)
            if match_index == -1:
                return f"Missing the heading '## {title}'. Use that exact markdown heading (no prefixes) and include substantive content beneath it."
            section_start = match_index + len(heading_token)
            next_section_index = len(text)
            for future_title in ordered_titles[idx + 1 :]:
                future_pos = lower_text.find(
                    f"## {future_title.lower()}", section_start
                )
                if future_pos != -1:
                    next_section_index = future_pos
                    break
            section_body = text[section_start:next_section_index].strip()
            word_count = _count_words(section_body)
            min_words = int(min_words_by_section.get(title, 25))
            if word_count < min_words:
                return (
                    f"The '{title}' section is too brief ({word_count} words). Expand it to at least {min_words} words "
                    "and ensure it concludes on a full sentence."
                )
            search_start = section_start
        return None

    return _validator


def _make_no_extra_sections_validator(
    include_health_rating: bool,
) -> Callable[[str], Optional[str]]:
    """Fail if the model adds extra headings beyond the canonical memo structure.

    Extra sections (e.g., "Valuation", "Outlook") break the fixed distribution because
    they consume word budget that is supposed to belong to the 6/7 canonical sections.
    """

    allowed = {
        title
        for title, _ in SUMMARY_SECTION_REQUIREMENTS
        if include_health_rating or title != "Financial Health Rating"
    }

    heading_re = re.compile(r"^\s*##\s+(.+?)\s*$", re.MULTILINE)

    def _validator(text: str) -> Optional[str]:
        if not text:
            return None
        for match in heading_re.finditer(text):
            raw_title = (match.group(1) or "").strip()
            canon = _standard_section_name_from_heading(f"## {raw_title}")
            if canon not in allowed:
                return (
                    f"Extra section heading detected: '## {raw_title}'. "
                    "Only use the required headings (Financial Health Rating, Executive Summary, "
                    "Financial Performance, Management Discussion & Analysis, Risk Factors, Key Metrics, Closing Takeaway). "
                    "Fold any extra content into the closest relevant required section (usually MD&A)."
                )
        return None

    return _validator


def _make_section_balance_validator(include_health_rating: bool, target_length: int):
    """
    Validate that section lengths are reasonably proportional to the target budgets.

    This protects against a common failure mode: the model spends too many words early,
    then compresses or drops later sections (especially Risk Factors) to satisfy the
    strict total word band.
    """
    budgets = _calculate_section_word_budgets(
        target_length, include_health_rating=include_health_rating
    )
    lower = target_length - 10
    upper = target_length + 10

    # User requirement: enforce the proportional distribution tightly.
    # Budgets are for *section body* words (heading titles excluded).
    section_tolerance = 10

    required_titles: List[Tuple[str, int]] = [
        (title, minimum)
        for title, minimum in SUMMARY_SECTION_REQUIREMENTS
        if title != "Financial Health Rating"
    ]
    if include_health_rating:
        required_titles = SUMMARY_SECTION_REQUIREMENTS

    ordered_titles = [title for title, _ in required_titles]

    def _validator(text: str) -> Optional[str]:
        lower_text = text.lower()
        search_start = 0

        for idx, title in enumerate(ordered_titles):
            heading_token = f"## {title.lower()}"
            match_index = lower_text.find(heading_token, search_start)
            if match_index == -1:
                # Let the completeness validator produce the canonical error message,
                # but return something sensible if called standalone.
                return f"Missing the heading '## {title}'."

            section_start = match_index + len(heading_token)
            next_section_index = len(text)
            for future_title in ordered_titles[idx + 1 :]:
                future_pos = lower_text.find(f"## {future_title.lower()}", section_start)
                if future_pos != -1:
                    next_section_index = future_pos
                    break

            section_body = text[section_start:next_section_index].strip()
            word_count = len(re.findall(r"\b\w+\b", section_body))

            expected = int(budgets.get(title, 0) or 0)
            if expected <= 0:
                search_start = section_start
                continue

            min_allowed = max(1, expected - section_tolerance)
            max_allowed = expected + section_tolerance

            if word_count < min_allowed:
                return (
                    f"Section balance issue: '{title}' is underweight ({word_count} words; target ~{expected}±{section_tolerance}). "
                    f"Expand it and shorten other sections proportionally so the memo stays within {lower}-{upper} words."
                )
            if word_count > max_allowed:
                return (
                    f"Section balance issue: '{title}' is overweight ({word_count} words; target ~{expected}±{section_tolerance}). "
                    f"Tighten it and reallocate words to the shorter sections (especially Risk Factors), "
                    f"while staying within {lower}-{upper} words."
                )

            search_start = section_start

        return None

    return _validator


def _extract_markdown_section_body(text: str, title: str) -> Optional[str]:
    if not text or not title:
        return None
    pattern = re.compile(
        rf"^\s*##\s*{re.escape(title)}\s*\n+(.*?)(?=^\s*##\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _count_numeric_tokens(text: str) -> int:
    if not text:
        return 0
    # Tokens that contain at least one digit (captures FY24, 10-K, $9.2B, 1.5x, 36.2%, etc.)
    return len(re.findall(r"\b[\w$€£]*\d[\w%/.\-]*\b", text))


def _make_numbers_discipline_validator(target_length: Optional[int]) -> Callable[[str], Optional[str]]:
    max_exec = 8 if (target_length or 0) >= 600 else 6
    max_closing = 4 if (target_length or 0) >= 600 else 3

    def _validator(text: str) -> Optional[str]:
        exec_body = _extract_markdown_section_body(text, "Executive Summary")
        if exec_body:
            exec_nums = _count_numeric_tokens(exec_body)
            if exec_nums > max_exec:
                return (
                    f"Numbers discipline: Executive Summary is too numeric ({exec_nums} numeric tokens). "
                    "Keep it mostly qualitative with only 1-2 anchor figures; move dense metrics to Financial Performance / Key Metrics."
                )

        closing_body = _extract_markdown_section_body(text, "Closing Takeaway")
        if closing_body:
            closing_nums = _count_numeric_tokens(closing_body)
            if closing_nums > max_closing:
                return (
                    f"Numbers discipline: Closing Takeaway is too numeric ({closing_nums} numeric tokens). "
                    "Synthesize the narrative and keep numbers minimal; focus on verdict + what would change the view."
                )

        return None

    return _validator


def _make_closing_recommendation_validator(
    *, persona_requested: bool, company_name: str
) -> Callable[[str], Optional[str]]:
    def _validator(text: str) -> Optional[str]:
        closing_body = _extract_markdown_section_body(text, "Closing Takeaway")
        if not closing_body:
            return None
        if not re.search(r"\b(buy|hold|sell)\b", closing_body, re.IGNORECASE):
            voice = "first-person" if persona_requested else "third-person"
            return (
                "Closing Takeaway is missing an explicit Buy/Hold/Sell recommendation. "
                f"Add a clear {voice} recommendation sentence that mentions {company_name}."
            )
        return None

    return _validator


def _make_risk_specificity_validator(
    *, risk_factors_excerpt: Optional[str]
) -> Callable[[str], Optional[str]]:
    excerpt_raw = risk_factors_excerpt or ""
    excerpt_norm = " ".join(excerpt_raw.split()).lower()

    def _norm_for_search(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
        return " ".join(cleaned.split())

    excerpt_search = _norm_for_search(excerpt_raw)

    risk_item_pattern = re.compile(
        r"\*\*(?P<name>[^*]{2,120})\*\*\s*:\s*(?P<body>.+?)(?=(?:\n\s*\*\*[^*]+\*\*\s*:)|\Z)",
        re.DOTALL,
    )

    quote_pattern = re.compile(r"[\"“](.+?)[\"”]")

    def _has_grounded_quote(body: str) -> bool:
        if not excerpt_norm:
            return True
        if not body:
            return False
        for match in quote_pattern.finditer(body):
            quote = " ".join((match.group(1) or "").split())
            if not quote:
                continue
            word_count = len(quote.split())
            if word_count < 4 or word_count > 12:
                continue
            quote_search = _norm_for_search(quote)
            if quote_search and quote_search in excerpt_search:
                return True
        return False

    def _validator(text: str) -> Optional[str]:
        risk_body = _extract_markdown_section_body(text, "Risk Factors")
        if not risk_body:
            return None

        items = list(risk_item_pattern.finditer(risk_body))
        if len(items) < 2:
            return (
                "Risk Factors are not in the required format. Provide 2-3 risks using: "
                "**Risk Name**: 2-3 sentences with company-specific mechanisms."
            )

        seen_names: set[str] = set()
        grounded_quotes = 0

        for match in items:
            name = (match.group("name") or "").strip()
            body = (match.group("body") or "").strip()
            canon = _canonicalize_section_title(name)
            if canon in seen_names:
                return "Risk Factors contain duplicate risk names. Use distinct, non-overlapping drivers."
            seen_names.add(canon)

            if len(body.split()) < 18:
                return (
                    f"Risk Factors are too thin under '{name}'. Expand each risk to 2-3 substantive sentences with a clear mechanism."
                )

            if not re.search(r"\d", body):
                return (
                    f"Risk Factors under '{name}' must include at least one numeric anchor from this memo (%, $, ratio) to quantify impact."
                )

            if excerpt_norm and _has_grounded_quote(body):
                grounded_quotes += 1

        if excerpt_norm and grounded_quotes < min(2, len(items)):
            return (
                "Risk Factors are too generic relative to the filing text. "
                "For at least two risks, include a short verbatim quote (4-10 words) from the provided RISK FACTORS excerpt in quotation marks."
            )

        return None

    return _validator


def _build_preference_instructions(
    preferences: Optional[FilingSummaryPreferences],
    company_name: Optional[str] = None,
) -> str:
    """Convert user-provided preferences into prompt guidance."""
    if not preferences or preferences.mode == "default":
        base = (
            "- Use the standard structure below with a balanced, neutral tone suitable for institutional investors.\n"
            "- NO PERSONA: Write as a neutral professional analyst. Use third-person language ('The company...', 'The data indicates...').\n"
            "- FORBIDDEN: First-person language ('I', 'my view'), famous investor voices (Buffett, Munger, Graham, etc.), folksy analogies.\n"
            "- FOCUS ON: Quantitative metrics, objective analysis, evidence-based conclusions."
        )
        if preferences and preferences.target_length:
            base += f"\nFinal deliverable must contain {preferences.target_length} words (±10 words)."
        if preferences and preferences.tone:
            base += f"\nTone must remain {preferences.tone}."
        return base

    instructions: List[str] = [
        "=== USER CUSTOMIZATION REQUIREMENTS (MANDATORY - ZERO TOLERANCE FOR DEVIATION) ===",
        "The user has provided SPECIFIC customization preferences. You MUST follow ALL of these exactly:",
        "",
        "CRITICAL: These user preferences OVERRIDE any default behavior. Failure to comply = invalid output.",
        "",
    ]

    investor_focus = (
        preferences.investor_focus.strip() if preferences.investor_focus else None
    )
    if investor_focus:
        focus_clause = (
            f"{investor_focus} as it relates to {company_name}"
            if company_name
            else investor_focus
        )
        instructions.append(f"Investor brief (absolute priority): {focus_clause}")
        instructions.append(
            "You must explicitly reference this persona by name in your narrative to prove you adopted the viewpoint."
        )
        instructions.append(
            f"You MUST adopt this persona/viewpoint COMPLETELY. This is not optional. "
            f"Use STRONG first-person language ('I', 'me', 'my view', 'from my perspective'). "
            f"EVERY section must reflect this viewpoint - not just the intro."
        )
        # NOTE: Investor Lens section removed - persona voice is now integrated into Executive Summary directly
        instructions.append(
            "- In the 'Executive Summary', provide your decisive verdict. Use phrases like 'I like...', 'I am concerned about...', 'My take is...'. Be opinionated based on the persona's criteria."
        )
        instructions.append(
            "- In EVERY major section, include at least one sentence explaining why the content matters to YOU (the persona) before citing generic takeaways. Do NOT write like a neutral analyst."
        )
        instructions.append(
            "- CRITICAL FOR MD&A: If the 'Management Discussion & Analysis' section is not explicitly labeled or appears missing, you MUST infer management's perspective from the 'FULL TEXT CONTEXT' provided at the end of the input. Do NOT state that the section is missing. Extract insights on strategy, R&D, and future outlook from the available text."
        )
    else:
        # No persona selected - enforce objective, neutral analysis
        instructions.append(
            "=== NO PERSONA - OBJECTIVE ANALYST MODE ===\n"
            "No investor persona was selected. You MUST write as a NEUTRAL PROFESSIONAL ANALYST.\n\n"
            "FORBIDDEN (DO NOT USE):\n"
            "- First-person language: 'I', 'my view', 'I would', 'I believe', 'my conviction'\n"
            "- Famous investor voices or catchphrases (Buffett, Munger, Graham, Lynch, etc.)\n"
            "- Folksy analogies or colorful investor expressions\n\n"
            "REQUIRED (ALWAYS USE):\n"
            "- Third-person objective language: 'The analysis indicates...', 'The data suggests...'\n"
            "- Professional research analyst tone\n"
            "- Quantitative focus: revenue growth %, margins, ROE, valuation multiples\n"
            "- Evidence-based conclusions tied to specific financial metrics"
        )

    if preferences.focus_areas:
        joined = ", ".join(preferences.focus_areas)
        instructions.append(
            f"Primary focus areas (cover strictly in this order and dedicate space):\n{joined}\n"
            f"EACH focus area MUST have its own dedicated paragraph or subsection."
        )
        ordered_lines = "\n".join(
            f"   {idx + 1}. {area}" for idx, area in enumerate(preferences.focus_areas)
        )
        instructions.append("Focus area execution order:\n" + ordered_lines)

    if preferences.tone:
        instructions.append(
            f"Tone must remain {preferences.tone}. Use this tone consistently across ALL sections. If tone drifts, rewrite."
        )

    if preferences.target_length:
        instructions.append(
            f"Final deliverable must contain {preferences.target_length} words (±10 words)."
        )

    detail_prompt = DETAIL_LEVEL_PROMPTS.get((preferences.detail_level or "").lower())
    if detail_prompt:
        detail_upper = (preferences.detail_level or "").upper()
        instructions.append(
            f"\n=== DETAIL LEVEL (USER-SPECIFIED: {detail_upper}) ===\n"
            f"{detail_prompt}\n"
            f"You MUST match this detail level exactly. Too brief = invalid. Too verbose = invalid."
        )

    output_prompt = OUTPUT_STYLE_PROMPTS.get((preferences.output_style or "").lower())
    if output_prompt:
        style_upper = (preferences.output_style or "").upper()
        instructions.append(
            f"\n=== OUTPUT STYLE (USER-SPECIFIED: {style_upper}) ===\n"
            f"{output_prompt}\n"
            f"Follow this format strictly throughout the document."
        )

    complexity_prompt = COMPLEXITY_LEVEL_PROMPTS.get(
        (preferences.complexity or "intermediate").lower()
    )
    if complexity_prompt:
        complexity_upper = (preferences.complexity or "intermediate").upper()
        instructions.append(
            f"\n=== COMPLEXITY LEVEL (USER-SPECIFIED: {complexity_upper}) ===\n"
            f"{complexity_prompt}"
        )

    target_length = _clamp_target_length(preferences.target_length)
    if target_length:
        min_words = target_length - 10  # Strict tolerance: ±10 words
        max_words = target_length + 10

        # Determine if health rating is included (based on preferences)
        include_health = bool(
            preferences and _resolve_health_rating_config(preferences)
        )

        # Add section word budgets for proportional distribution
        section_budgets = _format_section_word_budgets(target_length, include_health)

        instructions.append(
            f"""
=== LENGTH GUIDANCE (STRICT - WITHIN 10 WORDS OF TARGET) ===
TARGET: Exactly {target_length} words (strict range: {min_words}-{max_words} words)

CRITICAL PRIORITY ORDER:
1. SENTENCE COMPLETION - HIGHEST PRIORITY (NEVER cut off mid-sentence)
2. Section completeness - All sections must be finished
3. Word count target - You MUST hit {target_length} ±10 words

ABSOLUTE RULE - NEVER CUT OFF MID-SENTENCE:
- It is ALWAYS better to write 10 extra words than to cut off a sentence
- It is ALWAYS better to write 10 fewer words than to leave thoughts incomplete
- EVERY sentence MUST end with proper punctuation (period, question mark, exclamation point)
- If you're approaching the word limit, FINISH YOUR CURRENT THOUGHT before stopping

FORBIDDEN (will invalidate your output):
- "...and the..." (incomplete)
- "...which is..." (incomplete)
- "...because I..." (incomplete)  
- "$1." or "$3." (cut-off numbers)
- Any sentence ending with an article, preposition, or conjunction

If you must choose between hitting {target_length} words exactly OR completing all sentences:
ALWAYS CHOOSE COMPLETING SENTENCES. Word count is a guide, not a hard limit.

CRITICAL: You MUST hit the target word count ({min_words}-{max_words} words).
- If you are running SHORT: Add more analysis, specific data points, and elaboration to EACH section.
- Do NOT rely on filler phrases. Add substantive content with real insights.
- Count your words before submitting. If under {min_words}, go back and expand your analysis.
=== END LENGTH GUIDANCE ===

{section_budgets}
"""
        )
        if target_length > 450:
            instructions.append(
                "- To meet this high word count, you MUST provide extensive detail, historical context, and deep analysis in every section. "
                "Do NOT be concise. Elaborate on every point. But NEVER sacrifice sentence completion for length."
            )

    instructions.append(
        """
CRITICAL COMPLETENESS INSTRUCTION:
- You MUST complete all sections. Do not stop mid-sentence.
- Allocate your word count wisely. Do not spend too many words on early sections if it means cutting off the end.
- The 'Key Metrics' section MUST be included before the Closing Takeaway.
"""
    )

    instructions.append(
        """
=== FLOW AND QUALITY REQUIREMENTS ===
- Ensure each section transitions naturally to the next. The summary should read as a cohesive document.
- Avoid one-sentence paragraphs in narrative sections (Health/Exec/Financial Performance/MD&A/Closing). Use 2-4 sentence paragraphs for flow.
- NEVER repeat the same sentence or phrase anywhere in the summary. Each point should be made ONCE.
- Vary your sentence structure and openings to maintain reader engagement.
- The Financial Health Rating section should flow naturally into the Executive Summary.
- Avoid redundancy: if you mention a metric in one section, do not repeat the same observation elsewhere.
"""
    )

    # Add mandatory closing verdict for persona-based analyses
    if investor_focus:
        instructions.append(
            """
=== CLOSING TAKEAWAY - PERSONA VOICE REQUIREMENT (CRITICAL) ===

After the 'Key Metrics' section, you MUST include a '## Closing Takeaway' section.

THIS SECTION MUST:
1. Be written ENTIRELY in the first-person voice of your selected persona
2. Use the persona's SIGNATURE PHRASES and MENTAL MODELS
3. Apply their specific DECISION FRAMEWORK to reach a verdict
4. Sound like the ACTUAL INVESTOR wrote it - not a generic analyst

REQUIRED CONTENT (5-7 sentences):
1. QUALITY VERDICT: Is this a wonderful/fair/poor business? (Use persona's language)
2. INVESTMENT STANCE: BUY / HOLD / SELL / WAIT - stated clearly
3. KEY REASONING: The #1 factor driving your decision (in persona's framework)
4. SUPPORTING FACTORS: Secondary considerations that reinforce your stance
5. ACTIONABLE CONDITION: What would change your mind (price target, metric threshold, or catalyst)
6. PERSONAL CLOSING (MANDATORY): End with a first-person BUY/HOLD/SELL recommendation (wording should vary; avoid repeating fixed phrases like "I personally would") - this should feel like genuine advice from the persona to a friend

PERSONA-SPECIFIC VOICE EXAMPLES:

WARREN BUFFETT must use: "wonderful business", "moat", "owner earnings", "circle of competence", 
"Mr. Market", "hold for decades", "fair price for a wonderful business"

CHARLIE MUNGER must use: "invert", "incentives", "obviously stupid", "mental models", 
"nothing to add", "avoid stupidity"

BENJAMIN GRAHAM must use: "margin of safety", "intrinsic value", "Mr. Market", 
"intelligent investor", "speculation vs investment"

PETER LYNCH must use: "the story", "PEG ratio", "stalwart/fast grower/turnaround", 
"tenbagger potential", "invest in what you know"

RAY DALIO must use: "cycle position", "risk parity", "debt cycle", "paradigm shift", 
"correlation", "all-weather"

CATHIE WOOD must use: "disruptive innovation", "Wright's Law", "S-curve", "exponential growth", 
"2030 vision", "convergence"

JOEL GREENBLATT must use: "return on capital", "earnings yield", "magic formula", "good company cheap"

JOHN BOGLE must use: "stay the course", "costs matter", "the haystack vs the needle", 
"index fund", "90% of active managers fail"

HOWARD MARKS must use: "second-level thinking", "pendulum", "cycle", "risk-reward asymmetry", 
"what's priced in", "consensus vs reality"

BILL ACKMAN must use: "simple, predictable, free-cash-flow generative", "the catalyst", 
"the fix", "management MUST", "high conviction"

DO NOT write a generic conclusion. Sound EXACTLY like the persona.
DO NOT end with incomplete sentences. Every thought must be finished.
This is the MOST IMPORTANT section - it's what the reader remembers.
IMPORTANT: This section counts toward your total word count. Stay within the user's requested length.
=== END CLOSING REQUIREMENT ===
"""
        )

    # Add compliance summary
    instructions.append(
        """
=== COMPLIANCE CHECKLIST (VERIFY BEFORE SUBMITTING) ===
Before you finish, verify you have followed ALL user requirements:
[ ] Persona/viewpoint maintained throughout EVERY section
[ ] All specified focus areas covered with dedicated content
[ ] Tone matches user specification consistently
[ ] Detail level matches user specification
[ ] Output style matches user specification  
[ ] Word count within specified range
[ ] Closing Takeaway in persona voice (if persona specified)

If ANY checkbox would be unchecked, REVISE your output before submitting.
=== END USER CUSTOMIZATION REQUIREMENTS ===
"""
    )

    return "\n".join(instructions)


def _health_pref_to_dict(pref: Optional[Any]) -> Dict[str, Any]:
    if pref is None:
        return {}
    if hasattr(pref, "model_dump"):
        try:
            return pref.model_dump(exclude_none=True)
        except TypeError:
            return pref.model_dump()
    if isinstance(pref, dict):
        return {key: value for key, value in pref.items() if value is not None}
    return {}


def _resolve_health_rating_config(
    preferences: Optional[FilingSummaryPreferences],
) -> Optional[Dict[str, Any]]:
    pref_data = _health_pref_to_dict(getattr(preferences, "health_rating", None))

    if not pref_data or not pref_data.get("enabled"):
        return None

    config = dict(DEFAULT_HEALTH_RATING_CONFIG)
    for key in (
        "framework",
        "primary_factor_weighting",
        "risk_tolerance",
        "analysis_depth",
        "display_style",
    ):
        value = pref_data.get(key)
        if value:
            config[key] = value
    return config


def _build_health_rating_instructions(
    preferences: Optional[FilingSummaryPreferences],
    company_name: str,
    persona_id: Optional[str] = None,
    *,
    target_length: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    config = _resolve_health_rating_config(preferences)
    if not config:
        return None, None

    display_style = config.get("display_style", "score_plus_grade")
    is_custom_mode = preferences and preferences.mode == "custom"

    # If the user requested a total length, make the health-rating directives
    # budget-aware so they do not fight the fixed distribution requirements.
    health_budget_min: Optional[int] = None
    health_budget_max: Optional[int] = None
    if target_length and int(target_length) > 0:
        try:
            budgets = _calculate_section_word_budgets(
                int(target_length), include_health_rating=True
            )
            budget = int(budgets.get("Financial Health Rating", 0) or 0)
            if budget > 0:
                tol = _section_budget_tolerance_words(budget, max_tolerance=10)
                health_budget_min = max(1, budget - tol)
                health_budget_max = budget + tol
        except Exception:
            health_budget_min = None
            health_budget_max = None

    # Build header based on whether user has customized
    if is_custom_mode:
        directives = [
            "=== FINANCIAL HEALTH RATING (USER-CONFIGURED - MANDATORY) ===",
            "The user has specified CUSTOM health score settings. You MUST follow these EXACTLY.",
            "",
            f"Generate a Financial Health Rating for {company_name} on a 0–100 scale (100 = exceptional strength).",
            "",
        ]
    else:
        directives = [
            "=== FINANCIAL HEALTH RATING ===",
            f"Generate a Financial Health Rating for {company_name} on a 0–100 scale (100 = exceptional strength).",
            "",
        ]

    # Only include detailed pillar breakdown instructions for score_plus_pillars display style
    if display_style == "score_plus_pillars":
        directives.extend(
            [
                "USER SELECTED: Score + 4 Pillars breakdown",
                "Deliver a four-pillar breakdown (Profitability | Risk | Liquidity | Growth).",
                "",
                "CRITICAL: Do NOT show any equations, formulas, or point-by-point math. The overall score is pre-computed by the backend.",
                "For each pillar, cite the relevant metric(s) and give a short qualitative read (e.g., 'Profitability: strong given 18% operating margin').",
                "If a pillar lacks data, omit it rather than writing 'N/A' or 'not calculable'.",
            ]
        )
    elif display_style == "score_only":
        directives.extend(
            [
                "USER SELECTED: 0-100 Score Only",
                "",
                "CRITICAL - SCORE ONLY FORMAT:",
                "- Present ONLY a single overall score (e.g., '78/100').",
                "- Add a brief 1-2 sentence explanation of what drove the score.",
                "- DO NOT show letter grades, traffic lights, or pillar breakdowns.",
                "- DO NOT use the format 'Category: X/Y'.",
                "",
                f"CORRECT FORMAT: '{company_name} receives a Financial Health Rating of 78/100. Strong profitability and cash generation are offset by elevated leverage.'",
            ]
        )
    elif display_style == "score_plus_grade":
        directives.extend(
            [
                "USER SELECTED: Score + Rating Label",
                "",
                "MANDATORY FORMAT (YOU MUST INCLUDE ALL ELEMENTS):",
                "1. The score (0-100) with rating label (Very Healthy/Healthy/Watch/At Risk)",
                "2. NO letter grades or abbreviations in parentheses",
                "3. A MANDATORY explanation of 3-5 sentences (length must fit the memo's Financial Health Rating word budget if provided)",
                "",
                "YOUR EXPLANATION MUST COVER:",
                "- What drove the score (specific metrics with values)",
                "- Key strength(s) identified",
                "- Key concern(s) or risk(s)",
                "- How the user's selected framework influenced the assessment",
                "",
                "FLOW (CRITICAL): Write this as a cohesive mini-paragraph that naturally sets up the Executive Summary. Avoid abrupt, one-line conclusions.",
                "",
                "DO NOT just write the score and stop. The explanation is REQUIRED.",
                "",
                f"CORRECT FORMAT EXAMPLE:",
                f"'{company_name} receives a Financial Health Rating of 78/100 - Healthy. The score reflects strong profitability ",
                f"with a 56% net margin and robust free cash flow generation of $22B. The balance sheet is conservatively managed ",
	                f"with minimal debt relative to cash holdings. However, the score stays conservative because customer concentration and cyclical demand patterns could ",
                f"pressure durability and justify some caution. The score would be higher if those risks were lower and cash conversion remained consistently strong.'",
                "",
                "FORBIDDEN: Just writing '{company_name} receives a Financial Health Rating of 78/100 - Healthy.' and stopping.",
            ]
        )
    elif display_style == "score_plus_traffic_light":
        directives.extend(
            [
                "USER SELECTED: Score + Traffic Light",
                "",
                "MANDATORY FORMAT (YOU MUST INCLUDE ALL ELEMENTS):",
                "1. The score (0-100) with traffic light: 70-100=GREEN, 50-69=YELLOW, 0-49=RED",
                "2. A MANDATORY explanation of 3-4 sentences (length must fit the memo's Financial Health Rating word budget if provided)",
                "",
                "YOUR EXPLANATION MUST COVER:",
                "- What drove the score (specific metrics with values)",
                "- Why this traffic light color is appropriate",
                "- Key factors supporting or concerning the assessment",
                "",
                f"CORRECT FORMAT EXAMPLE:",
                f"'{company_name} receives a Financial Health Rating of 78/100 - GREEN LIGHT. This green light reflects exceptional ",
                f"profitability with 63% operating margins and strong cash generation. The company maintains a fortress balance sheet ",
                f"with $11B cash against minimal debt. While cyclical risks exist in the semiconductor industry, the current financial ",
                f"position supports a constructive investment stance for long-term investors.'",
            ]
        )
    elif display_style == "score_with_narrative":
        directives.extend(
            [
                "USER SELECTED: Score + Narrative",
                "",
                "MANDATORY FORMAT - EXTENDED NARRATIVE REQUIRED:",
                "1. The score (0-100) with rating label",
                "2. A DETAILED narrative paragraph of 4-7 sentences (length must fit the memo's Financial Health Rating word budget if provided)",
                "",
                "YOUR NARRATIVE MUST ANALYZE:",
                "- Profitability metrics (margins, ROE, ROA) with specific values",
                "- Cash flow quality (FCF, FCF conversion, cash generation)",
                "- Balance sheet strength (leverage, liquidity, debt levels)",
                "- Growth trajectory and sustainability",
                "- Key risks that impact the score",
                "- How the user's framework influenced the assessment",
                "",
                f"The narrative should read like a mini-analysis, not a single sentence.",
            ]
        )
    else:
        # Fallback for any other display style
        directives.extend(
            [
                "MANDATORY FORMAT - EXPLANATION REQUIRED:",
                "1. The score (0-100) with rating label",
                "2. A MANDATORY explanation of 3-4 sentences (40-75 words)",
                "",
                "YOUR EXPLANATION MUST COVER:",
                "- What metrics drove the score",
                "- Key strengths identified",
                "- Key concerns or risks",
                "",
                f"CORRECT FORMAT: '{company_name} receives a Financial Health Rating of 78/100 - Healthy. The score reflects [specific metrics]. Key strengths include [details]. However, [concerns]. Overall, [assessment].'",
                "",
                "FORBIDDEN: Just writing the score without explanation.",
            ]
        )

    # Common directives for all display styles
    directives.extend(
        [
            "",
            "RATING LABELS (MANDATORY - include with score):",
            "- 85-100 = Very Healthy",
            "- 70-84 = Healthy",
            "- 50-69 = Watch",
            "- 0-49 = At Risk",
            "",
            "=== LENGTH + CONTENT REQUIREMENT (CRITICAL) ===",
            (
                f"The Financial Health Rating section body MUST be between {health_budget_min} and {health_budget_max} words. "
                "If any other instruction conflicts with this word budget, the word budget WINS."
            )
            if (health_budget_min is not None and health_budget_max is not None)
            else "The Financial Health Rating section MUST be at least 40 words.",
            "A single line like 'Company receives 78/100 - Healthy.' is INVALID.",
            "You MUST explain WHY the company received this score with specific metrics.",
            "",
            "REQUIRED ELEMENTS IN YOUR EXPLANATION:",
            "1. At least ONE specific profitability metric (e.g., 'net margin of 56%')",
            "2. At least ONE cash flow metric (e.g., 'FCF of $22B')",
            "3. At least ONE balance sheet observation (e.g., 'conservative debt levels')",
            "4. At least ONE risk or concern that impacts the score",
            "",
            (
                f"VERIFICATION: Your Financial Health Rating body word count is {health_budget_min}–{health_budget_max}. "
                "If outside that band, rewrite this section (do not steal words from other sections)."
            )
            if (health_budget_min is not None and health_budget_max is not None)
            else "If you write fewer than 40 words for this section, your output is INVALID.",
            "=== END LENGTH + CONTENT REQUIREMENT ===",
        ]
    )

    # Custom framework/weighting instructions for CUSTOM mode
    if is_custom_mode:
        framework = config.get("framework")
        weighting = config.get("primary_factor_weighting")
        risk = config.get("risk_tolerance")
        depth = config.get("analysis_depth")

        directives.append("")
        directives.append(
            "=== USER-SPECIFIED HEALTH SCORE PARAMETERS (MUST FOLLOW) ==="
        )

        framework_prompt = HEALTH_FRAMEWORK_PROMPTS.get(framework)
        if framework_prompt:
            directives.append(f"")
            directives.append(f"FRAMEWORK (User selected: {framework}):")
            directives.append(f"  {framework_prompt}")
            directives.append(
                f"  You MUST evaluate the company through this specific lens."
            )

        weighting_prompt = HEALTH_WEIGHTING_PROMPTS.get(weighting)
        if weighting_prompt:
            directives.append(f"")
            directives.append(f"PRIMARY FACTOR WEIGHTING (User selected: {weighting}):")
            directives.append(f"  {weighting_prompt}")
            directives.append(
                f"  This factor should have the MOST influence on the final score."
            )

        risk_prompt = HEALTH_RISK_PROMPTS.get(risk)
        if risk_prompt:
            directives.append(f"")
            directives.append(f"RISK TOLERANCE (User selected: {risk}):")
            directives.append(f"  {risk_prompt}")
            directives.append(
                f"  Apply this risk tolerance when penalizing or rewarding factors."
            )

        depth_prompt = HEALTH_ANALYSIS_DEPTH_PROMPTS.get(depth)
        if depth_prompt:
            directives.append(f"")
            directives.append(f"ANALYSIS DEPTH (User selected: {depth}):")
            directives.append(f"  {depth_prompt}")
            directives.append(f"  Your analysis must reach this level of depth.")

        directives.append("")
        directives.append(
            "COMPLIANCE CHECK: The health score MUST reflect ALL user-specified parameters above."
        )
        directives.append(
            "If the score doesn't align with user's framework, weighting, and risk tolerance, REVISE IT."
        )

        # Add explicit narrative personalization requirements
        framework_display = (framework or "value_investor").replace("_", " ").title()
        weighting_display = (weighting or "profitability").replace("_", " ")
        risk_display = (risk or "moderate").replace("_", " ")

        directives.append("")
        directives.append("=== NARRATIVE PERSONALIZATION (MANDATORY) ===")
        directives.append(
            f"Your explanation MUST explicitly reference the user's selections in the text:"
        )
        directives.append(
            f"- Start with a phrase like: 'Applying the {framework_display} framework...' or 'From a {framework_display} perspective...'"
        )
        directives.append(
            f"- Reference the weighting: 'With {weighting_display} as the primary driver...' or 'Prioritizing {weighting_display}...'"
        )
        directives.append(
            f"- The narrative should feel customized to the user's settings, NOT generic."
        )
        directives.append("")
        directives.append("EXAMPLE OF GOOD PERSONALIZED NARRATIVE:")
        directives.append(
            f"'{company_name} receives a Financial Health Rating of 72/100 - Watch. Applying the {framework_display} framework with {weighting_display} as the primary driver, the company's strong 45% gross margins provide a solid foundation. However, taking a {risk_display} approach to risk, the elevated debt-to-equity ratio of 1.8 and declining free cash flow warrant caution. The score would be higher but for these balance sheet concerns that a prudent investor must monitor.'"
        )
        directives.append("")
        directives.append("BAD (too generic - DO NOT DO THIS):")
        directives.append(
            f"'{company_name} receives a Financial Health Rating of 72/100 - Watch. Strong margins offset by leverage concerns.'"
        )

    # Add persona-specific health rating instructions if persona is selected
    if persona_id:
        # Persona intro phrases for health rating
        persona_health_intros = {
            "buffett": (
                "Warren Buffett",
                "Through my moat-focused lens",
                ["owner earnings", "durable economics", "moat", "compounding"],
            ),
            "munger": (
                "Charlie Munger",
                "Inverting the usual analysis",
                ["invert", "incentives", "rational", "avoid stupidity"],
            ),
            "graham": (
                "Benjamin Graham",
                "Applying margin-of-safety analysis",
                [
                    "margin of safety",
                    "intrinsic value",
                    "balance sheet",
                    "quantitative",
                ],
            ),
            "lynch": (
                "Peter Lynch",
                "Looking at the story behind the numbers",
                ["the story", "PEG ratio", "growth", "tenbagger"],
            ),
            "dalio": (
                "Ray Dalio",
                "Analyzing cycle position and fundamentals",
                ["cycle", "debt levels", "economic machine", "paradigm"],
            ),
            "wood": (
                "Cathie Wood",
                "Through a disruptive innovation lens",
                ["disruption", "innovation", "exponential growth", "S-curve"],
            ),
            "greenblatt": (
                "Joel Greenblatt",
                "Applying Magic Formula criteria",
                ["return on capital", "earnings yield", "good company cheap"],
            ),
            "bogle": (
                "John Bogle",
                "From an index-investor perspective",
                ["costs matter", "diversification", "long-term", "simplicity"],
            ),
            "marks": (
                "Howard Marks",
                "Using second-level thinking",
                ["second-level thinking", "cycles", "risk-reward", "asymmetry"],
            ),
            "ackman": (
                "Bill Ackman",
                "From an activist value standpoint",
                ["catalysts", "free cash flow", "value creation", "capital allocation"],
            ),
        }

        persona_info = persona_health_intros.get(persona_id.lower())
        if persona_info:
            persona_name, persona_intro, vocabulary = persona_info
            vocab_str = ", ".join(f'"{w}"' for w in vocabulary[:3])

            directives.append("")
            directives.append(f"=== PERSONA VOICE: {persona_name} (MANDATORY) ===")
            directives.append(
                f"The user has selected {persona_name} as their investment persona."
            )
            directives.append(
                f"Write the health rating narrative in FIRST-PERSON as {persona_name}."
            )
            directives.append("")
            directives.append(
                f"OPENING: Start with '{persona_intro}...' or a similar persona-authentic phrase."
            )
            directives.append(
                f"VOCABULARY: Use characteristic terms like {vocab_str} where appropriate."
            )
            directives.append(
                f"TONE: The assessment should sound like personal advice from {persona_name} to a trusted colleague."
            )
            directives.append("")
            directives.append(f"EXAMPLE ({persona_name} voice):")
            directives.append(
                f"'{company_name} receives a Financial Health Rating of 72/100 - Watch. {persona_intro}, the company's fundamentals present a mixed picture. The strong gross margins suggest pricing power, but I'm concerned about the elevated debt levels. For my own portfolio, I'd want to see improvement in cash conversion before committing significant capital.'"
            )

    directives.append("")
    directives.append(
        "PLACEMENT: The Financial Health Rating section MUST appear FIRST, before the Executive Summary."
    )

    return config, "\n".join(directives)


def _sample_entries_for_ticker(ticker: str) -> List[Dict[str, Any]]:
    """Return sample filing entries for tickers when live data is unavailable."""
    samples = sample_filings_by_ticker.get((ticker or "").upper(), [])
    formatted_entries: List[Dict[str, Any]] = []
    for sample in samples:
        formatted_entries.append(
            {
                "filing_type": sample.get("filing_type", "10-Q"),
                "date_str": sample.get("filing_date"),
                "income_statement": sample.get("income_statement", {}),
                "balance_sheet": sample.get("balance_sheet", {}),
                "cash_flow": sample.get("cash_flow", {}),
                "url": sample.get("url", "https://www.sec.gov"),
            }
        )
    return formatted_entries


def _build_document_path(filing_id: str, settings) -> str:
    return f"/api/{settings.api_version}/filings/{filing_id}/document"


def _strip_html_to_text(raw_html: str) -> str:
    """Convert HTML document into plain text for AI consumption."""
    # Remove script and style blocks
    cleaned = re.sub(
        r"<(script|style)[^>]*>.*?</\\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Remove HTML tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Unescape HTML entities
    cleaned = unescape(cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\\s+", " ", cleaned)
    return cleaned.strip()


def _extract_section(text: str, start_pattern: str, end_patterns: List[str]) -> str:
    """Extract a section from text bounded by start regex and optional end regexes."""
    start_regex = re.compile(start_pattern, re.IGNORECASE)
    match = start_regex.search(text)
    if not match:
        return ""

    start_idx = match.start()
    content_start_idx = match.end()
    end_idx = len(text)

    for end_pattern in end_patterns:
        end_regex = re.compile(end_pattern, re.IGNORECASE)
        end_match = end_regex.search(text, content_start_idx)
        if end_match and end_match.start() < end_idx:
            end_idx = end_match.start()

    section = text[start_idx:end_idx].strip()
    return section


def _load_document_excerpt(path: Path, limit: Optional[int] = None) -> str:
    """Load filing document and extract the most relevant textual sections."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_text(errors="ignore")

    if path.suffix.lower() in {".htm", ".html"}:
        text = _strip_html_to_text(raw)
    else:
        text = raw

    # Extract key sections commonly used by investors
    sections: List[str] = []
    # Regex patterns for flexibility
    # Note: SEC filings often have varied formatting. Patterns must be flexible.
    extraction_rules = [
        (r"ITEM\s+1\.?\s+BUSINESS", [r"ITEM\s+1A\.?"], "BUSINESS OVERVIEW"),
        (
            r"ITEM\s+1A\.?\s+RISK\s+FACTORS",
            [r"ITEM\s+1B\.?", r"ITEM\s+2\.?"],
            "RISK FACTORS",
        ),
        # MD&A patterns - multiple variations to catch different filing formats
        # 10-Q Item 2
        (
            r"ITEM\s+2[\.\s:]+(?:MANAGEMENT['']?S?\s+DISCUSSION|MD&A)",
            [r"ITEM\s+3\.?", r"ITEM\s+4\.?"],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        # 10-K Item 7
        (
            r"ITEM\s+7[\.\s:]+(?:MANAGEMENT['']?S?\s+DISCUSSION|MD&A)",
            [r"ITEM\s+7A\.?", r"ITEM\s+8\.?"],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        # Standalone MD&A header (no Item number)
        (
            r"MANAGEMENT[''\u2019]?S?\s+DISCUSSION\s+AND\s+ANALYSIS\s+OF\s+FINANCIAL\s+CONDITION",
            [
                r"ITEM\s+7A\.?",
                r"ITEM\s+8\.?",
                r"QUANTITATIVE\s+AND\s+QUALITATIVE",
                r"ITEM\s+3\.?",
            ],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        # Alternative: Just "MANAGEMENT DISCUSSION" without possessive
        (
            r"MANAGEMENT\s+DISCUSSION\s+AND\s+ANALYSIS",
            [r"ITEM\s+7A\.?", r"ITEM\s+8\.?", r"QUANTITATIVE", r"ITEM\s+3\.?"],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        # NVIDIA-specific patterns (often uses dashes)
        (
            r"MANAGEMENT[''\u2019]?S?\s+DISCUSSION\s+AND\s+ANALYSIS\s+[-–—]",
            [r"ITEM\s+7A\.?", r"ITEM\s+8\.?", r"QUANTITATIVE", r"ITEM\s+3\.?"],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        # Results of Operations (often part of MD&A)
        (
            r"RESULTS\s+OF\s+OPERATIONS",
            [
                r"LIQUIDITY\s+AND\s+CAPITAL",
                r"ITEM\s+3\.?",
                r"ITEM\s+7A\.?",
                r"ITEM\s+8\.?",
            ],
            "MANAGEMENT DISCUSSION & ANALYSIS",
        ),
        (r"ITEM\s+7A\.?\s+QUANTITATIVE", [r"ITEM\s+8\.?"], "MARKET RISK"),
        (
            r"ITEM\s+8\.?\s+FINANCIAL\s+STATEMENTS",
            [r"ITEM\s+9\.?"],
            "FINANCIAL STATEMENTS",
        ),
    ]

    for start_pat, end_pats, header in extraction_rules:
        section = _extract_section(text, start_pat, end_pats)
        if section:
            # Avoid duplicate MD&A if multiple patterns match
            if header == "MANAGEMENT DISCUSSION & ANALYSIS" and any(
                s.startswith("MANAGEMENT DISCUSSION & ANALYSIS") for s in sections
            ):
                continue
            # Log success for debugging
            if header == "MANAGEMENT DISCUSSION & ANALYSIS":
                print(
                    f"✅ MD&A extracted using pattern: {start_pat[:50]}... ({len(section)} chars)"
                )
            sections.append(f"{header}\n{section}")

    # Fallback: if no sections found, return a generous chunk of the start
    if not sections:
        return text[:100000]

    # CRITICAL FALLBACK: If MD&A is missing but other sections were found,
    # append a large chunk of text to ensure the AI has context.
    has_mda = any(s.startswith("MANAGEMENT DISCUSSION & ANALYSIS") for s in sections)
    if not has_mda:
        print("⚠️ MD&A not found in extracted sections. Appending raw text fallback.")
        sections.append(
            f"FULL TEXT CONTEXT (MD&A MISSING FROM EXTRACTION)\n{text[:150000]}"
        )

    return "\n\n".join(sections)


def _extract_labeled_excerpt(
    document_text: str, label: str, *, max_chars: int = 15_000
) -> Optional[str]:
    """Extract a labeled section from `_load_document_excerpt()` output.

    The excerpt loader joins blocks like:
      "RISK FACTORS\\n...\\n\\nMANAGEMENT DISCUSSION & ANALYSIS\\n..."

    This helper pulls the requested block and truncates it to `max_chars`.
    """
    if not document_text or not label:
        return None

    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(label)}\s*\n([\s\S]*?)(?=\n\n[A-Z][A-Z &]+\n|\Z)"
    )
    match = pattern.search(document_text)
    if not match:
        return None
    excerpt = (match.group(1) or "").strip()
    if not excerpt:
        return None
    if max_chars and max_chars > 0 and len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip()
    return excerpt


def _extract_latest_numeric(line_item: Any) -> Optional[float]:
    """Return the most recent numeric value from a line item dictionary."""
    if isinstance(line_item, (int, float)):
        return float(line_item)
    if isinstance(line_item, str):
        try:
            return float(line_item.replace(",", ""))
        except ValueError:
            return None
    if isinstance(line_item, list):
        for value in line_item:
            result = _extract_latest_numeric(value)
            if result is not None:
                return result
        return None
    if not isinstance(line_item, dict):
        return None
    try:
        sorted_entries = sorted(
            line_item.items(), key=lambda itm: str(itm[0]), reverse=True
        )
    except Exception:
        sorted_entries = line_item.items()
    for _, value in sorted_entries:
        nested = _extract_latest_numeric(value)
        if nested is not None:
            return nested
    return None


def _format_dollar(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def _build_financial_snapshot(statements: Optional[Dict[str, Any]]) -> str:
    """Create a concise financial snapshot from cached statements."""
    if not statements or not isinstance(statements, dict):
        return ""

    data = statements.get("statements") or {}

    income_statement = data.get("income_statement", {})
    balance_sheet = data.get("balance_sheet", {})
    cash_flow = data.get("cash_flow", {})

    revenue = _extract_latest_numeric(
        income_statement.get("totalRevenue") or income_statement.get("Revenue")
    )
    operating_income = _extract_latest_numeric(
        income_statement.get("OperatingIncomeLoss")
        or income_statement.get("OperatingIncome")
    )
    net_income = _extract_latest_numeric(
        income_statement.get("NetIncomeLoss") or income_statement.get("NetIncome")
    )
    eps = _extract_latest_numeric(income_statement.get("DilutedEPS"))

    total_assets = _extract_latest_numeric(balance_sheet.get("TotalAssets"))
    total_liabilities = _extract_latest_numeric(balance_sheet.get("TotalLiabilities"))
    cash = _extract_latest_numeric(
        balance_sheet.get("CashAndCashEquivalentsAtCarryingValue")
        or balance_sheet.get("CashAndCashEquivalents")
    )

    operating_cash_flow = _extract_latest_numeric(
        cash_flow.get("NetCashProvidedByUsedInOperatingActivities")
    )
    capex = _extract_latest_numeric(
        cash_flow.get("PaymentsToAcquirePropertyPlantAndEquipment")
    )
    free_cash_flow = (
        operating_cash_flow - capex
        if operating_cash_flow is not None and capex is not None
        else None
    )

    snapshot_lines: List[str] = []
    for label, value in [
        ("Revenue", _format_dollar(revenue)),
        ("Operating Income", _format_dollar(operating_income)),
        ("Net Income", _format_dollar(net_income)),
        ("Diluted EPS", f"${eps:.2f}" if eps is not None else None),
        ("Operating Cash Flow", _format_dollar(operating_cash_flow)),
        ("Capital Expenditures", _format_dollar(capex)),
        ("Free Cash Flow", _format_dollar(free_cash_flow)),
        ("Total Assets", _format_dollar(total_assets)),
        ("Total Liabilities", _format_dollar(total_liabilities)),
        ("Cash & Equivalents", _format_dollar(cash)),
    ]:
        if value:
            snapshot_lines.append(f"- {label}: {value}")

    return "\n".join(snapshot_lines)


def _build_calculated_metrics(
    statements: Optional[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """Derive key metrics from financial statements for AI guidance."""
    if not statements or not isinstance(statements, dict):
        return {}

    data = statements.get("statements") or {}

    income_statement = data.get("income_statement", {})
    balance_sheet = data.get("balance_sheet", {})
    cash_flow = data.get("cash_flow", {})

    def _extract_from_candidates(
        source: Dict[str, Any], candidates: List[str]
    ) -> Optional[float]:
        for key in candidates:
            value = source.get(key)
            result = _extract_latest_numeric(value)
            if result is not None:
                return result
        return None

    revenue = _extract_from_candidates(
        income_statement,
        [
            "revenue",  # EODHD normalized
            "totalRevenue",
            "Revenue",
            "TotalRevenue",
            "revenues",
            "total_revenue",
            "revenuesUSD",
        ],
    )
    net_income = _extract_from_candidates(
        income_statement,
        [
            "net_income",
            "NetIncomeLoss",
            "NetIncome",
            "netIncome",
            "netIncomeLoss",
            "NetIncomeApplicableToCommonShares",
        ],
    )
    operating_income = _extract_from_candidates(
        income_statement,
        [
            "operating_income",
            "OperatingIncomeLoss",
            "OperatingIncome",
            "operatingIncome",
            "OperatingIncomeLossUSD",
        ],
    )
    eps = _extract_from_candidates(
        income_statement,
        ["DilutedEPS", "dilutedEPS", "EPSDiluted", "epsDiluted"],
    )

    operating_cash_flow = _extract_from_candidates(
        cash_flow,
        [
            "operating_cash_flow",  # EODHD normalized format
            "totalCashFromOperatingActivities",  # EODHD raw format
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByOperatingActivities",
            "netCashProvidedByOperatingActivities",
            "OperatingCashFlow",
            "operatingCashFlow",
        ],
    )
    capex_raw = _extract_from_candidates(
        cash_flow,
        [
            "capital_expenditures",  # EODHD normalized format
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CapitalExpenditures",
            "capitalExpenditures",
            "CapitalExpenditure",
            "PurchaseOfPPE",
        ],
    )
    capex = abs(capex_raw) if capex_raw is not None else None

    # Calculate FCF - use operating cash flow as fallback if capex is missing
    if operating_cash_flow is not None and capex is not None:
        free_cash_flow = operating_cash_flow - capex
    elif operating_cash_flow is not None:
        # If no capex data, use operating cash flow as FCF proxy
        # (common for service companies with minimal capex)
        free_cash_flow = operating_cash_flow
    else:
        free_cash_flow = None

    cash = _extract_from_candidates(
        balance_sheet,
        [
            "cash",  # EODHD normalized
            "CashAndCashEquivalentsAtCarryingValue",
            "CashAndCashEquivalents",
            "cashAndCashEquivalents",
            "CashCashEquivalentsAndShortTermInvestments",
        ],
    )
    marketable_securities = _extract_from_candidates(
        balance_sheet,
        ["MarketableSecurities", "ShortTermInvestments", "marketableSecurities"],
    )
    total_assets = _extract_from_candidates(
        balance_sheet,
        ["total_assets", "TotalAssets", "totalAssets", "TotalAssetsUSD"],
    )
    total_liabilities = _extract_from_candidates(
        balance_sheet,
        [
            "total_liabilities",
            "totalLiab",
            "TotalLiabilities",
            "totalLiabilities",
            "TotalLiabilitiesNetMinorityInterest",
        ],
    )

    current_assets = _extract_from_candidates(
        balance_sheet,
        [
            "current_assets",
            "CurrentAssets",
            "TotalCurrentAssets",
            "totalCurrentAssets",
            "AssetsCurrent",
        ],
    )
    current_liabilities = _extract_from_candidates(
        balance_sheet,
        [
            "current_liabilities",
            "CurrentLiabilities",
            "TotalCurrentLiabilities",
            "totalCurrentLiabilities",
            "LiabilitiesCurrent",
        ],
    )
    inventory = _extract_from_candidates(
        balance_sheet,
        ["inventories", "Inventory", "InventoryNet", "inventory", "Inventories"],
    )
    interest_expense = _extract_from_candidates(
        income_statement,
        [
            "interest_expense",
            "InterestExpense",
            "interestExpense",
            "InterestAndDebtExpense",
            "InterestIncomeExpense",
        ],
    )

    operating_margin = (
        (operating_income / revenue) * 100
        if operating_income is not None and revenue
        else None
    )
    net_margin = (
        (net_income / revenue) * 100 if net_income is not None and revenue else None
    )

    dividends_paid = _extract_from_candidates(
        cash_flow,
        [
            "PaymentsOfDividends",
            "DividendsPaid",
            "dividendsPaid",
            "CashDividendsPaid",
        ],
    )
    share_repurchases = _extract_from_candidates(
        cash_flow,
        [
            "PaymentsForRepurchaseOfCommonStock",
            "RepurchaseOfCapitalStock",
            "purchaseOfStock",
            "CommonStockRepurchased",
        ],
    )

    metrics = {
        "revenue": revenue,
        "operating_income": operating_income,
        "net_income": net_income,
        "diluted_eps": eps,
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditures": capex,
        "free_cash_flow": free_cash_flow,
        "cash": cash,
        "marketable_securities": marketable_securities,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "inventory": inventory,
        "interest_expense": interest_expense,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "dividends_paid": dividends_paid,
        "share_repurchases": share_repurchases,
    }

    return {key: value for key, value in metrics.items() if value is not None}


def _compute_health_score_data(
    calculated_metrics: Dict[str, Any],
    weighting_preset: Optional[str] = None,
    ai_growth_assessment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute health score data with component breakdown from calculated metrics.

    Args:
        calculated_metrics: Dictionary of calculated financial metrics
        weighting_preset: Optional user-selected weighting preset (e.g., 'cash_flow_conversion')
        ai_growth_assessment: Optional AI-generated growth assessment dict with 'score' and 'description'
    """
    if not calculated_metrics:
        return {}

    revenue = calculated_metrics.get("revenue")
    operating_income = calculated_metrics.get("operating_income")
    net_income = calculated_metrics.get("net_income")
    operating_cash_flow = calculated_metrics.get("operating_cash_flow")
    free_cash_flow = calculated_metrics.get("free_cash_flow")
    total_assets = calculated_metrics.get("total_assets")
    total_liabilities = calculated_metrics.get("total_liabilities")
    current_assets = calculated_metrics.get("current_assets")
    current_liabilities = calculated_metrics.get("current_liabilities")
    inventory = calculated_metrics.get("inventory")
    interest_expense = calculated_metrics.get("interest_expense")

    total_equity = (
        (total_assets - total_liabilities)
        if total_assets and total_liabilities
        else None
    )

    ratios = {}

    if calculated_metrics.get("operating_margin") is not None:
        ratios["operating_margin"] = calculated_metrics["operating_margin"] / 100
    if calculated_metrics.get("net_margin") is not None:
        ratios["net_margin"] = calculated_metrics["net_margin"] / 100
    if revenue and operating_income:
        ratios["gross_margin"] = operating_income / revenue
    if net_income and total_assets:
        ratios["roa"] = net_income / total_assets
    if net_income and total_equity and total_equity > 0:
        ratios["roe"] = net_income / total_equity
    if total_liabilities and total_equity and total_equity > 0:
        ratios["debt_to_equity"] = total_liabilities / total_equity
    if free_cash_flow is not None:  # Allow negative FCF
        ratios["fcf"] = free_cash_flow
    if free_cash_flow is not None and revenue and revenue > 0:
        ratios["fcf_margin"] = free_cash_flow / revenue

    # Add net_income and operating_cash_flow for governance/cash flow calculations
    if net_income is not None:
        ratios["net_income"] = net_income
    if operating_cash_flow is not None:
        ratios["operating_cash_flow"] = operating_cash_flow

    # Liquidity ratios
    if current_assets and current_liabilities and current_liabilities > 0:
        ratios["current_ratio"] = current_assets / current_liabilities
        # Quick ratio excludes inventory
        quick_assets = current_assets - (inventory or 0)
        if quick_assets > 0:
            ratios["quick_ratio"] = quick_assets / current_liabilities

    # Interest coverage ratio
    if operating_income and interest_expense and interest_expense != 0:
        # Interest expense is typically negative, so we use absolute value
        ratios["interest_coverage"] = operating_income / abs(interest_expense)

    try:
        # Debug: log what ratios we're passing to health scorer
        logger.info(
            f"Health score ratios being passed: fcf={ratios.get('fcf')}, net_income={ratios.get('net_income')}, operating_cash_flow={ratios.get('operating_cash_flow')}, operating_margin={ratios.get('operating_margin')}, debt_to_equity={ratios.get('debt_to_equity')}, weighting_preset={weighting_preset}"
        )
        health_data = calculate_health_score(
            ratios,
            weighting_preset=weighting_preset,
            ai_growth_assessment=ai_growth_assessment,
        )
        logger.info(
            f"Health score component scores: {health_data.get('component_scores', {})}"
        )
        return health_data
    except Exception as e:
        logger.warning(f"Health score calculation failed: {e}")
        return {}


def _format_metric_value(key: str, value: float) -> str:
    """Format a metric value for display in the Key Metrics block.

    NOTE: We must NOT hallucinate values. This function only formats numbers already
    present in calculated_metrics or deterministically derived from them.
    """

    if value is None:
        return ""

    pct_keys = {
        "operating_margin",
        "net_margin",
        "gross_margin",
        "revenue_growth_yoy",
        "fcf_margin",
        "roe",
        "roic",
        "roa",
    }
    ratio_keys = {
        "current_ratio",
        "quick_ratio",
        "debt_to_equity",
        "interest_coverage",
        "leverage",
    }

    if key == "diluted_eps":
        return f"${float(value):.2f}"
    if key in pct_keys:
        return f"{float(value):.1f}%"
    if key in ratio_keys:
        return f"{float(value):.1f}x"

    formatted = _format_dollar(float(value))
    return formatted or f"{float(value):,.2f}"


def _build_key_metrics_block(
    calculated_metrics: Dict[str, Any],
    *,
    target_length: Optional[int] = None,
    include_health_rating: bool = True,
    health_score_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a compact, scannable Key Metrics data block.

    For long memos, Key Metrics is capped and extra length is shifted into the
    narrative sections to avoid low-quality repetition.
    """

    max_words = 0
    if target_length and target_length > 0:
        if int(target_length) >= int(KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS):
            max_words = int(KEY_METRICS_MAX_WORDS)
        else:
            budgets = _calculate_section_word_budgets(
                target_length, include_health_rating=include_health_rating
            )
            max_words = int(budgets.get("Key Metrics", 0) or 0)

    if max_words > 0:
        max_words = min(int(max_words), int(KEY_METRICS_MAX_WORDS))

    max_metric_lines = 12
    if target_length and target_length > 0:
        if int(target_length) < 500:
            max_metric_lines = 8
        elif int(target_length) < 900:
            max_metric_lines = 10

    def _get(key: str) -> Any:
        return calculated_metrics.get(key)

    def _push_line(line: str) -> None:
        line = (line or "").strip()
        if not line:
            return
        if line in lines:
            return
        if len([ln for ln in lines if ln.strip()]) >= int(max_metric_lines):
            return
        if max_words > 0:
            candidate = ("\n".join([*lines, line])).strip()
            if _count_words(candidate) > int(max_words):
                return
        lines.append(line)

    def _add_line(label: str, key: str, value: Any, *, fmt_key: Optional[str] = None) -> None:
        if value is None:
            return
        try:
            rendered = _format_metric_value((fmt_key or key).lower(), float(value))
        except Exception:
            return
        if not rendered:
            return
        _push_line(f"→ {label}: {rendered}")

    lines: List[str] = []

    # --- Core scale ---
    revenue = _get("revenue") or _get("total_revenue")
    _add_line("Revenue", "revenue", revenue)

    # --- Profitability (keep tight and thesis-relevant) ---
    _add_line(
        "Operating Margin",
        "operating_margin",
        _get("operating_margin"),
        fmt_key="operating_margin",
    )
    _add_line("Net Margin", "net_margin", _get("net_margin"), fmt_key="net_margin")

    operating_income = _get("operating_income")
    net_income = _get("net_income")

    # --- Cash flow / reinvestment ---
    fcf = _get("free_cash_flow")
    capex = _get("capital_expenditures")
    _add_line("Free Cash Flow", "free_cash_flow", fcf)
    _add_line("FCF Margin", "fcf_margin", _get("fcf_margin"), fmt_key="fcf_margin")
    if (fcf is not None) and (revenue is not None) and _get("fcf_margin") is None:
        try:
            derived_fcf_margin = (float(fcf) / float(revenue)) * 100
            _add_line("FCF Margin", "fcf_margin", derived_fcf_margin, fmt_key="fcf_margin")
        except Exception:
            pass
    if (capex is not None) and (revenue is not None) and float(revenue) != 0:
        try:
            capex_intensity = (abs(float(capex)) / float(revenue)) * 100
            _add_line("Capex as % Rev", "capex_intensity", capex_intensity, fmt_key="gross_margin")
        except Exception:
            pass

    # --- Balance sheet / liquidity ---
    cash = _get("cash")
    securities = _get("marketable_securities")
    total_liabilities = _get("total_liabilities")
    total_debt = _get("total_debt")

    if cash is not None or securities is not None:
        try:
            cash_total = float(cash or 0) + float(securities or 0)
            _add_line("Cash + Securities", "cash_total", cash_total)
        except Exception:
            pass

    _add_line("Total Liabilities", "total_liabilities", total_liabilities)

    total_equity = None
    if (cash is not None or securities is not None) and total_debt is not None:
        try:
            cash_total = float(cash or 0) + float(securities or 0)
            net_cash = cash_total - float(total_debt)
            label = "Net Cash" if net_cash >= 0 else "Net Debt"
            _add_line(label, "net_cash", net_cash)
        except Exception:
            pass

    # Deterministically derive common ratios when not already present.
    debt_to_equity = _get("debt_to_equity")
    _add_line("Debt / Equity", "debt_to_equity", debt_to_equity, fmt_key="debt_to_equity")

    current_assets = _get("current_assets")
    current_liabilities = _get("current_liabilities")
    current_ratio = _get("current_ratio")
    if current_ratio is None and current_assets is not None and current_liabilities is not None and float(current_liabilities) != 0:
        try:
            current_ratio = float(current_assets) / float(current_liabilities)
        except Exception:
            current_ratio = None
    _add_line("Current Ratio", "current_ratio", current_ratio, fmt_key="current_ratio")

    interest_coverage = _get("interest_coverage")
    interest_expense = _get("interest_expense")
    if interest_coverage is None and operating_income is not None and interest_expense is not None and float(interest_expense) != 0:
        try:
            interest_coverage = float(operating_income) / abs(float(interest_expense))
        except Exception:
            interest_coverage = None
    _add_line("Interest Coverage", "interest_coverage", interest_coverage, fmt_key="interest_coverage")

    if not [ln for ln in lines if ln.strip()]:
        return "→ No reliable structured metrics available from this filing."

    block = "\n".join(lines).strip()

    # Optional, bounded watch list (unique lines only; never repeat to hit a word target).
    if max_words > 0:
        watch_templates = [
            "→ Watch: cash conversion vs earnings",
            "→ Watch: incentive intensity vs margin",
            "→ Watch: regulatory / labor classification",
            "→ Watch: leverage and refinancing terms",
            "→ Watch: SBC and dilution cadence",
        ]
        used = set((block or "").splitlines())
        added = 0
        for template in watch_templates:
            if added >= int(KEY_METRICS_MAX_WATCH_ITEMS):
                break
            if template in used:
                continue
            candidate = (block + "\n" + template).strip()
            if _count_words(candidate) > max_words:
                break
            block = candidate
            used.add(template)
            added += 1

        if _count_words(block) > max_words:
            block = _trim_appendix_preserving_rows(block, max_words)

    return block.strip() or "→ No reliable structured metrics available from this filing."


def _build_health_driver_block(
    calculated_metrics: Dict[str, Any],
    health_score_data: Dict[str, Any],
) -> Optional[str]:
    """Construct a concise, data-driven health driver block."""
    if not calculated_metrics or not health_score_data:
        return None

    def _pct(val: Optional[float]) -> Optional[str]:
        return f"{val:.1f}%" if isinstance(val, (int, float)) else None

    def _ratio(val: Optional[float]) -> Optional[str]:
        return f"{val:.1f}x" if isinstance(val, (int, float)) else None

    revenue = calculated_metrics.get("revenue") or calculated_metrics.get(
        "total_revenue"
    )
    operating_income = calculated_metrics.get("operating_income")
    operating_margin = calculated_metrics.get("operating_margin")
    net_margin = calculated_metrics.get("net_margin")
    operating_cash_flow = calculated_metrics.get("operating_cash_flow")
    free_cash_flow = calculated_metrics.get("free_cash_flow")
    total_assets = calculated_metrics.get("total_assets")
    total_liabilities = calculated_metrics.get("total_liabilities")
    current_assets = calculated_metrics.get("current_assets")
    current_liabilities = calculated_metrics.get("current_liabilities")
    interest_expense = calculated_metrics.get("interest_expense")
    cash = calculated_metrics.get("cash")
    marketable_securities = calculated_metrics.get("marketable_securities")

    total_equity = (
        (total_assets - total_liabilities)
        if total_assets and total_liabilities
        else None
    )

    fcf_margin = None
    if free_cash_flow is not None and revenue:
        try:
            fcf_margin = (free_cash_flow / revenue) * 100
        except Exception:
            fcf_margin = None

    leverage = None
    if total_liabilities and total_assets:
        try:
            leverage = total_liabilities / total_assets
        except Exception:
            leverage = None

    current_ratio = None
    if current_assets and current_liabilities:
        try:
            current_ratio = (
                current_assets / current_liabilities
                if current_liabilities != 0
                else None
            )
        except Exception:
            current_ratio = None

    interest_coverage = None
    if operating_income and interest_expense:
        try:
            interest_coverage = (
                operating_income / abs(interest_expense)
                if interest_expense != 0
                else None
            )
        except Exception:
            interest_coverage = None

    cash_reserves = (cash or 0) + (marketable_securities or 0)

    lines: List[str] = ["Health Score Drivers:"]

    profitability_bits: List[str] = []
    if operating_margin is not None:
        profitability_bits.append(f"operating margin {_pct(operating_margin)}")
    if net_margin is not None:
        profitability_bits.append(f"net margin {_pct(net_margin)}")
    if profitability_bits:
        lines.append(
            f"→ Profitability: {', '.join(bit for bit in profitability_bits if bit)}."
        )

    cash_bits: List[str] = []
    if operating_cash_flow is not None:
        ocf_str = _format_dollar(operating_cash_flow)
        if ocf_str:
            cash_bits.append(f"operating cash flow {ocf_str}")
    if free_cash_flow is not None:
        fcf_str = _format_dollar(free_cash_flow)
        if fcf_str:
            cash_bits.append(f"FCF {fcf_str}")
    if fcf_margin is not None:
        cash_bits.append(f"FCF margin {_pct(fcf_margin)}")
    if cash_bits:
        lines.append(
            f"→ Cash conversion: {', '.join(bit for bit in cash_bits if bit)}."
        )

    balance_bits: List[str] = []
    if cash_reserves and cash_reserves > 0:
        cash_str = _format_dollar(cash_reserves)
        if cash_str:
            balance_bits.append(f"cash + securities of {cash_str}")
    if total_liabilities is not None:
        liabilities_str = _format_dollar(total_liabilities)
        if liabilities_str:
            balance_bits.append(f"liabilities of {liabilities_str}")
    if leverage is not None:
        balance_bits.append(f"leverage {_ratio(leverage)} assets")
    if interest_coverage is not None:
        balance_bits.append(f"interest coverage {_ratio(interest_coverage)}")
    if balance_bits:
        lines.append(
            f"→ Balance sheet: {', '.join(bit for bit in balance_bits if bit)}."
        )

    liquidity_bits: List[str] = []
    if current_ratio is not None:
        liquidity_bits.append(f"current ratio {_ratio(current_ratio)}")
    if liquidity_bits:
        lines.append(f"→ Liquidity: {', '.join(bit for bit in liquidity_bits if bit)}.")

    return "\n".join(lines) if len(lines) > 1 else None


def _inject_health_drivers(
    summary_text: str,
    calculated_metrics: Dict[str, Any],
    health_score_data: Dict[str, Any],
) -> str:
    """Insert a concise Health Score Drivers block under the Key Metrics section."""
    block = _build_health_driver_block(calculated_metrics, health_score_data)
    if not block:
        return summary_text

    lines = summary_text.splitlines()

    def _is_heading(line: str) -> bool:
        return bool(re.match(r"^\s*##\s+", line))

    # Locate Key Metrics section (if present)
    km_start: Optional[int] = None
    for idx, line in enumerate(lines):
        if re.match(r"^\s*##\s*(Key Metrics|Key Data Appendix)\b", line, re.IGNORECASE):
            km_start = idx
            break

    # Remove any Health Score Drivers blocks that appear outside Key Metrics
    cleaned_lines: List[str] = []
    in_km_section = False
    in_drivers_block = False
    for idx, line in enumerate(lines):
        if km_start is not None and idx == km_start:
            in_km_section = True
            in_drivers_block = False
            cleaned_lines.append(line)
            continue

        if in_km_section and _is_heading(line):
            in_km_section = False

        if not in_km_section:
            if re.match(r"^\s*Health\s+Score\s+Drivers?\s*:?", line, re.IGNORECASE):
                in_drivers_block = True
                continue
            if in_drivers_block:
                if _is_heading(line):
                    in_drivers_block = False
                    cleaned_lines.append(line)
                continue
            cleaned_lines.append(line)
        else:
            cleaned_lines.append(line)

    lines = cleaned_lines

    # Re-find Key Metrics after cleaning
    km_start = None
    for idx, line in enumerate(lines):
        if re.match(r"^\s*##\s*(Key Metrics|Key Data Appendix)\b", line, re.IGNORECASE):
            km_start = idx
            break

    if km_start is None:
        # No Key Metrics section yet; don't inject drivers into other sections.
        return "\n".join(lines).strip()

    km_end = len(lines)
    for j in range(km_start + 1, len(lines)):
        if _is_heading(lines[j]):
            km_end = j
            break

    km_body = "\n".join(lines[km_start + 1 : km_end])
    if re.search(r"Health\s+Score\s+Drivers", km_body, re.IGNORECASE):
        return "\n".join(lines).strip()

    insert_idx = km_start + 1
    while insert_idx < len(lines) and lines[insert_idx].strip() == "":
        insert_idx += 1
    lines.insert(insert_idx, block)
    return "\n".join(lines).strip()


def _build_health_score_line(
    company_name: str,
    score: float,
    band_label: Optional[str],
    calculated_metrics: Dict[str, Any],
    *,
    health_rating_config: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the health score opener with specific driver context when available."""
    band_text = f" - {band_label}" if band_label else ""
    clauses: List[str] = []

    operating_margin = calculated_metrics.get("operating_margin")
    net_margin = calculated_metrics.get("net_margin")
    free_cash_flow = calculated_metrics.get("free_cash_flow")
    operating_cash_flow = calculated_metrics.get("operating_cash_flow")
    cash = calculated_metrics.get("cash")
    liabilities = calculated_metrics.get("total_liabilities")

    if operating_margin is not None:
        clauses.append(
            f"operating margin of {operating_margin:.1f}% supports the earnings base"
        )
    elif net_margin is not None:
        clauses.append(f"net margin of {net_margin:.1f}% supports the earnings base")

    if free_cash_flow is not None:
        fcf_str = _format_dollar(free_cash_flow)
        if fcf_str:
            if free_cash_flow < 0:
                clauses.append(f"negative free cash flow ({fcf_str}) limits flexibility")
            else:
                clauses.append(f"free cash flow of {fcf_str} funds reinvestment")
    elif operating_cash_flow is not None:
        ocf_str = _format_dollar(operating_cash_flow)
        if ocf_str:
            clauses.append(f"operating cash flow of {ocf_str} underwrites liquidity")

    cash_str = _format_dollar(cash) if cash is not None else None
    liab_str = _format_dollar(liabilities) if liabilities is not None else None
    if cash_str and liab_str:
        if cash and liabilities and cash > liabilities:
            clauses.append(f"net cash ({cash_str} vs {liab_str}) adds balance-sheet cushion")
        else:
            clauses.append(
                f"{liab_str} liabilities against {cash_str} cash frames the margin for error"
            )
    elif liab_str:
        clauses.append(f"{liab_str} of liabilities shapes the balance-sheet buffer")
    elif cash_str:
        clauses.append(f"{cash_str} of cash on hand supports near-term flexibility")

    if clauses:
        if len(clauses) >= 3:
            clauses_text = ", ".join(clauses[:2]) + f", and {clauses[2]}"
        elif len(clauses) == 2:
            clauses_text = " and ".join(clauses)
        else:
            clauses_text = clauses[0]
        return (
            f"{company_name} receives a Financial Health Rating of {score:.0f}/100{band_text} "
            f"because {clauses_text}."
        )

    return (
        f"{company_name} receives a Financial Health Rating of {score:.0f}/100{band_text} "
        "because profitability, cash conversion, and balance-sheet resilience all feed into the score."
    )


def _ensure_health_rating_section(
    summary_text: str,
    health_score_data: Dict[str, Any],
    calculated_metrics: Dict[str, Any],
    company_name: str,
    *,
    health_rating_config: Optional[Dict[str, Any]] = None,
    target_length: Optional[int] = None,
) -> str:
    """Guarantee the health rating section is present, well-placed, and reads coherently."""
    if not summary_text or not health_score_data:
        return summary_text

    score = health_score_data.get("overall_score")
    band = health_score_data.get("score_band")
    if score is None:
        return summary_text

    def _score_to_band(score_val: float) -> str:
        for threshold, _abbr, label in RATING_SCALE:
            if score_val >= threshold:
                return label
        return "At Risk"

    band_label = (band or "").strip()
    if not band_label:
        try:
            band_label = _score_to_band(float(score))
        except Exception:
            band_label = ""

    heading_pattern = re.compile(
        r"^\s*##\s*Financial Health Rating\b", re.IGNORECASE | re.MULTILINE
    )
    inline_score_pattern = re.compile(
        r"^\s*(?:Financial\s+Health\s+Rating|Health\s+Score)\s*:\s*\d{1,3}(?:\.\d+)?/100\b.*$",
        re.IGNORECASE,
    )

    score_line = _build_health_score_line(
        company_name,
        float(score),
        band_label,
        calculated_metrics,
        health_rating_config=health_rating_config,
    )
    health_data_for_narrative = dict(health_score_data or {})
    if band_label:
        health_data_for_narrative["score_band"] = band_label
    narrative = _build_health_narrative(
        calculated_metrics,
        health_score_data=health_data_for_narrative,
        health_rating_config=health_rating_config,
        target_length=target_length,
    )

    lines = summary_text.splitlines()
    heading_idx = next(
        (idx for idx, line in enumerate(lines) if heading_pattern.match(line)), None
    )

    if heading_idx is None:
        cleaned_lines = [line for line in lines if not inline_score_pattern.match(line)]
        cleaned_text = "\n".join(cleaned_lines).strip()
        return (
            f"## Financial Health Rating\n{score_line}\n\n{narrative}\n\n{cleaned_text}"
        ).strip()

    # Find end of the health section
    section_end = len(lines)
    for idx in range(heading_idx + 1, len(lines)):
        if re.match(r"^\s*##\s+", lines[idx]):
            section_end = idx
            break

    # Remove stray inline "Financial Health Rating: X/100" lines outside the health section
    cleaned: List[str] = []
    for idx, line in enumerate(lines):
        if idx < heading_idx or idx >= section_end:
            if inline_score_pattern.match(line):
                continue
        cleaned.append(line)
    lines = cleaned

    # Recompute indices after cleanup
    heading_idx = next(
        (idx for idx, line in enumerate(lines) if heading_pattern.match(line)), None
    )
    if heading_idx is None:
        return "\n".join(lines).strip()

    section_end = len(lines)
    for idx in range(heading_idx + 1, len(lines)):
        if re.match(r"^\s*##\s+", lines[idx]):
            section_end = idx
            break

    body_lines = lines[heading_idx + 1 : section_end]

    # Drop Health Score Drivers from this section; it belongs under Key Metrics
    filtered_body: List[str] = []
    skipping_drivers = False
    for line in body_lines:
        if re.search(r"Health\s+Score\s+Drivers", line, re.IGNORECASE):
            skipping_drivers = True
            continue
        if skipping_drivers:
            if not line.strip():
                continue
            if re.match(r"^\s*[-•→]", line):
                continue
            skipping_drivers = False
        filtered_body.append(line)

    # Normalize the score line to be the first non-empty line after the heading
    while filtered_body and not filtered_body[0].strip():
        filtered_body.pop(0)

    if not filtered_body:
        filtered_body = [score_line]
    else:
        first = filtered_body[0].strip()
        if "/100" in first or "financial health rating" in first.lower():
            filtered_body[0] = score_line
        else:
            filtered_body.insert(0, score_line)

    # Preserve pillar breakdown lines (if present), but ensure a coherent narrative paragraph exists.
    pillar_re = re.compile(
        r"^\s*(Profitability|Risk|Liquidity|Growth)\s*:", re.IGNORECASE
    )
    pillar_lines = [line for line in filtered_body[1:] if pillar_re.match(line)]
    narrative_lines = [line for line in filtered_body[1:] if not pillar_re.match(line)]
    narrative_text = "\n".join(narrative_lines).strip()

    if target_length and target_length >= 550:
        min_narrative_words = 80
    elif target_length and target_length >= 450:
        min_narrative_words = 65
    else:
        min_narrative_words = 45
    has_reasoning = bool(
        re.search(
            r"\b(because|driven|reflects|due to|offset|tempered|supported by)\b",
            narrative_text,
            re.IGNORECASE,
        )
    )
    has_tradeoff = bool(
        re.search(
            r"\b(however|but|risk|concern|pressure|offset|tempered)\b",
            narrative_text,
            re.IGNORECASE,
        )
    )
    needs_rebuild = (
        _count_words(narrative_text) < min_narrative_words
        or narrative_text.count(".") + narrative_text.count("!") + narrative_text.count("?") < 2
        or not re.search(r"\d", narrative_text)
        or re.search(r"Health\s+Score\s+Drivers", narrative_text, re.IGNORECASE)
        # The model often repeats this stock phrase; rebuild so the section reads fresh.
        or re.search(r"Under\s+a\s+value[-\s]investor\s+lens", narrative_text, re.IGNORECASE)
        or not (has_reasoning and has_tradeoff)
    )
    if needs_rebuild:
        narrative_text = narrative

    # Normalize narrative formatting inside the Health Rating section.
    # Users sometimes see awkward extra blank lines (or a dangling one-liner)
    # that reads low-quality. Collapse internal newlines and remove redundant
    # trailing phrasing.
    narrative_text = re.sub(r"\s*\n\s*", " ", (narrative_text or "").strip())
    narrative_text = re.sub(r"\s{2,}", " ", narrative_text).strip()
    # Avoid repetitive boilerplate phrasing the model tends to reuse.
    narrative_text = re.sub(
        r"(?i)Under\s+a\s+value[-\s]investor\s+lens,\s*the\s+score",
        "The score",
        narrative_text,
    )
    narrative_text = re.sub(
        r"(?i)\bUnder\s+a\s+value[-\s]investor\s+lens,\s*",
        "",
        narrative_text,
    )
    narrative_text = narrative_text.strip()
    if narrative_text:
        first_char = narrative_text[0]
        if first_char.isalpha():
            narrative_text = first_char.upper() + narrative_text[1:]
    # Remove the low-quality dangling sentence if the model emits it (often as its own paragraph).
    narrative_text = re.sub(
        r"\s*Leverage\s+remains\s+elevated\s+relative\s+to\s+cash\.?\s*",
        " ",
        narrative_text,
        flags=re.IGNORECASE,
    )
    narrative_text = re.sub(r"\s{2,}", " ", narrative_text).strip()
    narrative_text = re.sub(r"[ \t]+\.", ".", narrative_text)
    narrative_text = re.sub(r"\.\s*\.", ".", narrative_text)
    if narrative_text and not narrative_text.endswith((".", "!", "?")):
        narrative_text += "."

    rebuilt_section_lines: List[str] = [score_line, "", narrative_text.strip()]
    if pillar_lines:
        rebuilt_section_lines.extend(["", *pillar_lines])

    replacement = ["## Financial Health Rating", *rebuilt_section_lines]

    lines = lines[:heading_idx] + replacement + lines[section_end:]
    return "\n".join(lines).strip()


def _build_health_narrative(
    calculated_metrics: Dict[str, Any],
    *,
    health_score_data: Optional[Dict[str, Any]] = None,
    health_rating_config: Optional[Dict[str, Any]] = None,
    target_length: Optional[int] = None,
) -> str:
    """Compose a metric-backed health explanation that reads like a mini-paragraph."""
    band = (health_score_data or {}).get("score_band")

    framework = (health_rating_config or {}).get("framework")
    weighting = (health_rating_config or {}).get("primary_factor_weighting")

    framework_labels = {
        "value_investor_default": "a value-investor lens",
        "quality_moat_focus": "a quality-first lens",
        "financial_resilience": "a resilience/stress-test lens",
        "growth_sustainability": "a growth-sustainability lens",
        "user_defined_mix": "a balanced multi-factor lens",
    }
    weighting_labels = {
        "profitability_margins": "profitability and margin quality",
        "cash_flow_conversion": "cash conversion and free cash flow",
        "balance_sheet_strength": "leverage and balance sheet resilience",
        "liquidity_near_term_risk": "near-term liquidity and refinancing risk",
        "execution_competitiveness": "execution and operating efficiency",
    }

    sentences: List[str] = []
    lens = framework_labels.get(framework)
    focus = weighting_labels.get(weighting)
    if focus:
        # Avoid repeating the literal phrase "under a value-investor lens" across memos.
        # If the user selected a non-default framework, acknowledge it once; otherwise
        # explain the weighting in plain English.
        if lens and framework and framework != "value_investor_default":
            sentences.append(
                f"Using {lens}, the score weights {focus} most heavily because it best captures durability in this setup."
            )
        else:
            sentences.append(
                f"The score weights {focus} most heavily because it best captures durability in this setup."
            )

    operating_margin = calculated_metrics.get("operating_margin")
    net_margin = calculated_metrics.get("net_margin")
    revenue = calculated_metrics.get("revenue") or calculated_metrics.get("total_revenue")
    operating_cash_flow = calculated_metrics.get("operating_cash_flow")
    free_cash_flow = calculated_metrics.get("free_cash_flow")
    ocf_str = (
        _format_dollar(operating_cash_flow) if operating_cash_flow is not None else None
    )
    fcf_str = _format_dollar(free_cash_flow) if free_cash_flow is not None else None
    fcf_margin = None
    if free_cash_flow is not None and revenue:
        try:
            fcf_margin = (free_cash_flow / revenue) * 100
        except Exception:
            fcf_margin = None

    cash = calculated_metrics.get("cash")
    liabilities = calculated_metrics.get("total_liabilities")
    cash_str = _format_dollar(cash) if cash is not None else None
    liab_str = _format_dollar(liabilities) if liabilities is not None else None

    if operating_margin is not None and net_margin is not None:
        gap = net_margin - operating_margin
        gap_clause = ""
        if abs(gap) >= 5:
            gap_clause = (
                " with meaningful below-the-line drag"
                if gap < 0
                else " helped by below-the-line items"
            )
        sentences.append(
            f"Operating margin of {operating_margin:.1f}% and net margin of {net_margin:.1f}% describe the profitability profile{gap_clause}."
        )
    elif operating_margin is not None:
        sentences.append(
            f"Operating margin of {operating_margin:.1f}% provides the clearest read on core profitability."
        )
    elif net_margin is not None:
        sentences.append(
            f"Net margin of {net_margin:.1f}% is the headline profitability signal, though non-operating items can distort the trend."
        )

    if ocf_str and fcf_str:
        margin_clause = f" (FCF margin {fcf_margin:.1f}%)" if fcf_margin is not None else ""
        sentences.append(
            f"Operating cash flow of {ocf_str} translating to free cash flow of {fcf_str}{margin_clause} supports financial flexibility."
        )
    elif fcf_str:
        sentences.append(
            f"Free cash flow of {fcf_str} provides an important buffer for reinvestment and resilience."
        )
    elif ocf_str:
        sentences.append(
            f"Operating cash flow of {ocf_str} supports reinvestment capacity and near-term resilience."
        )

    if cash_str and liab_str:
        leverage_clause = ""
        if cash and liabilities:
            if cash > liabilities:
                leverage_clause = ", leaving the balance sheet net-cash overall"
            elif liabilities > cash * 3:
                leverage_clause = ", with liabilities more than 3x cash and higher refinancing risk"
        sentences.append(
            f"Cash of {cash_str} against liabilities of {liab_str} frames the leverage and refinancing risk{leverage_clause}."
        )
    elif liab_str:
        sentences.append(
            f"Liabilities of {liab_str} are the main balance-sheet constraint and deserve close attention."
        )
    elif cash_str:
        sentences.append(
            f"Cash on hand of {cash_str} provides a liquidity cushion against shocks."
        )

    current_assets = calculated_metrics.get("current_assets")
    current_liabilities = calculated_metrics.get("current_liabilities")
    if current_assets and current_liabilities:
        try:
            current_ratio = (
                current_assets / current_liabilities if current_liabilities != 0 else None
            )
        except Exception:
            current_ratio = None
        if current_ratio is not None:
            sentences.append(
                f"A current ratio around {current_ratio:.1f}x suggests near-term obligations are manageable."
            )

    # Add a forward-looking, underwriting-style linkage so this section doesn't read like
    # a disconnected list of metrics.
    if operating_margin is not None and net_margin is not None and abs(net_margin - operating_margin) >= 5:
        sentences.append(
            "Because net margin can be flattered by non-operating items, the health signal should be underwritten off operating profitability and cash conversion."
        )
    sentences.append(
        "The score tends to move with sustained free cash flow conversion and balance-sheet flexibility because those inputs determine resilience; margin compression or rising leverage would pull it lower."
    )

    if band == "Very Healthy":
        sentences.append(
            "Overall, the metrics support a Very Healthy profile provided cash conversion remains consistent."
        )
    elif band == "Healthy":
        sentences.append(
            "Overall, this lands in the Healthy range, with leverage and cash conversion determining whether the score drifts higher or lower."
        )
    elif band == "Watch":
        sentences.append(
            "Overall, this is a Watch profile: the fundamentals are workable, but the weaker inputs leave less room for error."
        )
    elif band == "At Risk":
        sentences.append(
            "Overall, this is an At Risk profile: the balance between cash generation and obligations looks tight and the margin for error is thin."
        )

    if band:
        sentences.append(
            f"Taken together, these drivers explain why the score sits in the {band} range rather than a materially higher rating."
        )
    else:
        sentences.append(
            "Taken together, these drivers explain why the score sits where it does rather than a materially higher rating."
        )

    sentences.append(
        "This health snapshot provides the balance-sheet backdrop for the operating analysis that follows."
    )

    if target_length and target_length < 500:
        sentences = sentences[:5]

    return " ".join(sentences).strip()


def _prepare_filing_response(raw_filing: Dict[str, Any], settings) -> Filing:
    filing_data = {
        key: value
        for key, value in raw_filing.items()
        if key not in {"local_document_path", "source_doc_url"}
    }
    filing_id = str(filing_data.get("id"))
    if filing_id:
        filing_data["url"] = _build_document_path(filing_id, settings)
    return Filing(**filing_data)


def _resolve_filing_context(filing_id: str, settings) -> Dict[str, Any]:
    filing_key = str(filing_id)

    def _get_fallback_context() -> Optional[Dict[str, Any]]:
        filing = fallback_filings_by_id.get(filing_key)
        if not filing:
            return None

        company_id = str(filing.get("company_id"))
        company = fallback_companies.get(company_id)
        if not company:
            return None

        return {
            "filing": filing,
            "company": company,
            "source": "fallback",
        }

    fallback_context = _get_fallback_context()

    # If the ID is not a valid UUID, prefer fallback data (tests use string IDs)
    try:
        UUID(filing_key)
    except ValueError as exc:
        if fallback_context:
            return fallback_context
        raise HTTPException(status_code=400, detail="Invalid filing ID format") from exc

    if not _supabase_configured(settings):
        if fallback_context:
            return fallback_context
        raise HTTPException(status_code=404, detail="Filing not found")

    supabase = get_supabase_client()

    try:
        filing_response = (
            supabase.table("filings").select("*").eq("id", filing_key).execute()
        )
        if not filing_response.data:
            if fallback_context:
                return fallback_context
            raise HTTPException(status_code=404, detail="Filing not found")

        filing = filing_response.data[0]
        company_id = filing.get("company_id")

        company_response = (
            supabase.table("companies")
            .select("id, ticker, exchange, cik, name, country")
            .eq("id", company_id)
            .execute()
        )
        if not company_response.data:
            if fallback_context:
                return fallback_context
            raise HTTPException(status_code=404, detail="Company not found for filing")

        company = company_response.data[0]

        return {
            "filing": filing,
            "company": company,
            "source": "supabase",
        }
    except HTTPException as http_exc:
        if http_exc.status_code in {400, 404} and fallback_context:
            logger.warning(
                "Supabase lookup failed for %s with status %s. Using fallback cache.",
                filing_key,
                http_exc.status_code,
            )
            return fallback_context
        raise
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            if fallback_context:
                return fallback_context
            raise HTTPException(status_code=404, detail="Filing not found")

        if fallback_context:
            logger.warning(
                "Supabase lookup error for filing %s, using fallback cache: %s",
                filing_key,
                exc,
            )
            return fallback_context
        raise HTTPException(
            status_code=500, detail=f"Error resolving filing context: {exc}"
        )


def _fetch_eodhd_document(
    ticker: str, exchange: Optional[str] = None, filter_param: Optional[str] = None
) -> Dict[str, Any]:
    client = get_eodhd_client()
    exchange_code = (exchange or "US") or "US"
    return client.get_fundamentals(
        ticker, exchange=exchange_code, filter_param=filter_param
    )


def _ensure_storage_dir(settings) -> Path:
    storage_dir = Path(settings.data_dir).expanduser().resolve() / "filings"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _build_local_document_path(storage_dir: Path, filing_id: str) -> Path:
    return storage_dir / f"{filing_id}.html"


def _is_sec_document_url(url: str) -> bool:
    """Return True only for SEC-hosted filing URLs (prevents downloading arbitrary HTML)."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host.endswith("sec.gov")


def _looks_like_cloud_run_console_page(text: str) -> bool:
    """Heuristic to detect Google Cloud Console / Cloud Run pages mistakenly cached as filings."""
    lowered = (text or "").lower()
    if "console.cloud.google.com" in lowered:
        return True
    hits = sum(
        token in lowered
        for token in (
            "revision tags",
            "traffic",
            "deployed",
        )
    )
    return "revisions" in lowered and hits >= 2


def _read_text_head(path: Path, max_chars: int = 60_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:  # noqa: BLE001
        try:
            return path.read_text(errors="ignore")[:max_chars]
        except Exception:  # noqa: BLE001
            return ""


def _persist_filing_field_updates(
    context: Dict[str, Any], filing_id: str, updates: Dict[str, Any]
) -> None:
    """Best-effort persistence for Supabase-backed filings."""
    if context.get("source") != "supabase":
        return
    if not filing_id or not updates:
        return
    try:
        supabase = get_supabase_client()
        supabase.table("filings").update(updates).eq("id", filing_id).execute()
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            return
        logger.debug("Unable to persist filing updates for %s: %s", filing_id, exc)


def _ensure_local_document(context: Dict[str, Any], settings) -> Optional[Path]:
    filing = context["filing"]
    company = context["company"]
    storage_dir = _ensure_storage_dir(settings)

    filing_id = filing.get("id")
    filing_id_str = str(filing_id) if filing_id is not None else ""

    existing_path = filing.get("local_document_path")
    if existing_path:
        path_obj = Path(existing_path)
        if path_obj.exists():
            head = _read_text_head(path_obj)
            if _looks_like_cloud_run_console_page(head):
                logger.warning(
                    "Cached filing document for %s looks like a Cloud Console page; ignoring %s",
                    filing_id_str or "<unknown>",
                    path_obj,
                )
                filing.pop("local_document_path", None)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": None}
                )
            else:
                return path_obj
        else:
            filing.pop("local_document_path", None)
            _persist_filing_field_updates(
                context, filing_id_str, {"local_document_path": None}
            )

    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")

    source_doc_url = filing.get("source_doc_url")
    if source_doc_url and not _is_sec_document_url(str(source_doc_url)):
        logger.warning(
            "Ignoring non-SEC source_doc_url for filing %s: %s",
            filing_id_str or "<unknown>",
            source_doc_url,
        )
        filing.pop("source_doc_url", None)
        _persist_filing_field_updates(context, filing_id_str, {"source_doc_url": None})
        source_doc_url = None

    if not source_doc_url:
        cik_value = company.get("cik") if company else None
        if cik_value and filing_type and filing_date:
            try:
                sec_filings = get_company_filings(
                    cik=cik_value,
                    filing_types=[filing_type],
                    max_results=200,
                )
                for candidate in sec_filings:
                    if candidate.get("filing_type") != filing_type:
                        continue

                    if (
                        candidate.get("filing_date") == filing_date
                        or candidate.get("period_end") == filing_date
                    ):
                        source_doc_url = candidate.get("url")
                        filing["source_doc_url"] = source_doc_url
                        if source_doc_url:
                            _persist_filing_field_updates(
                                context,
                                filing_id_str,
                                {"source_doc_url": source_doc_url},
                            )
                        break
            except Exception as sec_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to resolve SEC document for filing %s: %s",
                    filing_id_str,
                    sec_exc,
                )

    if not source_doc_url:
        return None

    target_path = _build_local_document_path(storage_dir, filing_id_str)

    try:
        if download_filing(source_doc_url, str(target_path)):
            filing["local_document_path"] = str(target_path)
            _persist_filing_field_updates(
                context, filing_id_str, {"local_document_path": str(target_path)}
            )
            return target_path
    except Exception as download_exc:  # noqa: BLE001
        logger.warning(
            "Failed to download SEC filing %s: %s",
            source_doc_url,
            download_exc,
        )

    return None


def _ensure_company_country(
    company: Dict[str, Any], *, supabase=None, company_key: Optional[str] = None
) -> Dict[str, Any]:
    """Hydrate and persist company domicile country when missing or US placeholder."""
    if not company:
        return company

    original = company.get("country")
    # Treat an unresolved US placeholder (or missing) as "missing" so we don't persist it.
    original_missing = should_hydrate_country(original)
    normalized = normalize_country(original)
    if normalized and normalized != original:
        company["country"] = normalized

    resolved_confidently = False

    if should_hydrate_country(company.get("country")):
        inferred = infer_country_from_company_name(company.get("name"))
        if inferred:
            company["country"] = inferred
            resolved_confidently = True

    if should_hydrate_country(company.get("country")) and company.get("ticker"):
        inferred_from_ticker = infer_country_from_ticker(company.get("ticker"))
        if inferred_from_ticker:
            company["country"] = inferred_from_ticker
            resolved_confidently = True

    if should_hydrate_country(company.get("country")):
        inferred_exchange = infer_country_from_exchange(company.get("exchange"))
        if inferred_exchange and inferred_exchange != "US":
            company["country"] = inferred_exchange
            resolved_confidently = True

    if should_hydrate_country(company.get("country")) and company.get("cik"):
        sec_country = resolve_country_from_sec_submission(company.get("cik"))
        if sec_country:
            company["country"] = normalize_country(sec_country) or sec_country
            resolved_confidently = True

    if should_hydrate_country(company.get("country")) and company.get("ticker"):
        yahoo_country = resolve_country_from_yahoo_asset_profile(company.get("ticker"))
        if yahoo_country:
            company["country"] = normalize_country(yahoo_country) or yahoo_country
            resolved_confidently = True

    if should_hydrate_country(company.get("country")):
        hydrated = hydrate_country_with_eodhd(
            company.get("ticker"), company.get("exchange")
        )
        if hydrated:
            company["country"] = normalize_country(hydrated) or hydrated

    # Avoid persisting a US placeholder when the only result came from EODHD.
    if (
        should_hydrate_country(company.get("country"))
        and not resolved_confidently
        and original_missing
    ):
        company["country"] = None

    if company.get("country") == original:
        return company

    if supabase and company.get("id"):
        try:
            supabase.table("companies").update({"country": company.get("country")}).eq(
                "id", company.get("id")
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not persist hydrated country for %s: %s",
                company.get("ticker"),
                exc,
            )
    elif company_key is not None:
        fallback_companies[company_key] = company
        save_fallback_companies()

    return company


async def _start_fetch_with_fallback_company(
    company_key: str,
    company_data: Any,
    request: FilingsFetchRequest,
    settings,
) -> FilingsFetchResponse:
    """
    Populate filings from local/sample data when Supabase is unavailable.
    Mirrors the legacy non-database flow so callers (including Supabase fallbacks)
    can reuse the same logic.
    """
    if hasattr(company_data, "model_dump"):
        company = company_data.model_dump()
    else:
        company = dict(company_data)

    company = _ensure_company_country(company, company_key=company_key)
    fallback_companies[company_key] = company
    save_fallback_companies()

    ticker = company.get("ticker")
    if not ticker:
        raise HTTPException(
            status_code=400, detail="Company is missing a ticker symbol"
        )

    entries_to_ingest: List[Dict[str, Any]] = []

    try:
        financial_data = get_eodhd_client().get_financial_statements(
            ticker, exchange="US"
        )
        eodhd_url = f"https://eodhd.com/api/fundamentals/{ticker}.US"

        quarterly_income = financial_data.get("income_statement", {}).get(
            "quarterly", {}
        )
        for date_str, statement in quarterly_income.items():
            entries_to_ingest.append(
                {
                    "filing_type": "10-Q",
                    "date_str": date_str,
                    "income_statement": statement,
                    "balance_sheet": financial_data.get("balance_sheet", {})
                    .get("quarterly", {})
                    .get(date_str, {}),
                    "cash_flow": financial_data.get("cash_flow", {})
                    .get("quarterly", {})
                    .get(date_str, {}),
                    "url": eodhd_url,
                }
            )

        yearly_income = financial_data.get("income_statement", {}).get("yearly", {})
        for date_str, statement in yearly_income.items():
            entries_to_ingest.append(
                {
                    "filing_type": "10-K",
                    "date_str": date_str,
                    "income_statement": statement,
                    "balance_sheet": financial_data.get("balance_sheet", {})
                    .get("yearly", {})
                    .get(date_str, {}),
                    "cash_flow": financial_data.get("cash_flow", {})
                    .get("yearly", {})
                    .get(date_str, {}),
                    "url": eodhd_url,
                }
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (EODHDAccessError, EODHDClientError) as exc:
        logger.warning(
            "EODHD data unavailable for %s: %s. Set EODHD_API_KEY to a paid token to enable live fundamentals.",
            ticker,
            exc,
        )
        entries_to_ingest = _sample_entries_for_ticker(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure while fetching EODHD data for %s", ticker)
        entries_to_ingest = _sample_entries_for_ticker(ticker)

    if not entries_to_ingest:
        logger.warning(
            "No sample filings available for %s; continuing with empty dataset.", ticker
        )

    cutoff_date = None
    if request.max_history_years:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(
            days=365 * request.max_history_years
        )

    company_filings = fallback_filings.setdefault(company_key, [])
    existing_pairs = {
        (filing["filing_type"], filing["filing_date"]) for filing in company_filings
    }
    saved_count = 0

    for existing in company_filings:
        fallback_filings_by_id.setdefault(str(existing["id"]), existing)

    storage_dir = _ensure_storage_dir(settings)
    sec_filings_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    cik_value = company.get("cik")
    ticker_symbol = company.get("ticker")

    if (not cik_value or not str(cik_value).isdigit()) and ticker_symbol:
        try:
            general_info = get_eodhd_client().get_company_info(
                ticker_symbol, exchange=company.get("exchange") or "US"
            )
            candidate_cik = general_info.get("CIK") or general_info.get("cik")
            if candidate_cik:
                cik_value = str(candidate_cik)
                company["cik"] = cik_value
                fallback_companies[company_key]["cik"] = cik_value
                save_fallback_companies()
        except Exception:
            pass

    if (not cik_value or not str(cik_value).isdigit()) and ticker_symbol:
        try:
            matches = await search_company_by_ticker_or_cik(ticker_symbol)
            if matches:
                candidate_cik = matches[0].get("cik")
                if candidate_cik:
                    cik_value = str(candidate_cik)
                    company["cik"] = cik_value
                    fallback_companies[company_key]["cik"] = cik_value
                    save_fallback_companies()
        except Exception as cik_exc:  # noqa: BLE001
            logger.warning(
                "Unable to resolve CIK for company %s: %s",
                company_key,
                cik_exc,
            )

    if cik_value:
        cik_value = str(cik_value)
        cik_digits = "".join(ch for ch in cik_value if ch.isdigit())
        cik_value = cik_digits.zfill(10) if cik_digits else None

    if cik_value:
        try:
            sec_filings = get_company_filings(
                cik=cik_value,
                filing_types=request.filing_types or ["10-K", "10-Q"],
                max_results=200,
            )
            for entry in sec_filings:
                filing_type_value = entry.get("filing_type")
                filing_date_value = entry.get("filing_date")
                period_end_value = entry.get("period_end")

                if filing_type_value and filing_date_value:
                    sec_filings_map[
                        (filing_type_value, filing_date_value, "filing_date")
                    ] = entry
                if filing_type_value and period_end_value:
                    sec_filings_map[
                        (filing_type_value, period_end_value, "period_end")
                    ] = entry
        except Exception as sec_exc:  # noqa: BLE001
            logger.warning(
                "Unable to retrieve SEC filings for CIK %s: %s",
                cik_value,
                sec_exc,
            )
    else:
        logger.warning(
            "CIK not available for company %s; SEC document download skipped",
            company_key,
        )

    if not entries_to_ingest and sec_filings_map:
        unique_entries: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for entry in sec_filings_map.values():
            filing_type_value = entry.get("filing_type")
            reference_date = entry.get("filing_date") or entry.get("period_end")
            if not filing_type_value or not reference_date:
                continue
            key = (filing_type_value, reference_date)
            unique_entries.setdefault(key, entry)

        sorted_entries = sorted(
            unique_entries.values(),
            key=lambda item: item.get("filing_date") or item.get("period_end") or "",
            reverse=True,
        )

        max_entries = 8
        if request.max_history_years:
            max_entries = max(2, request.max_history_years * 2)

        for entry in sorted_entries[:max_entries]:
            entries_to_ingest.append(
                {
                    "filing_type": entry.get("filing_type"),
                    "date_str": entry.get("filing_date") or entry.get("period_end"),
                    "income_statement": {},
                    "balance_sheet": {},
                    "cash_flow": {},
                    "url": entry.get("url"),
                }
            )

    def _maybe_add_filing(
        filing_type: str,
        date_str: str,
        income_statement: dict,
        balance_sheet: dict,
        cash_flow: dict,
        source_url: str,
    ) -> None:
        nonlocal saved_count, existing_pairs, company_filings

        if request.filing_types and filing_type not in request.filing_types:
            return

        try:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return

        if cutoff_date and filing_date < cutoff_date:
            return

        key = (filing_type, filing_date)
        if key in existing_pairs:
            return

        filing_id = uuid4()
        filing_id_str = str(filing_id)
        now = datetime.now(timezone.utc)

        filing_record = {
            "id": filing_id,
            "company_id": request.company_id,
            "filing_type": filing_type,
            "filing_date": filing_date,
            "period_end": filing_date,
            "url": source_url,
            "pages": None,
            "raw_file_path": f"eodhd_{ticker}_{filing_type.replace('-', '')}_{date_str}",
            "parsed_json_path": None,
            "status": "parsed",
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }

        sec_match = None
        if sec_filings_map:
            sec_match = sec_filings_map.get((filing_type, date_str, "filing_date"))
            if not sec_match:
                sec_match = sec_filings_map.get((filing_type, date_str, "period_end"))
        local_document_path = None
        source_doc_url = None

        if sec_match:
            source_doc_url = sec_match.get("url")
            if source_doc_url:
                target_path = _build_local_document_path(storage_dir, filing_id_str)
                try:
                    if download_filing(source_doc_url, str(target_path)):
                        local_document_path = str(target_path)
                except Exception as download_exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to download SEC filing %s: %s",
                        source_doc_url,
                        download_exc,
                    )

        if source_doc_url:
            filing_record["source_doc_url"] = source_doc_url
        if local_document_path:
            filing_record["local_document_path"] = local_document_path

        company_filings.append(filing_record)
        existing_pairs.add(key)
        saved_count += 1

        fallback_filings_by_id[str(filing_id)] = filing_record

        fallback_financial_statements[str(filing_id)] = {
            "filing_id": filing_id,
            "period_start": filing_date,
            "period_end": filing_date,
            "currency": "USD",
            "statements": {
                "income_statement": income_statement,
                "balance_sheet": balance_sheet,
                "cash_flow": cash_flow,
            },
            "created_at": now,
            "updated_at": now,
        }

    for entry in entries_to_ingest:
        _maybe_add_filing(
            entry["filing_type"],
            entry["date_str"],
            entry.get("income_statement", {}),
            entry.get("balance_sheet", {}),
            entry.get("cash_flow", {}),
            entry.get("url", "https://www.sec.gov"),
        )

    company_filings.sort(key=lambda filing: filing["filing_date"], reverse=True)

    task_id = f"local-{uuid4()}"
    return FilingsFetchResponse(
        task_id=task_id,
        message=(
            f"Fetched {saved_count} filings for {company.get('name', ticker)}"
            if saved_count
            else "No new filings were fetched"
        ),
    )


@router.post("/fetch", response_model=FilingsFetchResponse)
async def fetch_filings(request: FilingsFetchRequest):
    """
    Initiate background task to fetch filings for a company from SEC EDGAR.
    Returns a task ID for tracking progress.
    """
    settings = get_settings()

    if not _supabase_configured(settings):
        company_key = str(request.company_id)
        company = fallback_companies.get(company_key)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        return await _start_fetch_with_fallback_company(
            company_key, company, request, settings
        )

    supabase = get_supabase_client()

    # Verify company exists
    try:
        company_response = (
            supabase.table("companies")
            .select("*")
            .eq("id", str(request.company_id))
            .execute()
        )
        if not company_response.data:
            raise HTTPException(status_code=404, detail="Company not found")

        company = _ensure_company_country(company_response.data[0], supabase=supabase)
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            fallback_company = fallback_companies.get(str(request.company_id))
            if fallback_company:
                return await _start_fetch_with_fallback_company(
                    str(request.company_id),
                    fallback_company,
                    request,
                    settings,
                )
            raise HTTPException(
                status_code=404,
                detail="Company not found (Supabase tables missing and no cached companies)",
            )
        raise HTTPException(
            status_code=500, detail=f"Error verifying company: {str(e)}"
        )

    # Create task
    try:
        task = fetch_filings_task.delay(
            company_id=str(request.company_id),
            ticker=company["ticker"],
            cik=company.get("cik"),
            filing_types=request.filing_types,
            max_history_years=request.max_history_years,
        )

        # Store task status
        task_data = {
            "task_id": task.id,
            "task_type": "fetch_filings",
            "status": "pending",
            "progress": 0,
        }
        supabase.table("task_status").insert(task_data).execute()

        return FilingsFetchResponse(
            task_id=task.id,
            message=f"Started fetching filings for {company['name']}",
        )

    except Exception as celery_exc:
        logger.warning(
            "Celery broker unavailable for filings fetch; running inline fallback: %s",
            celery_exc,
        )
        try:
            inline_result = run_fetch_filings_inline(
                company_id=str(request.company_id),
                ticker=company["ticker"],
                cik=company.get("cik"),
                filing_types=request.filing_types,
                max_history_years=request.max_history_years,
            )
        except Exception as inline_exc:  # noqa: BLE001
            logger.exception("Inline filings fetch failed")
            raise HTTPException(
                status_code=500,
                detail=f"Error starting fetch task: {inline_exc}",
            ) from inline_exc

        inline_task_id = f"inline-{uuid4()}"
        message = inline_result.get("message") or (
            f"Fetched {inline_result.get('filings_count', 0)} filings for {company['name']}"
        )
        task_record = {
            "task_id": inline_task_id,
            "task_type": "fetch_filings",
            "status": "completed",
            "progress": 100,
            "result": inline_result,
        }
        try:
            supabase.table("task_status").insert(task_record).execute()
        except Exception as status_exc:  # noqa: BLE001
            if is_supabase_table_missing_error(status_exc):
                fallback_task_status[inline_task_id] = task_record
            else:
                logger.debug("Unable to persist inline fetch status: %s", status_exc)

        return FilingsFetchResponse(task_id=inline_task_id, message=message)


@router.get("/{filing_id}/document")
async def get_filing_document(filing_id: str, raw: bool = False):
    """Serve a reader-friendly view of the filing or raw content when requested."""
    settings = get_settings()
    context = _resolve_filing_context(filing_id, settings)
    filing = context["filing"]
    company = context["company"]

    ticker = company.get("ticker")
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker not available for filing")

    exchange = company.get("exchange") or "US"
    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")

    local_document = _ensure_local_document(context, settings)
    local_exists = bool(local_document and local_document.exists())
    source_doc_url = filing.get("source_doc_url")
    if source_doc_url and not _is_sec_document_url(str(source_doc_url)):
        logger.warning(
            "Ignoring non-SEC source_doc_url for filing document redirect %s: %s",
            filing_id,
            source_doc_url,
        )
        filing.pop("source_doc_url", None)
        _persist_filing_field_updates(
            context, str(filing.get("id") or ""), {"source_doc_url": None}
        )
        source_doc_url = None

    if not raw and local_exists:
        return RedirectResponse(
            url=f"/api/{settings.api_version}/filings/{filing_id}/document?raw=1"
        )

    if local_document and local_document.exists():
        suffix = local_document.suffix.lower()
        if suffix == ".pdf":
            media_type = "application/pdf"
        elif suffix in {".txt", ".text"}:
            media_type = "text/plain"
        else:
            media_type = "text/html"

        return FileResponse(
            path=local_document,
            media_type=media_type,
            headers={"Content-Disposition": "inline"},
        )

    if source_doc_url:
        return RedirectResponse(url=source_doc_url)

    try:
        fundamentals = _fetch_eodhd_document(ticker, exchange=exchange)
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "ticker": ticker,
                    "exchange": exchange,
                    "source": "eodhd",
                    "filing_type": filing_type,
                    "filing_date": filing_date,
                    "data": fundamentals,
                }
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to retrieve EODHD fundamentals for ticker %s (filing %s)",
            ticker,
            filing_id,
            exc_info=exc,
        )
        fallback_statement = fallback_financial_statements.get(str(filing_id))
        if fallback_statement:
            return JSONResponse(
                content=jsonable_encoder(
                    {
                        "ticker": ticker,
                        "exchange": exchange,
                        "source": "cache",
                        "filing_type": filing_type,
                        "filing_date": filing_date,
                        "data": fallback_statement,
                    }
                )
            )
        if context["source"] == "supabase":
            try:
                supabase = get_supabase_client()
                statement_response = (
                    supabase.table("financial_statements")
                    .select("*")
                    .eq("filing_id", filing.get("id"))
                    .execute()
                )
                if statement_response.data:
                    return JSONResponse(
                        content=jsonable_encoder(
                            {
                                "ticker": ticker,
                                "exchange": exchange,
                                "source": "supabase",
                                "filing_type": filing_type,
                                "filing_date": filing_date,
                                "data": statement_response.data,
                            }
                        )
                    )
            except Exception as supabase_error:  # noqa: BLE001
                logger.exception(
                    "Failed to retrieve financial statements from Supabase for filing %s",
                    filing_id,
                    exc_info=supabase_error,
                )
        raise HTTPException(
            status_code=502, detail="Unable to retrieve filing document from provider"
        )


@router.get("/{filing_id}", response_model=Filing)
async def get_filing(filing_id: str):
    """Get filing details by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filing = fallback_filings_by_id.get(filing_id) or fallback_filings_by_id.get(
            str(filing_id)
        )
        if not filing:
            raise HTTPException(status_code=404, detail="Filing not found")
        return _prepare_filing_response(filing, settings)

    supabase = get_supabase_client()

    try:
        response = supabase.table("filings").select("*").eq("id", filing_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Filing not found")
        return _prepare_filing_response(response.data[0], settings)

    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            filing = fallback_filings_by_id.get(
                filing_id
            ) or fallback_filings_by_id.get(str(filing_id))
            if filing:
                return _prepare_filing_response(filing, settings)
            raise HTTPException(
                status_code=404,
                detail="Filing not found (Supabase tables missing and no cached filing).",
            )
        raise HTTPException(
            status_code=500, detail=f"Error retrieving filing: {str(e)}"
        )


@router.get("/company/{company_id}", response_model=List[Filing])
async def list_company_filings(
    company_id: str, filing_type: str = None, limit: int = 50, offset: int = 0
):
    """List filings for a specific company."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filings = fallback_filings.get(company_id, [])
        if filing_type:
            filings = [
                filing for filing in filings if filing["filing_type"] == filing_type
            ]
        sliced = filings[offset : offset + limit]
        return [_prepare_filing_response(filing, settings) for filing in sliced]

    supabase = get_supabase_client()

    try:
        query = supabase.table("filings").select("*").eq("company_id", company_id)

        if filing_type:
            query = query.eq("filing_type", filing_type)

        response = (
            query.order("filing_date", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )

        return [_prepare_filing_response(filing, settings) for filing in response.data]

    except Exception as e:
        if is_supabase_table_missing_error(e):
            filings = fallback_filings.get(company_id, [])
            if filing_type:
                filings = [
                    filing for filing in filings if filing["filing_type"] == filing_type
                ]
            sliced = filings[offset : offset + limit]
            return [_prepare_filing_response(filing, settings) for filing in sliced]
        raise HTTPException(status_code=500, detail=f"Error listing filings: {str(e)}")


@router.post("/{filing_id}/summary")
def generate_filing_summary(
    filing_id: str,
    preferences: Optional[FilingSummaryPreferences] = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Generate a filing summary.

    IMPORTANT: This endpoint also logs a durable "summary generated" event so the
    dashboard can track total summary generations over time even if the user
    later removes the summary snapshot from the dashboard.
    """
    settings = get_settings()
    preferences = preferences or FilingSummaryPreferences()
    target_length = _clamp_target_length(preferences.target_length)
    use_default_cache = preferences.mode == "default"

    include_health_rating = bool(
        preferences.health_rating and preferences.health_rating.enabled
    )

    # Reset progress
    progress_cache[str(filing_id)] = "Initializing AI Agent..."

    usage_status = get_summary_usage_status(user.id)
    if usage_status.remaining <= 0:
        if usage_status.plan == "pro":
            reset_date = usage_status.period_end.date().isoformat() if usage_status.period_end else "the next cycle"
            detail = (
                f"Monthly summary limit reached (100/month). "
                f"Your limit resets on {reset_date}."
            )
        else:
            detail = (
                "Free trial summary already used. "
                "Upgrade to Pro to continue generating summaries."
            )
        progress_cache[str(filing_id)] = "Monthly summary limit reached."
        raise HTTPException(status_code=402, detail=detail)

    # Check cache first
    if (
        use_default_cache and False
    ):  # Cache disabled to force regeneration with new prompts
        cached_summary = fallback_filing_summaries.get(str(filing_id))
        if cached_summary:
            progress_cache[str(filing_id)] = "Complete"
            record_summary_generated_event(
                summary_id=str(filing_id),
                company_id=None,
                user_id=user.id,
                kind=getattr(preferences, "mode", None),
                cached=True,
                source=None,
            )
            return JSONResponse(
                content={
                    "filing_id": filing_id,
                    "summary": cached_summary,
                    "cached": True,
                }
            )

    # Get filing context
    try:
        progress_cache[str(filing_id)] = "Reading Filing Content..."
        context = _resolve_filing_context(filing_id, settings)
        filing = context["filing"]
        company = context["company"]

        # Ensure company has a domicile country *before* we generate / save a dashboard summary snapshot.
        # This prevents foreign issuers with US filings/ADRs from being plotted as US by default.
        try:
            supabase_client = (
                get_supabase_client() if context.get("source") == "supabase" else None
            )
            company_key = (
                str(company.get("id")) if context.get("source") == "fallback" else None
            )
            company = _ensure_company_country(
                company, supabase=supabase_client, company_key=company_key
            )
            context["company"] = company
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Unable to hydrate company country for filing %s summary: %s",
                filing_id,
                exc,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error resolving filing: {exc}")

    # Get document content
    local_document = _ensure_local_document(context, settings)
    progress_cache[str(filing_id)] = "Extracting Financial Data..."
    statements = fallback_financial_statements.get(str(filing_id))
    if statements is None and context.get("source") == "supabase":
        try:
            supabase = get_supabase_client()
            statement_response = (
                supabase.table("financial_statements")
                .select("*")
                .eq("filing_id", filing.get("id"))
                .limit(1)
                .execute()
            )
            if statement_response.data:
                statements = statement_response.data[0]
                fallback_financial_statements[str(filing_id)] = statements
        except Exception as stmt_exc:  # noqa: BLE001
            if is_supabase_table_missing_error(stmt_exc):
                statements = fallback_financial_statements.get(str(filing_id))
            else:
                logger.warning(
                    "Unable to load Supabase financial statements for %s: %s",
                    filing_id,
                    stmt_exc,
                )

    document_text = None
    if local_document and local_document.exists():
        try:
            document_text = _load_document_excerpt(local_document)
        except Exception as read_exc:
            logger.warning(f"Failed to process local document for summary: {read_exc}")

    if not document_text:
        # Fallback to financial statements
        if statements:
            try:
                safe_statements = jsonable_encoder(statements)
                document_text = json.dumps(safe_statements, indent=2)
            except (TypeError, ValueError) as serialization_error:
                logger.warning(
                    "Failed to serialize financial statements for filing %s: %s",
                    filing_id,
                    serialization_error,
                )
                document_text = json.dumps(
                    jsonable_encoder({"statements": statements}), indent=2
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="No document content available for summarization",
            )

    logger.debug(
        "Generating summary for filing %s (%s) using document=%s statements=%s",
        filing_id,
        filing.get("filing_type"),
        bool(local_document),
        bool(statements),
    )

    # Generate summary with Gemini
    try:
        if not settings.gemini_api_key or settings.gemini_api_key.strip() == "":
            raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")

        gemini_client = get_gemini_client()
        summary_request_id = uuid4().hex
        set_usage_context = getattr(gemini_client, "set_usage_context", None)
        if callable(set_usage_context):
            try:
                set_usage_context(
                    {
                        "request_id": summary_request_id,
                        "request_type": "filing_summary",
                        "user_id": user.id,
                        "filing_id": str(filing_id),
                        "company_id": str(company.get("id")) if company.get("id") else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Unable to set Gemini usage context: %s", exc)

        filing_type = filing.get("filing_type", "")
        filing_date = filing.get("filing_date", "")
        company_name = company.get("name", company.get("ticker", "Unknown"))

        financial_snapshot = _build_financial_snapshot(statements)
        calculated_metrics = _build_calculated_metrics(statements)

        # Extract user's weighting preference from health_rating settings
        weighting_preset = None
        if preferences and preferences.health_rating:
            weighting_preset = preferences.health_rating.primary_factor_weighting

        # Optional AI growth assessment (disabled by default for speed)
        ai_growth_assessment = None
        if settings.enable_growth_assessment:
            try:
                progress_cache[str(filing_id)] = "Analyzing Growth Potential..."
                # Build comprehensive ratios dict for growth context
                ratios_for_growth = {}
                if calculated_metrics.get("operating_margin") is not None:
                    ratios_for_growth["operating_margin"] = (
                        calculated_metrics["operating_margin"] / 100
                    )
                if calculated_metrics.get("net_margin") is not None:
                    ratios_for_growth["net_margin"] = (
                        calculated_metrics["net_margin"] / 100
                    )
                if calculated_metrics.get("revenue_growth_yoy") is not None:
                    ratios_for_growth["revenue_growth_yoy"] = (
                        calculated_metrics["revenue_growth_yoy"] / 100
                    )
                if calculated_metrics.get("fcf_margin") is not None:
                    ratios_for_growth["fcf_margin"] = (
                        calculated_metrics["fcf_margin"] / 100
                    )
                if calculated_metrics.get("gross_margin") is not None:
                    ratios_for_growth["gross_margin"] = (
                        calculated_metrics["gross_margin"] / 100
                    )
                ai_growth_assessment = generate_growth_assessment(
                    filing_text=document_text,
                    company_name=company_name,
                    weighting_preference=weighting_preset,
                    ratios=ratios_for_growth,
                )
                logger.info(
                    f"AI growth assessment: score={ai_growth_assessment.get('score')}, description={ai_growth_assessment.get('description')}"
                )
            except Exception as growth_err:
                logger.warning(f"AI growth assessment failed: {growth_err}")
                ai_growth_assessment = None

        # Pre-calculate health score BEFORE generating summary so we can inject it into the prompt
        pre_calculated_health = _compute_health_score_data(
            calculated_metrics,
            weighting_preset=weighting_preset,
            ai_growth_assessment=ai_growth_assessment,
        )
        logger.debug(
            "Pre-calculated health score for %s: %s",
            filing_id,
            {
                "overall_score": pre_calculated_health.get("overall_score")
                if pre_calculated_health
                else None,
                "score_band": pre_calculated_health.get("score_band")
                if pre_calculated_health
                else None,
            },
        )
        pre_calculated_score = (
            pre_calculated_health.get("overall_score")
            if pre_calculated_health
            else None
        )
        pre_calculated_band = (
            pre_calculated_health.get("score_band") if pre_calculated_health else None
        )

        progress_cache[str(filing_id)] = "Analyzing Risk Factors..."
        metrics_lines = _build_key_metrics_block(
            calculated_metrics,
            target_length=target_length,
            include_health_rating=include_health_rating,
            health_score_data=pre_calculated_health,
        )
        token_budget: Optional[TokenBudget] = _summary_token_budget()
        if token_budget.total_tokens <= 0:
            token_budget = None
        max_output_tokens = _summary_max_output_tokens()
        context_excerpt = (
            document_text
            if len(document_text) <= MAX_GEMINI_CONTEXT_CHARS
            else document_text[:MAX_GEMINI_CONTEXT_CHARS]
        )
        truncated_note = (
            ""
            if len(context_excerpt) == len(document_text)
            else "\n\nNote: Filing text truncated to fit model context."
        )
        risk_factors_excerpt = _extract_labeled_excerpt(
            document_text, "RISK FACTORS", max_chars=15_000
        )
        risk_factors_block = (
            f"\n\nRISK FACTORS (FILING EXCERPT - USE THIS FOR THE 'Risk Factors' SECTION):\n{risk_factors_excerpt}\n"
            if risk_factors_excerpt
            else ""
        )
        company_label = company.get("name") or company.get("ticker") or "the company"
        preference_block = _build_preference_instructions(preferences, company_label)

        # Extract persona name if a persona is selected
        investor_focus = (
            preferences.investor_focus.strip()
            if preferences and preferences.investor_focus
            else None
        )
        selected_persona_name = _extract_persona_name(investor_focus)

        if include_health_rating:
            progress_cache[str(filing_id)] = "Computing Health Score..."

        # Convert full persona name to persona_id for health rating instructions
        persona_id_for_health = None
        if selected_persona_name:
            # Map full names to persona IDs
            name_to_id = {
                "warren buffett": "buffett",
                "charlie munger": "munger",
                "benjamin graham": "graham",
                "peter lynch": "lynch",
                "ray dalio": "dalio",
                "cathie wood": "wood",
                "joel greenblatt": "greenblatt",
                "john bogle": "bogle",
                "howard marks": "marks",
                "bill ackman": "ackman",
            }
            persona_id_for_health = name_to_id.get(selected_persona_name.lower())

        health_config = None
        health_rating_block = None
        if include_health_rating:
            health_config, health_rating_block = _build_health_rating_instructions(
                preferences,
                company_label,
                persona_id_for_health,
                target_length=target_length,
            )
        health_directives_section = ""
        if health_rating_block:
            health_directives_section = (
                f"\n HEALTH RATING DIRECTIVES\n {health_rating_block}\n"
            )
        section_descriptions: List[Tuple[str, str]] = []

        # Section budgets (body words, headings excluded) for enforcing the fixed distribution.
        # User requirement: keep each section within ±10 words of its budget.
        section_budget_tolerance = 10
        section_budgets: Dict[str, int] = {}
        if target_length and target_length > 0:
            section_budgets = _calculate_section_word_budgets(
                int(target_length), include_health_rating=include_health_rating
            )

        health_budget = int(section_budgets.get("Financial Health Rating", 0) or 0)
        exec_budget = int(section_budgets.get("Executive Summary", 0) or 0)
        perf_budget = int(section_budgets.get("Financial Performance", 0) or 0)
        mdna_budget = int(section_budgets.get("Management Discussion & Analysis", 0) or 0)
        risk_budget = int(section_budgets.get("Risk Factors", 0) or 0)
        key_metrics_budget = int(section_budgets.get("Key Metrics", 0) or 0)
        closing_budget = int(section_budgets.get("Closing Takeaway", 0) or 0)

        def _budget_sentence(section: str, budget: int) -> str:
            if budget <= 0:
                return ""
            tol = _section_budget_tolerance_words(
                int(budget), max_tolerance=int(section_budget_tolerance)
            )
            return (
                f"LENGTH TARGET: ~{budget} words for this section body "
                f"(acceptable range: {max(1, budget - tol)}–{budget + tol})."
            )

        health_budget_sentence = _budget_sentence("Financial Health Rating", health_budget)
        exec_budget_sentence = _budget_sentence("Executive Summary", exec_budget)
        perf_budget_sentence = _budget_sentence("Financial Performance", perf_budget)
        mdna_budget_sentence = _budget_sentence(
            "Management Discussion & Analysis", mdna_budget
        )
        risk_budget_sentence = _budget_sentence("Risk Factors", risk_budget)
        key_metrics_budget_sentence = _budget_sentence("Key Metrics", key_metrics_budget)
        risk_budget_sentence_block = (
            f"{risk_budget_sentence}\n" if risk_budget_sentence else ""
        )
        if health_rating_block:
            if pre_calculated_score is not None and pre_calculated_band:
                health_rating_description = (
                    f"!!! MANDATORY SCORE - DO NOT CHANGE !!!\n"
                    f"THE FINANCIAL HEALTH SCORE IS PRE-CALCULATED: {pre_calculated_score:.1f}/100 - {pre_calculated_band}\n\n"
                    f"YOU MUST WRITE EXACTLY: '{pre_calculated_score:.0f}/100 - {pre_calculated_band}'\n\n"
                    f"CRITICAL RULES:\n"
                    f"1. The score is {pre_calculated_score:.1f} - DO NOT calculate a different score\n"
                    f"2. DO NOT write 1/100, 62/100, or ANY other score - ONLY {pre_calculated_score:.0f}/100\n"
                    f"3. The band is '{pre_calculated_band}' - use this EXACT label\n"
                    f"4. Start the section with: '{pre_calculated_score:.0f}/100 - {pre_calculated_band}. ...'\n"
                    f"5. Then EXPLAIN why this score was assigned based on the metrics.\n"
                    f"   - {health_budget_sentence or 'Write a cohesive paragraph of 6-8 sentences (~90-130 words).'}\n"
                    f"   - Use specific figures (margins, cash flow, leverage/liquidity).\n"
                    f"   - Avoid one-sentence paragraphs and disconnected one-liners.\n"
                    f"   - End with one sentence that sets up the operating analysis that follows.\n\n"
                    f"FORBIDDEN: Calculating your own score. The score {pre_calculated_score:.1f} is mathematically computed from actual financial ratios.\n"
                    f"NO letter grades (A, B, C, D). Use the numeric score and band label only."
                )
            else:
                health_rating_description = (
                    "Provide the 0-100 score with descriptive label (Very Healthy 85-100, Healthy 70-84, Watch 50-69, At Risk 0-49). "
                    "NO letter grades (A, B, C, D). Format: '72/100 - Healthy'. "
                    "Explain why the score landed there with specific metrics: margins, cash flow, leverage, liquidity. "
                    "A company with 60%+ margins should score 70+ unless there are severe balance sheet issues.\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    f"- {health_budget_sentence or 'After the score line, write 6-8 sentences (~90-130 words) as one cohesive paragraph.'}\n"
                    "- Use at least 3 concrete figures (%, $ amounts, ratios).\n"
                    "- Avoid one-sentence paragraphs and disconnected one-liners.\n"
                    "- End with one sentence that sets up the operating analysis that follows."
                )
            section_descriptions.append(
                ("Financial Health Rating", health_rating_description)
            )
        section_descriptions.extend(
            [
                (
                    "Executive Summary",
                    "THIS IS THE HERO SECTION - the premium insight users pay for.\n"
                    f"{exec_budget_sentence or 'LENGTH TARGET: 80-110 words for this section body.'}\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    "- Write 2 cohesive paragraphs (2-4 sentences each).\n"
                    "- Avoid one-sentence paragraphs and staccato one-liners.\n"
                    "- End with a sentence that sets up the Financial Performance section.\n\n"
                    "NUMBERS DISCIPLINE (MANDATORY):\n"
                    "- Keep this section mostly qualitative. Use at most 1-2 anchor figures total.\n"
                    "- Do NOT stack multiple metrics in a single sentence; save density for Financial Performance / Key Metrics.\n\n"
                    "Write a compelling, substantive investment thesis that:\n"
                    "1. OPENS with your conviction level and stance (bullish/bearish/neutral with HIGH/MEDIUM/LOW conviction)\n"
                    "2. SYNTHESIZES the investment case - why does this company matter RIGHT NOW?\n"
                    "3. IDENTIFIES the key narrative driving the stock (e.g., 'AI infrastructure play', 'turnaround story', 'secular growth compounder')\n"
                    "4. ADDRESSES the core tension - what's the bull case vs bear case in 1-2 sentences each?\n"
                    "5. PROVIDES differentiated insight - what is the market missing or mispricing?\n"
                    "6. STATES clear catalysts or risks that could change the thesis\n\n"
                    "This should read like a PREMIUM hedge fund memo opening - sharp, opinionated, and actionable.\n"
                    "The reader should understand your COMPLETE investment view from this section alone.\n"
                    "Use strategic language: 'The market is underappreciating...', 'The key unlock is...', 'What makes this interesting is...'\n\n"
                    "CRITICAL: Every sentence MUST be complete. End with a clear, actionable stance. "
                    "Do NOT use vague phrases like 'I want to see...' or 'I need to determine...' - TAKE A POSITION.",
                ),
                (
                    "Financial Performance",
                    f"{perf_budget_sentence or 'Quantitative analysis (100-135 words).'}\n"
                    "Cover KEY metrics:\n"
                    "- Revenue with YoY% change and mix shifts (e.g., Mobility vs Delivery)\n"
                    "- Margin bridge: revenue growth → operating margin → net margin gap (explain drivers)\n"
                    "- Cash conversion: OCF to FCF, QoQ changes, capex context\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    "- Write 2 paragraphs (no bullets in the body).\n"
                    "- Paragraph 1: revenue + margins + the operating vs net bridge.\n"
                    "- Paragraph 2: cash conversion + earnings quality + sustainability.\n\n"
                    "FLOW: Connect numbers: 'Revenue grew X% but margins compressed Y% due to Z; cash conversion improved/eroded because...'.\n"
                    "NUMBERS DISCIPLINE: Do not list more than 2 metrics in any single sentence; tie each figure to a cause-and-effect interpretation.\n"
                    "Explain what numbers mean for sustainability and earnings quality.\n\n"
                    "FORBIDDEN: 'Additional detail covers...', 'Capital allocation remarks...' - write real analysis.",
                ),
                (
                    "Management Discussion & Analysis",
                    f"{mdna_budget_sentence or 'Expanded management assessment (125-175 words).'}\n"
                    "Focus on:\n"
                    "1. Strategy and capital deployment priorities (R&D, incentives, capex, buybacks/M&A)\n"
                    "2. Alignment check: do the claims MATCH operating margin trajectory and cash conversion?\n"
                    "3. Earnings quality: reconcile operating income vs net income and call out one-offs\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    "- Write 2 cohesive paragraphs.\n"
                    "- Avoid slogan-y one-liners; integrate conclusions into the paragraph.\n"
                    "- End with a sentence that naturally leads into the Risk Factors section.\n\n"
                    "FLOW: 'Management stated X → results show Y → this implies Z for margins/cash/investment pacing.'\n\n"
                    "CRITICAL: This must read like a real filing summary, not disconnected sentences.",
                ),
                (
                    "Risk Factors",
                    "2-3 MATERIAL, company-specific risks (concise but substantive).\n"
                    f"{risk_budget_sentence_block}"
                    "Each MUST:\n"
                    "1. Have a clear name that includes the *specific driver* (segment/product/platform/regulation). Avoid generic labels like 'Margin Compression Risk' unless you tie it to a named driver (e.g., TAC, capex, incentives, insurance).\n"
                    "2. Be 2-3 sentences with concrete mechanisms and quantified impact where possible\n"
                    "3. Be specific to THIS business model (not generic macro filler)\n"
                    "4. Be distinct: no duplicate names or overlapping drivers\n"
                    "5. Be grounded in the filing's RISK FACTORS excerpt in the prompt (do not invent risks)\n"
                    "6. For EACH risk, cite (a) a filing-specific driver from the excerpt and (b) at least one numeric metric from this memo (margins, OCF→FCF, capex, cash vs liabilities).\n"
                    "7. If a RISK FACTORS excerpt is provided above, include a SHORT verbatim quote (4-10 words) from that excerpt in quotation marks inside EACH risk to prove grounding.\n"
                    "Format: **Risk Name**: Explanation with specifics.\n"
                    "Skip generic macro risks unless the filing clearly makes them company-specific.",
                ),
                (
                    "Key Metrics",
                    "Scannable data appendix (arrow format).\n"
                    f"{key_metrics_budget_sentence or ''}\n"
                    "CRITICAL: This section should be ~10% of the total memo length (fixed distribution). "
                    "Include ENOUGH metric rows to match the Key Metrics word budget; do not keep it artificially short.\n\n"
                    "FORMAT RULES:\n"
                    "- Use arrow lines only (start each line with '→ ').\n"
                    "- NO narrative paragraphs, NO emojis, NO formulas/equations (no '=' signs).\n"
                    "- Use ONLY metrics provided in the 'KEY METRICS' reference block above (do not invent numbers).\n"
                    "- If a metric is missing, OMIT the line (no 'N/A' placeholders).\n\n"
                    "SUGGESTED ROWS (include what is available):\n"
                    "→ Revenue: $X.XB\n"
                    "→ Revenue YoY: X.X%\n"
                    "→ Gross Margin: X.X%\n"
                    "→ Operating Margin: X.X%\n"
                    "→ Net Margin: X.X%\n"
                    "→ Operating Cash Flow: $X.XB\n"
                    "→ Free Cash Flow: $X.XB\n"
                    "→ FCF Margin: X.X%\n"
                    "→ Cash + Securities: $X.XB\n"
                    "→ Total Debt: $X.XB\n"
                    "→ Current Ratio: X.Xx\n\n"
                    "If Health Rating is enabled, also include a sub-block:\n"
                    "Health Score Drivers:\n"
                    "→ Profitability: ...\n"
                    "→ Cash conversion: ...\n"
                    "→ Balance sheet: ...\n"
                    "→ Liquidity: ...\n",
                ),
                _build_closing_takeaway_description(
                    selected_persona_name,
                    company_name,
                    target_length=target_length,
                    persona_requested=bool(investor_focus),
                    budget_words=closing_budget,
                    budget_tolerance=section_budget_tolerance,
                ),
            ]
        )
        section_requirements = "\n".join(
            f"## {title}\n{description}" for title, description in section_descriptions
        )

        tone = preferences.tone or "objective"
        detail_level = preferences.detail_level or "comprehensive"
        output_style = preferences.output_style or "paragraph"

        # Build no-persona block for objective analysis only when the user did NOT provide any persona.
        if investor_focus:
            no_persona_block = ""
        else:
            no_persona_block = """
=== NO PERSONA MODE - OBJECTIVE ANALYSIS ===
CRITICAL: No investor persona was selected. You MUST write as a NEUTRAL PROFESSIONAL ANALYST.

FORBIDDEN - NEVER USE:
- First-person language: 'I', 'my view', 'I would', 'I believe', 'my conviction', 'I see'
- Famous investor voices: No Buffett ('wonderful business', 'moat', 'owner earnings'), no Munger ('invert'),
  no Graham ('margin of safety'), no Lynch ('ten-bagger'), no Ackman, no Dalio, etc.
- Folksy analogies or colorful investor expressions
- Investment club language ('I would buy/sell/hold')

REQUIRED - ALWAYS USE:
- Third-person objective language: 'The analysis indicates...', 'The data suggests...', 'This company demonstrates...'
- Professional research analyst tone
- Quantitative focus: revenue growth %, margins, ROE, valuation multiples
- Evidence-based conclusions tied directly to financial metrics

EXAMPLE CORRECT PHRASING:
- 'The company's 35% gross margin expansion indicates operational improvement.'
- 'Revenue growth of 22% YoY suggests strong market positioning.'
- 'Based on the financial data, a Hold rating appears warranted.'

EXAMPLE INCORRECT PHRASING (DO NOT USE):
- 'I would hold this stock because...' (first person)
- 'This is a wonderful business with a wide moat.' (Buffett persona)
- 'Inverting the question, what could go wrong?' (Munger persona)
=== END NO PERSONA MODE ===
"""

        # Build the opening identity based on whether a persona is selected
        if selected_persona_name:
            identity_block = f"""You are a senior analyst at a top-tier hedge fund writing a high-conviction briefing for portfolio managers.
You are adopting the persona of {selected_persona_name}. Write as if you ARE {selected_persona_name}.
Your goal is to provide actionable, differentiated insight, not just a summary of facts."""
        elif investor_focus:
            identity_block = """You are a senior analyst writing a high-conviction briefing for portfolio managers.
You are adopting the investor persona described in the user customization requirements above. Write as if you ARE that persona.
Your goal is to provide actionable, differentiated insight, not just a summary of facts."""
        else:
            identity_block = """=== CRITICAL: NO PERSONA MODE ===
YOU ARE A NEUTRAL, OBJECTIVE FINANCIAL ANALYST. 

ABSOLUTE PROHIBITION - READ THIS FIRST:
- You have NOT been assigned any investor persona
- Do NOT adopt ANY famous investor's voice or perspective
- Do NOT use first-person language ('I', 'my view', 'I would', 'I believe', 'my conviction')
- Do NOT imitate: Warren Buffett, Charlie Munger, Peter Lynch, Benjamin Graham, Howard Marks, Bill Ackman, Ray Dalio, Cathie Wood, John Bogle, Joel Greenblatt, or ANY other investor

REQUIRED WRITING STYLE:
- Write in THIRD PERSON only ('The analysis indicates...', 'The data suggests...', 'The company demonstrates...')
- Use professional equity research tone (like Goldman Sachs or Morgan Stanley analyst reports)
- Focus on quantitative metrics and evidence-based conclusions

THIS IS YOUR PRIMARY DIRECTIVE. VIOLATION = INVALID OUTPUT.
=== END CRITICAL INSTRUCTION ===

You are a professional equity research analyst writing a financial briefing.
Your goal is to provide actionable, differentiated insight, not just a summary of facts."""

        section_header_example = (
            "Financial Health Rating, Executive Summary, Financial Performance, etc."
        )
        if not include_health_rating:
            section_header_example = "Executive Summary, Financial Performance, etc."
        health_constraint_line = (
            "- Do NOT repeat the Financial Health Rating in the Key Metrics section.\n"
            if include_health_rating
            else ""
        )

        anti_repetition_rules = ""
        if target_length and int(target_length) >= 1000:
            anti_repetition_rules = (
                " - NO REPETITION: Do not repeat the same sentence (or near-identical phrasing) to hit length.\n"
                " - VARIETY: Avoid looping discourse openers like 'From my perspective' / 'In my view' / 'For me'; use them sparingly.\n"
                " - EXPAND WITH NEW ANGLES: Add depth via drivers (pricing, incentives, mix), unit economics, segment/geography, working capital, and cycle sensitivity — not re-statements.\n"
                " - KEY METRICS LENGTH: Keep Key Metrics compact; do not add long watch lists or repeated rows.\n"
            )

        company_profile_lines: List[str] = []
        if company.get("ticker"):
            company_profile_lines.append(f"- Ticker: {company.get('ticker')}")
        if company.get("exchange"):
            company_profile_lines.append(f"- Exchange: {company.get('exchange')}")
        if company.get("sector"):
            company_profile_lines.append(f"- Sector: {company.get('sector')}")
        if company.get("industry"):
            company_profile_lines.append(f"- Industry: {company.get('industry')}")
        if company.get("country"):
            company_profile_lines.append(f"- Country: {company.get('country')}")
        company_profile_block = (
            f"\nCOMPANY PROFILE (Reference):\n{chr(10).join(company_profile_lines)}\n"
            if company_profile_lines
            else ""
        )

        base_prompt = f"""
{identity_block}
Analyze the following filing for {company_name} ({filing_type}, {filing_date}).
{company_profile_block}

CONTEXT:
{context_excerpt}{truncated_note}

FINANCIAL SNAPSHOT (Reference only):
{financial_snapshot}

KEY METRICS (Use these for calculations and evidence):
{metrics_lines}
{risk_factors_block}

INSTRUCTIONS:
1. Tone: {tone.title()} (Professional, Insightful, Direct)
2. Detail Level: {detail_level.title()}
3. Output Style: {output_style.title()}
4. Target Length: {target_length} words (STRICT: ±10 words tolerance)

STRUCTURE & CONTENT REQUIREMENTS:
{section_requirements}
{health_directives_section}
{preference_block}

=== MANDATORY FORMATTING RULES (CRITICAL) ===
1. Each section MUST start on its own line with the ## header
2. There MUST be a blank line BEFORE each section header
3. There MUST be a blank line AFTER each section header before the content
4. NEVER put a section header inline with content from the previous section

CORRECT FORMAT:
```
...previous section content ends here.

## Executive Summary

This section content starts on a new line after the header.
```

INCORRECT FORMAT (DO NOT DO THIS):
```
...previous section content ends here. ## Executive Summary This section content...
```

EVERY section header ({section_header_example}) MUST:
- Be on its own line
- Start with "## "
- Have blank lines before and after
=== END FORMATTING RULES ===

=== USER CUSTOMIZATION PRIORITY (READ CAREFULLY) ===
If the user has specified ANY custom preferences (persona, tone, focus areas, detail level, output style,
health score configuration), these OVERRIDE default behavior. Your output MUST:

1. PERSONA/VIEWPOINT: If specified, maintain this voice in EVERY section, not just the introduction.
   - Use first-person language throughout
   - Apply their specific mental models and vocabulary
   - The Closing Takeaway MUST sound exactly like the persona

2. HEALTH SCORE: If configured, follow the user's framework, weighting, risk tolerance, and display format EXACTLY.
   - Score must reflect their specified primary factor weighting
   - Apply their specified risk tolerance when penalizing/rewarding
   - Use ONLY their selected display format (score only, pillars, traffic light, etc.)

3. TONE/DETAIL/STYLE: Match user specifications consistently throughout. No switching mid-document.

4. FOCUS AREAS: If specified, these topics get PRIORITY coverage with dedicated sections.

Failure to follow user customizations = INVALID OUTPUT. Re-read the preference block above if unsure.
=== END USER CUSTOMIZATION PRIORITY ===
{no_persona_block}
 CRITICAL RULES:
 - MAINTAIN CONSISTENT TONE throughout the entire document. Do NOT switch tones mid-document.
 - Do NOT use markdown bolding (**) within the narrative body except where explicitly required (e.g., Risk Factors labels).
 - Ensure every claim is backed by the provided text or metrics.
 - If data is missing, omit that data point rather than saying "not disclosed" or "not available".
 - SYNTHESIZE, DO NOT SUMMARIZE. Tell us what the numbers mean, not just what they are.
 - SPECIFY TIME PERIODS: Always label figures with their time period (FY24, Q3 FY25, TTM, etc.).
 - NO REDUNDANCY: Avoid repeating the same metric across multiple sections. Executive Summary should be mostly qualitative; keep dense figures in Financial Performance / Key Metrics.
{anti_repetition_rules}
 - **SUSTAINABILITY**: Do NOT mention sustainability or ESG efforts unless they are a primary revenue driver (e.g., for a solar company). For most companies, this is fluff.
 - **MD&A**: Do NOT say "Management discusses..." or "In the MD&A section...". Just state the facts found there.
 - USE TRANSITIONS: Connect sections logically. Each section should flow naturally from the previous one.

=== #1 PRIORITY: SENTENCE COMPLETION (WITHIN THE STRICT WORD BAND) ===
THIS IS YOUR SINGLE MOST IMPORTANT RULE.

FUNDAMENTAL PRINCIPLE: You MAY use the full ±10-word tolerance to finish a sentence, but you MUST still land inside the required band.

EVERY SENTENCE MUST BE COMPLETE. ZERO EXCEPTIONS. ZERO TOLERANCE.

FORBIDDEN CUT-OFF PATTERNS (YOUR OUTPUT WILL BE REJECTED IF ANY APPEAR):
   - "...and." or "...and the..." (INCOMPLETE - finish the thought)
   - "...give." or "...competitors give." (INCOMPLETE - what do they give?)
   - "...of." or "...percentage of." (INCOMPLETE - percentage of what?)
   - "...$1." or "...$3." (CUT-OFF NUMBER - write the full amount)
   - "...as I." or "...as wide as I." (INCOMPLETE - finish the sentence)
   - "...in accelerated." (INCOMPLETE - accelerated what?)
   - "...I would..." (INCOMPLETE - finish with what you would do)
   - "...which is..." (INCOMPLETE - which is what?)
   - "...because I..." (INCOMPLETE - because you what?)

REAL EXAMPLES OF CUT-OFFS TO AVOID:
   BAD: "...the cyclical nature of the semiconductor industry and." 
   GOOD: "...the cyclical nature of the semiconductor industry and its potential impact on long-term returns."
   
   BAD: "...represent a significant percentage of."
   GOOD: "...represent a significant percentage of total revenue, creating concentration risk."
   
   BAD: "...Capital expenditures total $1."
   GOOD: "...Capital expenditures total $1.2B, directed primarily toward manufacturing capacity."
   
   BAD: "...the moat may not be as wide as I."
   GOOD: "...the moat may not be as wide as I would prefer for a long-term holding."

HOW TO HANDLE WORD COUNT VS COMPLETION:
- If you're near the limit and need a few words to finish a sentence, FINISH IT — then tighten earlier sentences so the TOTAL stays within ±10 words.
- If you're at the limit, DO NOT start a new thought you can't finish.
- Plan your sections so you have room to complete the Closing Takeaway fully.

BEFORE SUBMITTING - MANDATORY CHECK:
Read the LAST WORD of EVERY sentence. If it's an article, preposition, conjunction, pronoun, or incomplete number, REWRITE IT.

=== END SENTENCE COMPLETION REQUIREMENT ===

NARRATIVE QUALITY:
- Start each section with a clear topic sentence that states the key insight.
- End each section with a forward-looking implication or action item that is COMPLETE.
- Do NOT end sections with a string of short generic one-liners ("staccato" filler). If you need words, expand with a cohesive paragraph tied to the specific metrics above.
- Avoid starting consecutive sentences with the same word.
- Vary sentence length and structure for readability.
- THE LAST SENTENCE OF EACH SECTION MUST BE A COMPLETE THOUGHT ending in a period, question mark, or exclamation point.
- If you write a subordinate clause (starting with "which", "that", "although", "while", "but", "however"), you MUST complete it.

=== FINAL PRE-SUBMISSION CHECKLIST (MANDATORY) ===
Before you output anything, verify:
[ ] Every sentence ends with . ? or ! (not with "and", "the", "of", "I", etc.)
[ ] All dollar amounts are complete (e.g., "$18.77B" not "$18.")
[ ] The Closing Takeaway section is FULLY complete with a clear verdict
[ ] No section ends mid-thought
[ ] If you're over the word count, that's OK - incomplete sentences are NOT OK
=== END CHECKLIST ===
"""

        # Enforce per-summary token budget by trimming the CONTEXT block (input)
        # before we make any Gemini calls.
        if token_budget:
            max_prompt_tokens = max(0, token_budget.remaining_tokens - max_output_tokens)
            max_prompt_chars = max_prompt_tokens * CHARS_PER_TOKEN_ESTIMATE
            if max_prompt_chars > 0 and len(base_prompt) > max_prompt_chars:
                base_prompt = _truncate_prompt_to_token_budget(
                    base_prompt,
                    max_prompt_chars=max_prompt_chars,
                    budget_note="\n\nNote: Filing text truncated to fit per-summary token budget.",
                )

        progress_cache[str(filing_id)] = "Synthesizing Investor Insights..."
        quality_validators: List[Callable[[str], Optional[str]]] = [
            _make_section_completeness_validator(
                include_health_rating, target_length=target_length
            )
        ]
        quality_validators.append(
            _make_no_extra_sections_validator(include_health_rating)
        )
        quality_validators.append(
            _make_risk_specificity_validator(risk_factors_excerpt=risk_factors_excerpt)
        )
        quality_validators.append(_make_numbers_discipline_validator(target_length))
        quality_validators.append(
            _make_closing_recommendation_validator(
                persona_requested=bool(investor_focus), company_name=company_name
            )
        )
        if target_length:
            quality_validators.append(
                _make_section_balance_validator(include_health_rating, target_length)
            )

        summary_text = _generate_summary_with_quality_control(
            gemini_client,
            base_prompt,
            target_length=target_length,
            quality_validators=quality_validators,
            filing_id=filing_id,
            timeout_seconds=SUMMARY_TOTAL_TIMEOUT_SECONDS,
            token_budget=token_budget,
            max_output_tokens=max_output_tokens,
        )

        progress_cache[str(filing_id)] = "Polishing Output..."
        # Post-processing to ensure structure
        summary_text = _fix_inline_section_headers(
            summary_text
        )  # CRITICAL: Fix headers appearing inline first
        summary_text = _normalize_section_headings(summary_text, include_health_rating)
        summary_text = _fix_trailing_ellipsis(
            summary_text
        )  # Fix sentences ending with ...
        summary_text = _validate_complete_sentences(
            summary_text
        )  # Fix other incomplete sentences
        summary_text = _remove_filler_phrases(
            summary_text
        )  # Remove filler phrases that slipped through
        summary_text = _remove_generic_heuristic_paragraphs(summary_text)
        summary_text = _normalize_casing(
            summary_text
        )  # Convert shouty/all-caps body text to sentence case
        summary_text = _strip_directive_lines(summary_text)
        summary_text = _dedupe_consecutive_sentences(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)
        # NOTE: _ensure_required_sections() moved to end to avoid duplicate Closing Takeaway
        if target_length:
            summary_text = _finalize_length_band(
                summary_text, target_length, tolerance=10
            )
            # NOTE: Removed duplicate _ensure_required_sections() call here to avoid duplicate Closing Takeaway
            summary_text = _finalize_length_band(
                summary_text, target_length, tolerance=10
            )
            # Final pass to normalize headings and length in case prior rewrites removed structure
            summary_text = _fix_inline_section_headers(summary_text)
            summary_text = _normalize_section_headings(
                summary_text, include_health_rating
            )
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                health_score_data=pre_calculated_health,
                company_name=company_name,
                risk_factors_excerpt=risk_factors_excerpt,
                health_rating_config=health_config,
                persona_name=selected_persona_name,
                persona_requested=bool(investor_focus),
                target_length=target_length,
            )
            summary_text = _finalize_length_band(
                summary_text, target_length, tolerance=10
            )
            summary_text = _force_final_band(
                summary_text, target_length, tolerance=10, allow_padding=False
            )
            summary_text = _clamp_to_band(
                summary_text,
                target_length - 10,
                target_length + 10,
                allow_padding=False,
            )
        else:
            # When no target_length, only backfill sections if the model produced headers
            if re.search(r"^\s*##\s", summary_text or "", re.MULTILINE):
                summary_text = _ensure_required_sections(
                    summary_text,
                    include_health_rating=include_health_rating,
                    metrics_lines=metrics_lines,
                    calculated_metrics=calculated_metrics,
                    health_score_data=pre_calculated_health,
                    company_name=company_name,
                    risk_factors_excerpt=risk_factors_excerpt,
                    health_rating_config=health_config,
                    persona_name=selected_persona_name,
                    persona_requested=bool(investor_focus),
                    target_length=target_length,
                )

        # Final ellipsis cleanup after all length adjustments
        summary_text = _fix_trailing_ellipsis(summary_text)
        summary_text = _remove_filler_phrases(summary_text)  # Final filler removal
        summary_text = _remove_generic_heuristic_paragraphs(summary_text)
        summary_text = _normalize_casing(summary_text)
        summary_text = _strip_directive_lines(summary_text)
        summary_text = _dedupe_consecutive_sentences(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)

        # Fix health score if AI generated a different score than pre-calculated
        if pre_calculated_score is not None and pre_calculated_band:
            summary_text = _fix_health_score_in_summary(
                summary_text,
                pre_calculated_score,
                pre_calculated_band,
            )

        if target_length:
            summary_text = _force_final_band(summary_text, target_length, tolerance=10)

        # Use pre-calculated health score data (computed before summary generation)
        health_score_data = pre_calculated_health
        has_health_section = bool(
            re.search(
                r"^\s*##\s*Financial Health Rating",
                summary_text or "",
                re.IGNORECASE | re.MULTILINE,
            )
        )

        # Final cleanup after any length adjustments to remove stray directives
        if include_health_rating and (target_length is not None or has_health_section):
            summary_text = _ensure_health_rating_section(
                summary_text,
                health_score_data or {},
                calculated_metrics,
                company_name,
                health_rating_config=health_config,
                target_length=target_length,
            )
            summary_text = _inject_health_drivers(
                summary_text, calculated_metrics, health_score_data or {}
            )
        summary_text = _enforce_section_order(
            summary_text, include_health_rating=include_health_rating
        )
        summary_text = _strip_directive_lines(summary_text)
        summary_text = _dedupe_consecutive_sentences(summary_text)
        summary_text = _normalize_casing(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)
        if target_length:
            # Re-enforce strict word band after cleanup; padding uses substantive templates
            summary_text = _force_final_band(
                summary_text, target_length, tolerance=10, allow_padding=True
            )
            summary_text = _clamp_to_band(
                summary_text, target_length - 10, target_length + 10, allow_padding=True
            )
            # Final structural normalization in case clamps introduced inline headings
            summary_text = _fix_inline_section_headers(summary_text)
            summary_text = _normalize_section_headings(
                summary_text, include_health_rating
            )
            summary_text = _enforce_section_order(
                summary_text, include_health_rating=include_health_rating
            )
            summary_text = _normalize_casing(summary_text)

            # Extra guard for whitespace token counts (tests/UI count markdown tokens too)
            ws_upper = target_length + 10
            ws_count = len(summary_text.split())
            if ws_count > ws_upper:
                excess = ws_count - ws_upper
                target_words = max(
                    target_length - 10, _count_words(summary_text) - excess
                )
                summary_text = _truncate_text_to_word_limit(summary_text, target_words)
                summary_text = _fix_inline_section_headers(summary_text)
                summary_text = _normalize_section_headings(
                    summary_text, include_health_rating
                )
                summary_text = _enforce_section_order(
                    summary_text, include_health_rating=include_health_rating
                )

        # Final backfill to guarantee Closing Takeaway and Key Metrics survive trims
        if target_length or re.search(r"^\s*##\s", summary_text or "", re.MULTILINE):
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                health_score_data=pre_calculated_health,
                company_name=company_name,
                risk_factors_excerpt=risk_factors_excerpt,
                health_rating_config=health_config,
                persona_name=selected_persona_name,
                persona_requested=bool(investor_focus),
                target_length=target_length,
            )
            summary_text = _enforce_section_order(
                summary_text, include_health_rating=include_health_rating
            )
            if target_length:
                summary_text = _force_final_band(
                    summary_text, target_length, tolerance=10, allow_padding=True
                )

        # Final quality cleanup before enforcing the user-visible band.
        # (Do this before the last word-band pass so any removals can be compensated.)
        summary_text = _normalize_underwriting_questions_formatting(summary_text)
        summary_text = _merge_underwriting_question_lines(summary_text)
        summary_text = _relocate_underwriting_questions_to_mdna(summary_text)
        summary_text = _remove_filler_phrases(summary_text)
        summary_text = _remove_generic_heuristic_paragraphs(summary_text)
        summary_text = _dedupe_consecutive_sentences(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)

        # Final health-rating normalization (prevents dangling "68/100 -" lines and
        # keeps the section readable after trims/padding).
        if include_health_rating:
            # Even if the scorer failed (health_score_data empty), we should still fix
            # common formatting issues like "64/100 -" by inferring the band from the score.
            health_fix_data: Dict[str, Any] = dict(health_score_data or {})

            if health_fix_data.get("overall_score") is None and pre_calculated_score is not None:
                health_fix_data["overall_score"] = pre_calculated_score
            if (
                (health_fix_data.get("score_band") is None or str(health_fix_data.get("score_band") or "").strip() == "")
                and pre_calculated_band
            ):
                health_fix_data["score_band"] = pre_calculated_band

            if health_fix_data.get("overall_score") is None:
                m = re.search(
                    r"(?is)##\s*Financial\s+Health\s+Rating\b[\s\S]*?(\d{1,3})(?:\.\d+)?/100",
                    summary_text or "",
                )
                if m:
                    try:
                        health_fix_data["overall_score"] = float(m.group(1))
                    except Exception:
                        pass

            if (
                (health_fix_data.get("score_band") is None or str(health_fix_data.get("score_band") or "").strip() == "")
                and health_fix_data.get("overall_score") is not None
            ):
                try:
                    score_val = float(health_fix_data.get("overall_score"))
                    inferred = None
                    for threshold, _abbr, label in RATING_SCALE:
                        if score_val >= threshold:
                            inferred = label
                            break
                    health_fix_data["score_band"] = inferred or "At Risk"
                except Exception:
                    pass

            if health_fix_data.get("overall_score") is not None:
                summary_text = _ensure_health_rating_section(
                    summary_text,
                    health_fix_data,
                    calculated_metrics,
                    company_name,
                    health_rating_config=health_config,
                    target_length=target_length,
                )
                summary_text = _inject_health_drivers(
                    summary_text, calculated_metrics, health_fix_data
                )
                summary_text = _merge_staccato_paragraphs(summary_text)

        if target_length:
            # Final user-visible word-count enforcement (UI/tests count raw whitespace tokens).
            summary_text = _enforce_whitespace_word_band(
                summary_text, target_length, tolerance=10, allow_padding=True
            )

        # Final backfill AFTER the last band clamp to prevent short/low-quality
        # Risk Factors or Closing Takeaway from surviving trims.
        if target_length or re.search(r"^\s*##\s", summary_text or "", re.MULTILINE):
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                health_score_data=pre_calculated_health,
                company_name=company_name,
                risk_factors_excerpt=risk_factors_excerpt,
                health_rating_config=health_config,
                persona_name=selected_persona_name,
                persona_requested=bool(investor_focus),
                target_length=target_length,
            )
            summary_text = _enforce_section_order(
                summary_text, include_health_rating=include_health_rating
            )
            if target_length:
                summary_text = _enforce_whitespace_word_band(
                    summary_text, target_length, tolerance=10, allow_padding=True
                )

        # Post-band formatting: safe (does not change token/word counts meaningfully).
        summary_text = _normalize_underwriting_questions_formatting(summary_text)
        summary_text = _relocate_underwriting_questions_to_mdna(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)

        # Final guardrail: enforce the fixed section distribution after *all* cleanup.
        if target_length:
            distribution_tolerance = 10
            if int(target_length) >= int(KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS):
                # Long-form outputs can vary slightly by section; overly tight enforcement
                # encourages low-quality padding and repetition.
                distribution_tolerance = 40
            summary_text = _enforce_section_budget_distribution(
                summary_text,
                target_length=int(target_length),
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                section_tolerance=distribution_tolerance,
            )
            summary_text = _enforce_section_order(
                summary_text, include_health_rating=include_health_rating
            )
            summary_text = _ensure_final_strict_word_band(
                summary_text,
                int(target_length),
                include_health_rating=include_health_rating,
                tolerance=10,
            )

        # Final guard: ensure the document ends with punctuation for substantive outputs
        if (
            summary_text
            and _count_words(summary_text) >= 5
            and not summary_text.rstrip().endswith((".", "!", "?"))
        ):
            summary_text = summary_text.rstrip() + "."

        # Cache result
        if use_default_cache:
            fallback_filing_summaries[str(filing_id)] = summary_text

        # Log the generation event (best-effort, should never fail the request).
        record_summary_generated_event(
            summary_id=str(filing_id),
            company_id=str(company.get("id"))
            if company and company.get("id")
            else None,
            user_id=user.id,
            kind=getattr(preferences, "mode", None),
            cached=False,
            source=context.get("source"),
        )

        response_data = {
            "filing_id": filing_id,
            "summary": summary_text,
            "cached": False,
            "company_country": company.get("country"),
        }

        if health_score_data:
            response_data["health_score"] = health_score_data.get("overall_score")
            response_data["health_band"] = health_score_data.get("score_band")
            response_data["health_components"] = health_score_data.get(
                "component_scores"
            )
            response_data["health_component_weights"] = health_score_data.get(
                "component_weights"
            )
            response_data["health_component_descriptions"] = health_score_data.get(
                "component_descriptions"
            )
            response_data["health_component_metrics"] = health_score_data.get(
                "component_metrics"
            )

        return JSONResponse(content=response_data)

    except GeminiRateLimitError as rate_limit_exc:
        # Rate limit exceeded - return 429 with retry information
        progress_cache[str(filing_id)] = (
            "Rate limit exceeded - please retry in a moment"
        )

        retry_after_seconds = rate_limit_exc.retry_after or 60
        logger.warning(
            "Gemini rate limit hit for filing %s. Retry after: %s seconds. "
            "This occurred after %d retry attempts with exponential backoff.",
            filing_id,
            retry_after_seconds,
            5,  # max_retries from gemini client
        )

        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": "The AI service rate limit has been exceeded. Please wait and try again.",
                "retry_after_seconds": retry_after_seconds,
                "filing_id": str(filing_id),
                "attempts_made": 5,
            },
        ) from rate_limit_exc

    except GeminiTimeoutError as timeout_exc:
        # Request timed out
        progress_cache[str(filing_id)] = "Request timed out - please try again"
        logger.error(
            "Gemini request timed out for filing %s: %s", filing_id, timeout_exc
        )

        raise HTTPException(
            status_code=504,
            detail={
                "error": "request_timeout",
                "message": "The AI service took too long to respond. Try again or use a shorter summary length.",
                "filing_id": str(filing_id),
            },
        ) from timeout_exc

    except GeminiAPIError as api_exc:
        # Other API errors (4xx/5xx)
        progress_cache[str(filing_id)] = f"API error: {api_exc.status_code}"
        logger.error(
            "Gemini API error for filing %s: status=%s, message=%s",
            filing_id,
            api_exc.status_code,
            str(api_exc),
        )

        # Map Gemini errors to appropriate HTTP codes
        if 400 <= api_exc.status_code < 500:
            # Client error (bad request, auth failure, etc.)
            status_code = 400
            error_type = "bad_request"
            user_message = "Invalid request to AI service. Please check your inputs."
        else:
            # Server error (5xx from Gemini)
            status_code = 502  # Bad Gateway
            error_type = "upstream_service_error"
            user_message = (
                "The AI service encountered an error. Please try again later."
            )

        raise HTTPException(
            status_code=status_code,
            detail={
                "error": error_type,
                "message": user_message,
                "filing_id": str(filing_id),
                "upstream_status": api_exc.status_code,
            },
        ) from api_exc

    except TimeoutError as timeout_exc:
        # Application-level timeout (not Gemini timeout)
        progress_cache[str(filing_id)] = "Generation timed out"
        logger.error(
            "Summary generation timed out for %s after %s seconds",
            filing_id,
            SUMMARY_TOTAL_TIMEOUT_SECONDS,
        )

        raise HTTPException(
            status_code=504,
            detail={
                "error": "generation_timeout",
                "message": f"Summary generation exceeded {SUMMARY_TOTAL_TIMEOUT_SECONDS}s timeout. "
                "Try with default mode or shorter target length.",
                "filing_id": str(filing_id),
                "timeout_seconds": SUMMARY_TOTAL_TIMEOUT_SECONDS,
            },
        ) from timeout_exc

    except HTTPException:
        # Preserve intended FastAPI error responses (400/401/402/etc).
        raise

    except Exception as unexpected_exc:
        # Fallback for truly unexpected errors
        with open("debug_error.txt", "w") as f:
            f.write(f"UNEXPECTED ERROR: {unexpected_exc}\n")
            f.write(f"Type: {type(unexpected_exc)}\n")
            traceback.print_exc(file=f)

        logger.exception(
            f"Unexpected error during summary generation for filing {filing_id}"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "An unexpected error occurred. Our team has been notified.",
                "filing_id": str(filing_id),
            },
        )


@router.post("/{filing_id}/parse")
async def parse_filing(filing_id: str):
    """
    Initiate background task to parse a filing.
    Returns a task ID for tracking progress.
    """
    from app.tasks.parse import parse_document_task

    settings = get_settings()

    if not _supabase_configured(settings):
        raise HTTPException(
            status_code=404,
            detail="Filings not available without Supabase configuration",
        )

    supabase = get_supabase_client()

    # Verify filing exists
    try:
        filing_response = (
            supabase.table("filings").select("*").eq("id", filing_id).execute()
        )
        if not filing_response.data:
            raise HTTPException(status_code=404, detail="Filing not found")

        filing = filing_response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verifying filing: {str(e)}")

    # Create task
    try:
        task = parse_document_task.delay(filing_id=filing_id)

        # Store task status
        task_data = {
            "task_id": task.id,
            "task_type": "parse_document",
            "status": "pending",
            "progress": 0,
        }
        supabase.table("task_status").insert(task_data).execute()

        return {"task_id": task.id, "message": f"Started parsing filing {filing_id}"}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error starting parse task: {str(e)}"
        )


WORD_COUNT_PATTERN = re.compile(r"WORD\s+COUNT:\s*(\d+)\s*$", re.IGNORECASE)


def _extract_word_count_control(text: str) -> Tuple[str, Optional[int]]:
    """Remove control line 'WORD COUNT: ###' if present and return cleaned text with reported value."""
    stripped = text.rstrip()
    lines = stripped.splitlines()
    if not lines:
        return stripped, None
    last_line = lines[-1].strip()
    match = WORD_COUNT_PATTERN.match(last_line)
    if not match:
        return stripped, None
    reported = int(match.group(1))
    cleaned = "\n".join(lines[:-1]).rstrip()
    return cleaned, reported


def _normalize_casing(summary_text: str) -> str:
    """
    Convert shouty/all-caps body lines to sentence case while preserving headings.
    Only adjusts lines where most alphabetic characters are uppercase.
    """

    def _sentence_case(line: str) -> str:
        lowered = line.lower()
        return re.sub(
            r"(^|[\.!?])\s*([A-Z])", lambda m: m.group(1) + m.group(2).upper(), lowered
        )

    normalized_lines: List[str] = []
    for line in summary_text.splitlines():
        if line.strip().startswith("##"):
            normalized_lines.append(line)
            continue
        letters_only = "".join(c for c in line if c.isalpha())
        if not letters_only:
            normalized_lines.append(line)
            continue
        upper_ratio = sum(1 for c in letters_only if c.isupper()) / len(letters_only)
        is_all_caps = letters_only.isupper()
        # Treat lines as shouty if all caps or majority caps (>=40%)
        if is_all_caps or upper_ratio >= 0.4:
            normalized_lines.append(_sentence_case(line))
        else:
            normalized_lines.append(line)
    return "\n".join(normalized_lines)


def _strip_directive_lines(text: str) -> str:
    """
    Remove residual directive lines (e.g., "Add liquidity...", "Expand margin...") that degrade quality.
    """
    directive_patterns = [
        r"^\s*Add liquidity and leverage observations",
        r"^\s*Tie capital deployment",
        r"^\s*Clarify risk scenarios",
        r"^\s*Expand margin and cash conversion commentary",
        r"^\s*Anchor valuation view",
        r"^\s*Discuss competitive position",
        r"^\s*Highlight how revenue growth translated into operating margin movement",
        r"^\s*Highlight how leverage and liquidity shape flexibility",
        r"^\s*Address how leverage and liquidity shape flexibility",
        r"^\s*Call out whether cash conversion improved quarter over quarter and why",
        r"^\s*Point to the key catalyst or risk that could change the rating in the next 12 months",
        r"^\s*Emphasize what the market may be underpricing about cash generation durability",
        r"^\s*Note any mix shift between Mobility and Delivery that affected unit economics",
    ]
    compiled = [re.compile(pat, re.IGNORECASE) for pat in directive_patterns]
    cleaned_lines: List[str] = []
    for line in text.splitlines():
        if any(p.search(line) for p in compiled):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)

    # Remove lingering directive sentences even if embedded mid-paragraph
    phrase_patterns = [
        r"Highlight how revenue growth translated into operating margin movement over the latest period\.?",
        r"Call out whether cash conversion improved quarter over quarter and why\.?",
        r"Note any mix shift between Mobility and Delivery that affected unit economics\.?",
        r"Address how leverage and liquidity shape flexibility for capital deployment\.?",
        r"Point to the key catalyst or risk that could change the rating in the next 12 months\.?",
        r"Emphasize what the market may be underpricing about cash generation durability\.?",
    ]
    for pat in phrase_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    # Remove standalone imperative lines or bullets that start with directive verbs
    cleaned = re.sub(
        r"^\s*(?:[-•→]?\s*)?(?:Highlight|Call out|Note|Address|Point to|Emphasize)\b[^\n]*",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # Tidy up extra whitespace left after removals
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _dedupe_consecutive_sentences(text: str) -> str:
    """
    Remove consecutive duplicate sentences to reduce repetition.
    """
    if not text:
        return text

    lines = text.splitlines()
    cleaned_lines: List[str] = []

    # Track the last sentence norm to avoid stutter inside paragraphs.
    prev_sentence_norm: Optional[str] = None
    # Track the last *content line* to remove duplicated paragraphs even if separated
    # by a blank line.
    prev_content_line_norm: Optional[str] = None

    pending_blank_lines: List[str] = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            pending_blank_lines.append(line)
            continue

        # Headings reset dedupe state.
        if stripped.startswith("#"):
            cleaned_lines.extend(pending_blank_lines)
            pending_blank_lines = []
            cleaned_lines.append(line)
            prev_sentence_norm = None
            prev_content_line_norm = None
            continue

        # Deduplicate consecutive sentences within the line.
        sentences = re.split(r"(?<=[.!?])\s+", stripped)
        unique_sentences: List[str] = []
        for sent in sentences:
            norm = " ".join((sent or "").lower().split())
            if not norm:
                continue
            if norm != prev_sentence_norm:
                unique_sentences.append(sent)
            prev_sentence_norm = norm
        rebuilt_line = " ".join(unique_sentences).strip()

        # Deduplicate repeated paragraphs/lines, even when there's an empty line in between.
        content_norm = " ".join(rebuilt_line.lower().split())
        if content_norm and content_norm == prev_content_line_norm:
            pending_blank_lines = []
            continue

        cleaned_lines.extend(pending_blank_lines)
        pending_blank_lines = []
        cleaned_lines.append(rebuilt_line)
        prev_content_line_norm = content_norm

    return "\n".join(cleaned_lines).strip()


def _merge_staccato_paragraphs(summary_text: str) -> str:
    """Reduce one-line / staccato paragraphs inside narrative sections for better flow.

    This is a deterministic readability pass. It does not change word count, only
    paragraph breaks, by merging very short single-sentence paragraphs into the
    prior paragraph within the same section.
    """
    if not summary_text:
        return summary_text

    heading_regex = re.compile(r"^\s*##\s+.+")
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []
    preamble: List[str] = []
    first_heading_seen = False

    for line in summary_text.splitlines():
        if heading_regex.match(line):
            if not first_heading_seen and buffer:
                preamble = buffer[:]
                buffer = []
            first_heading_seen = True
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).rstrip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)
        else:
            preamble.append(line)

    preamble_text = "\n".join(preamble).rstrip()
    if current_heading is not None:
        sections.append((current_heading, "\n".join(buffer).rstrip()))

    if not sections:
        return summary_text

    target_sections = {
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Closing Takeaway",
    }

    def _sentence_count(paragraph: str) -> int:
        # Approximate sentence count; good enough to detect single-sentence throwaways.
        return len(re.findall(r"[.!?](?:\s|$)", paragraph or ""))

    def _is_structured_line(paragraph: str) -> bool:
        stripped = (paragraph or "").lstrip()
        return bool(
            stripped.startswith(("→", "- ", "* ", "• "))
            or stripped.startswith("**")
        )

    rebuilt_sections: List[str] = []
    for heading, body in sections:
        section_name = _standard_section_name_from_heading(heading)
        if section_name not in target_sections:
            body_text = (body or "").strip()
            section_text = (
                f"{heading}\n\n{body_text}".strip() if body_text else heading.strip()
            )
            rebuilt_sections.append(section_text)
            continue

        raw_body = (body or "").strip()
        if not raw_body:
            section_text = heading.strip()
            rebuilt_sections.append(section_text)
            continue

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw_body) if p.strip()]
        merged: List[str] = []
        for paragraph in paragraphs:
            if not merged:
                merged.append(paragraph)
                continue

            if _is_structured_line(paragraph):
                merged.append(paragraph)
                continue

            words = _count_words(paragraph)
            sentences = _sentence_count(paragraph)
            is_staccato = sentences <= 1 and words <= 22

            if is_staccato and not _is_structured_line(merged[-1]):
                merged[-1] = f"{merged[-1].rstrip()} {paragraph.lstrip()}".strip()
            else:
                merged.append(paragraph)

        body_text = "\n\n".join(merged).strip()
        section_text = f"{heading}\n\n{body_text}".strip()
        rebuilt_sections.append(section_text)

    rebuilt = "\n\n".join([s for s in ([preamble_text] if preamble_text else []) + rebuilt_sections if s]).strip()
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    return rebuilt


def _normalize_section_headings(text: str, include_health_rating: bool) -> str:
    """Ensure each required section begins with the expected markdown heading on its own line.

    This handles cases where:
    1. Headers appear inline with content (e.g., "...business. ## Executive Summary As Bill...")
    2. Headers are missing the ## prefix
    3. Headers have extra whitespace or formatting issues
    """
    # Normalize legacy alias headings up-front so downstream logic can stay
    # opinionated about canonical section names.
    text = re.sub(
        r"(?im)^\s*(?:##\s*)?Key\s+Data\s+Appendix\s*$",
        "## Key Metrics",
        text or "",
    )

    required_titles = [
        title
        for title, _ in SUMMARY_SECTION_REQUIREMENTS
        if title != "Financial Health Rating"
    ]
    if include_health_rating:
        required_titles = [title for title, _ in SUMMARY_SECTION_REQUIREMENTS]

    normalized_lines: List[str] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if stripped.lower() in {"f", "e", "m", "r", "s", "k"} and idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            target_match = next(
                (
                    heading
                    for heading in required_titles
                    if next_line.lower().startswith(heading.lower())
                ),
                None,
            )
            if target_match:
                line = f"## {target_match}"
                idx += 1
        normalized_lines.append(line)
        idx += 1

    normalized_text = "\n".join(normalized_lines)

    # CRITICAL: First, handle INLINE section headers (headers appearing mid-line)
    # This catches patterns like "...business. ## Executive Summary As Bill..."
    # and splits them into proper separate lines
    for title in required_titles:
        # Pattern to find inline headers: text before + ## Title + trailing content
        # IMPORTANT: Capture the first character after the title to preserve content
        inline_pattern = re.compile(
            rf"([.!?])\s*(?:##?\s*)?({re.escape(title)})\s*(\S)", re.IGNORECASE
        )
        # Replace with: punctuation + double newline + ## Title + double newline + preserved trailing char
        normalized_text = inline_pattern.sub(
            lambda m: f"{m.group(1)}\n\n## {title}\n\n{m.group(3)}", normalized_text
        )

    # Also handle cases where the header appears without preceding punctuation but inline
    # e.g., "some text ## Executive Summary more text"
    for title in required_titles:
        inline_no_punct_pattern = re.compile(
            rf"(\S)\s+(?:##?\s*)({re.escape(title)})\s+(\S)", re.IGNORECASE
        )
        normalized_text = inline_no_punct_pattern.sub(
            lambda m: f"{m.group(1)}\n\n## {title}\n\n{m.group(3)}", normalized_text
        )

    # Now normalize headers that are on their own lines but might be missing ##
    for title in required_titles:
        pattern = re.compile(
            rf"(^|\n)\s*(?:##\s*)?{re.escape(title)}\s*(?:\n|$)",
            re.IGNORECASE | re.MULTILINE,
        )
        normalized_text = pattern.sub(
            lambda _: f"\n\n## {title}\n\n", normalized_text, count=1
        )

    # Clean up any excessive newlines (more than 2 consecutive)
    normalized_text = re.sub(r"\n{4,}", "\n\n\n", normalized_text)

    # Ensure headers have exactly one blank line before and after
    for title in required_titles:
        # Fix cases where header doesn't have proper spacing
        header_spacing_pattern = re.compile(
            rf"([^\n])(\n*)(\s*##\s*{re.escape(title)})(\n*)([^\n])", re.IGNORECASE
        )

        def ensure_spacing(m):
            before_char = m.group(1)
            before_newlines = "\n\n" if before_char not in "\n" else ""
            after_newlines = "\n\n"
            after_char = m.group(5)
            return (
                f"{before_char}{before_newlines}## {title}{after_newlines}{after_char}"
            )

        normalized_text = header_spacing_pattern.sub(ensure_spacing, normalized_text)

    return normalized_text.strip()


def _format_metric_value_for_text(key: str, value: float) -> str:
    if key in {"operating_margin", "net_margin"}:
        return f"{value:.1f}%"
    return _format_dollar(value) or f"{value:,.2f}"


def _score_to_grade(score: float) -> Tuple[str, str]:
    for threshold, grade, label in RATING_SCALE:
        if score >= threshold:
            return grade, label
    return "NR", "Not Rated"


def _estimate_health_score(metrics: Dict[str, Any]) -> float:
    score = 60.0
    free_cash_flow = metrics.get("free_cash_flow")
    operating_margin = metrics.get("operating_margin")
    net_margin = metrics.get("net_margin")
    total_assets = metrics.get("total_assets")
    total_liabilities = metrics.get("total_liabilities")
    cash = metrics.get("cash")

    if free_cash_flow and free_cash_flow > 0:
        score += 10
    if operating_margin is not None:
        if operating_margin > 30:
            score += 10
        elif operating_margin > 20:
            score += 6
        elif operating_margin < 5:
            score -= 8
    if net_margin is not None:
        if net_margin > 20:
            score += 4
        elif net_margin < 5:
            score -= 6
    if total_assets and total_liabilities:
        leverage = total_liabilities / total_assets if total_assets else 1
        if leverage > 0.8:
            score -= 10
        elif leverage < 0.5:
            score += 4
    if cash and total_liabilities:
        liquidity_ratio = cash / total_liabilities if total_liabilities else 1
        if liquidity_ratio > 0.3:
            score += 4
    return max(0.0, min(100.0, score))


def _format_number_or_default(value: Optional[float]) -> str:
    """Format a number or return 'not disclosed' if missing."""
    if value is None:
        return "not disclosed"
    formatted = _format_dollar(value)
    if formatted:
        return formatted
    return f"{value:,.2f}"


_PERSONAL_VERDICT_RE = re.compile(
    r"(?is)\b(?:"
    r"i\s*(?:personally\s+)?would"
    r"|i\s*['’]d"
    r"|for\s+my\s+(?:own\s+)?portfolio"
    r"|my\s+(?:call|stance|recommendation)\s*:?"
    r")\b[\s\S]{0,160}\b(?:buy|hold|sell|wait|avoid|pass)\b"
)


def _contains_personal_verdict(text: str) -> bool:
    return bool(text and _PERSONAL_VERDICT_RE.search(text))


_OBJECTIVE_RECOMMENDATION_RE = re.compile(
    r"(?is)\b(?:buy|hold|sell)\b\s+(?:rating|recommendation|stance)\b"
    r"|\b(?:rating|recommendation|stance)\b[\s:]{0,12}(?:is\s+)?(?:a\s+)?\b(?:buy|hold|sell)\b"
)


def _contains_objective_recommendation(text: str) -> bool:
    """Return True if the text contains an explicit Buy/Hold/Sell recommendation in third person."""
    if not text:
        return False
    lowered = text.lower()
    # Buy/sell are rarely used in neutral prose outside of an explicit stance.
    if re.search(r"\bbuy\b", lowered) or re.search(r"\bsell\b", lowered):
        return True
    # "hold" can be used as a verb; require it to show up as a stance.
    return bool(_OBJECTIVE_RECOMMENDATION_RE.search(lowered))


def _ensure_objective_recommendation(
    closing_text: str,
    company_name: str,
    strengths: Optional[List[str]] = None,
    concerns: Optional[List[str]] = None,
) -> str:
    """Append a clear third-person Buy/Hold/Sell sentence when no persona is used."""
    if not closing_text:
        return closing_text
    if _contains_objective_recommendation(closing_text):
        return closing_text

    strengths = strengths or []
    concerns = concerns or []

    if strengths and not concerns:
        verdict = "buy"
    elif concerns and not strengths:
        verdict = "sell"
    else:
        verdict = "hold"

    verdict_word = verdict.title()
    closing = closing_text.rstrip()
    if closing and not closing.endswith((".", "!", "?")):
        closing += "."

    driver: Optional[str] = None
    if verdict == "buy":
        driver = strengths[0] if strengths else None
    elif verdict == "sell":
        driver = concerns[0] if concerns else None
    else:
        driver = concerns[0] if concerns else (strengths[0] if strengths else None)

    driver_clause = f" given {driver}" if driver else ""
    seed_material = f"{company_name}|{verdict_word}|{closing}|{driver_clause}"
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    variants = [
        f"A {verdict_word} rating appears warranted for {company_name}{driver_clause}.",
        f"The appropriate stance is {verdict_word} on {company_name}{driver_clause}.",
        f"A {verdict_word} recommendation is justified for {company_name}{driver_clause}.",
        f"{company_name} screens as a {verdict_word} at current levels{driver_clause}.",
        f"On balance, a {verdict_word} rating is appropriate for {company_name}{driver_clause}.",
    ]
    closing += " " + rng.choice(variants)
    return closing


def _ensure_personal_verdict(
    closing_text: str,
    company_name: str,
    strengths: Optional[List[str]] = None,
    concerns: Optional[List[str]] = None,
) -> str:
    """
    Append a clear first-person verdict when a persona is used.
    If the text already contains a personal verdict, it is left unchanged.
    """
    if not closing_text:
        return closing_text

    if _contains_personal_verdict(closing_text):
        return closing_text

    strengths = strengths or []
    concerns = concerns or []

    if strengths and not concerns:
        verdict = "buy"
    elif concerns and not strengths:
        verdict = "sell"
    else:
        verdict = "hold"

    verdict_word = verdict.upper()

    closing = closing_text.rstrip()
    if closing and not closing.endswith((".", "!", "?")):
        closing += "."
    driver: Optional[str] = None
    if verdict == "buy":
        driver = strengths[0] if strengths else None
    elif concerns:
        driver = concerns[0]
    elif strengths:
        driver = strengths[0]
    driver_clause = f" given {driver}" if driver else ""
    seed_material = f"{company_name}|{verdict_word}|{closing}|{driver_clause}"
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    verdict_variants = [
        f"For my own portfolio, I'd {verdict_word} {company_name}{driver_clause}.",
        f"If I had to act today, I'd {verdict_word} {company_name}{driver_clause}.",
        f"On balance, I'd {verdict_word} {company_name}{driver_clause}.",
        f"My call: {verdict_word} {company_name}{f' ({driver})' if driver else ''}.",
        f"I'd {verdict_word} {company_name}{driver_clause}.",
        f"Personally, I'd {verdict_word} {company_name}{driver_clause}.",
        f"For me, it's a {verdict_word} on {company_name}{driver_clause}.",
        f"Bottom line: {verdict_word} {company_name}{driver_clause}.",
    ]
    closing += " " + rng.choice(verdict_variants)
    return closing


def _generate_fallback_closing_takeaway(
    company_name: str,
    calculated_metrics: Dict[str, Any],
    persona_name: Optional[str] = None,
    *,
    persona_requested: bool = False,
) -> str:
    """Generate a substantive closing takeaway from available financial metrics.

    This provides a data-driven conclusion when the AI fails to generate one.
    If a persona is selected, the output is styled to match that persona's voice.
    """
    # Extract key metrics
    operating_margin = calculated_metrics.get("operating_margin")
    net_margin = calculated_metrics.get("net_margin")
    free_cash_flow = calculated_metrics.get("free_cash_flow")
    cash = calculated_metrics.get("cash")
    total_debt = calculated_metrics.get("total_debt") or calculated_metrics.get(
        "total_liabilities"
    )
    revenue = calculated_metrics.get("revenue") or calculated_metrics.get(
        "total_revenue"
    )

    # Assess overall financial quality
    strengths = []
    concerns = []

    # Profitability assessment
    if operating_margin is not None:
        if operating_margin > 25:
            strengths.append("exceptional profitability")
        elif operating_margin > 15:
            strengths.append("solid profitability")
        elif operating_margin < 5:
            concerns.append("thin margins")

    # Cash flow assessment
    if free_cash_flow is not None:
        if free_cash_flow > 0:
            fcf_str = _format_dollar(free_cash_flow)
            if fcf_str:
                strengths.append(f"strong cash generation ({fcf_str} FCF)")
            else:
                strengths.append("positive free cash flow")
        else:
            concerns.append("negative free cash flow")

    # Balance sheet assessment
    if cash is not None and total_debt is not None:
        if cash > total_debt:
            strengths.append("net cash position")
        elif total_debt > cash * 3:
            concerns.append("elevated leverage")

    # Determine quality assessment
    if strengths and not concerns:
        quality = "high-quality" if len(strengths) >= 2 else "solid"
        is_positive = True
        is_mixed = False
    elif concerns and not strengths:
        quality = "challenged"
        is_positive = False
        is_mixed = False
    elif strengths and concerns:
        quality = "mixed"
        is_positive = False
        is_mixed = True
    else:
        quality = "uncertain"
        is_positive = False
        is_mixed = False

    # Persona-specific closing templates
    if persona_requested and persona_name and persona_name in PERSONA_CLOSING_INSTRUCTIONS:
        closing = _generate_persona_flavored_closing(
            persona_name,
            company_name,
            strengths,
            concerns,
            quality,
            is_positive,
            is_mixed,
            revenue,
            operating_margin,
        )

        # Add an explicit "what changes my view" trigger so persona closings don't
        # collapse into a punchline without underwriting closure.
        trigger_parts: List[str] = []
        if operating_margin is not None and operating_margin < 8:
            trigger_parts.append("sustained operating margin improvement")
        elif operating_margin is not None and operating_margin >= 8:
            trigger_parts.append("margin durability through a softer period")

        if free_cash_flow is not None:
            trigger_parts.append("repeatable free cash flow conversion")

        if cash is not None and total_debt is not None and total_debt > cash * 3:
            trigger_parts.append("better balance-sheet flexibility")

        trigger = " and ".join(trigger_parts[:2]) if trigger_parts else "durable cash conversion"
        primary_concern = concerns[0] if concerns else "the weak spots"
        seed_material = "|".join([persona_name, company_name, quality, trigger, primary_concern])
        digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        trigger_variants = [
            f"I'd get more constructive with clearer evidence of {trigger}; I'd get more cautious if {primary_concern} worsens and the margin for error shrinks.",
            f"I would revisit my stance if {trigger} shows up consistently, or if {primary_concern} deteriorates and the cushion narrows.",
            f"What changes my view: {trigger} on the upside, or a worsening in {primary_concern} that tightens the margin for error.",
        ]
        trigger_sentence = rng.choice(trigger_variants)

        closing = f"{closing} {trigger_sentence}".strip()
        return _ensure_personal_verdict(closing, company_name, strengths, concerns)

    # Generic persona (custom prompt): first-person, no famous investor mimicry.
    if persona_requested:
        seed_material = "|".join(
            [
                company_name or "",
                str(operating_margin),
                str(net_margin),
                str(free_cash_flow),
                str(cash),
                str(total_debt),
                str(revenue),
                quality,
                "persona",
            ]
        )
        digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))

        strengths_str = " and ".join(strengths[:2]) if strengths else "limited visibility"
        concerns_str = " and ".join(concerns[:2]) if concerns else "no obvious red flags"
        rev_str = _format_dollar(revenue) if revenue else None

        if strengths and not concerns:
            variants = [
                [
                    f"My takeaway is that {company_name} looks {quality} on the numbers, led by {strengths_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "The durability test is whether cash conversion stays tight as reinvestment needs normalize.",
                    "If margins hold through a softer period, the earnings base should be more repeatable than the market may be assuming.",
                    "My view would change if operating margins compress materially or free cash flow conversion weakens for multiple quarters.",
                ],
                [
                    f"From my perspective, {company_name} screens as a {quality} business with {strengths_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "What matters next is whether operating leverage translates into durable free cash flow after capex and working-capital swings.",
                    "If management keeps capital allocation disciplined, downside scenarios remain more manageable.",
                    "I would revisit the stance quickly if cash conversion diverges from reported earnings.",
                ],
            ]
            sentences = rng.choice(variants)
        elif concerns and not strengths:
            variants = [
                [
                    f"In my view, {company_name} is constrained by {concerns_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "In this setup, I care more about cash conversion and balance-sheet flexibility than headline revenue growth.",
                    "I would need to see sustained stabilization in operating margins and free cash flow before underwriting upside.",
                    "My view would improve after clearer evidence the weak inputs are repairing rather than being masked by timing.",
                ],
                [
                    f"My read is cautious on {company_name}: {concerns_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "If cash lags earnings for multiple quarters, valuation support tends to erode quickly.",
                    "I would want proof of repeatable free cash flow and cleaner earnings quality before changing posture.",
                    "My view would change with sustained margin recovery and better cash conversion.",
                ],
            ]
            sentences = rng.choice(variants)
        else:
            variants = [
                [
                    f"My takeaway on {company_name} is mixed: {strengths[0] if strengths else 'some strengths'} offset by {concerns[0] if concerns else 'meaningful uncertainties'}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "The underwriting hinge is whether the weaker input is temporary (timing/cycle) or structural (unit economics and competitive intensity).",
                    "I would stay patient until the business proves profitability converts to durable free cash flow without balance-sheet strain.",
                    "My view would improve on clearer cash conversion and margin durability, or on a meaningfully better entry price.",
                ],
                [
                    f"{company_name} has real positives, but I keep coming back to {concerns[0] if concerns else 'the uncertainty around durability'}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                    "In this posture, I want confirmation that cash generation tracks reported profitability after normalizing capex and working capital.",
                    "I would revisit quickly if the next filing shows an inflection in the weak area.",
                    "My view would turn more cautious if leverage rises or if cash conversion keeps slipping.",
                ],
            ]
            sentences = rng.choice(variants)

        closing = " ".join(sentences).strip()
        return _ensure_personal_verdict(closing, company_name, strengths, concerns)

    # Generic fallback (no persona selected) - longer and reasoned (~80-110 words)
    seed_material = "|".join(
        [
            company_name or "",
            str(operating_margin),
            str(net_margin),
            str(free_cash_flow),
            str(cash),
            str(total_debt),
            str(revenue),
            quality,
        ]
    )
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    strengths_str = (
        " and ".join(strengths[:2])
        if strengths
        else "limited visibility into fundamentals"
    )
    concerns_str = " and ".join(concerns[:2]) if concerns else "no major red flags"
    rev_str = _format_dollar(revenue) if revenue else None

    if strengths and not concerns:
        variants = [
            [
                f"{company_name} demonstrates {quality} fundamentals, led by {strengths_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                "The earnings base looks supported by operating profitability and free-cash-flow generation rather than purely accounting outcomes.",
                "Balance-sheet flexibility should remain a differentiator if demand softens or reinvestment needs rise.",
                "A Buy rating is reasonable if valuation is not embedding peak margins and the cash conversion holds through a normalizing cycle.",
                "The stance would change if operating margins compress materially or free cash flow conversion weakens for multiple quarters.",
            ],
            [
                f"{company_name} shows {quality} financial strength with {strengths_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                "What matters for durability is whether cash flow tracks reported earnings after normalizing working capital and capex.",
                "If capital allocation stays disciplined, the business can compound without forcing balance-sheet leverage.",
                "A Buy recommendation is justified when the market is not overpaying for near-term strength.",
                "Revisit the view if margins erode faster than revenue growth can offset.",
            ],
        ]
        sentences = rng.choice(variants)
    elif concerns and not strengths:
        variants = [
            [
                f"{company_name} faces headwinds from {concerns_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}, which weakens earnings durability.",
                "In that setup, cash conversion and balance-sheet flexibility matter more than headline revenue growth because downside scenarios tighten quickly.",
                "A Sell rating is justified if the business is relying on one-offs or funding flexibility to sustain operations.",
                "A Hold stance can be reasonable only if there is clear evidence of stabilization in operating margins and free cash flow.",
                "The recommendation would improve after sustained margin recovery and cleaner cash conversion through working-capital normalization.",
            ],
            [
                f"{company_name} is constrained by {concerns_str}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                "That makes near-term earnings power harder to trust, especially if cash generation is lagging reported profits.",
                "A Sell recommendation is appropriate until operating performance improves and liquidity risk is clearly reduced.",
                "The key confirmation is sustained free cash flow after capex, not a single-quarter profit print.",
                "Re-rate the stance once margins stabilize and cash conversion becomes repeatable.",
            ],
        ]
        sentences = rng.choice(variants)
    elif strengths and concerns:
        variants = [
            [
                f"{company_name} presents a mixed picture: {strengths[0]} offset by {concerns[0]}{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                "The underwriting hinge is whether the weaker input is temporary (cycle/timing) or structural (unit economics and competitive intensity).",
                "A Hold rating is appropriate while the business proves that profitability converts to durable free cash flow without balance-sheet strain.",
                "The clearest upgrade trigger is improving cash conversion alongside stable operating margins.",
                "The view would turn more cautious if leverage rises or if the margin gap is being closed only through non-operating items.",
            ],
            [
                f"{company_name} has real positives, but {concerns[0]} tempers the upside{' on ' + rev_str + ' of revenue' if rev_str else ''}.",
                "In this posture, the market needs to see improving earnings quality and cash conversion, not just top-line momentum.",
                "A Hold recommendation is justified until the weak area shows a clear inflection.",
                "The risk-reward improves either on better fundamentals or a materially better entry price.",
                "Revisit after the next filing confirms margin durability and repeatable cash generation.",
            ],
        ]
        sentences = rng.choice(variants)
    else:
        variants = [
            [
                f"{company_name} requires deeper due diligence to form a definitive view{', especially around earnings quality and cash conversion' if rev_str else ''}.",
                "The near-term signal to underwrite is operating profitability and whether it converts to free cash flow after capex and working capital.",
                "A Hold stance is the most defensible recommendation until the durability of margins and cash generation is clearer.",
                "Upgrade the view after consistent cash conversion and improved balance-sheet flexibility are visible in reported results.",
            ],
            [
                f"{company_name}{', with ' + rev_str + ' in revenue' if rev_str else ''}, needs more context to judge durability.",
                "The key unknown is whether reported profitability is repeatable once one-offs and timing effects normalize.",
                "A Hold recommendation is appropriate until margin direction and free cash flow conversion are clearly improving.",
                "The stance would improve after evidence of sustained operating leverage and cleaner cash generation.",
            ],
        ]
        sentences = rng.choice(variants)

    return " ".join(sentences).strip()


def _generate_persona_flavored_closing(
    persona_name: str,
    company_name: str,
    strengths: List[str],
    concerns: List[str],
    quality: str,
    is_positive: bool,
    is_mixed: bool,
    revenue: Optional[float],
    operating_margin: Optional[float],
) -> str:
    """Generate a closing takeaway in the voice of the selected persona."""

    strengths_str = (
        " and ".join(strengths[:2])
        if strengths
        else "limited visibility into fundamentals"
    )
    concerns_str = " and ".join(concerns[:2]) if concerns else "no major red flags"
    margin_str = (
        f"{operating_margin:.1f}%" if operating_margin else "undisclosed margins"
    )

    if persona_name == "Warren Buffett":
        if is_positive:
            return (
                f"This is a wonderful business with {strengths_str}. "
                f"The economics of {company_name} suggest a durable moat, and I would be comfortable holding for decades. "
                f"At current valuations, Mr. Market appears to be offering a fair deal for patient capital."
            )
        elif is_mixed:
            return (
                f"{company_name} has attractive qualities—{strengths[0] if strengths else 'decent operations'}—but {concerns[0] if concerns else 'some uncertainties'} gives me pause. "
                f"I prefer businesses where the path forward is clear. This one requires more conviction than I currently have."
            )
        else:
            return (
                f"I struggle to understand the long-term economics here. {company_name} faces {concerns_str}, "
                f"which makes it difficult to assess the durability of any moat. I would pass and wait for a better opportunity."
            )

    elif persona_name == "Charlie Munger":
        if is_positive:
            return (
                f"Inverting the question: what would make {company_name} a disaster? Not much, given {strengths_str}. "
                f"The incentives appear aligned and the economics make sense. I have nothing to add."
            )
        elif is_mixed:
            return (
                f"{company_name} isn't obviously stupid, but it isn't obviously wonderful either. "
                f"{strengths[0].capitalize() if strengths else 'Some merit'} is offset by {concerns[0] if concerns else 'uncertainty'}. "
                f"The intelligent thing is to wait for better clarity."
            )
        else:
            return (
                f"Avoid this one. {company_name} has {concerns_str}—the kind of structural issues that tend to compound. "
                f"There are simpler, better businesses to own."
            )

    elif persona_name == "Benjamin Graham":
        if is_positive:
            return (
                f"The margin of safety at {company_name} appears adequate, supported by {strengths_str}. "
                f"For the intelligent investor, this represents a reasonable investment rather than speculation. "
                f"The balance sheet strength supports the thesis."
            )
        elif is_mixed:
            return (
                f"{company_name} presents a mixed margin of safety calculation. While {strengths[0] if strengths else 'some factors'} provides support, "
                f"{concerns[0] if concerns else 'other factors'} undermines the thesis. A more conservative investor would require a lower entry price."
            )
        else:
            return (
                f"The margin of safety is insufficient. {company_name} shows {concerns_str}, "
                f"leaving limited downside protection. This is speculation, not investment."
            )

    elif persona_name == "Peter Lynch":
        if is_positive:
            return (
                f"Here's the story: {company_name} has {strengths_str}—the kind of business you can explain to anyone. "
                f"With {margin_str}, this looks like a solid stalwart or fast grower worth owning. You don't need an MBA to understand this one."
            )
        elif is_mixed:
            return (
                f"The story at {company_name} is complicated. On one hand, {strengths[0] if strengths else 'there is potential'}; "
                f"on the other, {concerns[0] if concerns else 'some issues'}. I prefer cleaner stories where the path to earnings growth is obvious."
            )
        else:
            return (
                f"{company_name} doesn't fit my playbook. With {concerns_str}, the story here is more turnaround than growth. "
                f"I would rather find a company where the growth is already visible."
            )

    elif persona_name == "Ray Dalio":
        if is_positive:
            return (
                f"Understanding the machine: {company_name} shows {strengths_str}, positioning it well for the current cycle. "
                f"The risk-reward correlation favors a constructive stance, though position sizing should reflect broader macro uncertainties."
            )
        elif is_mixed:
            seed_material = "|".join(
                [
                    persona_name,
                    company_name,
                    quality,
                    strengths_str,
                    concerns_str,
                ]
            )
            digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
            openers = [
                "Cycle check: ",
                "From a cycle standpoint, ",
                "At this point in the cycle, ",
                "On cycle positioning, ",
                "Zooming out to the macro backdrop, ",
            ]
            setup_variants = [
                f"{company_name} presents {strengths[0] if strengths else 'some positives'} alongside {concerns[0] if concerns else 'risks'}",
                f"{company_name} has {strengths[0] if strengths else 'some positives'}, but {concerns[0] if concerns else 'risks'} keep the setup balanced",
                f"{company_name} shows {strengths[0] if strengths else 'some positives'}, yet {concerns[0] if concerns else 'risks'} widen the distribution of outcomes",
            ]
            sizing_variants = [
                "The correlation to macro factors argues for disciplined position sizing.",
                "Macro sensitivity suggests careful sizing rather than a big bet.",
                "Risk parity thinking says size this like a macro-linked asset, not a standalone story.",
            ]
            return f"{rng.choice(openers)}{rng.choice(setup_variants)}. {rng.choice(sizing_variants)}"
        else:
            return (
                f"The economic machine suggests caution. {company_name} faces {concerns_str}, "
                f"which could amplify in a deleveraging scenario. Risk parity considerations favor underweight or avoidance."
            )

    elif persona_name == "Cathie Wood":
        if is_positive:
            return (
                f"The disruptive innovation potential at {company_name} is compelling. With {strengths_str}, "
                f"the S-curve adoption could drive exponential growth. By 2030, I see significant upside if the innovation thesis plays out."
            )
        elif is_mixed:
            seed_material = "|".join(
                [
                    persona_name,
                    company_name,
                    quality,
                    strengths_str,
                    concerns_str,
                ]
            )
            digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
            concern = concerns[0] if concerns else "execution risk"

            setup_variants = [
                f"{company_name} has innovation potential, but {concern} keeps the setup balanced.",
                f"I like the innovation ambition at {company_name}, but {concern} widens the range of outcomes.",
                f"{company_name} could still surprise to the upside, yet {concern} makes timing and scaling less obvious.",
            ]
            followups = [
                "I want to see evidence the business is moving up an S-curve—improving unit economics and scaling free cash flow—before I raise conviction.",
                "Conviction goes up when Wright's Law shows up in the numbers: costs down, adoption up, and cash flow starting to compound.",
                "I need clearer proof that disruption is translating into operating leverage and cash generation, not just narrative momentum.",
            ]
            return f"{rng.choice(setup_variants)} {rng.choice(followups)}"
        else:
            return (
                f"{company_name} faces {concerns_str}, which constrains its ability to invest in disruptive innovation. "
                f"Without clear technology catalysts, I would look elsewhere for exponential growth opportunities."
            )

    elif persona_name == "Joel Greenblatt":
        if is_positive:
            return (
                f"By the Magic Formula, {company_name} looks attractive. With {margin_str} operating margins and {strengths_str}, "
                f"the return on capital is solid and the earnings yield appears reasonable. This is the kind of good and cheap I look for."
            )
        elif is_mixed:
            return (
                f"{company_name} is either good or cheap, but not clearly both. {strengths[0].capitalize() if strengths else 'Some positives'} "
                f"is partially offset by {concerns[0] if concerns else 'valuation concerns'}. The Magic Formula works best with cleaner situations."
            )
        else:
            return (
                f"The Magic Formula doesn't favor {company_name} here. With {concerns_str}, "
                f"the return on capital or earnings yield is insufficient. Pass."
            )

    elif persona_name == "John Bogle":
        if is_positive:
            return (
                f"{company_name} is a fine business with {strengths_str}. But why own one needle when you can own the haystack? "
                f"Costs matter, and most stock pickers fail to beat the index. For those who insist on individual stocks, "
                f"I would HOLD this position rather than add at current valuations—the fundamentals are sound but no single stock justifies concentration risk. "
                f"If valuation became significantly more attractive, I might reconsider. The prudent investor stays the course with diversification."
            )
        elif is_mixed:
            return (
                f"{company_name} shows {strengths[0] if strengths else 'some merit'} but also {concerns[0] if concerns else 'uncertainty'}. "
                f"This uncertainty is precisely why I advocate for index funds—no single stock is predictable. "
                f"For individual stock holders, I would HOLD but not add. The mixed signals warrant caution, and I would want to see improved clarity before changing my view."
            )
        else:
            return (
                f"{company_name} faces {concerns_str}—exactly the kind of company-specific risk that diversification eliminates. "
                f"For individual stock holders, I would SELL or avoid entirely. These challenges underscore why I believe in index investing. "
                f"Only a dramatic improvement in fundamentals would change my view. Stay the course with the index fund."
            )

    elif persona_name == "Howard Marks":
        if is_positive:
            return (
                f"Second-level thinking: the market may be underestimating {company_name}. With {strengths_str}, "
                f"the risk-reward asymmetry appears favorable. The pendulum hasn't swung too far to optimism here."
            )
        elif is_mixed:
            seed_material = "|".join(
                [
                    persona_name,
                    company_name,
                    quality,
                    strengths_str,
                    concerns_str,
                ]
            )
            digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
            openers = [
                "Cycle positioning: ",
                "A quick pendulum check: ",
                "On where we are in the cycle, ",
                "At this point in the cycle, ",
                "Stepping back to the cycle, ",
            ]
            setup_variants = [
                f"{company_name} has {strengths[0] if strengths else 'positives'} but {concerns[0] if concerns else 'risks'}.",
                f"{company_name} offers {strengths[0] if strengths else 'positives'}, yet {concerns[0] if concerns else 'risks'} keep me from leaning in.",
                f"{company_name} looks acceptable on the surface, but {concerns[0] if concerns else 'risks'} are easy for the market to underprice.",
            ]
            conclusion_variants = [
                "Second-level thinking suggests patience until the pendulum swings further.",
                "Second-level thinking pushes me to demand better asymmetry before committing capital.",
                "I'd rather be early to caution than late to regret while the pendulum is still near the middle.",
            ]
            return f"{rng.choice(openers)}{rng.choice(setup_variants)} {rng.choice(conclusion_variants)}"
        else:
            return (
                f"The risk here is not being adequately compensated. {company_name} shows {concerns_str}, "
                f"and the pendulum of sentiment may have further to fall. I would wait for better asymmetry."
            )

    elif persona_name == "Bill Ackman":
        if is_positive:
            return (
                f"This is simple, predictable, and free-cash-flow generative. {company_name} has {strengths_str}. "
                f"The catalyst for further value creation is execution on current initiatives. I would own this with high conviction."
            )
        elif is_mixed:
            return (
                f"{company_name} has potential but needs a catalyst. While {strengths[0] if strengths else 'fundamentals are okay'}, "
                f"{concerns[0] if concerns else 'the path forward'} is unclear. Management must address this to unlock value."
            )
        else:
            return (
                f"{company_name} is not the kind of simple, predictable business I favor. With {concerns_str}, "
                f"there's no clear catalyst to unlock value. I would pass."
            )

    # Fallback if persona not matched (shouldn't happen given the check above)
    return f"{company_name} requires further analysis to form a definitive investment view."


def _ensure_required_sections(
    summary_text: str,
    *,
    include_health_rating: bool,
    metrics_lines: str,
    calculated_metrics: Dict[str, Any],
    health_score_data: Optional[Dict[str, Any]] = None,
    company_name: str,
    risk_factors_excerpt: Optional[str] = None,
    health_rating_config: Optional[Dict[str, Any]] = None,
    persona_name: Optional[str] = None,
    persona_requested: bool = False,
    target_length: Optional[int] = None,
) -> str:
    """Ensure all required sections are present.

    IMPORTANT: This function should ONLY fill in sections if the AI completely
    failed to generate them. It should NOT add placeholder text like 'not extracted'
    or 'see above'. If a section is missing, we either:
    1. Generate minimal factual content from available metrics
    2. Skip the section entirely rather than add useless placeholders

    The goal is NO placeholders in the final output.
    """
    text = summary_text
    persona_mode = bool(persona_requested) or bool(persona_name)

    def _short_company_label(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9& ]+", " ", (name or "")).strip()
        parts = [p for p in cleaned.split() if p]
        if not parts:
            return "Company"
        suffixes = {
            "inc",
            "incorporated",
            "corp",
            "corporation",
            "co",
            "company",
            "ltd",
            "limited",
            "plc",
            "group",
            "holdings",
            "holding",
            "sa",
            "ag",
            "nv",
            "llc",
            "lp",
        }
        filtered = [p for p in parts if p.lower().rstrip(".") not in suffixes]
        parts = filtered or parts
        return parts[0][:24]

    def _derive_risk_theme_label(excerpt: Optional[str]) -> str:
        base = _short_company_label(company_name)
        if not excerpt:
            return base

        company_tokens = set(
            t
            for t in re.findall(r"[a-z0-9]+", company_name.lower())
            if t
        )
        company_tokens.discard("the")

        generic = {
            "company",
            "companies",
            "business",
            "operations",
            "operating",
            "financial",
            "results",
            "factors",
            "factor",
            "risk",
            "risks",
            "may",
            "might",
            "could",
            "would",
            "including",
            "include",
            "includes",
            "also",
            "such",
            "subject",
            "material",
            "materially",
            "adverse",
            "adversely",
            "impact",
            "affect",
            "affects",
            "future",
            "forward",
            "looking",
            "statements",
            "item",
            "items",
            "section",
        }

        deprioritized = {
            "regulatory",
            "regulation",
            "antitrust",
            "privacy",
            "security",
            "cyber",
            "cybersecurity",
            "litigation",
            "lawsuit",
            "competition",
            "competitive",
            "macro",
            "economic",
            "economy",
            "geopolitical",
            "data",
        }

        aliases = {
            "advertising": "Ads",
            "advertiser": "Ads",
            "advertisers": "Ads",
            "ads": "Ads",
            "search": "Search",
            "cloud": "Cloud",
            "youtube": "YouTube",
            "android": "Android",
            "subscription": "Subscriptions",
            "subscriptions": "Subscriptions",
            "device": "Devices",
            "devices": "Devices",
            "semiconductor": "Semis",
            "semiconductors": "Semis",
            "chip": "Chips",
            "chips": "Chips",
            "gpu": "GPU",
            "gpus": "GPU",
            "loan": "Loans",
            "loans": "Loans",
            "deposit": "Deposits",
            "deposits": "Deposits",
            "oil": "Oil",
            "gas": "Gas",
            "fda": "FDA",
            "gdpr": "GDPR",
            "dma": "DMA",
            "doj": "DOJ",
            "ftc": "FTC",
            "tac": "TAC",
            "capex": "Capex",
            "ai": "AI",
        }

        words = re.findall(r"[a-z][a-z0-9]{2,}", excerpt.lower())
        freqs: Dict[str, int] = {}
        for w in words:
            if w in generic or w in company_tokens:
                continue
            freqs[w] = freqs.get(w, 0) + 1
        if not freqs:
            return base

        ranked = sorted(freqs.items(), key=lambda kv: kv[1], reverse=True)

        def _pick_terms(allow_deprioritized: bool) -> List[str]:
            picked: List[str] = []
            for w, _count in ranked:
                if not allow_deprioritized and w in deprioritized:
                    continue
                label = aliases.get(w, w.title())
                if label.lower() == base.lower():
                    continue
                if label in picked:
                    continue
                picked.append(label)
                if len(picked) >= 2:
                    break
            return picked

        terms = _pick_terms(allow_deprioritized=False) or _pick_terms(
            allow_deprioritized=True
        )
        if not terms:
            return base
        theme = "/".join(terms)[:28]
        return theme or base

    def _regulatory_driver_label(excerpt: Optional[str]) -> Optional[str]:
        if not excerpt:
            return None
        acronyms = {
            a
            for a in re.findall(r"\b[A-Z]{2,6}\b", excerpt)
            if a not in {"ITEM", "SEC", "GAAP", "IFRS", "USA", "U.S", "US"}
        }
        lower = excerpt.lower()
        if "dma" in lower and "EU" in acronyms:
            return "EU DMA"
        if "gdpr" in lower or "GDPR" in acronyms:
            return "GDPR"
        if "DOJ" in acronyms and "FTC" in acronyms:
            return "DOJ/FTC"
        if "DOJ" in acronyms:
            return "DOJ"
        if "FTC" in acronyms:
            return "FTC"
        if "FDA" in acronyms:
            return "FDA"
        if "OFAC" in acronyms:
            return "OFAC"
        if "antitrust" in lower:
            return "Antitrust"
        if "privacy" in lower:
            return "Privacy"
        if "regulat" in lower:
            return "Regulatory"
        return None

    def _rewrite_generic_risk_name(name: str) -> str:
        theme = _derive_risk_theme_label(risk_factors_excerpt)
        reg_driver = _regulatory_driver_label(risk_factors_excerpt)
        norm = _normalize_risk_name(name)

        def _themed(base: str) -> str:
            if theme and theme.lower() not in {"company"}:
                return f"{theme} {base}".strip()
            return base

        if norm in {"margin compression risk"}:
            return _themed("Margin / Reinvestment Risk")
        if norm in {
            "cash conversion risk",
            "cash conversion reversal risk",
            "cash flow visibility risk",
        }:
            return _themed("Cash Conversion / Capex Risk")
        if norm in {
            "balance sheet flexibility risk",
            "liquidity tightening risk",
        }:
            return _themed("Liquidity / Funding Risk")
        if norm in {"earnings quality risk"}:
            return _themed("Earnings Quality / Normalization Risk")
        if norm in {"competitive spend risk"}:
            return _themed("Pricing / Competitive Spend Risk")
        if norm in {"regulatory and antitrust scrutiny"} or (
            ("regulatory" in norm or "regulation" in norm)
            and "antitrust" in norm
            and any(
                k in norm
                for k in {
                    "scrutiny",
                    "pressure",
                    "enforcement",
                    "remedy",
                    "remedies",
                    "investigation",
                }
            )
            and not any(k in norm for k in {"dma", "gdpr", "doj", "ftc", "eu"})
        ):
            driver = reg_driver or "Regulatory"
            # Avoid "Regulatory Regulatory" style repeats.
            if driver.lower() in theme.lower():
                return f"{theme} Enforcement Risk".strip()
            return f"{driver} / {theme} Enforcement Risk".strip(" /")
        return name

    def _section_present(title: str) -> bool:
        # Detect section headings robustly (case-insensitive, whitespace-tolerant, heading-anchored).
        # Avoid false positives from body text mentioning "Risk Factors" etc.
        pattern = re.compile(rf"(?im)^\s*##\s*{re.escape(title)}\b")
        if pattern.search(text or ""):
            return True
        # Backwards-compatible aliasing for older headings
        if title.lower() == "key metrics":
            return bool(
                re.search(r"(?im)^\s*##\s*Key\s+Data\s+Appendix\b", text or "")
            )
        return False

    def _append_section(title: str, body: str) -> None:
        nonlocal text
        text = text.rstrip() + f"\n\n## {title}\n{body.strip()}\n"

    def _has_valid_data(value: str) -> bool:
        """Check if the value contains actual data, not placeholder."""
        return bool(value) and value != "not disclosed"

    def _get_score_label(score: float) -> str:
        """Get descriptive label matching dashboard SCORE_BANDS."""
        if score >= 85:
            return "Very Healthy"
        elif score >= 70:
            return "Healthy"
        elif score >= 50:
            return "Watch"
        else:
            return "At Risk"

    # Target-aware minimums so short requested lengths don't force over-expansion.
    min_words_by_section = _calculate_section_min_words_for_target(
        target_length, include_health_rating=include_health_rating
    )

    def _count_sentences(body: str) -> int:
        return len(re.findall(r"[.!?](?:\s|$)", body or ""))

    def _normalize_risk_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

    def _extract_risk_entries(body: str) -> List[Tuple[str, str]]:
        entries: List[Tuple[str, str]] = []
        cleaned_body = (body or "").strip()
        if not cleaned_body:
            return entries

        pattern = re.compile(
            r"\*\*(.+?)\*\*\s*:\s*([\s\S]*?)(?=\n\s*\*\*.+?\*\*\s*:|\Z)"
        )
        for match in pattern.finditer(cleaned_body):
            name = match.group(1).strip()
            desc = " ".join((match.group(2) or "").split())
            if name and desc:
                entries.append((name, desc))

        if entries:
            return entries

        # Fallback: handle one-line entries without blank-line separators.
        for line in cleaned_body.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^[\-\*]\s*", "", line)
            match = re.match(r"\*\*(.+?)\*\*\s*:\s*(.+)", line)
            if not match:
                continue
            name = match.group(1).strip()
            desc = match.group(2).strip()
            if name and desc:
                entries.append((name, desc))
        return entries

    def _risk_desc_tokens(desc: str) -> Set[str]:
        stopwords = {
            "the",
            "and",
            "or",
            "of",
            "to",
            "a",
            "an",
            "in",
            "on",
            "for",
            "if",
            "with",
            "as",
            "by",
            "at",
            "is",
            "are",
            "be",
            "can",
            "could",
            "may",
            "might",
            "this",
            "that",
            "these",
            "those",
            "from",
            "than",
            "then",
            "into",
            "over",
            "their",
            "its",
            "it",
            "they",
            "them",
        }
        cleaned = re.sub(r"[^a-z0-9]+", " ", (desc or "").lower())
        tokens = [t for t in cleaned.split() if t not in stopwords and len(t) > 2]
        return set(tokens)

    def _is_desc_duplicate(tokens: Set[str], seen_tokens: List[Set[str]]) -> bool:
        if not tokens:
            return False
        for prior in seen_tokens:
            if not prior:
                continue
            overlap = len(tokens & prior)
            denom = max(len(tokens), len(prior))
            if denom and (overlap / denom) >= 0.85:
                return True
        return False

    def _normalize_risk_factors_section() -> None:
        nonlocal text
        pattern = re.compile(
            r"##\s*Risk Factors\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
            re.IGNORECASE,
        )
        match = pattern.search(text or "")
        if not match:
            return
        body = (match.group(1) or "").strip()
        if not body:
            replacement = _synthesize_risk_factors_addendum()
            if replacement:
                text = pattern.sub(
                    lambda _m: f"## Risk Factors\n{replacement}\n",
                    text,
                    count=1,
                )
            return

        entries = _extract_risk_entries(body)
        cleaned_entries: List[Tuple[str, str]] = []
        seen_names: Set[str] = set()
        seen_desc_norms: Set[str] = set()
        seen_desc_tokens: List[Set[str]] = []
        for name, desc in entries:
            name = _rewrite_generic_risk_name(name)
            name_norm = _normalize_risk_name(name)
            desc_norm = " ".join(
                re.sub(r"[^a-z0-9]+", " ", desc.lower()).split()
            )
            desc_tokens = _risk_desc_tokens(desc)
            if not name_norm or name_norm in seen_names:
                continue
            if desc_norm and desc_norm in seen_desc_norms:
                continue
            if desc_tokens and _is_desc_duplicate(desc_tokens, seen_desc_tokens):
                continue
            if _count_words(desc) < 18:
                continue
            cleaned_entries.append((name, desc))
            seen_names.add(name_norm)
            if desc_norm:
                seen_desc_norms.add(desc_norm)
            if desc_tokens:
                seen_desc_tokens.append(desc_tokens)

        # Do NOT force generic padding risks when the model already produced
        # multiple company-specific items. Only backfill if we lack a second
        # substantive risk entry.
        if len(cleaned_entries) < 2:
            fallback_entries = _extract_risk_entries(
                _synthesize_risk_factors_addendum()
            )
            for name, desc in fallback_entries:
                name = _rewrite_generic_risk_name(name)
                name_norm = _normalize_risk_name(name)
                desc_norm = " ".join(
                    re.sub(r"[^a-z0-9]+", " ", desc.lower()).split()
                )
                desc_tokens = _risk_desc_tokens(desc)
                if not name_norm or name_norm in seen_names:
                    continue
                if desc_norm and desc_norm in seen_desc_norms:
                    continue
                if desc_tokens and _is_desc_duplicate(desc_tokens, seen_desc_tokens):
                    continue
                if _count_words(desc) < 18:
                    continue
                cleaned_entries.append((name, desc))
                seen_names.add(name_norm)
                if desc_norm:
                    seen_desc_norms.add(desc_norm)
                if desc_tokens:
                    seen_desc_tokens.append(desc_tokens)
                if len(cleaned_entries) >= 2:
                    break

        if not cleaned_entries:
            return

        cleaned_entries = cleaned_entries[:3]
        rebuilt_body = "\n\n".join(
            f"**{name}**: {desc}" for name, desc in cleaned_entries
        )
        text = pattern.sub(
            lambda _m: f"## Risk Factors\n{rebuilt_body}\n", text, count=1
        )

    # 1. Financial Health Rating - only add if we have actual data
    if include_health_rating and not _section_present("Financial Health Rating"):
        score_val = None
        band_label = None

        if health_score_data:
            score_val = health_score_data.get("overall_score")
            band_label = health_score_data.get("score_band")

        if score_val is None:
            score_match = re.search(
                r"Financial Health Rating[:\s]+(\d{1,3})", text, re.IGNORECASE
            )
            if score_match:
                score_val = float(score_match.group(1))
            else:
                score_val = _estimate_health_score(calculated_metrics)

        if not band_label:
            band_label = _get_score_label(float(score_val))

        score_line = _build_health_score_line(
            company_name,
            float(score_val),
            band_label,
            calculated_metrics,
            health_rating_config=health_rating_config,
        )

        narrative = _build_health_narrative(
            calculated_metrics,
            health_score_data={"score_band": band_label} if band_label else None,
            health_rating_config=health_rating_config,
        )

        _append_section("Financial Health Rating", f"{score_line}\n\n{narrative}".strip())

    def _synthesize_financial_performance() -> str:
        parts: List[str] = []
        rev_match = re.search(r"Revenue[:\s]+\$?([\d.,]+)", metrics_lines)
        op_inc_match = re.search(r"Operating Income[:\s]+\$?([\d.,]+)", metrics_lines)
        net_inc_match = re.search(r"Net Income[:\s]+\$?([\d.,]+)", metrics_lines)
        op_margin_match = re.search(r"Operating Margin[:\s]+([-\d.]+)%", metrics_lines)
        net_margin_match = re.search(r"Net Margin[:\s]+([-\d.]+)%", metrics_lines)
        fcf_match = re.search(r"Free Cash Flow[:\s]+\$?([\d.,]+)", metrics_lines)
        ocf_match = re.search(r"Operating Cash Flow[:\s]+\$?([\d.,]+)", metrics_lines)
        capex_match = re.search(
            r"Capital Expenditures[:\s]+\$?([\d.,]+)", metrics_lines
        )

        if rev_match and op_inc_match:
            parts.append(
                f"Revenue of ${rev_match.group(1)} with operating income of ${op_inc_match.group(1)} frames current scale."
            )
        if op_margin_match:
            parts.append(
                f"Operating margin of {op_margin_match.group(1)}% shows core profitability."
            )
        if net_margin_match:
            parts.append(
                f"Net margin of {net_margin_match.group(1)}% highlights the impact of below-the-line items."
            )
        if ocf_match:
            ocf = ocf_match.group(1)
            fcf_text = (
                f"free cash flow of ${fcf_match.group(1)}"
                if fcf_match
                else "free cash flow not provided"
            )
            capex_text = (
                f"capex of ${capex_match.group(1)}"
                if capex_match
                else "capex not disclosed"
            )
            parts.append(
                f"Operating cash flow of ${ocf} converts to {fcf_text} with {capex_text}."
            )
        elif fcf_match:
            parts.append(
                f"Free cash flow of ${fcf_match.group(1)} suggests healthy cash conversion."
            )

        if not parts:
            parts.append(
                "Financial performance commentary not provided in the draft; add revenue, margin, and cash flow context."
            )
        parts.append(
            "Connect revenue trends to margin direction and cash conversion to show sustainability."
        )
        return " ".join(parts)

    def _synthesize_executive_summary_addendum() -> str:
        """Add a short, metric-anchored paragraph to strengthen a too-brief Exec Summary."""

        is_persona = persona_mode

        revenue = calculated_metrics.get("revenue")
        operating_income = calculated_metrics.get("operating_income")
        net_income = calculated_metrics.get("net_income")
        operating_margin = calculated_metrics.get("operating_margin")
        net_margin = calculated_metrics.get("net_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        fcf = calculated_metrics.get("free_cash_flow")
        capex = calculated_metrics.get("capital_expenditures")
        cash = calculated_metrics.get("cash")
        securities = calculated_metrics.get("marketable_securities")
        liabilities = calculated_metrics.get("total_liabilities")

        cash_total = None
        if cash is not None:
            cash_total = cash + (securities or 0)

        rev_str = _format_metric_value_for_text("revenue", revenue) if revenue is not None else None
        op_inc_str = (
            _format_metric_value_for_text("operating_income", operating_income)
            if operating_income is not None
            else None
        )
        net_inc_str = (
            _format_metric_value_for_text("net_income", net_income)
            if net_income is not None
            else None
        )
        ocf_str = _format_metric_value_for_text("operating_cash_flow", ocf) if ocf is not None else None
        fcf_str = _format_metric_value_for_text("free_cash_flow", fcf) if fcf is not None else None
        capex_str = _format_metric_value_for_text("capital_expenditures", capex) if capex is not None else None
        cash_total_str = _format_metric_value_for_text("cash", cash_total) if cash_total is not None else None
        liabilities_str = _format_metric_value_for_text("total_liabilities", liabilities) if liabilities is not None else None

        fcf_margin_pct = None
        if fcf is not None and revenue:
            try:
                fcf_margin_pct = (fcf / revenue) * 100
            except Exception:
                fcf_margin_pct = None

        intro = "In my view," if is_persona else "The key question is whether"

        sentences: List[str] = []
        if rev_str and op_inc_str:
            sentences.append(
                f"{intro} {company_name}'s {rev_str} revenue base can translate into durable operating earnings (operating income {op_inc_str}) without relying on one-off items."
            )
        elif rev_str:
            sentences.append(
                f"{intro} {company_name}'s {rev_str} revenue base can translate into durable operating profitability as the business scales."
            )

        if operating_margin is not None and net_margin is not None:
            sentences.append(
                f"With operating margin at {operating_margin:.1f}% versus net margin at {net_margin:.1f}%, earnings quality matters as much as the headline profit figure."
            )

        if ocf_str and fcf_str:
            margin_clause = f" (~{fcf_margin_pct:.1f}% FCF margin)" if fcf_margin_pct is not None else ""
            capex_clause = f" after capex of {capex_str}" if capex_str else ""
            sentences.append(
                f"Cash conversion is the anchor: operating cash flow {ocf_str} converts to free cash flow {fcf_str}{margin_clause}{capex_clause}."
            )

        if cash_total_str and liabilities_str:
            sentences.append(
                f"Balance-sheet flexibility is {cash_total_str} cash and securities against {liabilities_str} liabilities, which keeps refinancing and downside scenarios in view."
            )

        if not sentences:
            return (
                f"In my view, the investment case hinges on whether operating profitability and cash conversion can compound without margin fragility or balance-sheet stress."
                if is_persona
                else "The investment case hinges on whether operating profitability and cash conversion can compound without margin fragility or balance-sheet stress."
            )

        return " ".join(sentences).strip()

    def _synthesize_financial_performance_addendum() -> str:
        """Add a short, concrete extension when the Financial Performance section is too thin."""

        is_persona = persona_mode

        revenue = calculated_metrics.get("revenue")
        operating_income = calculated_metrics.get("operating_income")
        net_income = calculated_metrics.get("net_income")
        operating_margin = calculated_metrics.get("operating_margin")
        net_margin = calculated_metrics.get("net_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        capex = calculated_metrics.get("capital_expenditures")
        fcf = calculated_metrics.get("free_cash_flow")

        rev_str = _format_metric_value_for_text("revenue", revenue) if revenue is not None else None
        op_inc_str = (
            _format_metric_value_for_text("operating_income", operating_income)
            if operating_income is not None
            else None
        )
        net_inc_str = (
            _format_metric_value_for_text("net_income", net_income)
            if net_income is not None
            else None
        )
        ocf_str = _format_metric_value_for_text("operating_cash_flow", ocf) if ocf is not None else None
        capex_str = _format_metric_value_for_text("capital_expenditures", capex) if capex is not None else None
        fcf_str = _format_metric_value_for_text("free_cash_flow", fcf) if fcf is not None else None

        sentences: List[str] = []
        lead = "I focus on" if is_persona else "Focus on"

        if rev_str and op_inc_str and operating_margin is not None:
            sentences.append(
                f"{lead} the run-rate engine: {rev_str} of revenue with operating income {op_inc_str} implies an operating margin of {operating_margin:.1f}%."
            )

        if net_inc_str and net_margin is not None and operating_margin is not None:
            gap = net_margin - operating_margin
            if abs(gap) >= 5:
                sentences.append(
                    f"The spread to net income {net_inc_str} (net margin {net_margin:.1f}%) signals meaningful below-the-line items, so I discount net margin when underwriting durability."
                    if is_persona
                    else f"The spread to net income {net_inc_str} (net margin {net_margin:.1f}%) signals meaningful below-the-line items, so net margin should be treated cautiously when underwriting durability."
                )

        if ocf_str and fcf_str:
            capex_clause = f" after capex of {capex_str}" if capex_str else ""
            sentences.append(
                f"Operating cash flow {ocf_str} converts to free cash flow {fcf_str}{capex_clause}, which is the cash that can fund reinvestment, buybacks, or balance-sheet de-risking."
            )

        sentences.append(
            "Watch whether unit economics improve via pricing discipline and efficiency, or compress under incentives, insurance, and regulatory costs."
        )

        return " ".join([s for s in sentences if s]).strip()

    def _synthesize_risk_factors_addendum() -> str:
        """Add concrete risk scenarios when Risk Factors is too thin.

        Goal: make Risk Factors feel like *underwriting*, not boilerplate.
        We keep this metric-anchored so it reads substantive.
        """

        is_persona = persona_mode
        lead = "I worry that" if is_persona else "A key risk is that"

        excerpt_lower = (risk_factors_excerpt or "").lower()
        reg_driver = _regulatory_driver_label(risk_factors_excerpt)

        revenue = calculated_metrics.get("revenue")
        operating_margin = calculated_metrics.get("operating_margin")
        net_margin = calculated_metrics.get("net_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        fcf = calculated_metrics.get("free_cash_flow")
        capex = calculated_metrics.get("capital_expenditures")
        cash = calculated_metrics.get("cash")
        liabilities = calculated_metrics.get("total_liabilities")

        fcf_str = (
            _format_metric_value_for_text("free_cash_flow", fcf) if fcf is not None else None
        )
        ocf_str = (
            _format_metric_value_for_text("operating_cash_flow", ocf)
            if ocf is not None
            else None
        )
        cash_str = (
            _format_metric_value_for_text("cash", cash) if cash is not None else None
        )
        liabilities_str = (
            _format_metric_value_for_text("total_liabilities", liabilities)
            if liabilities is not None
            else None
        )
        capex_str = (
            _format_metric_value_for_text("capital_expenditures", capex)
            if capex is not None
            else None
        )

        fcf_margin_pct = None
        if fcf is not None and revenue:
            try:
                fcf_margin_pct = (fcf / revenue) * 100
            except Exception:
                fcf_margin_pct = None

        risks: List[str] = []

        def _business_driver_hint() -> Optional[str]:
            if not excerpt_lower:
                return None
            if "advertis" in excerpt_lower or re.search(r"\bads?\b", excerpt_lower):
                return "advertising and monetization surfaces"
            if "search" in excerpt_lower:
                return "search distribution and traffic acquisition dynamics"
            if "cloud" in excerpt_lower:
                return "cloud competition and pricing"
            if "subscription" in excerpt_lower:
                return "subscription retention and churn"
            if "semiconductor" in excerpt_lower or "chip" in excerpt_lower:
                return "chip supply constraints and customer demand timing"
            if "loan" in excerpt_lower or "credit" in excerpt_lower:
                return "credit performance and funding costs"
            return None

        driver_hint = _business_driver_hint()

        # 0) Regulatory / enforcement when the filing flags it.
        if reg_driver:
            risk_name = _rewrite_generic_risk_name("Regulatory and Antitrust Scrutiny")
            margin_clause = (
                f" With operating margin around {operating_margin:.1f}%, even modest monetization friction can matter."
                if operating_margin is not None
                else ""
            )
            risks.append(
                f"**{risk_name}**: {lead} the filing's emphasis on {reg_driver.lower()} exposure translates into remedies, fines, or product changes that reduce monetization efficiency."
                f"{margin_clause} If enforcement forces changes to data use, distribution, or bundling, the impact often shows up first as slower pricing power and higher compliance cost."
            )

        # 1) Unit economics / cost shocks.
        if operating_margin is not None:
            risk_name = _rewrite_generic_risk_name("Margin Compression Risk")
            driver_clause = (
                f" This is most relevant given the business' dependence on {driver_hint}."
                if driver_hint
                else ""
            )
            risks.append(
                f"**{risk_name}**: {lead} the current operating margin (~{operating_margin:.1f}%) leaves less cushion if reinvestment, incentives, insurance, or compliance costs rise faster than pricing. "
                f"If growth slows at the same time, modest cost inflation can translate into outsized profit compression.{driver_clause}"
            )
        elif net_margin is not None:
            risk_name = _rewrite_generic_risk_name("Earnings Quality Risk")
            risks.append(
                f"**{risk_name}**: {lead} headline net margin ({net_margin:.1f}%) can be a noisy signal if below-the-line items fade or competitive spend re-accelerates. "
                "If profitability is being supported by one-offs, the market can reprice quickly when operating income normalizes."
            )
        else:
            risk_name = _rewrite_generic_risk_name("Competitive Spend Risk")
            risks.append(
                f"**{risk_name}**: {lead} competitive intensity can force higher incentive spend, compressing unit economics and delaying durable profitability. "
                "If the company cannot hold price, the path to operating leverage becomes more fragile."
            )

        # 2) Cash conversion durability.
        if ocf_str and fcf_str:
            risk_name = _rewrite_generic_risk_name("Cash Conversion Reversal Risk")
            margin_clause = (
                f" (~{fcf_margin_pct:.1f}% FCF margin)"
                if fcf_margin_pct is not None
                else ""
            )
            capex_clause = ""
            if capex_str:
                capex_clause = f" The OCF→FCF bridge implies heavy reinvestment (capex {capex_str})."
            elif ocf is not None and fcf is not None:
                implied = ocf - fcf
                implied_str = _format_metric_value_for_text("capital_expenditures", implied)
                if implied_str:
                    capex_clause = f" The OCF→FCF bridge implies material reinvestment (implied capex ~{implied_str})."
            risks.append(
                f"**{risk_name}**: With operating cash flow {ocf_str} and free cash flow {fcf_str}{margin_clause}, the risk is that cash conversion proves cyclical and normalizes lower if growth slows. "
                f"Working-capital timing and capex cycles can make a strong quarter look repeatable when it is not.{capex_clause}"
            )
        elif fcf_str:
            risk_name = _rewrite_generic_risk_name("Cash Conversion Risk")
            risks.append(
                f"**{risk_name}**: Free cash flow {fcf_str} is a strength, but the downside case is weaker cash conversion if competition forces higher spend or payments/working-capital terms worsen. "
                "If cash lags earnings for multiple quarters, valuation support can erode even if reported EPS holds up."
            )
        else:
            risk_name = _rewrite_generic_risk_name("Cash Flow Visibility Risk")
            risks.append(
                f"**{risk_name}**: {lead} reported profitability may not translate into free cash flow if working capital and capex are moving against the company. "
                "Without durable cash conversion, headline earnings become harder to underwrite."
            )

        # 3) Balance-sheet flexibility / refinancing.
        if cash_str and liabilities_str and cash and liabilities:
            if liabilities > cash * 2:
                risk_name = _rewrite_generic_risk_name("Balance-Sheet Flexibility Risk")
                reg_clause = (
                    " If enforcement outcomes drive fines, settlements, or higher ongoing compliance spend, liquidity can tighten faster than expected."
                    if reg_driver
                    else ""
                )
                risks.append(
                    f"**{risk_name}**: {liabilities_str} of liabilities versus {cash_str} cash can constrain optionality if credit spreads widen or refinancing windows tighten. "
                    f"In a downside scenario, management may be forced to prioritize de-risking over growth investment.{reg_clause}"
                )
            else:
                risk_name = _rewrite_generic_risk_name("Liquidity Tightening Risk")
                risks.append(
                    f"**{risk_name}**: Liquidity appears workable today ({cash_str} cash against {liabilities_str} liabilities), but a sharper downturn could still tighten flexibility if funding costs rise. "
                    "If leverage increases from here, the risk-reward can deteriorate quickly."
                )

        return "\n".join([r for r in risks if r]).strip()

    def _has_numeric_content(section_body: str) -> bool:
        return bool(re.search(r"\d", section_body))

    def _synthesize_mdna() -> str:
        """Fallback MD&A content: concise, concrete, and metric-anchored (no placeholders)."""

        is_persona = persona_mode

        revenue = calculated_metrics.get("revenue")
        operating_income = calculated_metrics.get("operating_income")
        operating_margin = calculated_metrics.get("operating_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        fcf = calculated_metrics.get("free_cash_flow")
        cash = calculated_metrics.get("cash")
        securities = calculated_metrics.get("marketable_securities")
        liabilities = calculated_metrics.get("total_liabilities")

        cash_total = None
        if cash is not None:
            cash_total = cash + (securities or 0)

        rev_str = (
            _format_metric_value_for_text("revenue", revenue)
            if revenue is not None
            else None
        )
        op_inc_str = (
            _format_metric_value_for_text("operating_income", operating_income)
            if operating_income is not None
            else None
        )
        ocf_str = (
            _format_metric_value_for_text("operating_cash_flow", ocf)
            if ocf is not None
            else None
        )
        fcf_str = (
            _format_metric_value_for_text("free_cash_flow", fcf)
            if fcf is not None
            else None
        )
        cash_total_str = (
            _format_metric_value_for_text("cash", cash_total)
            if cash_total is not None
            else None
        )
        liabilities_str = (
            _format_metric_value_for_text("total_liabilities", liabilities)
            if liabilities is not None
            else None
        )

        sentences: List[str] = []

        sentences.append(
            (
                "In my view, management has to convert scale into durable operating leverage while keeping growth investments disciplined."
                if is_persona
                else "The management narrative is about converting scale into operating leverage while keeping growth investments disciplined."
            )
        )

        if operating_margin is not None and rev_str and op_inc_str:
            sentences.append(
                f"With {rev_str} of revenue and operating income {op_inc_str}, the implied operating margin (~{operating_margin:.1f}%) puts the spotlight on pricing, incentive intensity, and cost discipline."
            )
        elif operating_margin is not None:
            sentences.append(
                f"With operating margin around {operating_margin:.1f}%, the key levers are pricing discipline, incentive intensity, and operating cost control."
            )

        if ocf_str and fcf_str:
            sentences.append(
                f"Cash generation (operating cash flow {ocf_str} and free cash flow {fcf_str}) creates optionality, but capital allocation choices determine whether value compounds or merely cycles."
            )

        if cash_total_str and liabilities_str:
            sentences.append(
                f"Balance-sheet context—{cash_total_str} cash and securities versus {liabilities_str} liabilities—frames how aggressive management can be on buybacks, M&A, or de-risking."
            )

        sentences.append(
            "A strong MD&A reconciles KPI commentary with cash flow and makes the next margin and risk trade-offs explicit rather than relying on adjusted optics."
        )

        return " ".join([s for s in sentences if s]).strip()

    def _synthesize_mdna_addendum() -> str:
        """Add a short, metric-anchored continuation to strengthen MD&A flow."""

        is_persona = persona_mode
        lead = "I want to see" if is_persona else "The key check is"

        operating_margin = calculated_metrics.get("operating_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        fcf = calculated_metrics.get("free_cash_flow")
        cash = calculated_metrics.get("cash")
        securities = calculated_metrics.get("marketable_securities")
        liabilities = calculated_metrics.get("total_liabilities")

        cash_total = None
        if cash is not None:
            cash_total = cash + (securities or 0)

        ocf_str = (
            _format_metric_value_for_text("operating_cash_flow", ocf)
            if ocf is not None
            else None
        )
        fcf_str = (
            _format_metric_value_for_text("free_cash_flow", fcf) if fcf is not None else None
        )
        cash_total_str = (
            _format_metric_value_for_text("cash", cash_total)
            if cash_total is not None
            else None
        )
        liabilities_str = (
            _format_metric_value_for_text("total_liabilities", liabilities)
            if liabilities is not None
            else None
        )

        sentences: List[str] = []

        if operating_margin is not None:
            sentences.append(
                f"{lead} operating leverage stay visible in the reported operating margin (~{operating_margin:.1f}%) without relying on one-offs or short-term timing benefits."
            )

        if ocf_str and fcf_str:
            sentences.append(
                f"With operating cash flow {ocf_str} and free cash flow {fcf_str}, the durability test is whether conversion holds as reinvestment and working-capital needs normalize."
            )

        if cash_total_str and liabilities_str:
            sentences.append(
                f"Against {liabilities_str} of liabilities, {cash_total_str} of cash and securities supports flexibility—but buybacks and M&A only compound if they do not raise refinancing risk or dilute the core margin profile."
            )
        else:
            sentences.append(
                "Capital allocation compounds best when reinvestment returns are clear and leverage stays contained."
            )

        return " ".join([s for s in sentences if s]).strip()

    # Add minimal Financial Performance and MD&A sections if the model omitted them
    # 0. Executive Summary (rarely missing, but catastrophic when it is)
    if not _section_present("Executive Summary"):
        _append_section("Executive Summary", _synthesize_executive_summary_addendum())

    if not _section_present("Financial Performance"):
        _append_section("Financial Performance", _synthesize_financial_performance())
    else:
        # If section exists but lacks numbers, append a concise metric summary
        fp_match = re.search(
            r"##\s*Financial Performance\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
            text,
            re.IGNORECASE,
        )
        if fp_match:
            body = fp_match.group(1).strip()
            if not _has_numeric_content(body):
                supplement = _synthesize_financial_performance()
                text = text.replace(
                    fp_match.group(0),
                    f"## Financial Performance\n{body}\n\n{supplement}\n",
                )

    # If Executive Summary / Financial Performance are present but too short, top them up.
    def _top_up_section_if_short(title: str, min_words: int, addendum: str) -> None:
        nonlocal text
        pattern = re.compile(
            rf"##\s*{re.escape(title)}\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if not m:
            return
        body = (m.group(1) or "").strip()
        if _count_words(body) >= min_words:
            return

        def _norm_sentence(sentence: str) -> str:
            sentence = (sentence or "").replace("\u00A0", " ")
            sentence = " ".join(sentence.lower().split())
            return sentence.rstrip(".!?")

        def _split_sentences(blob: str) -> List[str]:
            blob = (blob or "").strip()
            if not blob:
                return []
            return [s.strip() for s in re.split(r"(?<=[.!?])\s+", blob) if s.strip()]

        # Avoid introducing duplicates when the addendum overlaps with what the model
        # already wrote (a common repetition pattern users notice).
        body_norms = {_norm_sentence(s) for s in _split_sentences(body)}
        filtered_addendum_sentences: List[str] = []
        for sentence in _split_sentences(addendum):
            norm = _norm_sentence(sentence)
            if not norm or norm in body_norms:
                continue
            filtered_addendum_sentences.append(sentence)
            body_norms.add(norm)
        addendum = " ".join(filtered_addendum_sentences).strip()
        if not addendum:
            return

        # Append as a continuation to preserve flow (avoid orphan "one-liner" paragraphs).
        if title == "Risk Factors":
            joiner = "\n\n"
        else:
            cleaned = body.rstrip()
            cleaned = re.sub(r"[-\u2013\u2014]+\s*$", "", cleaned).rstrip()
            joiner = " " if cleaned.endswith((".", "!", "?")) else ". "
            body = cleaned
        new_body = f"{body}{joiner}{addendum}".strip()
        text = pattern.sub(lambda _mm: f"## {title}\n{new_body}\n", text, count=1)

    # Ensure MD&A exists before we try to balance section lengths.
    if not _section_present("Management Discussion & Analysis"):
        _append_section("Management Discussion & Analysis", _synthesize_mdna())
    else:
        # Replace low-quality MD&A stubs the model sometimes emits.
        mdna_match = re.search(
            r"##\s*Management\s+Discussion\s*(?:&|and)\s*Analysis\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
            text,
            re.IGNORECASE,
        )
        if mdna_match:
            body = (mdna_match.group(1) or "").strip()
            if re.search(r"\bmanagement\s+discussion\s+should\s+focus\b", body, re.IGNORECASE) or re.search(
                r"\breference\s+operating\s+cash\s+flow\b",
                body,
                re.IGNORECASE,
            ):
                replacement = _synthesize_mdna()
                text = re.sub(
                    r"##\s*Management\s+Discussion\s*(?:&|and)\s*Analysis\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                    lambda _m: f"## Management Discussion & Analysis\n{replacement}\n",
                    text,
                    count=1,
                    flags=re.IGNORECASE,
                )

    # Ensure Risk Factors exists before we try to balance section lengths.
    # If the model omitted it (often due to length pressure), synthesize a concrete,
    # underwriting-style risks paragraph rather than leaving the memo incomplete.
    if not _section_present("Risk Factors"):
        _append_section("Risk Factors", _synthesize_risk_factors_addendum())

    # Top up short narrative sections to keep distribution more even.
    _top_up_section_if_short(
        "Executive Summary",
        min_words_by_section.get("Executive Summary", 80),
        _synthesize_executive_summary_addendum(),
    )
    _top_up_section_if_short(
        "Financial Performance",
        min_words_by_section.get("Financial Performance", 95),
        _synthesize_financial_performance_addendum(),
    )
    _top_up_section_if_short(
        "Management Discussion & Analysis",
        min_words_by_section.get("Management Discussion & Analysis", 140),
        _synthesize_mdna_addendum(),
    )
    _normalize_risk_factors_section()

    # 6. Key Metrics - always useful to include a factual appendix
    if not _section_present("Key Metrics") and metrics_lines.strip():
        body = metrics_lines.strip()
        if (
            include_health_rating
            and health_score_data
            and "Health Score Drivers" not in body
        ):
            drivers_block = _build_health_driver_block(
                calculated_metrics, health_score_data
            )
            if drivers_block:
                body = f"{body}\n\n{drivers_block}".strip()
        _append_section("Key Metrics", body)

    # 8. Closing Takeaway - ensure there's a closing verdict if missing OR too short
    # Generate a data-driven closing takeaway if the AI failed to include one
    # Also replace if the existing one is under the minimum word count
    # Pass persona_name to maintain persona voice in fallback
    def _min_closing_words() -> int:
        """Minimum closing length aligned to the fixed section distribution."""
        base_min = int(min_words_by_section.get("Closing Takeaway", 25))
        if not target_length or target_length <= 0:
            # No explicit target length => keep a substantive close.
            return max(base_min, 35)

        budgets = _calculate_section_word_budgets(
            target_length, include_health_rating=include_health_rating
        )
        budget = int(budgets.get("Closing Takeaway", 0) or 0)
        if budget <= 0:
            return base_min

        # Require most of the allocated budget so the closing doesn't collapse.
        floor = max(1, int(budget * 0.70))
        return max(base_min, floor)

    def _min_closing_sentences() -> int:
        if not target_length or target_length <= 0:
            return 3

        budgets = _calculate_section_word_budgets(
            target_length, include_health_rating=include_health_rating
        )
        budget = int(budgets.get("Closing Takeaway", 0) or 0)
        if budget >= 75:
            return 4
        if budget >= 45:
            return 3
        return 2

    def _closing_has_reasoned_takeaway(text: str) -> bool:
        if not text:
            return False
        has_reason = bool(
            re.search(
                r"\b(because|driven|reflects|due to|given|supported by)\b",
                text,
                re.IGNORECASE,
            )
        ) or bool(re.search(r"\d", text))
        has_change = bool(
            re.search(
                r"\b(if|unless|would change|upgrade|downgrade|revisit|re-rate|improve|deteriorate)\b",
                text,
                re.IGNORECASE,
            )
        )
        return has_reason and has_change

    min_closing_words = _min_closing_words()
    min_closing_sentences = _min_closing_sentences()

    # Check if closing takeaway exists and count its words
    existing_closing = None
    closing_match = re.search(
        r"##\s*Closing\s+Takeaway\s*\n+([\s\S]*?)(?=\n##\s|\Z)", text, re.IGNORECASE
    )
    if closing_match:
        existing_closing = closing_match.group(1).strip()
        existing_word_count = _count_words(existing_closing)
        existing_sentence_count = _count_sentences(existing_closing)

        verdict_strengths: List[str] = []
        verdict_concerns: List[str] = []
        if calculated_metrics:
            op_margin = calculated_metrics.get("operating_margin", 0)
            net_margin = calculated_metrics.get("net_margin", 0)
            fcf = calculated_metrics.get("free_cash_flow", 0)
            revenue = calculated_metrics.get("revenue") or calculated_metrics.get(
                "total_revenue"
            )
            cash = calculated_metrics.get("cash")
            securities = calculated_metrics.get("marketable_securities") or 0
            total_liabilities = calculated_metrics.get("total_liabilities")
            total_assets = calculated_metrics.get("total_assets")
            current_ratio = calculated_metrics.get("current_ratio")
            interest_coverage = calculated_metrics.get("interest_coverage")

            cash_total = None
            if cash is not None:
                cash_total = cash + (securities or 0)

            fcf_margin_pct = None
            if fcf and revenue:
                try:
                    fcf_margin_pct = (fcf / revenue) * 100
                except Exception:
                    fcf_margin_pct = None

            if op_margin and op_margin > 20:
                verdict_strengths.append("strong operating margin")
            if net_margin and net_margin > 10:
                verdict_strengths.append("healthy profitability")
            if fcf and fcf > 0 and fcf_margin_pct is not None and fcf_margin_pct >= 10:
                verdict_strengths.append("strong free cash flow conversion")
            elif fcf and fcf > 0:
                verdict_strengths.append("positive free cash flow")

            if op_margin and op_margin < 5:
                verdict_concerns.append("thin margins")
            if net_margin and net_margin < 0:
                verdict_concerns.append("net losses")
            if fcf is not None and fcf <= 0:
                verdict_concerns.append("negative free cash flow")
            if fcf_margin_pct is not None and fcf_margin_pct < 8:
                verdict_concerns.append("low free-cash-flow margin")

            if cash_total is not None and total_liabilities is not None:
                if cash_total > total_liabilities:
                    verdict_strengths.append("net cash balance sheet")
                elif total_liabilities > cash_total * 2:
                    verdict_concerns.append("large liability load versus cash")

            if total_liabilities and total_assets:
                try:
                    leverage = total_liabilities / total_assets
                except Exception:
                    leverage = None
                if leverage is not None and leverage > 0.8:
                    verdict_concerns.append("elevated leverage")

            if current_ratio is not None and current_ratio >= 1.5:
                verdict_strengths.append("solid liquidity")
            elif current_ratio is not None and current_ratio < 1.0:
                verdict_concerns.append("tight liquidity")

            if interest_coverage is not None and interest_coverage >= 8:
                verdict_strengths.append("strong interest coverage")
            elif interest_coverage is not None and interest_coverage < 2:
                verdict_concerns.append("thin interest coverage")

        # If persona is requested but the closing lacks an explicit personal verdict, add one.
        if persona_mode and not _contains_personal_verdict(existing_closing):
            patched = _ensure_personal_verdict(
                existing_closing,
                company_name,
                strengths=verdict_strengths,
                concerns=verdict_concerns,
            )
            # Use a function replacement to avoid backreference/template parsing issues
            text = re.sub(
                r"##\s*Closing\s+Takeaway\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                lambda _m: f"## Closing Takeaway\n{patched}\n",
                text,
                flags=re.IGNORECASE,
            )
            existing_closing = patched
            existing_word_count = _count_words(existing_closing)
            existing_sentence_count = _count_sentences(existing_closing)
        elif (not persona_mode) and not _contains_objective_recommendation(existing_closing):
            patched = _ensure_objective_recommendation(
                existing_closing,
                company_name,
                strengths=verdict_strengths,
                concerns=verdict_concerns,
            )
            text = re.sub(
                r"##\s*Closing\s+Takeaway\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                lambda _m: f"## Closing Takeaway\n{patched}\n",
                text,
                flags=re.IGNORECASE,
            )
            existing_closing = patched
            existing_word_count = _count_words(existing_closing)
            existing_sentence_count = _count_sentences(existing_closing)
    else:
        existing_word_count = 0
        existing_sentence_count = 0

    needs_closing_rebuild = (
        not _section_present("Closing Takeaway")
        or existing_word_count < min_closing_words
        or existing_sentence_count < min_closing_sentences
        or not _closing_has_reasoned_takeaway(existing_closing or "")
        or (
            persona_mode
            and not _contains_personal_verdict(existing_closing or "")
        )
        or (
            (not persona_mode)
            and not _contains_objective_recommendation(existing_closing or "")
        )
    )

    if needs_closing_rebuild:
        closing_body = _generate_fallback_closing_takeaway(
            company_name,
            calculated_metrics,
            persona_name,
            persona_requested=persona_mode,
        )
        if existing_closing:
            # Remove the existing closing takeaway and replace it
            text = re.sub(
                r"##\s*Closing\s+Takeaway\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                "",
                text,
                flags=re.IGNORECASE,
            )
        _append_section("Closing Takeaway", closing_body)

    # Normalize Key Metrics body to the deterministic block sized to the fixed
    # proportional distribution (prevents hallucinated numbers / emojis).
    if metrics_lines.strip() and _section_present("Key Metrics"):
        desired_body = metrics_lines.strip()
        if include_health_rating and health_score_data:
            drivers_block = _build_health_driver_block(
                calculated_metrics, health_score_data
            )
            if drivers_block and "Health Score Drivers" not in desired_body:
                desired_body = f"{desired_body}\n\n{drivers_block}".strip()

        # Keep Key Metrics near its allocated budget for the chosen target length.
        if target_length and target_length > 0:
            budgets = _calculate_section_word_budgets(
                target_length, include_health_rating=include_health_rating
            )
            km_budget = int(budgets.get("Key Metrics", 0) or 0)
            if km_budget > 0:
                min_words = int(min_words_by_section.get("Key Metrics", 1))
                max_words = max(min_words, int(km_budget * 1.15))
                if _count_words(desired_body) > max_words:
                    desired_body = _trim_appendix_preserving_rows(desired_body, max_words)
        km_pattern = re.compile(
            r"##\s*(Key Metrics|Key Data Appendix)\s*\n+[\s\S]*?(?=\n##\s|\Z)",
            re.IGNORECASE,
        )
        text = km_pattern.sub(f"## Key Metrics\n{desired_body}\n", text)

    # If the model produced a highly lopsided memo (e.g., a huge Risk Factors section
    # but a stub MD&A), cap the *maximum* size of the core narrative sections based
    # on the computed budgets. This makes distribution more consistent even when the
    # model ignores instructions.
    if target_length and target_length > 0:
        budgets = _calculate_section_word_budgets(
            target_length, include_health_rating=include_health_rating
        )

        def _cap_section_to_budget(title: str) -> None:
            nonlocal text
            budget = budgets.get(title)
            if not budget:
                return
            min_words = int(min_words_by_section.get(title, 25))
            max_words = max(min_words, int(budget * 1.30))
            pattern = re.compile(
                rf"##\s*{re.escape(title)}\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
                re.IGNORECASE,
            )
            m = pattern.search(text)
            if not m:
                return
            body = (m.group(1) or "").strip()
            if _count_words(body) <= max_words:
                return
            trimmed = _truncate_text_to_word_limit(body, max_words)
            text = pattern.sub(lambda _mm: f"## {title}\n{trimmed}\n", text, count=1)

        core_titles: List[str] = [
            "Executive Summary",
            "Financial Performance",
            "Management Discussion & Analysis",
            "Risk Factors",
            "Closing Takeaway",
        ]
        if include_health_rating:
            core_titles.insert(0, "Financial Health Rating")

        for core_title in core_titles:
            _cap_section_to_budget(core_title)

    # Final repetition cleanup: this function can append addenda and padding that
    # occasionally overlaps with model output, so remove duplicates deterministically.
    text = _dedupe_consecutive_sentences(text)
    text = _deduplicate_sentences(text)

    return text


class SummaryExportRequest(BaseModel):
    format: Literal["pdf", "docx"] = Field(...)
    summary: str = Field(..., min_length=1, max_length=250_000)
    title: Optional[str] = Field(default=None, max_length=200)
    filing_type: Optional[str] = Field(default=None, max_length=50)
    filing_date: Optional[str] = Field(default=None, max_length=50)
    generated_at: Optional[str] = Field(default=None, max_length=50)


@router.post("/{filing_id}/summary/export")
async def export_filing_summary(
    filing_id: str,
    payload: SummaryExportRequest = Body(...),
):
    """Export a generated summary as a PDF or Word (DOCX) document."""
    metadata_lines: list[str] = [f"Filing ID: {filing_id}"]
    if payload.filing_type:
        metadata_lines.append(f"Filing Type: {payload.filing_type}")
    if payload.filing_date:
        metadata_lines.append(f"Filing Date: {payload.filing_date}")
    if payload.generated_at:
        metadata_lines.append(f"Generated: {payload.generated_at}")

    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", filing_id).strip("_")[:60] or "summary"

    try:
        if payload.format == "pdf":
            pdf_bytes = build_summary_pdf(
                summary_md=payload.summary,
                title=payload.title or "AI Brief",
                metadata_lines=metadata_lines,
            )
            return StreamingResponse(
                io.BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="summary-{safe_id}.pdf"'
                },
            )

        docx_bytes = build_summary_docx(
            summary_md=payload.summary,
            title=payload.title or "AI Brief",
            metadata_lines=metadata_lines,
        )
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="summary-{safe_id}.docx"'
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Summary export failed for %s", filing_id)
        raise HTTPException(status_code=500, detail="Failed to export summary") from exc


@router.get("/{filing_id}/progress")
async def get_filing_summary_progress(filing_id: str):
    """Get real-time progress of summary generation."""
    status = progress_cache.get(str(filing_id), "Initializing...")
    return {"status": status}


@router.get("/{filing_id}/health")
async def get_filing_health(filing_id: str):
    """Get health score for a filing."""
    settings = get_settings()
    try:
        context = _resolve_filing_context(filing_id, settings)
        filing = context["filing"]
        company = context["company"]

        # Get document content
        local_document = _ensure_local_document(context, settings)
        statements = fallback_financial_statements.get(str(filing_id))

        if not statements:
            # Fallback to financial statements
            try:
                supabase = get_supabase_client()
                statement_response = (
                    supabase.table("financial_statements")
                    .select("*")
                    .eq("filing_id", filing.get("id"))
                    .execute()
                )
                if statement_response.data:
                    statements = statement_response.data[0]
            except Exception as exc:
                logger.warning(
                    f"Failed to load financial statements for {filing_id}: {exc}"
                )

        if not statements:
            return JSONResponse(
                content={"error": "No financial data available for health scoring"}
            )

        # Extract calculated metrics
        calculated_metrics = _build_calculated_metrics(statements)

        # Compute health score
        health_data = _compute_health_score_data(calculated_metrics)

        return JSONResponse(
            content={
                "filing_id": filing_id,
                "company": company.get("name"),
                "health_score": health_data.get("overall_score"),
                "health_band": health_data.get("score_band"),
                "health_components": health_data.get("component_scores"),
                "health_component_weights": health_data.get("component_weights"),
                "health_component_descriptions": health_data.get(
                    "component_descriptions"
                ),
                "health_component_metrics": health_data.get("component_metrics"),
                "calculated_metrics": calculated_metrics,
            }
        )
    except Exception as exc:
        logger.error(f"Error getting filing health: {exc}")
        return JSONResponse(content={"error": str(exc)}, status_code=500)
