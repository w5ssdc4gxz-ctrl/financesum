"""Filings API endpoints."""

import anyio
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
from datetime import datetime, timedelta, timezone, date
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable, Literal, Set
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
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
    resolve_cik_from_ticker_sync,
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
from app.services.summary_progress import (
    start_summary_progress,
    set_summary_progress,
    complete_summary_progress,
    get_summary_progress_snapshot,
)
from app.services.billing_usage import get_summary_usage_status
from app.services.summary_export import build_summary_docx, build_summary_pdf
from app.services.gemini_client import get_gemini_client, generate_growth_assessment
from app.services.spotlight_kpi.service import build_spotlight_payload_for_filing
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

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None  # type: ignore[assignment]

# Gemini 2.0 Flash Lite supports up to ~1M tokens. Cap context to keep requests fast.
MAX_GEMINI_CONTEXT_CHARS = 200_000
# Summary quality can degrade quickly when we fall back to deterministic padding.
# Reduced from 3 to 2 attempts to speed up generation.
MAX_SUMMARY_ATTEMPTS = 2


def _extract_word_count_control(text: str) -> Tuple[str, Optional[int]]:
    """Extract an optional model-reported word count control token.

    Some prompts ask the model to append a line like `WORD_COUNT: 1234`.
    We return the text with that token stripped and the parsed count.
    """
    if not text:
        return text, None

    match = re.search(r"\bWORD_COUNT\s*[:=]\s*(\d{1,6})\b", text)
    if not match:
        return text, None

    reported: Optional[int]
    try:
        reported = int(match.group(1))
    except (TypeError, ValueError):
        reported = None

    cleaned = re.sub(r"\bWORD_COUNT\s*[:=]\s*\d{1,6}\b", "", text).strip()
    return cleaned, reported


def _normalize_casing(text: str) -> str:
    """Reduce shouty ALL-CAPS body text without touching headings."""
    if not text:
        return text

    lines = text.splitlines()
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("#"):
            out.append(line)
            continue

        letters = [c for c in stripped if c.isalpha()]
        if not letters:
            out.append(line)
            continue

        upper_letters = sum(1 for c in letters if c.isupper())
        upper_ratio = upper_letters / max(1, len(letters))
        if upper_ratio >= 0.9 and len(letters) >= 12:
            lowered = stripped.lower()
            lowered = lowered[:1].upper() + lowered[1:]
            out.append(lowered)
        else:
            out.append(line)

    return "\n".join(out)


# Allow a couple of rewrite passes so we hit the strict word band
# without relying on low-quality deterministic padding.
MAX_REWRITE_ATTEMPTS = 2  # Reduced from 3 to speed up generation
SUMMARY_TOTAL_TIMEOUT_SECONDS = 60  # Reduced from 120s to keep UI responsive

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
DEFAULT_GEMINI_MAX_OUTPUT_TOKENS = 4500
DEFAULT_SUMMARY_TOKEN_RESERVE = 0
CHARS_PER_TOKEN_ESTIMATE = 4
DEFAULT_SPOTLIGHT_DOCUMENT_EXCERPT_CHARS = 650_000
DEFAULT_SUMMARY_DOCUMENT_EXCERPT_CHARS = 240_000
DEFAULT_SUMMARY_PDF_MAX_PAGES = 80


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
        return (
            prompt_tokens + max(0, int(expected_output_tokens))
        ) <= self.remaining_tokens

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


def _spotlight_document_excerpt_limit() -> int:
    """Max chars to keep from filing text for Spotlight KPI extraction.

    Summary prompts are still truncated separately; this limit primarily improves
    Spotlight coverage for long filings where the best KPI tables often sit mid-document.
    """
    return _int_env(
        "SPOTLIGHT_DOCUMENT_EXCERPT_CHARS", DEFAULT_SPOTLIGHT_DOCUMENT_EXCERPT_CHARS
    )


def _summary_document_excerpt_limit() -> int:
    return _int_env(
        "SUMMARY_DOCUMENT_EXCERPT_CHARS", DEFAULT_SUMMARY_DOCUMENT_EXCERPT_CHARS
    )


def _summary_pdf_max_pages() -> int:
    return _int_env("SUMMARY_PDF_MAX_PAGES", DEFAULT_SUMMARY_PDF_MAX_PAGES)


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
    "value_investor_default": "Prioritize cash flow durability, balance sheet strength, and downside protection.",
    "quality_moat_focus": "Emphasize ROIC consistency, competitive advantage, and earnings stability.",
    "financial_resilience": "Stress-test liquidity, leverage, refinancing risk, and debt schedules.",
    "growth_sustainability": "Evaluate margin expansion, reinvestment efficiency, and the long-term growth path.",
    "user_defined_mix": "Treat profitability, risk, liquidity, growth, and efficiency with equal importance.",
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


TARGET_LENGTH_MIN_WORDS = 1
TARGET_LENGTH_MAX_WORDS = 3000


def _clamp_target_length(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(TARGET_LENGTH_MIN_WORDS, min(TARGET_LENGTH_MAX_WORDS, value))


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
        length_guidance = f"Target ~{int(budget_words)} words (±{int(budget_tolerance)}) in the Closing Takeaway body."
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
        "Explicitly reference the most important change versus the immediately prior comparable period (QoQ for 10-Q, YoY for 10-K) that drove your stance.\n"
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

    if (
        persona_requested
        and persona_name
        and persona_name in PERSONA_CLOSING_INSTRUCTIONS
    ):
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
            "- Persona-specific language patterns from any famous investor\n"
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
    eligible_for_tail_trim: List[bool] = []

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

    def _trim_one_tail_word(line: str) -> Tuple[str, int]:
        """Remove exactly one trailing word-like token from a line.

        This is a last-resort micro-trim when the text contains no removable filler
        words (e.g., synthetic test tokens) but we still need to shave 1–3 words to
        satisfy strict length bounds. It prefers trimming the *tail* to minimize
        semantic disruption.
        """
        original = (line or "").rstrip()
        if not original:
            return line, 0

        tokens = re.findall(r"\S+", original)
        if len(tokens) < 2:
            return line, 0

        # Find the last word-like token (contains at least one alnum).
        idx = None
        for i in range(len(tokens) - 1, -1, -1):
            if re.search(r"[A-Za-z0-9]", tokens[i]):
                idx = i
                break
        if idx is None:
            return line, 0

        trailing_punct = ""
        if original.endswith((".", "!", "?")):
            trailing_punct = original[-1]

        del tokens[idx]
        rebuilt = " ".join(tokens).strip()
        if trailing_punct and rebuilt and not rebuilt.endswith((".", "!", "?")):
            rebuilt = rebuilt.rstrip() + trailing_punct
        return rebuilt, 1

    for line in text.splitlines():
        m = heading_re.match(line)
        if m:
            current_section = " ".join(m.group(1).lower().split())
            out_lines.append(line)
            eligible_for_tail_trim.append(False)
            continue

        if removed >= max_remove:
            out_lines.append(line)
            eligible_for_tail_trim.append(False)
            continue

        if current_section and any(
            current_section.startswith(skip) for skip in _MICRO_TRIM_SKIP_SECTIONS
        ):
            out_lines.append(line)
            eligible_for_tail_trim.append(False)
            continue

        # Skip strict metric lines (arrow format) even outside Key Metrics.
        if line.lstrip().startswith("→"):
            out_lines.append(line)
            eligible_for_tail_trim.append(False)
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

        cleaned = _cleanup_spaces(working)
        out_lines.append(cleaned)
        eligible_for_tail_trim.append(bool(cleaned.strip()))

    if removed < max_remove:
        # Last resort: if we still need to shave a couple of words and the
        # conservative filler-trims did nothing, trim tail words from eligible lines.
        remaining = int(max_remove) - int(removed)
        for i in range(len(out_lines) - 1, -1, -1):
            if remaining <= 0:
                break
            if not eligible_for_tail_trim[i]:
                continue
            new_line, did = _trim_one_tail_word(out_lines[i])
            if did:
                out_lines[i] = new_line
                removed += did
                remaining -= did

    return "\n".join(out_lines), removed


def _micro_pad_tail_words(text: str, min_add_words: int) -> str:
    """Append a tiny, natural-looking phrase when we need 1–5 extra words.

    This is only a last resort when sentence-level padding templates are exhausted
    (e.g., due to dedupe rules) but strict word bands still need to be met.
    """
    if not text or min_add_words <= 0:
        return text

    # Minimal phrases with stable word counts (no punctuation-only tokens).
    #
    # IMPORTANT:
    # - Avoid "meta-filler" like "in sum / on balance / as things stand" which can
    #   accumulate into visible spam if padding runs multiple times.
    # - Avoid imperative "Watch/Monitor/Track" sentences because upstream filler
    #   cleanup may strip them, creating padding loops.
    phrases: List[str] = [
        "key swing factor",
        "primary swing factor",
        "near term factors",
        "in practical terms",
        "fundamental catalyst",
        "structural driver",
        "operating backdrop",
        "valuation context",
        "execution priority",
        "capital intensity backdrop",
        "on a forward basis",
        "relative to the cycle",
        "across the enterprise",
        "from a risk standpoint",
        "on a relative basis",
        "under current conditions",
        "given the trajectory",
        "within this framework",
    ]

    def _wc(s: str) -> int:
        return len([w for w in (s or "").split() if w.strip()])

    eligible = [p for p in phrases if _wc(p) >= min_add_words]
    if not eligible:
        eligible = ["key swing factor"]

    lowered = (text or "").lower()
    # Avoid repeating the *same* micro-phrase, but allow an alternate phrase if the draft
    # already contains one of them (models sometimes include "key swing factor" naturally).
    pool = [p for p in eligible if p.lower() not in lowered]
    if not pool:
        return text

    # Append as many distinct phrases as needed to reach the target.
    # We use a loop to avoid one long run-on sentence.
    current_text = text
    words_added = 0

    for _ in range(10):
        if words_added >= min_add_words:
            break

        # Avoid repeating the same micro-phrase.
        lowered = (current_text or "").lower()
        pool = [p for p in eligible if p.lower() not in lowered]
        if not pool:
            break

        pool.sort(key=_wc)
        smallest_wc = _wc(pool[0])
        same_wc = [p for p in pool if _wc(p) == smallest_wc]
        digest = hashlib.sha256(
            f"{len(current_text)}:{min_add_words}:{current_text[-120:]}".encode("utf-8")
        ).digest()
        idx = int.from_bytes(digest[:2], "big") % len(same_wc)
        best = same_wc[idx]

        lines = current_text.splitlines()
        success = False
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue

            # Append as a short clause.
            if line.endswith((".", "!", "?")):
                line = line[:-1].rstrip()
            lines[i] = f"{line} ({best})."
            current_text = "\n".join(lines).strip()
            words_added += _wc(best)
            success = True
            break

        if not success:
            # Fallback: append as a final sentence.
            suffix = best
            if not suffix.endswith((".", "!", "?")):
                suffix += "."
            current_text = f"{current_text.rstrip()}\n\n{suffix}".strip()
            words_added += _wc(best)

    return current_text


def _enforce_whitespace_word_band(
    text: str,
    target_length: int,
    tolerance: int = 10,
    *,
    allow_padding: bool = False,
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

    # Keep the final output inside the global target-length bounds even when the user
    # selects an extreme. This prevents "within ±10" from exceeding the absolute min/max.
    lower = max(TARGET_LENGTH_MIN_WORDS, target_length - tolerance)
    upper = min(TARGET_LENGTH_MAX_WORDS, target_length + tolerance)

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
            if re.match(r"^\s*[-*•]\s*$", line) or re.match(r"^\s*→\s*$", line):
                changed = True
                continue
            # Strip leading list markers (keep the content).
            stripped = re.sub(r"^(\s*)[-*•]\s+", r"\1", line)
            if stripped != line:
                changed = True
            # Merge leading arrow marker into the next token (Key Metrics style) so it
            # doesn't count as its own whitespace token (e.g., "→ Revenue" → "→Revenue").
            stripped2 = re.sub(r"^(\s*)→\s+", r"\1→", stripped)
            if stripped2 != stripped:
                changed = True
            stripped = stripped2
            # Merge whitespace-separated punctuation tokens that inflate `split()` but
            # do not affect `_count_words()` (e.g., "A / B" → "A/B").
            merged = re.sub(r"\s+/\s+", "/", stripped)
            if merged != stripped:
                changed = True
            stripped = merged
            out_lines.append(stripped)

        if not changed:
            return value
        return "\n".join(out_lines).strip()

    micro_pad_used = False

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
            if (
                split_count > upper
                and (split_count - stripped_count) > 0
                and stripped_count <= upper
            ):
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
        if deficit <= 5:
            if not micro_pad_used:
                text = _micro_pad_tail_words(text, deficit)
                micro_pad_used = True
            else:
                # Avoid chaining many tiny phrases; prefer a single short sentence.
                is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", text))
                padding = _generate_padding_sentences(
                    deficit,
                    exclude_norms=set(),
                    section=None,
                    is_persona=is_persona,
                    max_words=12,
                )
                if padding:
                    block = " ".join(padding).strip()
                    if block:
                        text = f"{text.rstrip()}\n\n{block}".strip()
            continue
        padded = _distribute_padding_across_sections(text, deficit)
        remaining = max(lower - len(padded.split()), lower - _count_words(padded), 0)
        if remaining > 0 and remaining <= 5:
            if not micro_pad_used:
                padded = _micro_pad_tail_words(padded, remaining)
                micro_pad_used = True
            else:
                is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", padded))
                padding = _generate_padding_sentences(
                    remaining,
                    exclude_norms=set(),
                    section=None,
                    is_persona=is_persona,
                    max_words=12,
                )
                if padding:
                    block = " ".join(padding).strip()
                    if block:
                        padded = f"{padded.rstrip()}\n\n{block}".strip()
        text = padded

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
        if deficit <= 5:
            text = _micro_pad_tail_words(text, deficit)
            continue
        padded = _distribute_padding_across_sections(text, deficit)
        remaining = max(lower - len(padded.split()), lower - _count_words(padded), 0)
        if remaining > 0 and remaining <= 5:
            padded = _micro_pad_tail_words(padded, remaining)
        text = padded

    # Absolute final guard: never return a memo that violates the user-visible band.
    # Prefer trimming over padding if we can't satisfy both counts simultaneously.
    for _ in range(50):
        split_count = len(text.split())
        stripped_count = _count_words(text)
        if lower <= split_count <= upper and lower <= stripped_count <= upper:
            return text

        if split_count > upper or stripped_count > upper:
            cleaned = _strip_markdown_list_tokens(text)
            if cleaned and cleaned != text and len(cleaned.split()) < split_count:
                text = cleaned
                continue

            delta = max(0, split_count - stripped_count)
            effective_upper = max(lower, upper - delta)

            excess = max(split_count - upper, stripped_count - upper, 1)
            target_words = max(lower, min(effective_upper, stripped_count - excess))
            next_text = _trim_preserving_headings(text, target_words)
            if next_text == text:
                # Force an extra word drop if trimming got stuck.
                next_text = _trim_preserving_headings(
                    text, max(lower, target_words - 1)
                )
            if next_text == text:
                break
            text = next_text
            continue

        deficit = max(lower - split_count, lower - stripped_count)
        if deficit <= 0 or not allow_padding:
            break
        next_text = _distribute_padding_across_sections(text, deficit)
        if next_text == text:
            break
        text = next_text

    # If we still can't hit the band, prioritize staying under the hard upper bound.
    # (This is safer than returning over-limit output.)
    for _ in range(25):
        split_count = len(text.split())
        stripped_count = _count_words(text)
        if split_count <= upper and stripped_count <= upper:
            break
        cleaned = _strip_markdown_list_tokens(text)
        if cleaned and cleaned != text:
            text = cleaned
            continue
        over_by = max(0, split_count - upper, stripped_count - upper)
        target_words = max(lower, stripped_count - max(over_by, 1))
        next_text = _trim_preserving_headings(text, target_words)
        if next_text == text:
            break
        text = next_text

    # Final reconciliation: if the "stay under upper" pass pushed us below the lower
    # bound, pad back up and re-trim if needed.
    for _ in range(20):
        split_count = len(text.split())
        stripped_count = _count_words(text)
        if lower <= split_count <= upper and lower <= stripped_count <= upper:
            return text

        if split_count < lower or stripped_count < lower:
            if not allow_padding:
                return text
            deficit = max(lower - split_count, lower - stripped_count)
            if deficit <= 5:
                text = _micro_pad_tail_words(text, deficit)
                continue
            next_text = _distribute_padding_across_sections(text, deficit)
            remaining = max(
                lower - len(next_text.split()), lower - _count_words(next_text), 0
            )
            if remaining > 0 and remaining <= 5:
                next_text = _micro_pad_tail_words(next_text, remaining)
            if next_text == text:
                break
            text = next_text
            continue

        # Over the band (should be rare after the previous loop); trim again.
        delta = max(0, split_count - stripped_count)
        effective_upper = max(lower, upper - delta)
        excess = max(split_count - upper, stripped_count - upper, 1)
        target_words = max(lower, min(effective_upper, stripped_count - excess))
        next_text = _trim_preserving_headings(text, target_words)
        if next_text == text:
            break
        text = next_text

    # Hard fallback: guarantee the strict word band even when the iterative passes
    # can't converge (e.g., pathological markdown-token-heavy output).
    def _truncate_to_whitespace_token_limit(value: str, max_tokens: int) -> str:
        if not value:
            return value
        if max_tokens <= 0:
            return ""

        punct = string.punctuation + "\u201c\u201d\u2018\u2019\u2014\u2013\u2026"
        matches = list(re.finditer(r"\S+", value))
        if len(matches) <= max_tokens:
            return value.rstrip()

        # Avoid cutting after a punctuation-only token like "##" which renders badly.
        cutoff_idx = max_tokens - 1
        while cutoff_idx > 0:
            raw = matches[cutoff_idx].group(0)
            token = raw.strip(punct)
            if token:
                break
            cutoff_idx -= 1

        cutoff = matches[cutoff_idx].end()
        truncated = value[:cutoff].rstrip()
        if truncated and not truncated.endswith((".", "!", "?")):
            truncated += "."
        return truncated

    hardened = text or ""
    # Treat "no real words" outputs as empty so padding can kick in deterministically.
    if hardened and _count_words(hardened) == 0 and allow_padding:
        hardened = ""

    is_persona = bool(re.search(r"\b(?:I|my|I'm|I’m)\b", hardened))

    def _seed_exclude_norms_from_memo(value: str) -> set[str]:
        norms: set[str] = set()
        for line in (value or "").splitlines():
            stripped = (line or "").strip()
            if not stripped:
                continue
            # Skip headings and strict metric lines.
            if stripped.startswith("#") or stripped.startswith("→"):
                continue
            for sent in re.split(r"(?<=[.!?])\s+", stripped):
                sent = (sent or "").strip()
                if len(sent.split()) < 2:
                    continue
                norms.add(sent)
        return norms

    used_sentences = _seed_exclude_norms_from_memo(hardened)

    for _ in range(50):
        split_count = len(hardened.split())
        stripped_count = _count_words(hardened)

        # Precision target: land within ±10 words
        if target_length - 10 <= stripped_count <= target_length + 10:
            if dedupe:
                cleaned = _dedupe_consecutive_sentences(hardened)
                cleaned = _deduplicate_sentences(cleaned)
                if cleaned != hardened:
                    hardened = cleaned
                    used_sentences = _seed_exclude_norms_from_memo(hardened)
                    continue
            return hardened

        if stripped_count > target_length + 10:
            # Too long: trim
            hardened = _truncate_text_to_word_limit(hardened, target_length)
            continue

        if not allow_padding:
            return hardened

        # Too short: pad
        deficit = target_length - stripped_count
        if deficit <= 5:
            padded = _micro_pad_tail_words(hardened, deficit)
            if padded != hardened:
                hardened = padded
                continue
            else:
                return hardened  # Stop if micro-padding fails

        budget = max(1, min(deficit + 8, 120))
        padding_sentences = _generate_padding_sentences(
            deficit,
            exclude_norms=used_sentences,
            section=None,
            is_persona=is_persona,
            max_words=budget,
        )
        if padding_sentences:
            used_sentences.update(padding_sentences)
            padding_block = " ".join(padding_sentences).strip()
            if padding_block:
                hardened = f"{hardened.rstrip()}\n\n{padding_block}".strip()
                continue

        # Absolute last resort: make minimal progress without creating token spam.
        # If micro-padding failed and no sentences were added, we stop to avoid repetition loops.
        padded = _micro_pad_tail_words(hardened, 1)
        if padded == hardened:
            return hardened
        hardened = padded

    return hardened


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

    ordered = _enforce_section_order(text, include_health_rating=include_health_rating)
    enforced = _enforce_whitespace_word_band(
        ordered, int(target_length), tolerance=int(tolerance), allow_padding=True
    )
    return _enforce_section_order(enforced, include_health_rating=include_health_rating)


def _call_gemini_client(
    gemini_client,
    prompt: str,
    *,
    allow_stream: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    stage_name: str = "Generating",
    expected_tokens: int = 4000,
    timeout_seconds: Optional[float] = None,
    generation_config_override: Optional[Dict[str, Any]] = None,
    retry: bool = True,
) -> str:
    """
    Generate text using the Gemini client, gracefully falling back when streaming helpers
    are unavailable (e.g., in tests that mock only the underlying model).
    """
    if allow_stream and hasattr(gemini_client, "stream_generate_content"):
        try:
            try:
                return gemini_client.stream_generate_content(
                    prompt,
                    progress_callback=progress_callback,
                    stage_name=stage_name,
                    expected_tokens=expected_tokens,
                    generation_config_override=generation_config_override,
                    timeout_seconds=timeout_seconds,
                    retry=retry,
                )
            except TypeError:
                # Back-compat for older clients/tests.
                return gemini_client.stream_generate_content(
                    prompt,
                    progress_callback=progress_callback,
                    stage_name=stage_name,
                    expected_tokens=expected_tokens,
                    generation_config_override=generation_config_override,
                    timeout_seconds=timeout_seconds,
                )
        except ValueError as exc:
            if "request_options" in str(exc) and hasattr(
                gemini_client, "force_http_fallback"
            ):
                gemini_client.force_http_fallback = True
                try:
                    try:
                        return gemini_client.stream_generate_content(
                            prompt,
                            progress_callback=progress_callback,
                            stage_name=stage_name,
                            expected_tokens=expected_tokens,
                            generation_config_override=generation_config_override,
                            timeout_seconds=timeout_seconds,
                            retry=retry,
                        )
                    except TypeError:
                        return gemini_client.stream_generate_content(
                            prompt,
                            progress_callback=progress_callback,
                            stage_name=stage_name,
                            expected_tokens=expected_tokens,
                            generation_config_override=generation_config_override,
                            timeout_seconds=timeout_seconds,
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

    try:
        response = generator(
            prompt, generation_config_override=generation_config_override
        )
    except TypeError:
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
        # Process/meta phrasing that reads like internal notes
        r"Keep the narrative moving:\s*one claim,\s*one mechanism,\s*one implication[^.]*\.",
        r"one claim,\s*one mechanism,\s*one implication[^.]*\.",
        r"one claim[\s\u00A0]+one mechanism[\s\u00A0]+one implication[^.]*\.",
        r"one claim[\s\u00A0]*-[\s\u00A0]*one mechanism[^.]*\.",
        r"keep the narrative moving[^.]*\.",
        r"If a claim is important,\s*it should be tied to a specific driver and a specific line item rather than repeated as a slogan\.?",
        r"Focus on what changed quarter over quarter and why,\s*then connect it back to durability and downside risk\.?",
        r"Risk discussion should be weighted,\s*not exhaustive:\s*the question is which risk is most likely to show up in the numbers next quarter\.?",
        r"If a risk is real,\s*it should surface in a specific metric\s*\([^)]+\)\s*rather than staying conceptual\.?",
        # Boilerplate framework lines that sometimes leak into output (often repeated).
        r"\bThe memo should\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe analysis should\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe best MD&A reads\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe primary constraint\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bA neutral stance is justified\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bCatalysts matter only\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe stance is gated\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bConviction should be tied\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bIf free cash flow falls while revenue rises\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bIf margins move\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bBelow-the-line support\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe underwriting lens should\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe cleanest signals are\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bCash conversion quality is\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bFundamentals matter more than\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bRisk is asymmetric when\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bOperating leverage is most\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bThe decision should be stated once\b[^.\n]{0,240}(?:\.|\n|$)",
        r"\bIn sum\b[^.\n]{0,240}(?:\.|\n|$)",
        # Token/phrase loops (e.g., "in sum in sum in sum ...")
        r"(?:(?:\bin\s+sum\b)[\s,;:]*){2,}",
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
    result = re.sub(
        r"\bfortress[-\s\u00A0]*like\b",
        "strong liquidity and low servicing risk",
        result,
        flags=re.IGNORECASE,
    )

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
        s = (s or "").replace("\u00a0", " ")
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
            stripped.startswith(("→", "- ", "* ", "• ")) or stripped.startswith("**")
        )

    rebuilt_sections: List[str] = []
    for heading, body in sections:
        section_name = _standard_section_name_from_heading(heading)
        raw_body = (body or "").strip()

        if section_name not in target_sections or not raw_body:
            section_text = (
                f"{heading}\n\n{raw_body}".strip() if raw_body else heading.strip()
            )
            rebuilt_sections.append(section_text)
            continue

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw_body) if p.strip()]
        kept: List[str] = []
        for paragraph in paragraphs:
            if _is_structured_paragraph(paragraph):
                kept.append(paragraph)
                continue
            sentences = [
                s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip()
            ]
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
        section_text = (
            f"{heading}\n\n{rebuilt_body}".strip() if rebuilt_body else heading.strip()
        )
        rebuilt_sections.append(section_text)

    rebuilt = "\n\n".join(
        [s for s in ([preamble_text] if preamble_text else []) + rebuilt_sections if s]
    ).strip()
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    return rebuilt


def _cleanup_sentence_artifacts(text: str) -> str:
    """Remove stray quote artifacts and fix obvious sentence fragments."""
    if not text:
        return text

    quote_chars = ['"', "“", "”", "'", "‘", "’"]
    trailing_fragments = {
        "and",
        "but",
        "or",
        "because",
        "which",
        "that",
        "while",
        "although",
        "if",
        "when",
        "whereas",
    }

    # Lines that leak from prompt/validator scaffolding and read like "draft notes".
    meta_line = re.compile(
        r"^\s*(?:[-*•→]?\s*)?"
        r"(?:system feedback|system|note|important|todo|draft|revision|rewrite|guidance|instruction|prompt|length requirement|word count)\b",
        re.IGNORECASE,
    )
    # Imperative verbs that make the output feel like guidance rather than a finished memo.
    # For bullets, we drop the verb and keep the object so the list remains informative.
    bullet_imperative = re.compile(
        r"^(\s*(?:[-*•→]|\d+\.)\s+)"
        r"(?:monitor|track|watch|assess|review|compare|consider|evaluate|test|benchmark)\b\s+",
        re.IGNORECASE,
    )

    cleaned_lines: List[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue

        # Drop draft/meta scaffolding lines early.
        if meta_line.match(stripped) or re.search(
            r"\bsystem feedback\b", stripped, re.IGNORECASE
        ):
            continue

        # Normalize a few guidance-like headings into more "sealed" labels.
        if stripped.startswith("##"):
            heading = stripped
            heading = re.sub(
                r"(?im)^\s*##\s*Key\s+KPIs\s+to\s+Monitor\s*$",
                "## Key KPIs",
                heading,
            )
            heading = re.sub(
                r"(?im)^\s*##\s*What\s+to\s+Watch\s*$",
                "## Swing Factors",
                heading,
            )
            cleaned_lines.append(heading)
            continue

        # Drop lines that are only punctuation/quotes.
        if re.fullmatch(r"[\"'“”‘’.,;:!?()\[\]{}-]+", stripped):
            continue

        # Remove a single stray quote at the edges.
        quote_count = sum(stripped.count(ch) for ch in quote_chars)
        if quote_count % 2 == 1:
            if stripped[0] in quote_chars:
                stripped = stripped[1:].lstrip()
            elif stripped[-1] in quote_chars:
                stripped = stripped[:-1].rstrip()
            else:
                for ch in quote_chars:
                    stripped = stripped.replace(ch, "")
            quote_count = sum(stripped.count(ch) for ch in quote_chars)
            if quote_count % 2 == 1 and len(stripped.split()) <= 4:
                continue

        # Fix trailing conjunction fragments.
        words = stripped.split()
        if words:
            last = re.sub(r"[.,;:!?]+$", "", words[-1].lower())
            if last in trailing_fragments:
                stripped = " ".join(words[:-1]).rstrip()
                if not stripped:
                    continue
                if stripped and stripped[-1] not in ".!?":
                    stripped += "."

        # Replace trailing commas/colons/semicolons with a period.
        if stripped.endswith((",", ";", ":")):
            stripped = stripped.rstrip(",;:").rstrip()
            if stripped and stripped[-1] not in ".!?":
                stripped += "."

        # Clean up punctuation collisions created by fragment removal (e.g., ",.").
        stripped = re.sub(r"[,:;]\.$", ".", stripped)

        # Remove guidance verbs from bullet/list items while preserving the substance.
        stripped = bullet_imperative.sub(r"\1", stripped)
        stripped = re.sub(r"\bto\s+monitor\b", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\bto\s+watch\b", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(
            r"\bto\s+keep\s+an\s+eye\s+on\b", "", stripped, flags=re.IGNORECASE
        )
        stripped = re.sub(
            r"\bnecessitate\s+a\s+While\b",
            "necessitate caution",
            stripped,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(r"[ \t]{2,}", " ", stripped).strip()
        if not stripped:
            continue

        cleaned_lines.append(stripped)

    cleaned = "\n".join(cleaned_lines)

    # Normalize ellipses that often leak from truncation (these read like "draft").
    cleaned = cleaned.replace("\u2026", ".")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)

    # Fix common "trim seam" fragments that leak when the model or a word-band trim
    # cuts between two sentence starters (these read like draft artifacts).
    cleaned = re.sub(
        r"(?is)\bOverall,\s*this\s+is\s+a\s*(?:\.{2,}|…)\s*Taken\s+together,\s*",
        "Taken together, ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*Overall,\s*this\s+is\s+a\s*(?:\.{2,}|…)\s*$",
        "",
        cleaned,
    )

    # If we end up with the stock phrasing truncated, rewrite to a complete sentence.
    cleaned = re.sub(
        r"(?im)^\s*Taken\s+together,\s*these\s+drivers\s+explain\s+why\s+the\s+score\s+sits\s+in\s+the\s*(?:\.{0,3}|…)?\s*$",
        "Taken together, these drivers explain the score.",
        cleaned,
    )

    # Repair common cut-offs that otherwise leave hanging clauses.
    cleaned = re.sub(
        r"(?i)\bProfitability\s+and\s+margins,\s*which\s+(?:I|we)\s*$",
        "Profitability and margins matter most here.",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\bProfitability\s+and\s+margins,\s*which\s+(?:I|we)\s*\.\s*$",
        "Profitability and margins matter most here.",
        cleaned,
    )

    # If a sentence starts a "signaling a need to ..." clause and gets cut, drop the clause.
    cleaned = re.sub(
        r"(?is)\s*(?:,|—|-)\s*signaling\s+a\s+need\s+to\s*(?:\.[^A-Za-z0-9]|$)",
        ". ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?is)\bsignaling\s+a\s+need\s+to\s*(?:\.|$)",
        "",
        cleaned,
    )

    # Glitch pass: remove repeated micro-filler phrases that make the output feel unedited.
    # Keep at most one occurrence of "in the near term" across the whole memo.
    phrase = "in the near term"
    hits = list(re.finditer(rf"(?i)\b{re.escape(phrase)}\b", cleaned))
    if len(hits) > 1:
        # Remove all but the first occurrence.
        first_end = hits[0].end()
        tail = cleaned[first_end:]
        tail = re.sub(rf"(?i)\b{re.escape(phrase)}\b", "", tail)
        cleaned = cleaned[:first_end] + tail
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)

    # Keep at most one occurrence of common time-window filler (these tend to appear
    # as copy/paste chains when padding fires repeatedly).
    for phrase in (
        "over the coming quarters",
        "over the next year",
        "over the next quarter",
    ):
        hits = list(re.finditer(rf"(?i)\b{re.escape(phrase)}\b", cleaned))
        if len(hits) > 1:
            first_end = hits[0].end()
            tail = cleaned[first_end:]
            tail = re.sub(rf"(?i)\b{re.escape(phrase)}\b", "", tail)
            cleaned = cleaned[:first_end] + tail
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
            cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)

    # Delete duplicated "as things stand today" everywhere; keep at most one.
    phrase = "as things stand today"
    hits = list(re.finditer(rf"(?i)\b{re.escape(phrase)}\b", cleaned))
    if len(hits) > 1:
        cleaned = re.sub(rf"(?i)\b{re.escape(phrase)}\b", "", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)

    # Collapse "overall overall overall" token spam (usually from padding/seams).
    cleaned = re.sub(
        r"(?i)\boverall\b(?:[\s,.;:\-\u2013\u2014]+\boverall\b){1,}",
        "overall",
        cleaned,
    )
    # Collapse single-token filler spam that can accumulate from micro-padding loops.
    cleaned = re.sub(
        r"(?i)\bnotably\b(?:[\s,.;:\-\u2013\u2014]+\bnotably\b){1,}",
        "notably",
        cleaned,
    )

    def _prune_maxim_runs(value: str) -> str:
        """Drop long runs of generic 'process maxims' that read like templated filler.

        This targets the failure mode where post-processing padding injects many
        short, number-free sentences (often starting with 'I ...' or 'If ...') into
        an otherwise complete section. Keep the first 1–2, drop the rest.
        """
        if not value:
            return value

        def _is_structured(paragraph: str) -> bool:
            s = (paragraph or "").lstrip()
            return bool(
                s.startswith(("##", "→", "- ", "* ", "• ", "**"))
                or re.match(r"^\s*\d+\.\s+", s)
            )

        def _is_maxim_sentence(sentence: str) -> bool:
            s = (sentence or "").strip()
            if not s:
                return False
            # If it contains numbers, it's likely tied to the specific memo.
            if re.search(r"\d", s):
                return False
            # Only treat as a maxim if it also contains the usual finance keywords.
            if not re.search(
                r"(?i)\b(cash|free cash flow|fcf|margin|capex|working capital|working-capital|leverage|liquidity|refinanc\w*|balance sheet|dilution|buybacks?|operating leverage|earnings|profitability|reinvestment)\b",
                s,
            ):
                return False
            return bool(
                re.match(
                    r"(?i)^(?:"
                    r"i\s+(?:assume|care|pay|look|prefer|treat|underwrite|reconcile)"
                    r"|for\s+narrative\s+discipline"
                    r"|sequential\s+margin\s+changes"
                    r"|working\s+capital\s+timing"
                    r"|when\s+margins\s+and\s+cash"
                    r"|the\s+balance\s+sheet\s+matters"
                    r"|capital\s+allocation\s+is"
                    r"|stock[-\s]based\s+compensation\s+is"
                    r"|position\s+sizing\s+should"
                    r"|when\s+profitability"
                    r"|when\s+the\s+cost\s+base"
                    r"|if\s+"
                    r")",
                    s,
                )
            )

        paragraphs = [p for p in re.split(r"\n{2,}", value) if p is not None]
        out: List[str] = []
        for paragraph in paragraphs:
            p = (paragraph or "").strip()
            if not p:
                continue
            if _is_structured(p):
                out.append(p)
                continue

            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
            if len(sentences) < 4:
                out.append(p)
                continue

            maxim_flags = [_is_maxim_sentence(s) for s in sentences]
            maxim_count = sum(1 for f in maxim_flags if f)
            if maxim_count < 3:
                out.append(p)
                continue

            kept: List[str] = []
            kept_maxims = 0
            for s, is_maxim in zip(sentences, maxim_flags):
                if is_maxim:
                    kept_maxims += 1
                    if kept_maxims > 1:
                        continue
                kept.append(s)

            rebuilt = " ".join(kept).strip()
            rebuilt = re.sub(r"[ \t]{2,}", " ", rebuilt).strip()
            out.append(rebuilt if rebuilt else p)

        return "\n\n".join(out).strip()

    cleaned = _prune_maxim_runs(cleaned)

    # If the model outputs many "maxim" paragraphs as separate short blocks (common when
    # trying to hit high word counts), prune long consecutive runs. This complements
    # `_prune_maxim_runs`, which operates within a single paragraph.
    def _prune_consecutive_maxim_paragraphs(value: str) -> str:
        if not value:
            return value

        def _is_structured(paragraph: str) -> bool:
            stripped = (paragraph or "").lstrip()
            return bool(
                stripped.startswith(("##", "→", "- ", "* ", "• "))
                or stripped.startswith("**")
            )

        def _is_maxim_paragraph(paragraph: str) -> bool:
            p = (paragraph or "").strip()
            if not p or _is_structured(p):
                return False
            if re.search(r"\d", p):
                return False
            if len(p.split()) > 40:
                return False
            return bool(
                re.match(
                    r"(?i)^(?:"
                    r"i\s+"
                    r"|if\s+"
                    r"|when\s+"
                    r"|for\s+narrative\s+discipline\b"
                    r"|guidance\s+and\s+management\s+tone\b"
                    r"|the\s+numbers\s+tell\b"
                    r"|a\s+company\s+can\s+look\b"
                    r"|the\s+balance\s+sheet\s+matters\b"
                    r"|working\s+capital\s+timing\b"
                    r"|sequential\s+margin\s+changes\b"
                    r"|capital\s+allocation\s+is\b"
                    r"|stock[-\s]based\s+compensation\s+is\b"
                    r")",
                    p,
                )
            )

        paragraphs = [p for p in re.split(r"\n{2,}", value) if p is not None]
        out: List[str] = []
        run: List[str] = []

        def _flush() -> None:
            nonlocal run, out
            if not run:
                return
            if len(run) >= 3:
                out.append(run[0])
            else:
                out.extend(run)
            run = []

        for paragraph in paragraphs:
            p = (paragraph or "").strip()
            if not p:
                continue
            if _is_maxim_paragraph(p):
                run.append(p)
                continue
            _flush()
            out.append(p)

        _flush()
        rebuilt = "\n\n".join([p for p in out if p]).strip()
        rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
        return rebuilt

    cleaned = _prune_consecutive_maxim_paragraphs(cleaned)

    # Process-line pruning: keep at most one instance of these template stems.
    # (They read like editing notes when repeated across sections.)
    stems = [
        r"margins are easier to underwrite",
        r"the main question is",
        r"the key read is",
        r"conviction rises when",
        r"a clean read comes from",
    ]
    for stem in stems:
        matches = list(re.finditer(rf"(?i)\b{stem}\b", cleaned))
        if len(matches) <= 1:
            continue
        # Remove all but the first *sentence* containing the stem.
        first = matches[0]
        before = cleaned[: first.start()]
        after = cleaned[first.start() :]
        # Split into sentences, drop those containing the stem except the first.
        parts = re.split(r"(?<=[.!?])\s+", after)
        kept: List[str] = []
        seen = 0
        for part in parts:
            if re.search(rf"(?i)\b{stem}\b", part):
                seen += 1
                if seen > 1:
                    continue
            kept.append(part)
        cleaned = (before + " ".join(kept)).strip()
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Keep at most one "margins + underwrite" sentence even when the model paraphrases
    # it slightly across multiple sections.
    meta_hits = list(re.finditer(r"(?i)\bmargins?\b.*\bunderwrit\w*\b", cleaned))
    if len(meta_hits) > 1:
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        kept: List[str] = []
        seen = 0
        for sentence in sentences:
            if re.search(r"(?i)\bmargins?\b.*\bunderwrit\w*\b", sentence or ""):
                seen += 1
                if seen > 1:
                    continue
            kept.append(sentence)
        cleaned = " ".join([s for s in kept if (s or "").strip()]).strip()
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # If we still have overlong sentences with multiple conclusions, split on semicolons.
    # This keeps word count stable while improving readability.
    def _split_semicolons_in_long_lines(value: str) -> str:
        out: List[str] = []
        for line in (value or "").splitlines():
            if (
                not line.strip()
                or line.lstrip().startswith("#")
                or line.lstrip().startswith(("→", "-", "*"))
            ):
                out.append(line)
                continue
            if ";" in line and len(line.split()) >= 40:
                out.append(re.sub(r";\s+", ". ", line))
            else:
                out.append(line)
        return "\n".join(out)

    cleaned = _split_semicolons_in_long_lines(cleaned)

    # Final anti-filler pass:
    # - Remove run-on "tail filler" chains (e.g., "in aggregate at present in sum ...").
    # - Collapse obvious token spam (e.g., "overall overall overall ...").
    # - Drop a few known broken fragment seams that can appear after trimming.
    def _strip_runon_tail_fillers(value: str) -> str:
        if not value:
            return value

        out = value

        # Collapse repeated token spam.
        out = re.sub(r"(?i)\b(overall\s+){2,}overall\b", "overall", out)
        out = re.sub(r"(?i)\b(overall\s+){3,}", "overall ", out)

        # Remove common fragment seams.
        out = re.sub(
            r"(?i)\bOverall,\s*this\s+is\s+a\s+(?=Taken together,)",
            "",
            out,
        )
        out = re.sub(r"(?i)\bOverall,\s*this\s+is\s+a\b\s*(?=\n|$)", "", out)
        out = re.sub(
            r"(?im)^\s*Taken together,\s*these drivers explain why the score sits in the\s*$",
            "",
            out,
        )
        out = re.sub(
            r"(?i)\bTaken together,\s*these drivers explain why the score sits in the\s+(?=(?:This|The)\b)",
            "",
            out,
        )

        # Capitalize a few common lowercased sentence starts caused by trimming.
        out = re.sub(
            r"([.!?])\s+margin compression\b",
            r"\1 Margin compression",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"([.!?])\s+downside risk\b", r"\1 Downside risk", out, flags=re.IGNORECASE
        )
        out = re.sub(
            r"([.!?])\s+upside requires\b",
            r"\1 Upside requires",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"([.!?])\s+the overall\b", r"\1 The overall", out, flags=re.IGNORECASE
        )

        # Remove tail chains of low-signal phrases (legacy micro-padding).
        tail_phrases = [
            "in aggregate",
            "at present",
            "in sum",
            "for now",
            "on balance",
            "on the margin",
            "as things stand",
            "as things stand overall",
            "at this point",
            "in the end",
        ]
        phrase_re = "|".join(
            sorted([re.escape(p) for p in tail_phrases], key=len, reverse=True)
        )
        # If the last ~200 chars are dominated by these phrases (3+ occurrences), strip them.
        tail_window = out[-250:]
        tail_hits = re.findall(rf"(?i)\b(?:{phrase_re})\b", tail_window)
        if len(tail_hits) >= 3:
            out = re.sub(
                rf"(?is)(?:[\s,;:\-]+(?:{phrase_re})\b)+\s*$",
                "",
                out,
            ).rstrip()
            if out and not out.endswith((".", "!", "?")):
                out += "."

        out = re.sub(r"\n{3,}", "\n\n", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        return out.strip()

    cleaned = _strip_runon_tail_fillers(cleaned)

    return cleaned.strip()


def _tone_down_emotive_adjectives(text: str) -> str:
    """Replace emotive adjectives with neutral finance language."""
    if not text:
        return text
    softened = text
    softened = re.sub(r"\balarming\b", "notable", softened, flags=re.IGNORECASE)
    softened = re.sub(r"\bastonishing\b", "notable", softened, flags=re.IGNORECASE)
    softened = re.sub(r"\beye[-\s]?opening\b", "notable", softened, flags=re.IGNORECASE)
    softened = re.sub(r"\bshocking\b", "notable", softened, flags=re.IGNORECASE)
    softened = re.sub(r"\bextraordinary\b", "unusual", softened, flags=re.IGNORECASE)
    softened = re.sub(r"\bdramatic\b", "meaningful", softened, flags=re.IGNORECASE)
    return softened


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
        # Common truncation in first-person Closing Takeaways / verdict sentences.
        r"\bbefore\s+(?:I|we)\s+would\s*$": " underwrite a higher-conviction stance.",
        # Common cutoff: "..., which I" / "..., so I"
        r"\s+which\s+(?:I|we)\s*$": ".",
        r"\s+so\s+(?:I|we)\s*$": ".",
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

    padding_sentences = _generate_padding_sentences(
        required_words, exclude_norms=exclude_norms
    )
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

    payload = payload.replace("\u00a0", " ").strip()
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

    payload = payload.replace("\u00a0", " ").strip()
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
        if risk_idx is None and re.match(
            r"^\s*##\s*Risk\s+Factors\b", line, re.IGNORECASE
        ):
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
            else (
                key_metrics_idx if key_metrics_idx is not None else len(cleaned_lines)
            )
        )
        # Ensure a clean paragraph break.
        while insert_at > 0 and cleaned_lines[insert_at - 1].strip() == "":
            insert_at -= 1
        insertion = ["", underwriting_line, ""]
        rebuilt_lines = (
            cleaned_lines[:insert_at] + insertion + cleaned_lines[insert_at:]
        )
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

    # NOTE: Deterministic padding is a last-resort length safety net and should not
    # inject a first-person "persona" voice. Keep padding neutral even if the draft
    # already contains "I" statements.
    def _voice(_persona: str, neutral: str) -> str:
        return neutral

    def _normalize_risk_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()

    def _risk_name_from_template(sentence: str) -> Optional[str]:
        match = re.match(r"\*\*(.+?)\*\*\s*:", sentence or "")
        if match:
            return _normalize_risk_name(match.group(1))
        return None

    section_templates: Dict[str, List[str]] = {
        "financial health rating": [
            "The score is driven by the interaction of margin profile, cash generation, and obligations.",
            "Profitability only matters if it converts into free cash flow after capex and working-capital swings.",
            "Balance-sheet strength is defined by liquidity headroom and refinancing flexibility under stress.",
            "Liquidity matters because it determines whether volatility can be self-funded without dilution or new leverage.",
            "Downside protection is best read through cash conversion and near-term obligations, not peak-cycle margins.",
            "A strong cash position provides optionality for reinvestment, buybacks, and de-risking without external financing.",
            "If cash generation weakens while obligations stay fixed, the downside path accelerates when growth slows.",
            "Earnings quality is stronger when operating profit and cash flow tell the same story over time.",
            "High servicing headroom (when present) lowers near-term financial risk, but does not eliminate execution risk.",
            "The rating is meant to reflect durability and flexibility, not just reported profitability in a single period.",
            "The health lens emphasizes cash flow durability, liquidity, and leverage sensitivity to a weaker backdrop.",
            "The key question is whether incremental growth is compounding free cash flow or absorbing it.",
        ],
        "executive summary": [
            "The stance is gated by whether earnings translate into repeatable free cash flow after reinvestment.",
            "Upside requires margin structure to hold while reinvestment intensity stays disciplined.",
            "The bear path is margin pressure plus elevated reinvestment, compressing free cash flow even if revenue grows.",
            "Conviction should be tied to measurable triggers (margins, cash flow, reinvestment), not general optimism.",
            "A neutral stance is justified when the business is strong but the cash trajectory is unstable.",
            "If cash conversion improves without a growth trade-off, the risk-reward can re-rate quickly.",
            "If below-the-line items drive net income, operating profit and cash flow are the cleaner signals.",
            "Liquidity provides cushion, but it does not substitute for durable cash generation.",
            "The near-term debate is operating leverage versus incremental capital intensity.",
            "Catalysts matter only if they change the margin and cash trajectory, not just the narrative.",
            "The thesis weakens when reinvestment rises faster than operating profit and free cash flow.",
            "The base case improves when cash conversion stabilizes on a run-rate basis.",
            "The primary constraint should be stated once and then supported with numbers, not repeated as doctrine.",
            "Capital allocation discipline determines whether growth translates into per-share value creation over time.",
            "Operational efficiency gains must be weighed against the structural costs of maintaining market leadership.",
            "The focus remains on organic growth durability rather than acquisition-fueled expansion or accounting tailwinds.",
            "Long-term value creation is predicated on the ability to maintain pricing power through competitive cycles.",
            "The gap between reported earnings and cash generation often signals the next stage of the margin cycle.",
            "Asset intensity and working-capital requirements define the upper bound of sustainable growth without leverage.",
            "Management's ability to flex the cost base is a critical differentiator in a softening demand environment.",
        ],
        "closing takeaway": [
            "The verdict should be anchored on durability: margins, cash conversion, and balance-sheet flexibility.",
            "An upgrade case requires repeatable cash conversion without sacrificing margin structure.",
            "A downgrade case follows sustained margin pressure and continued cash conversion slippage.",
            "The risk-reward improves when cash generation is visible on a run-rate basis, not a one-quarter spike.",
            "Triggers should be time-bound and measurable (next two periods), not open-ended monitoring.",
            "The underwriting check is whether reinvestment is compounding returns or simply absorbing cash.",
            "A stronger stance requires confirmation that operating leverage shows up in both profit and cash.",
            "A weaker stance is warranted if the cash bridge deteriorates alongside a softer demand backdrop.",
            "Balance-sheet flexibility matters most when fundamentals weaken, not when the cycle is strong.",
            "The cleanest signal is alignment: revenue, margins, and free cash flow improving together.",
            "The decision should be stated once; the rest of the memo should justify it with evidence.",
            "What changes the view should be framed as a concrete threshold in margins or cash conversion.",
        ],
        "financial performance": [
            "The signal is in how revenue growth translates into operating profit and free cash flow.",
            "A widening gap between operating and net margins suggests non-operating items are influencing optics.",
            "Cash conversion can diverge from earnings through working capital, capex, and tax timing.",
            "Capex intensity matters because it defines how much growth is self-funded versus externally funded.",
            "If operating cash flow weakens despite profit, working capital is often the swing driver.",
            "If margins move, the driver should be tied to pricing/mix versus cost intensity, not general statements.",
            "The OCF-to-FCF bridge is the core underwriting bridge for cash durability in the period.",
            "Sequential deltas matter because they show whether the trajectory is improving or deteriorating.",
            "A strong quarter is more credible when both margins and cash conversion improve together.",
            "If free cash flow falls while revenue rises, reinvestment and working capital are the usual culprits.",
            "Below-the-line support (tax/FX/other) should not be treated as durable operating improvement.",
            "The most important changes should be stated clearly, then backed by the delta bridge numbers.",
        ],
        "management discussion and analysis": [
            "Management posture shows up in reinvestment cadence, opex discipline, and the monetization path.",
            "Strategy only matters if it matches the observed margin and cash flow trajectory.",
            "Cost discipline can be inferred from opex growth relative to revenue and gross profit.",
            "Capital allocation compounds only when funded by durable free cash flow rather than one-offs.",
            "Execution risk often appears as rising cost intensity before margins visibly compress.",
            "A credible posture reconciles KPI commentary with cash flow, not just adjusted metrics.",
            "If reinvestment is accelerating, the question is payback and operating leverage that follows.",
            "The underwriting lens is whether growth initiatives improve unit economics or dilute them.",
            "When management targets efficiency, it should show up in operating margin and cash conversion.",
            "Guidance posture matters most when it changes reinvestment pacing or pricing strategy.",
            "If cash conversion is volatile, capital allocation choices become a first-order risk factor.",
            "The best MD&A reads are concrete: priorities, trade-offs, and the metrics that will prove execution.",
        ],
        # Padding must not create NEW risk bullets; if applied, it should read as
        # incremental weighting/monitoring of the existing risks.
        "risk factors": [
            "Each risk should map to a mechanism that hits margin, cash, or capital requirements.",
            "Risk severity rises when a negative driver can coincide with a growth slowdown and higher reinvestment.",
            "Second-order risks matter when they force structural cost increases or higher capital intensity.",
            "Mitigation should be framed as a measurable sign the risk is stabilizing or worsening.",
            "The main transmission channels are pricing, cost intensity, working capital, and capex intensity.",
            "A credible risk write-up distinguishes temporary noise from structural pressure on margins and cash.",
            "Balance-sheet risk is secondary when liquidity is ample, but escalates if cash conversion weakens.",
            "What matters is whether the risk shows up in operating margin, free cash flow, or capital needs.",
            "Risks should be distinct and non-overlapping, each tied to a specific driver in the filing.",
            "Probability should be described through leading indicators, not generic macro commentary.",
            "A good risk framing specifies the sign of the impact and the metric that will confirm it.",
            "The dominant downside scenario is usually the combination of weaker growth and higher cost intensity.",
        ],
    }

    # Fallback pool used when we can't reliably infer a section (or when section-specific
    # templates are exhausted). This must be broad enough to avoid repeating the same
    # "swing factor" sentences when strict word-floor padding runs multiple times.
    fallback_templates = [
        "The analysis should prioritize what changed in the latest period and why it matters for durability.",
        "Operating leverage is most credible when margins and free cash flow strengthen together.",
        "If net income moves more than operating profit, below-the-line items may be driving the optics.",
        "Cash durability is best assessed through the operating-cash to free-cash bridge, not reported EPS.",
        "Working-capital timing can temporarily inflate or depress cash conversion versus earnings.",
        "Capex intensity matters because it determines how much growth translates into distributable cash.",
        "Margin pressure is most dangerous when it coincides with higher reinvestment and weaker growth.",
        "Balance-sheet flexibility is the buffer when the operating trajectory softens.",
        "Liquidity risk rises when cash conversion weakens and refinancing windows tighten.",
        "The underwriting lens should separate transient noise from structural drivers of margin and cash.",
        "A durable thesis is supported by repeatable unit economics, not one-off gains or timing effects.",
        "Cash generation provides optionality, but capital allocation determines whether value compounds.",
        "The cleanest signals are time-consistent: trends that repeat across consecutive periods.",
        "Risk is asymmetric when cost structure is fixed and incremental revenue becomes less profitable.",
        "A credible bull case explains why incremental margins and cash conversion can improve from here.",
        "A credible bear case explains how reinvestment and competition can pressure margins and cash.",
        "The next two quarters should clarify whether recent deltas are noise or a new trajectory.",
        "When growth is strong, the focus is incremental margin; when growth slows, the focus is cash.",
        "Cash conversion quality is stronger when working capital normalizes and capex is steady.",
        "If reinvestment is rising, payback discipline becomes the key determinant of long-term returns.",
        "The base stance should remain consistent across sections rather than oscillating in tone.",
        "The memo should avoid restating the same constraint; each section should add a new angle.",
        "The key question is not whether the business is good, but whether the trajectory is improving.",
        "Fundamentals matter more than rhetoric: the numbers must corroborate the story.",
        "Efficiency in capital deployment is the long-term anchor for compounding per-share intrinsic value.",
        "Margin structure is a function of competitive positioning and the durability of the revenue mix.",
        "Refining the underwriting bridge requires a close look at the gap between accrual profit and realized cash.",
        "The probability of outsized returns increases when growth is self-funded through operating leverage.",
        "Operational risk is often masked by high growth, surfacing only when the market environment tightens.",
        "A disciplined cost base provides the necessary margin for error during unpredictable demand shifts.",
        "Transparency in capital allocation choices is a prerequisite for long-term conviction in the thesis.",
        "The most reliable indicators are those that link operational KPIs directly to financial durability.",
    ]

    templates: List[str] = []
    if canon_section in section_templates:
        templates.extend(section_templates[canon_section])
    else:
        templates.extend(fallback_templates)

    def _norm_sentence(s: str) -> str:
        s = (s or "").replace("\u00a0", " ")
        s = " ".join(s.lower().split())
        return s.rstrip(".!?")

    excluded = {(_norm_sentence(s)) for s in (exclude_norms or set()) if s}

    risk_name_blacklist = {
        _normalize_risk_name(name) for name in (exclude_risk_names or set()) if name
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

    # If the section templates are all too long to fit within the per-section slack,
    # add the fallback pool so we can still make incremental progress.
    if (
        max_words is not None
        and candidates
        and all(wc > int(max_words) for wc, _t in candidates)
    ):
        for t in fallback_templates:
            if excluded and _norm_sentence(t) in excluded:
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

    if not candidates:
        return []
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
        if (
            stripped.startswith("#")
            or stripped.startswith("→")
            or stripped.startswith("- ")
        ):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", stripped):
            sent = (sent or "").strip()
            if len(sent.split()) < 2:
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
            deficit = lower - words
            if deficit <= 5:
                text = _micro_pad_tail_words(text, deficit)
                continue
            padded = _distribute_padding_across_sections(text, deficit)
            remaining = max(0, lower - _count_words(padded))
            if remaining > 0 and remaining <= 5:
                padded = _micro_pad_tail_words(padded, remaining)
            text = padded
        else:
            text = _trim_preserving_headings(text, upper)

    # Final safety: truncate to upper, then pad back to lower if needed
    text = _truncate_text_to_word_limit(text, upper)
    words = _count_words(text)
    if words < lower and allow_padding:
        deficit = lower - words
        if deficit <= 5:
            return _micro_pad_tail_words(text, deficit)
        padded = _distribute_padding_across_sections(text, deficit)
        remaining = max(0, lower - _count_words(padded))
        if remaining > 0 and remaining <= 5:
            padded = _micro_pad_tail_words(padded, remaining)
        text = padded
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

    current_section: Optional[str] = None
    for line in lines:
        # Preserve headings and empty lines
        stripped_line = line.strip()
        if not stripped_line:
            result_lines.append(line)
            continue

        if stripped_line.startswith("#"):
            current_section = _standard_section_name_from_heading(stripped_line)
            result_lines.append(line)
            continue

        # Never deduplicate/strip Key Metrics lines; they often contain short numeric
        # fragments that look like "orphan" sentences under the generic heuristic.
        if current_section == "Key Metrics":
            result_lines.append(line)
            continue

        # Split line into sentences
        # Match sentences ending with . ! or ? followed by space or end
        sentences = re.split(r"(?<=[.!?])\s+", stripped_line)
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
            if (
                normalized
                and normalized not in seen_sentences
                and signature not in seen_signatures
            ):
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
    """Trim Key Metrics/Appendix body by removing rows from the bottom.

    Special-case the DATA_GRID_START/END format so we never drop the end marker:
    the frontend table parser relies on both markers being present.
    """
    if not body or max_words <= 0:
        return ""

    lines = body.splitlines()

    def _wc(line: str) -> int:
        return _count_words(line or "")

    start_idx = None
    end_idx = None
    for idx, line in enumerate(lines):
        if start_idx is None and line.strip().upper() == "DATA_GRID_START":
            start_idx = idx
        if line.strip().upper() == "DATA_GRID_END":
            end_idx = idx
            break

    # No grid present: simple top-down trim.
    if start_idx is None or end_idx is None or end_idx <= start_idx:
        trimmed: List[str] = []
        words = 0
        for line in lines:
            line_words = _wc(line)
            if words + line_words > max_words:
                break
            trimmed.append(line)
            words += line_words
        return "\n".join(trimmed).strip()

    pre = lines[:start_idx]
    grid = lines[start_idx : end_idx + 1]
    post = lines[end_idx + 1 :]

    out: List[str] = []
    words = 0

    # Include any preface lines that fit.
    for line in pre:
        line_words = _wc(line)
        if words + line_words > max_words:
            return "\n".join(out).strip()
        out.append(line)
        words += line_words

    # Always include the start marker.
    start_line = grid[0]
    start_words = _wc(start_line)
    if words + start_words > max_words:
        return "\n".join(out).strip()
    out.append(start_line)
    words += start_words

    # Reserve space for the end marker.
    end_line = grid[-1]
    end_words = _wc(end_line)
    remaining_for_rows = max_words - words - end_words
    if remaining_for_rows < 0:
        # We can include start but not end; drop the grid entirely.
        return "\n".join(
            [ln for ln in out if ln.strip().upper() != "DATA_GRID_START"]
        ).strip()

    # Include as many grid rows as fit (excluding markers).
    for line in grid[1:-1]:
        line_words = _wc(line)
        if remaining_for_rows - line_words < 0:
            break
        out.append(line)
        words += line_words
        remaining_for_rows -= line_words

    # Always include the end marker.
    if words + end_words <= max_words:
        out.append(end_line)
        words += end_words

    # Include any post-grid lines (e.g., Health Score Drivers) that fit.
    for line in post:
        line_words = _wc(line)
        if words + line_words > max_words:
            break
        out.append(line)
        words += line_words

    return "\n".join(out).strip()


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
    scaled_mins = [int(target_mins.get(key, default_min)) for key in section_keys]

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
        for idx in sorted(
            range(len(floors)), key=lambda i: remainders[i], reverse=True
        )[:remaining]:
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
    allocations = [
        min(current_body_counts[i], targets[i]) for i in range(len(sections))
    ]

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
        if (
            "key metrics" in section_keys[idx].lower()
            or "key data appendix" in section_keys[idx].lower()
        ):
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
        f"Compress the following investor memo to be ≤ {max_words} words.\n\n"
        "RULES:\n"
        "- KEEP every required section heading (## ...). Do NOT drop sections.\n"
        "- Remove redundancy and low-signal filler; merge overlapping sentences.\n"
        "- Preserve key metrics, core mechanism, and the final verdict.\n"
        "- Do NOT add new sections or an end-of-memo recap.\n"
        "- Every sentence must be complete.\n\n"
        "MEMO TO COMPRESS:\n"
        f"{summary_text}"
    )
    if token_budget and not token_budget.can_afford(compress_prompt, max_output_tokens):
        logger.warning(
            "Skipping compression rewrite due to token budget (remaining=%s tokens)",
            token_budget.remaining_tokens,
        )
        return None, None

    raw_text = _call_gemini_client(
        gemini_client,
        compress_prompt,
        allow_stream=True,
        stage_name="Compressing Summary",
        expected_tokens=min(max_output_tokens, 1200),
        timeout_seconds=20.0,
        generation_config_override={
            "maxOutputTokens": int(min(max_output_tokens, 1400)),
            "temperature": 0.2,
        },
        retry=False,
    )
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
    """Return tuple indicating if retry needed, actual count, tolerance size.

    Enforce a strict word-count band around the user's request.
    """
    tolerance = 10
    words = cached_count if cached_count is not None else _count_words(text)
    lower = max(1, int(target_length) - tolerance)
    upper = int(target_length) + tolerance
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
    Ensure the final memo does not exceed the requested max length.

    `target_length` is treated as a hard maximum; the system will not expand/pad
    short outputs to "hit" a minimum.
    """
    if not summary_text:
        return summary_text

    cap = int(target_length)
    actual_words = _count_words(summary_text)
    if actual_words <= cap:
        return summary_text

    overage = max(0, actual_words - cap)
    if overage > 80:
        compressed, compressed_words = _compress_summary_to_length(
            gemini_client,
            summary_text,
            cap,
            cap,
            0,
            token_budget=token_budget,
            max_output_tokens=max_output_tokens,
        )
        if compressed and compressed_words and compressed_words <= cap:
            return compressed

    trimmed = _trim_preserving_headings(summary_text, cap)
    return _enforce_whitespace_word_band(
        trimmed, cap, tolerance=10, allow_padding=True, dedupe=True
    )


def _enforce_strict_target_band(
    summary_text: str,
    target_length: int,
    *,
    calculated_metrics: Dict[str, Any],
    company_name: str,
    include_health_rating: bool,
) -> str:
    """Ensure the final memo lands within ±10 words of the requested target.

    This prefers:
    - trimming when over the band
    - adding *numeric, company-specific* addenda when under the band (no generic slogans)
    """
    if not summary_text or not target_length:
        return summary_text

    target = int(target_length)
    tolerance = 10
    lower = max(TARGET_LENGTH_MIN_WORDS, target - tolerance)
    upper = min(TARGET_LENGTH_MAX_WORDS, target + tolerance)

    text = summary_text

    def _stats(value: str) -> Tuple[int, int]:
        value = value or ""
        return len(value.split()), _count_words(value)

    def _in_band(value: str) -> bool:
        split_wc, stripped_wc = _stats(value)
        return lower <= split_wc <= upper and lower <= stripped_wc <= upper

    def _format_money(key: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        try:
            return _format_metric_value_for_text(key, value)
        except Exception:
            return None

    def _safe_insert_into_section(section_title: str, addendum: str) -> str:
        nonlocal text
        addendum = (addendum or "").strip()
        if not addendum:
            return text
        pattern = re.compile(
            rf"(?im)^##\s*{re.escape(section_title)}\s*\n+([\s\S]*?)(?=\n##\s|\Z)"
        )
        m = pattern.search(text or "")
        if not m:
            return text
        body = (m.group(1) or "").strip()
        cleaned = body.rstrip()
        cleaned = re.sub(r"[-\u2013\u2014]+\s*$", "", cleaned).rstrip()
        joiner = " " if cleaned.endswith((".", "!", "?")) else ". "
        merged = f"{cleaned}{joiner}{addendum}".strip()
        text = pattern.sub(lambda _mm: f"## {section_title}\n{merged}\n", text, count=1)
        return text

    def _build_numeric_addendum(max_words: int) -> str:
        revenue = calculated_metrics.get("revenue") or calculated_metrics.get(
            "total_revenue"
        )
        operating_income = calculated_metrics.get("operating_income")
        operating_margin = calculated_metrics.get("operating_margin")
        net_margin = calculated_metrics.get("net_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        fcf = calculated_metrics.get("free_cash_flow")
        capex = calculated_metrics.get("capital_expenditures")
        cash = calculated_metrics.get("cash")
        securities = calculated_metrics.get("marketable_securities") or 0
        liabilities = calculated_metrics.get("total_liabilities")

        cash_total = None
        if cash is not None:
            cash_total = cash + (securities or 0)

        rev_str = _format_money("revenue", revenue)
        op_inc_str = _format_money("operating_income", operating_income)
        ocf_str = _format_money("operating_cash_flow", ocf)
        fcf_str = _format_money("free_cash_flow", fcf)
        capex_str = _format_money("capital_expenditures", capex)
        cash_total_str = _format_money("cash", cash_total)
        liabilities_str = _format_money("total_liabilities", liabilities)

        capex_pct = None
        if capex is not None and revenue:
            try:
                capex_pct = (capex / revenue) * 100
            except Exception:
                capex_pct = None

        fcf_margin_pct = None
        if fcf is not None and revenue:
            try:
                fcf_margin_pct = (fcf / revenue) * 100
            except Exception:
                fcf_margin_pct = None

        candidates: List[str] = []

        # Prefer concrete, numeric sentences when possible.
        if rev_str and op_inc_str and operating_margin is not None:
            candidates.append(
                f"On a run-rate basis, {company_name} is converting {rev_str} of revenue into {op_inc_str} of operating income (operating margin ~{operating_margin:.1f}%)."
            )
        if ocf_str and fcf_str and capex_str:
            candidates.append(
                f"The OCF→FCF bridge is the underwriting hinge: operating cash flow {ocf_str} funds capex of {capex_str}, leaving free cash flow {fcf_str}."
            )
        if capex_pct is not None:
            candidates.append(
                f"Capex intensity is ~{capex_pct:.1f}% of revenue this period, so small changes in investment cadence can swing free cash flow materially."
            )
        if fcf_margin_pct is not None:
            candidates.append(
                f"Free-cash-flow margin is ~{fcf_margin_pct:.1f}%, which is the cleanest single metric for durability as reinvestment scales."
            )
        if operating_margin is not None and net_margin is not None:
            candidates.append(
                f"The spread between operating margin ({operating_margin:.1f}%) and net margin ({net_margin:.1f}%) is a reminder to underwrite on operating profitability and cash rather than below-the-line volatility."
            )
        if cash_total_str and liabilities_str:
            candidates.append(
                f"Liquidity is meaningful: {cash_total_str} of cash and securities versus {liabilities_str} of liabilities provides buffer, but it does not replace durable cash conversion."
            )
        if rev_str and op_inc_str:
            candidates.append(
                f"Revenue scale of {rev_str} paired with {op_inc_str} of operating income defines the current earnings power of the enterprise."
            )
        if fcf_str and ocf_str:
            candidates.append(
                f"The conversion from {ocf_str} of operating cash to {fcf_str} of free cash highlights the capital intensity required to maintain the current growth trajectory."
            )

        # If the extracted snapshot is missing key line items
        # add an explicit, non-repetitive
        # data-gap addendum so strict word-band enforcement can still converge without resorting
        # to generic slogans or templated “watch list” padding.

        if rev_str and (operating_income is None and operating_margin is None):
            candidates.append(
                f"Scale is visible (revenue {rev_str}), but profitability line items are not present in the extracted snapshot, so operating leverage cannot be underwritten from margin data in this draft."
            )
        if rev_str and operating_margin is None:
            candidates.append(
                f"Without an operating margin series, the practical question becomes whether incremental revenue is arriving with stable gross profit and controlled opex, or whether cost intensity is rising with growth."
            )
        if ocf is None:
            candidates.append(
                "Operating cash flow is not present in the extracted snapshot, so cash conversion needs to be validated against working-capital movements, tax timing, and any non-cash add-backs that can inflate earnings optics."
            )
        if capex is None:
            candidates.append(
                "Capital expenditure data is not present here; without it, free cash flow cannot be triangulated, and reinvestment intensity (and its payback) remains the dominant unknown."
            )
        if (cash_total_str is None) and (liabilities_str is None):
            candidates.append(
                "Balance-sheet context is limited in this draft; liquidity and obligations determine how much margin compression the company can absorb before capital allocation shifts from growth to de-risking."
            )
        if liabilities_str and cash_total_str is None:
            candidates.append(
                f"Liabilities are {liabilities_str}, but cash is not provided in the extracted snapshot; funding optionality is therefore unclear, which matters most if growth slows or risk costs rise."
            )
        if cash_total_str and liabilities_str is None:
            candidates.append(
                f"Liquidity is {cash_total_str} in the snapshot, but liabilities are not shown; the margin for error depends on near-term obligations and any refinancing cadence not visible in this excerpt."
            )

        # Add a few structure-preserving underwriting sentences that remain specific to the
        # metrics available, without repeating the “framework” boilerplate the product strips.
        if rev_str:
            candidates.append(
                f"With revenue at {rev_str}, the next-step underwriting is to reconcile that scale to unit economics: pricing/mix, cost-to-serve, and the extent to which fixed costs are providing (or losing) operating leverage."
            )
            candidates.append(
                "A clean way to pressure-test the story is to reconcile the full bridge—revenue to gross profit to opex to operating income to operating cash flow to capex to free cash flow—because surprises tend to sit in the missing links."
            )
            candidates.append(
                "If stock-based compensation or other non-cash charges are material, reported profitability can look stable while per-share value creation is weaker; that tension becomes visible when buyback capacity is compared to dilution."
            )
        candidates.append(
            "To avoid one-quarter noise, the clearest confirmation is a repeatable pattern across consecutive periods: revenue growth translating into stable (or improving) operating profit and cash generation after reinvestment."
        )
        candidates.append(
            "Working-capital drivers such as receivables, deferred revenue, and payables can swing operating cash flow meaningfully; without that bridge, a single quarter’s cash conversion can be more timing than trajectory."
        )
        candidates.append(
            "Capex can represent either capacity expansion (growth) or maintenance (defense); distinguishing the two changes whether a higher spend rate is value-accretive or simply the cost of staying competitive."
        )
        candidates.append(
            "When balance-sheet detail is limited, the most decision-relevant items are near-term obligations and any refinancing cadence, because those determine whether management can keep investing through a softer demand window."
        )
        candidates.append(
            "A more constructive stance typically follows when margin pressure eases while reinvestment stays purposeful, because that combination raises the probability that future growth compounds free cash flow rather than consuming it."
        )
        candidates.append(
            "If revenue slows while cost intensity stays high, fixed costs can turn small demand changes into outsized earnings volatility; the practical mitigation is visible operating leverage and a cost base that flexes with demand."
        )
        candidates.append(
            "The downside scenario is a simultaneous squeeze—higher cost intensity alongside elevated investment spend—which can compress free cash flow even if headline revenue remains resilient."
        )
        candidates.append(
            "Where the draft lacks numeric detail, the decision should be treated as provisional: the missing lines (margins, cash flow, capex, and obligations) are the variables that most directly change downside protection and capital-allocation flexibility."
        )

        # Keep only sentences that aren't already present verbatim-ish.
        existing_norm = " ".join((text or "").lower().split())
        kept: List[str] = []
        for sentence in candidates:
            norm = " ".join(sentence.lower().split())
            if norm and norm not in existing_norm:
                kept.append(sentence)
                existing_norm += " " + norm

        out: List[str] = []
        budget = int(max_words)
        for sentence in kept:
            if not sentence:
                continue
            next_block = (" ".join(out + [sentence])).strip()
            if _count_words(next_block) > budget:
                continue
            out.append(sentence)
            if _count_words(" ".join(out)) >= budget:
                break
        return " ".join(out).strip()

    # 1) Trim if overweight, but do not return early: sentence-preserving trimming can
    # overshoot below the lower bound, and strict-band enforcement must be able to pad.
    split_wc, stripped_wc = _stats(text)
    if split_wc > upper or stripped_wc > upper:
        text = _trim_preserving_headings(text, upper)
        if text and not text.rstrip().endswith((".", "!", "?")):
            text = text.rstrip() + "."

    # 2) If underweight, add addenda into narrative sections (not Key Metrics).
    # Use a bounded loop so we can add multiple distinct sentences without duplicating.
    for _ in range(12):
        if _in_band(text):
            break

        split_wc, stripped_wc = _stats(text)
        current = max(split_wc, stripped_wc)
        if split_wc > upper or stripped_wc > upper:
            text = _trim_preserving_headings(text, upper)
            break
        if current >= lower:
            break

        deficit = max(lower - split_wc, lower - stripped_wc)
        # Prefer adding to Financial Performance first; then MD&A; then Closing Takeaway.
        # Allow a larger addendum when we're far below target (e.g., after aggressive
        # post-processing) so we can converge without resorting to repetitive filler.
        addendum = _build_numeric_addendum(max_words=min(420, deficit + 40))
        if addendum:
            before = text
            text = _safe_insert_into_section("Financial Performance", addendum)
            if text == before:
                text = _safe_insert_into_section(
                    "Management Discussion & Analysis", addendum
                )
            if text == before:
                text = _safe_insert_into_section("Closing Takeaway", addendum)
        else:
            break

    # 3) Final precision adjustment (1–10 words) if still slightly short.
    # We allow a larger micro-padding window here to ensure we hit the ±10 band.
    if not _in_band(text):
        split_wc, stripped_wc = _stats(text)
        remaining = max(lower - split_wc, lower - stripped_wc)
        if remaining > 0:
            text = _micro_pad_tail_words(text, remaining)

    # Final reconciliation: guarantee BOTH backend and UI-visible word counts land
    # inside the ±10 band.
    text = _enforce_whitespace_word_band(
        text, target, tolerance=tolerance, allow_padding=True, dedupe=True
    )
    if _count_words(text) > upper or len(text.split()) > upper:
        text = _trim_preserving_headings(text, upper)
    return text.strip()


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
    tolerance = 10
    tolerance = 10
    start_time = time.time()

    def _progress_callback(percentage: int, status: str):
        if filing_id:
            # Keep progress in the 85-95% range during AI generation phase
            stage_pct = 85 + int(percentage * 0.1)  # Maps 0-100% -> 85-95%
            set_summary_progress(filing_id, status=status, stage_percent=stage_pct)

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
        elapsed = time.time() - start_time
        if timeout_seconds and elapsed > timeout_seconds:
            raise TimeoutError(f"Summary generation exceeded {timeout_seconds} seconds")
        stage_label = "Generating Summary"
        if filing_id:
            # Keep the status user-facing and stable across retries; internal attempt counts are noise.
            set_summary_progress(
                filing_id, status=f"{stage_label}...", stage_percent=88
            )

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

        # Hard-cap the *single request* time so we don't spend minutes in retry/backoff.
        request_timeout_s = (
            max(8.0, float(timeout_seconds - elapsed)) if timeout_seconds else 60.0
        )
        raw_text = _call_gemini_client(
            gemini_client,
            prompt,
            allow_stream=bool(filing_id),
            progress_callback=_progress_callback if filing_id else None,
            stage_name=stage_label if filing_id else "Generating",
            expected_tokens=expected_out_tokens,
            timeout_seconds=request_timeout_s,
            generation_config_override={
                "maxOutputTokens": int(min(max_output_tokens, expected_out_tokens)),
                "temperature": 0.35,
            },
            retry=False,
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
            # Word-count control lines are optional; the backend enforces max length post-hoc.
            pass

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

        prior_count = (
            int(actual_words)
            if actual_words is not None
            else _count_words(summary_text)
        )

        target_val = (
            int(target_length) if target_length is not None else TARGET_LENGTH_MAX_WORDS
        )
        tolerance_val = int(tolerance)
        lower = max(1, target_val - tolerance_val)
        upper = target_val + tolerance_val

        if prior_count > upper:
            abs_diff = prior_count - target_val
            sentences_to_cut = max(1, int(abs_diff / 10)) if abs_diff else 1
            action = (
                f"CONDENSE the memo to land between {lower}–{upper} words. You are {abs_diff} words OVER target {target_val}. "
                f"Remove approximately {sentences_to_cut} sentences of redundancy and low-signal filler, "
                "merge overlapping points, and keep the most decision-relevant numbers and mechanisms. "
                "Do NOT add new sections or any end-of-memo recap."
            )
            corrections.append(
                f"LENGTH CORRECTION #{attempt}: Draft contains {prior_count} words; required range is {lower}–{upper} (target {target_val}). "
                f"ACTION: {action}"
            )
        else:
            shortfall = target_val - prior_count
            # Expand with substance (numbers/mechanisms) rather than generic framework filler.
            action = (
                f"EXPAND the memo to land between {lower}–{upper} words. You are {shortfall} words UNDER target {target_val}. "
                "Add NEW, non-repetitive content by deepening mechanisms and quantifying trade-offs using ONLY the numbers already present in the memo/metrics. "
                "Concrete expansion options (pick 2–3): "
                "(1) add one paragraph in Financial Performance explaining the OCF→FCF bridge and the biggest sequential deltas; "
                "(2) add one paragraph in MD&A linking reinvestment cadence to margin trajectory and cash conversion; "
                "(3) add one additional risk item with a numeric anchor and a clear transmission channel; "
                "(4) add 2–3 measurable triggers in Closing Takeaway (thresholds, next 1–2 periods). "
                "Do NOT add process narration, definitions, or repeated 'framework' slogans."
            )
            corrections.append(
                f"LENGTH CORRECTION #{attempt}: Draft contains {prior_count} words; required range is {lower}–{upper} (target {target_val}). "
                f"ACTION: {action}"
            )
        prompt = _rebuild_prompt()

    if (
        target_length
        and summary_text
        and _count_words(summary_text) > int(target_length)
    ):
        summary_text = _enforce_length_constraints(
            summary_text,
            int(target_length),
            gemini_client,
            quality_validators,
            last_word_stats,
            token_budget=token_budget,
            max_output_tokens=max_output_tokens,
        )
        # Final deterministic guardrail: enforce strict band by trimming only.
        summary_text = _enforce_whitespace_word_band(
            summary_text, int(target_length), tolerance=10, allow_padding=False
        )

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
    #   - Financial Health Rating: 14%
    #   - Executive Summary: 14%
    #   - Financial Performance: 15% (Hero Section)
    #   - Management Discussion & Analysis: 15% (Hero Section)
    #   - Risk Factors: 14%
    #   - Key Metrics: 14%
    #   - Closing Takeaway: 14%
    "Financial Health Rating": 14,
    "Executive Summary": 14,
    "Financial Performance": 15,
    "Management Discussion & Analysis": 15,
    "Risk Factors": 14,
    "Key Metrics": 14,
    "Closing Takeaway": 14,
}

# Key Metrics is a fixed-format, scannable data block. Past a certain length,
# scaling it with the full memo causes low-quality output (repeated "watch" lines).
# For long targets, cap Key Metrics and redistribute the remaining budget across
# narrative sections using their existing weights.
KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS = 1000
KEY_METRICS_FIXED_BUDGET_WORDS = 350
KEY_METRICS_MAX_WORDS = 500
KEY_METRICS_MAX_WATCH_ITEMS = 12


def _section_budget_tolerance_words(
    budget_words: int, *, max_tolerance: int = 10
) -> int:
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
        fixed_key_metrics_budget = min(
            int(KEY_METRICS_FIXED_BUDGET_WORDS), int(body_target)
        )

    remaining_body_target = max(0, int(body_target) - int(fixed_key_metrics_budget))

    total_weight = sum(
        SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) for s in distribution_sections
    )
    if total_weight <= 0:
        total_weight = len(distribution_sections) if distribution_sections else 1

    exacts = {
        s: (
            SECTION_PROPORTIONAL_WEIGHTS.get(s, 0)
            * remaining_body_target
            / total_weight
        )
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
            order = sorted(
                sections_to_use, key=lambda s: budgets.get(s, 0), reverse=True
            )
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
            merged[canon] = (
                f"{combined}\n\n{addition}".strip() if addition else combined
            )
        else:
            merged[canon] = (body or "").strip()

    # If we captured any preamble, fold it into Executive Summary so it doesn't steal
    # from the proportional budgets.
    preamble_text = "\n".join([ln for ln in preamble if ln.strip()]).strip()
    if preamble_text:
        exec_body = (merged.get("Executive Summary") or "").strip()
        merged["Executive Summary"] = (
            f"{preamble_text}\n\n{exec_body}".strip() if exec_body else preamble_text
        )

    # Replace Key Metrics with deterministic, non-hallucinated block when available.
    if metrics_lines:
        merged["Key Metrics"] = (metrics_lines or "").strip()

    def _normalize_key_metrics_for_word_band(body: str) -> str:
        """Reduce whitespace-token inflation in Key Metrics without losing content."""
        body = (body or "").replace("\u00a0", " ").strip()
        if not body:
            return body

        out_lines: List[str] = []
        for raw in body.splitlines():
            line = (raw or "").rstrip()
            # Pipes are punctuation-only tokens under whitespace counting; convert them to commas.
            line = re.sub(r"\s*\|\s*", ", ", line)
            # Leading '-' bullets inflate `len(text.split())` but don't count as words in `_count_words()`.
            line = re.sub(r"^\s*-\s+", "", line)
            # Merge the arrow marker into the next token so it doesn't count as its own
            # word under `_count_words()` / `split()` (e.g., '→ Revenue' → '→Revenue').
            line = re.sub(r"^\s*→\s+", "→", line)
            # Prefer words over punctuation-only separators.
            line = line.replace(" + ", " and ")
            line = line.replace(" & ", " and ")
            out_lines.append(line)

        cleaned = "\n".join(out_lines).strip()
        cleaned = re.sub(r",\s*,+", ", ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    if "Key Metrics" in merged:
        merged["Key Metrics"] = _normalize_key_metrics_for_word_band(
            merged.get("Key Metrics") or ""
        )
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
                total_w = sum(
                    SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) for s in recipients
                ) or len(recipients)
                magnitude = abs(int(delta))
                exacts = {
                    s: (SECTION_PROPORTIONAL_WEIGHTS.get(s, 0) * magnitude / total_w)
                    for s in recipients
                }
                bump: Dict[str, int] = {s: int(exacts[s]) for s in recipients}
                remainders = {s: exacts[s] - bump[s] for s in recipients}
                drift = int(magnitude) - sum(bump.values())
                if drift:
                    order = sorted(
                        recipients, key=lambda s: remainders.get(s, 0), reverse=True
                    )
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

        # IMPORTANT: Keep Key Metrics in the normalized "→Watch:" form so subsequent
        # whitespace-token normalization steps are idempotent and do not alter the
        # MS-word-style counts used by section budgets.
        watch_templates = [f"→Watch: {topic}" for topic in watch_topics]
        candidates: List[Tuple[int, str]] = [
            (_count_words(t), t) for t in watch_templates
        ]
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
            remaining = max(0, int(target_words) - int(current))

            def _pick(options: List[Tuple[int, str]]) -> Optional[str]:
                if not options:
                    return None
                # Prefer the smallest line that covers the remaining gap.
                if remaining > 0:
                    cover = [(wc, t) for wc, t in options if wc >= remaining]
                    if cover:
                        min_wc = min(wc for wc, _t in cover)
                        for wc, t in cover:
                            if wc == min_wc:
                                return t
                # Otherwise (or if nothing can cover), take the largest that fits to make progress.
                max_wc = max(wc for wc, _t in options)
                for wc, t in options:
                    if wc == max_wc:
                        return t
                return options[-1][1]

            available_unique = [
                (wc, t) for wc, t in candidates if wc <= slack and t not in used
            ]
            chosen = _pick(available_unique)
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
                if len(sent.split()) < 2:
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
        body = (body or "").replace("\u00a0", " ").strip()
        if not body:
            return body
        # If the model emitted multiple risks inline (e.g., "**A**: ... **B**: ..."),
        # force each risk header to start on its own line so the UI renders it cleanly.
        body = re.sub(r"\s+(?=\*\*[^*]{2,120}\*\*\s*:)", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

        # Split into paragraphs so we can preserve non-risk framing/padding paragraphs
        # as preamble/postscript rather than accidentally folding them into the last
        # risk item (which can then get truncated away).
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if (p or "").strip()]
        header_re = re.compile(r"^\*\*[^*]{2,120}\*\*\s*:")
        preamble_parts: List[str] = []
        postscript_parts: List[str] = []
        risk_paragraphs: List[str] = []
        seen_risk = False
        for paragraph in paragraphs:
            if header_re.match(paragraph):
                seen_risk = True
                risk_paragraphs.append(paragraph)
            else:
                (preamble_parts if not seen_risk else postscript_parts).append(
                    paragraph
                )

        preamble = "\n\n".join([p for p in preamble_parts if p]).strip()
        postscript = "\n\n".join([p for p in postscript_parts if p]).strip()
        body = "\n\n".join([p for p in risk_paragraphs if p]).strip()
        if not body:
            # No structured risk bullets; keep the original content.
            return "\n\n".join([b for b in (preamble, postscript) if b]).strip() or (
                body or ""
            )

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
        if postscript:
            blocks.append(postscript)
        return "\n\n".join([b for b in blocks if b.strip()]).strip()

    # Seed a memo-level exclusion set so deterministic padding doesn't repeat the same
    # "process" sentences across multiple sections (users perceive this as templated).
    global_exclude_norms: set[str] = set()
    for existing_body in merged.values():
        global_exclude_norms.update(_seed_exclude_norms(existing_body or ""))

    # Adjust each canonical section into its budget band.
    for section_name, budget in budgets.items():
        # If the model omitted a canonical section, create an empty placeholder so
        # deterministic padding can still restore the fixed distribution.
        if section_name not in merged:
            merged[section_name] = ""
        budget = int(budget or 0)
        if budget <= 0:
            continue

        tol = _section_budget_tolerance_words(
            budget, max_tolerance=int(section_tolerance)
        )
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
                exclude_norms = set(global_exclude_norms)
                exclude_norms.update(_seed_exclude_norms(body))

                while wc < lower and wc < upper:
                    risk_name_exclusions = (
                        _extract_risk_names(body)
                        if section_name == "Risk Factors"
                        else None
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
                    for req in request_candidates:
                        pad_sentences = _generate_padding_sentences(
                            req,
                            exclude_norms=exclude_norms,
                            section=section_name,
                            is_persona=is_persona,
                            exclude_risk_names=risk_name_exclusions,
                            max_words=slack,
                        )
                        if section_name == "Risk Factors":
                            pad_text = "\n".join(
                                [s for s in pad_sentences if (s or "").strip()]
                            ).strip()
                        else:
                            pad_text = " ".join(pad_sentences).strip()
                        candidate = _append_padding(body, pad_text, section_name)
                        if _count_words(candidate) > upper:
                            candidate = _truncate_text_to_word_limit(candidate, upper)
                        new_wc = _count_words(candidate)
                        if new_wc > wc:
                            body = candidate
                            wc = new_wc
                            exclude_norms.update(pad_sentences)
                            global_exclude_norms.update(pad_sentences)
                            progressed = True
                            break

                    if not progressed:
                        # Fallback: templates may not fit. Stop here rather than forcing
                        # low-quality micro-padding loops. Section will remain slightly
                        # short, which is preferred over repetitive filler.
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
        tol = _section_budget_tolerance_words(
            budget, max_tolerance=int(section_tolerance)
        )
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
                exclude_norms = set(global_exclude_norms)
                exclude_norms.update(_seed_exclude_norms(body))
                for req in request_candidates:
                    pad_sentences = _generate_padding_sentences(
                        req,
                        exclude_norms=exclude_norms,
                        section=name,
                        is_persona=is_persona,
                        exclude_risk_names=risk_name_exclusions,
                        max_words=slack,
                    )
                    if not pad_sentences and global_exclude_norms:
                        pad_sentences = _generate_padding_sentences(
                            req,
                            exclude_norms=_seed_exclude_norms(body),
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
                        exclude_norms.update(pad_sentences)
                        global_exclude_norms.update(pad_sentences)
                        break

                # If templates couldn't fit within the slack, fall back to micro-padding
                # (up to the section upper bound) so we always make measurable progress.
                if _count_words(new_body) <= current_wc and slack > 0:
                    candidate = body
                    remaining_micro = min(deficit, slack)
                    while remaining_micro > 0:
                        before_wc = _count_words(candidate)
                        candidate2 = _micro_pad_tail_words(
                            candidate, min(remaining_micro, 4)
                        )
                        after_wc = _count_words(candidate2)
                        if after_wc <= before_wc:
                            break
                        candidate = candidate2
                        remaining_micro = max(
                            0, remaining_micro - (after_wc - before_wc)
                        )
                    if (
                        _count_words(candidate) <= sec_upper
                        and _count_words(candidate) > current_wc
                    ):
                        new_body = candidate

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

    # Keep section "minimums" light and non-scaling:
    # - This validator exists to prevent empty/missing sections, not to force padding.
    # - Scaling mins with high target lengths incentivizes repetition and filler.
    min_words_by_section: Dict[str, int] = {
        "Financial Health Rating": 12,
        "Executive Summary": 20,
        "Financial Performance": 20,
        "Management Discussion & Analysis": 25,
        "Risk Factors": 20,
        "Key Metrics": 5,
        "Closing Takeaway": 15,
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
            min_words = int(min_words_by_section.get(title, 15))
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
                future_pos = lower_text.find(
                    f"## {future_title.lower()}", section_start
                )
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


def _make_numbers_discipline_validator(
    target_length: Optional[int],
) -> Callable[[str], Optional[str]]:
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


def _make_verbatim_repetition_validator() -> Callable[[str], Optional[str]]:
    """
    Reject memos with repeated sentences/near-identical sentences (a common failure mode
    when the model pads word count by rephrasing the same point).
    """

    def _normalize(sentence: str) -> str:
        cleaned = (sentence or "").strip()
        cleaned = cleaned.replace("’", "'").replace("“", '"').replace("”", '"')
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.lower()
        cleaned = re.sub(r"[^a-z0-9%$ ]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _validator(text: str) -> Optional[str]:
        if not text:
            return None

        bodies: List[str] = []
        for title in (
            "Financial Health Rating",
            "Executive Summary",
            "Financial Performance",
            "Management Discussion & Analysis",
            "Risk Factors",
            "Closing Takeaway",
        ):
            body = _extract_markdown_section_body(text, title)
            if body:
                bodies.append(body)

        joined = "\n".join(bodies)
        joined = re.sub(r"^\s*WORD COUNT:\s*\d+\s*$", "", joined, flags=re.MULTILINE)

        sentences = re.split(r"(?<=[.!?])\s+", joined)
        counts: Dict[str, int] = {}
        samples: Dict[str, str] = {}
        for sentence in sentences:
            normalized = _normalize(sentence)
            if not normalized:
                continue
            # Skip short sentences; allow a small amount of unavoidable repetition.
            if len(normalized) < 70 or len(normalized.split()) < 10:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
            samples.setdefault(normalized, sentence.strip())

        repeated = [k for k, v in counts.items() if v >= 2]
        if repeated:
            sample = samples[repeated[0]]
            sample = re.sub(r"\s+", " ", sample).strip()
            return (
                "Verbatim repetition detected (same sentence appears multiple times). "
                f"Remove repeats and keep each idea once per section. Example repeated sentence: '{sample[:140]}...'"
            )

        return None

    return _validator


def _make_phrase_limits_validator() -> Callable[[str], Optional[str]]:
    """
    Reject common "manifesto"/definition loops that hurt readability.
    These are intentionally narrow (specific phrases) to avoid blocking legitimate analysis.
    """

    def _normalize(text: str) -> str:
        lowered = (text or "").lower()
        lowered = lowered.replace("’", "'")
        lowered = re.sub(r"[^a-z0-9%$ ]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    # pattern -> max allowed count
    limits: Dict[str, int] = {
        # Definitions / meta-commentary (forbidden)
        "free cash flow is the difference": 0,
        "thesis reads best": 0,
        "this thesis reads": 0,
        "what i care about": 0,
        "i care about": 0,
        "i focus on": 0,
        "what i watch": 0,
        "i will be watching": 0,
        "falsify the thesis": 0,
        "falsifies the thesis": 0,
        "what would falsify": 0,
        # Catchphrase spam (cap tightly)
        "margin for error": 1,
        "watch cash conversion": 1,
        "base case hold": 1,
    }

    def _validator(text: str) -> Optional[str]:
        normalized = _normalize(text)
        for phrase, max_count in limits.items():
            count = normalized.count(phrase)
            if count > max_count:
                if max_count == 0:
                    return f"Forbidden phrase detected ('{phrase}'). Remove it entirely and rewrite without meta/definition filler."
                return (
                    f"Over-repetition detected: '{phrase}' appears {count} times. "
                    f"Cap it at {max_count} (introduce once, then move on)."
                )
        return None

    return _validator


def _make_sentence_stem_repetition_validator() -> Callable[[str], Optional[str]]:
    """
    Detect "same sentence shape" repetition even when not verbatim.
    Heuristic: repeated leading word-stems across long sentences.
    """

    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
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
        "this",
        "that",
        "these",
        "those",
    }

    def _normalize(sentence: str) -> List[str]:
        cleaned = (sentence or "").lower()
        cleaned = cleaned.replace("’", "'").replace("“", '"').replace("”", '"')
        cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        words = [w for w in cleaned.split() if w and w not in stopwords]
        return words

    def _validator(text: str) -> Optional[str]:
        if not text:
            return None
        bodies: List[str] = []
        for title in (
            "Financial Health Rating",
            "Executive Summary",
            "Financial Performance",
            "Management Discussion & Analysis",
            "Risk Factors",
            "Closing Takeaway",
        ):
            body = _extract_markdown_section_body(text, title)
            if body:
                bodies.append(body)

        joined = "\n".join(bodies)
        joined = re.sub(r"^\s*WORD COUNT:\s*\d+\s*$", "", joined, flags=re.MULTILINE)

        sentences = re.split(r"(?<=[.!?])\s+", joined)
        counts: Dict[str, int] = {}
        samples: Dict[str, str] = {}
        for sentence in sentences:
            words = _normalize(sentence)
            if len(words) < 14:
                continue
            stem = " ".join(words[:8])
            if len(stem) < 25:
                continue
            counts[stem] = counts.get(stem, 0) + 1
            samples.setdefault(stem, sentence.strip())

        repeated = [k for k, v in counts.items() if v >= 3]
        if repeated:
            sample = re.sub(r"\s+", " ", samples[repeated[0]]).strip()
            return (
                "Repetition-by-structure detected (multiple long sentences start the same way). "
                f"Rewrite to avoid looping phrasing. Example: '{sample[:140]}...'"
            )
        return None

    return _validator


def _make_period_delta_bridge_validator(
    *, require_bridge: bool
) -> Callable[[str], Optional[str]]:
    def _validator(text: str) -> Optional[str]:
        if not require_bridge:
            return None
        perf_body = _extract_markdown_section_body(text, "Financial Performance")
        if not perf_body:
            return None

        lines = [ln.strip() for ln in perf_body.splitlines() if ln.strip()]
        bridge_window = lines[:16]

        bridge_lines = [
            ln
            for ln in bridge_window
            if ("→" in ln and "(Δ" in ln and ln.lstrip().startswith(("-", "•", "·")))
        ]

        if not (6 <= len(bridge_lines) <= 8):
            return (
                "Missing the required Q/Q (or Y/Y) delta bridge at the top of Financial Performance. "
                "Add exactly 6 short lines (no more than 8) using the pattern: "
                "Metric: prior → current (Δ ... ) — why changed — why it matters. "
                "Use the 'Q/Q DELTA BRIDGE NUMBERS' reference block if present; do not invent prior-period values."
            )

        lowered_bridge = " ".join(bridge_lines).lower()
        required_terms = (
            "revenue",
            "operating margin",
            "operating cash",
            "capex",
            "free cash",
        )
        if not all(term in lowered_bridge for term in required_terms):
            return (
                "Delta bridge is missing required lines. Include: Revenue, Operating margin, Operating cash flow, Capex, Free cash flow, "
                "and one balance-sheet liquidity line (Cash+securities or Cash)."
            )

        # Validate math + consistency for the bridge lines.
        money_pattern = re.compile(
            r"\$(?P<sign>[+\-])?(?P<num>\d+(?:\.\d+)?)(?P<unit>[BM])"
        )
        percent_pattern = re.compile(r"(?P<num>[+\-]?\d+(?:\.\d+)?)%")
        pp_pattern = re.compile(r"(?P<num>[+\-]?\d+(?:\.\d+)?)pp")

        def _money_to_billions(token: str) -> Optional[float]:
            match = money_pattern.search(token)
            if not match:
                return None
            sign = -1.0 if match.group("sign") == "-" else 1.0
            try:
                value = float(match.group("num")) * sign
            except Exception:
                return None
            unit = match.group("unit")
            if unit == "B":
                return value
            if unit == "M":
                return value / 1000.0
            return None

        for line in bridge_lines:
            head = line.split("—", 1)[0].strip()

            if "operating margin" in head.lower():
                percents = percent_pattern.findall(head)
                pp_match = pp_pattern.search(head)
                if len(percents) < 2 or not pp_match:
                    return (
                        "Delta bridge formatting error for Operating margin. Use: "
                        "Operating margin: prior% → current% (Δ +X.Xpp) — why changed — why it matters."
                    )
                prev = float(percents[0])
                cur = float(percents[1])
                delta_pp = float(pp_match.group("num"))
                if abs((cur - prev) - delta_pp) > 0.2:
                    return "Delta bridge math error for Operating margin. Ensure the pp delta matches prior → current."
                continue

            monies = money_pattern.findall(head)
            if len(monies) < 3:
                return (
                    "Delta bridge formatting error for money metrics. Use: "
                    "Metric: $prior → $current (Δ $delta, +X.X%) — why changed — why it matters."
                )
            tokens = money_pattern.finditer(head)
            amounts = [_money_to_billions(m.group(0)) for m in tokens]
            if len(amounts) < 3 or any(v is None for v in amounts[:3]):
                return "Delta bridge parsing error. Use $X.XXB or $X.XXM consistently for prior, current, and delta."
            prev_b, cur_b, delta_b = amounts[0], amounts[1], amounts[2]
            if abs((cur_b - prev_b) - delta_b) > 0.03:
                return "Delta bridge math error. Ensure delta equals current minus prior (prior → current) for each line."
            pct_match = percent_pattern.search(head)
            if pct_match:
                pct = float(pct_match.group("num"))
                if prev_b != 0:
                    expected = (delta_b / abs(prev_b)) * 100
                    if abs(expected - pct) > 0.6:
                        return "Delta bridge percent error. Ensure the percent change matches delta divided by prior."

            # Catch obvious language contradictions (e.g., negative delta described as growth to X).
            contradiction_phrases = (
                "growth to",
                "grew to",
                "increased to",
                "up to",
                "rose to",
                "expanded to",
            )
            if delta_b < 0 and any(p in line.lower() for p in contradiction_phrases):
                return (
                    "Delta bridge language contradiction: negative delta described as growth/increase. "
                    "Rewrite the driver sentence to match the direction (down/decline) without changing numbers."
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


def _make_stance_consistency_validator() -> Callable[[str], Optional[str]]:
    """Ensure the memo uses a single Buy/Hold/Sell stance consistently."""

    def _last_stance(text: str) -> Optional[str]:
        matches = list(re.finditer(r"\b(buy|hold|sell)\b", text or "", re.IGNORECASE))
        if not matches:
            return None
        return (matches[-1].group(1) or "").strip().lower() or None

    def _validator(text: str) -> Optional[str]:
        closing_body = _extract_markdown_section_body(text, "Closing Takeaway")
        if not closing_body:
            return None

        closing_stance = _last_stance(closing_body)
        if not closing_stance:
            return None

        # Scan all non-closing sections for stance words; they must either be absent
        # or match the Closing Takeaway stance.
        pattern = re.compile(r"^\s*##\s*(.+)\s*$", re.MULTILINE)
        matches = list(pattern.finditer(text or ""))
        for idx, match in enumerate(matches):
            heading_title = (match.group(1) or "").strip()
            heading_line = f"## {heading_title}".strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = (text or "")[start:end].strip()

            canon = _standard_section_name_from_heading(heading_line)
            if canon == "Closing Takeaway":
                continue

            other_stance = _last_stance(body or "")
            if other_stance and other_stance != closing_stance:
                return (
                    f"Recommendation stance is inconsistent: Closing Takeaway is '{closing_stance.upper()}', "
                    f"but '{canon}' contains '{other_stance.upper()}'. Use ONE stance consistently."
                )
        return None

    return _validator


def _make_persona_exclusivity_validator(
    *, persona_requested: bool, selected_persona_name: Optional[str]
) -> Callable[[str], Optional[str]]:
    """Prevent name-dropping or switching between famous investor personas."""

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

    forbidden_labels = [
        "Value Investor Default",
        "Quality & Moat Focus",
        "Financial Resilience",
        "Growth Sustainability",
        "User-Defined Mix",
    ]

    def _validator(text: str) -> Optional[str]:
        haystack = text or ""

        for label in forbidden_labels:
            if re.search(rf"(?i)\b{re.escape(label)}\b", haystack):
                return (
                    f"Output includes an internal framework label ('{label}'). "
                    "Remove framework labels; write the lens implicitly."
                )

        if not persona_requested:
            for name in persona_names:
                if re.search(rf"(?i)\b{re.escape(name)}\b", haystack):
                    return (
                        "Output name-drops a famous investor persona, but no persona was selected. "
                        "Rewrite in neutral third-person analyst voice."
                    )
            return None

        if selected_persona_name:
            for name in persona_names:
                if name == selected_persona_name:
                    continue
                if re.search(rf"(?i)\b{re.escape(name)}\b", haystack):
                    return (
                        f"Persona voice is inconsistent: output references '{name}' but the selected persona is "
                        f"'{selected_persona_name}'. Use ONE persona consistently and remove all other name-drops."
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
                return f"Risk Factors are too thin under '{name}'. Expand each risk to 2-3 substantive sentences with a clear mechanism."

            if not re.search(r"\d", body):
                return f"Risk Factors under '{name}' must include at least one numeric anchor from this memo (%, $, ratio) to quantify impact."

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
            "- FORBIDDEN: First-person language ('I', 'my view'), any famous investor voices/catchphrases, folksy analogies.\n"
            "- FOCUS ON: Quantitative metrics, objective analysis, evidence-based conclusions."
        )
        target_length = (
            _clamp_target_length(preferences.target_length) if preferences else None
        )
        if target_length:
            base += (
                f"\nTarget length: {target_length} words (must land within ±10 words). "
                "Do not add filler or repeat framework sentences to manipulate length."
            )
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
            "Adopt the user's requested persona as an editorial filter (what you emphasize, what you de-emphasize, and how you weigh trade-offs). "
            "However, the output must still read like an institutional-grade memo: decisive, hierarchical, and non-repetitive."
        )
        instructions.append(
            "- Do NOT name-drop any investors/framework labels in the prose (including the persona name)."
        )
        instructions.append(
            "- Do NOT switch personas or lenses mid-memo. Maintain ONE consistent identity and one consistent recommendation end-to-end."
        )
        # NOTE: Investor Lens section removed - persona voice is now integrated into Executive Summary directly
        instructions.append(
            "- Persona flavor should be subtle and decision-oriented. Avoid process narration and self-referential doctrine ('I always...', 'I care about...', 'what I watch...')."
        )
        instructions.append(
            "- If you use first-person at all, confine it to (a) a single stance sentence in Executive Summary and (b) the Closing Takeaway verdict. The rest should be written in an institutional, third-person research style."
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
            "- Any famous investor voices or catchphrases\n"
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
        instructions.append(
            f"""
=== LENGTH GUIDANCE (HARD CAP) ===
TARGET LENGTH: {target_length} words (must land within ±10 words).

RULES:
- You MUST land between {max(1, int(target_length) - 10)}–{int(target_length) + 10} words total.
- Do NOT pad with generic "framework" sentences, meta-process narration, or repeated constraints.
- If you are outside the band, rewrite by adding/removing substantive sentences (numbers + mechanisms), not filler.
=== END LENGTH GUIDANCE ===
"""
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

CRITICAL CONSISTENCY RULES:
- Do NOT name-drop other investors or frameworks.
- Do NOT include persona labels like "Value Investor Default" or "Magic Formula" unless the user explicitly asked for that persona/lens in the investor brief.
- Do NOT contradict your own stance: use ONE recommendation (Buy/Hold/Sell) consistently in Executive Summary and Closing Takeaway.
DO NOT end with incomplete sentences. Every thought must be finished.
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
            directives.append("FRAMEWORK (User-selected lens):")
            directives.append(f"  {framework_prompt}")
            directives.append(
                f"  You MUST evaluate the company through this specific lens."
            )

        weighting_prompt = HEALTH_WEIGHTING_PROMPTS.get(weighting)
        if weighting_prompt:
            directives.append(f"")
            directives.append("PRIMARY FACTOR WEIGHTING (User-selected):")
            directives.append(f"  {weighting_prompt}")
            directives.append(
                f"  This factor should have the MOST influence on the final score."
            )

        risk_prompt = HEALTH_RISK_PROMPTS.get(risk)
        if risk_prompt:
            directives.append(f"")
            directives.append("RISK TOLERANCE (User-selected):")
            directives.append(f"  {risk_prompt}")
            directives.append(
                f"  Apply this risk tolerance when penalizing or rewarding factors."
            )

        depth_prompt = HEALTH_ANALYSIS_DEPTH_PROMPTS.get(depth)
        if depth_prompt:
            directives.append(f"")
            directives.append("ANALYSIS DEPTH (User-selected):")
            directives.append(f"  {depth_prompt}")
            directives.append(f"  Your analysis must reach this level of depth.")

        directives.append("")
        directives.append(
            "COMPLIANCE CHECK: The health score MUST reflect ALL user-specified parameters above."
        )
        directives.append(
            "If the score doesn't align with user's framework, weighting, and risk tolerance, REVISE IT."
        )

        directives.append("")
        directives.append("=== NARRATIVE PERSONALIZATION (MANDATORY) ===")
        directives.append(
            "Your explanation must feel customized to the user's lens, weighting, and risk tolerance."
        )
        directives.append(
            "Do NOT name internal framework labels (e.g., 'Value Investor Default'). Make the lens explicit in plain language instead."
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

    # Inline XBRL (iXBRL) filings often embed large amounts of taxonomy metadata
    # as text nodes (URIs, namespace-qualified identifiers, etc.). That noise can
    # dominate the extracted text and make KPI extraction fail. Remove common
    # taxonomy/URI patterns before stripping tags.
    cleaned = re.sub(r"https?://\S{10,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"www\.\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:us-gaap|dei|srt|ifrs-full|xbrli|xbrldi|xbrldt|iso4217|xlink|link|ref|xsd|ix|ixt):[A-Za-z0-9_.-]+\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Drop other namespace-qualified tokens that frequently appear in iXBRL dumps
    # (e.g., company-specific namespaces like `trmb:...`).
    cleaned = re.sub(
        r"\b[a-z]{2,10}:[A-Za-z][A-Za-z0-9_.-]{2,}\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Preserve some structure before stripping tags.
    # Keeping newlines helps:
    # - section extraction (Item headings)
    # - KPI quote detection (tables / bullet-like rows)
    cleaned = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", cleaned)
    cleaned = re.sub(
        r"(?i)</\s*(p|div|tr|li|h[1-6]|table|section|article)\s*>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)<\s*(p|div|tr|li|h[1-6]|table|section|article)\b[^>]*>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(r"(?i)</\s*td\s*>", " ", cleaned)
    cleaned = re.sub(r"(?i)<\s*td\b[^>]*>", " ", cleaned)

    # Remove remaining HTML tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Unescape HTML entities
    cleaned = unescape(cleaned)

    # Repeat the iXBRL noise stripping after unescape because some documents encode
    # URLs/namespace tokens via entities (e.g., `http&#58;//...`, `us-gaap&#58;...`),
    # which only become matchable once unescaped.
    cleaned = re.sub(r"https?://\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"www\.\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:us-gaap|dei|srt|ifrs-full|xbrli|xbrldi|xbrldt|iso4217|xlink|link|ref|xsd|ix|ixt):[A-Za-z0-9_.-]+\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'\bcontextRef="[^"]{1,80}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bunitRef="[^"]{1,40}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bname="[^"]{1,140}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bid="[^"]{1,120}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bdecimals="[^"]{1,20}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bscale="[^"]{1,20}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bformat="[^"]{1,40}"', " ", cleaned, flags=re.IGNORECASE)

    # Normalize whitespace but preserve newlines
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Drop ultra-noisy lines (mostly taxonomy/IDs) that iXBRL sometimes leaves behind.
    lines = []
    for line in (cleaned or "").splitlines():
        s = line.strip()
        if not s:
            lines.append("")
            continue
        # Keep obvious section headers and human sentences.
        upper = s.upper()
        if (
            "ITEM " in upper
            or "MANAGEMENT DISCUSSION" in upper
            or "RISK FACTORS" in upper
            or "FINANCIAL STATEMENTS" in upper
            or "TABLE OF CONTENTS" in upper
        ):
            lines.append(s)
            continue

        tokens = s.split()
        if len(tokens) >= 10:
            noise_tokens = 0
            for t in tokens:
                if ":" in t or "/" in t:
                    noise_tokens += 1
            noise_ratio = noise_tokens / max(1, len(tokens))
            alpha = sum(1 for ch in s if ch.isalpha())
            digit = sum(1 for ch in s if ch.isdigit())
            # Drop lines that look like extracted XBRL context dumps:
            # - lots of namespace/URI-ish tokens
            # - very low natural language signal
            if noise_ratio >= 0.35 and alpha < 40 and digit >= 10:
                continue
            if noise_ratio >= 0.55 and alpha < 80:
                continue

        lines.append(s)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _looks_like_table_of_contents_snippet(text: str) -> bool:
    """Best-effort detector for SEC filing Table-of-Contents snippets.

    A common failure mode: regex section extraction matches the *first* Item heading,
    which is often inside the TOC, producing a tiny low-signal excerpt.
    """
    if not text:
        return False

    snippet = (text or "")[:8000]
    lower = snippet.lower()
    toc_pos = lower.find("table of contents")

    # NOTE: Many iXBRL filings include a repeated "Table of Contents" page header
    # inside real sections. Treat it as a TOC signal only when it appears near the
    # start *and* the surrounding snippet also looks like a TOC listing.
    has_toc_header = toc_pos != -1 and toc_pos < 600

    # Dot leaders: "........" show up heavily in TOCs.
    dot_leaders = len(re.findall(r"\\.{4,}", snippet))
    if dot_leaders >= 3:
        return True

    # Many item references in a very short span.
    item_hits = len(re.findall(r"\\bitem\\s+\\d+[a-z]?(?:\\.|\\b)", lower))
    if has_toc_header and item_hits >= 3:
        return True
    if item_hits >= 6 and len(snippet) < 6000:
        return True
    if item_hits >= 3 and len(snippet) < 2500:
        return True

    # Some filings have a one-line TOC with "PART I" / "PART II" etc.
    if "part i" in lower and item_hits >= 3 and len(snippet) < 8000:
        return True

    return False


def _extract_section(text: str, start_pattern: str, end_patterns: List[str]) -> str:
    """Extract a section from text bounded by start regex and optional end regexes.

    Important: SEC filings often include a Table of Contents where Item headings
    appear before the real section content. We scan multiple occurrences and pick
    the best match (usually the longest non-TOC block).
    """
    start_regex = re.compile(start_pattern, re.IGNORECASE)
    matches = list(start_regex.finditer(text or ""))
    if not matches:
        return ""

    # Evaluate a small set of likely-good occurrences:
    # - a couple of early matches (some filings have no TOC)
    # - several late matches (real section content usually appears later)
    candidate_matches: List[re.Match[str]] = []
    for m in matches[:2] + matches[-8:]:
        if not any(existing.start() == m.start() for existing in candidate_matches):
            candidate_matches.append(m)

    best = ""
    best_len = 0
    fallback_longest = ""
    fallback_len = 0

    for match in candidate_matches:
        start_idx = match.start()
        content_start_idx = match.end()
        end_idx = len(text)

        for end_pattern in end_patterns:
            end_regex = re.compile(end_pattern, re.IGNORECASE)
            end_match = end_regex.search(text, content_start_idx)
            if end_match and end_match.start() < end_idx:
                end_idx = end_match.start()

        section = (text[start_idx:end_idx] or "").strip()
        if not section:
            continue

        section_len = len(section)
        if section_len > fallback_len:
            fallback_longest = section
            fallback_len = section_len

        if _looks_like_table_of_contents_snippet(section):
            continue

        if section_len > best_len:
            best = section
            best_len = section_len

    return best or fallback_longest


def _load_document_excerpt(
    path: Path,
    limit: Optional[int] = None,
    *,
    max_pages: Optional[int] = None,
) -> str:
    """Load filing document and extract the most relevant textual sections."""
    effective_limit = int(limit) if limit else 220_000

    def _looks_like_pdf(p: Path) -> bool:
        """Best-effort PDF detection by extension or magic bytes.

        Some flows save SEC attachments to `.html` paths even when the bytes are a PDF.
        In those cases, extension checks fail and downstream extraction gets garbage.
        """
        try:
            if p.suffix.lower() == ".pdf":
                return True
            with p.open("rb") as handle:
                return handle.read(5) == b"%PDF-"
        except Exception:  # noqa: BLE001
            return False

    # PDFs must be parsed as binary; reading as UTF-8 produces garbage and breaks
    # section extraction + KPI snippet windows.
    if _looks_like_pdf(path):
        try:
            import fitz  # PyMuPDF

            # Use bytes-backed open so files with misleading extensions still parse.
            pdf_bytes = path.read_bytes()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            parts: List[str] = []
            total = 0
            # Extract a bounded amount of text for downstream regex section parsing.
            # Keep a little extra to reduce the chance we truncate mid-section.
            target = max(effective_limit, 220_000)
            hard_cap = max(target, int(target * 1.25))

            page_count = int(doc.page_count)
            page_indices = list(range(page_count))
            if max_pages and page_count > int(max_pages):
                cap = max(1, int(max_pages))
                head_n = min(8, page_count)
                tail_n = min(8, max(0, page_count - head_n))
                selected: set[int] = set(range(head_n))
                selected.update(range(max(0, page_count - tail_n), page_count))
                remaining = cap - len(selected)
                if remaining > 0 and page_count > 0:
                    step = max(1, page_count // max(1, remaining))
                    for p in range(0, page_count, step):
                        selected.add(p)
                        if len(selected) >= cap:
                            break
                page_indices = sorted(selected)[:cap]

            # Hard safety cap: PDF text extraction can be unexpectedly slow for some
            # filings (embedded fonts/images, large pages). Keep the API responsive.
            parse_timeout_raw = os.getenv("SUMMARY_PDF_PARSE_TIMEOUT_SECONDS", "8") or "8"
            try:
                parse_timeout_s = float(parse_timeout_raw)
            except ValueError:
                parse_timeout_s = 8.0
            parse_timeout_s = max(0.0, parse_timeout_s)
            parse_started = time.monotonic()

            for i in page_indices:
                if parse_timeout_s and (time.monotonic() - parse_started) > parse_timeout_s:
                    print(
                        f"⚠️ PDF text extraction timed out after {parse_timeout_s:.1f}s (pages_read={len(parts)})."
                    )
                    break
                try:
                    t = doc.load_page(i).get_text("text") or ""
                except Exception:  # noqa: BLE001
                    t = ""
                if not t:
                    continue
                parts.append(t)
                total += len(t)
                if total >= hard_cap:
                    break
            text = "\n".join(parts).strip()
            if not text:
                return ""
        except Exception:  # noqa: BLE001
            return ""
    else:
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
        # 10-Q Item 1
        (
            r"ITEM\s+1\.?\s+FINANCIAL\s+STATEMENTS",
            [r"ITEM\s+2\.?", r"ITEM\s+1A\.?"],
            "FINANCIAL STATEMENTS",
        ),
        (
            r"ITEM\s+1A\.?\s+RISK\s+FACTORS",
            [r"ITEM\s+1B\.?", r"ITEM\s+2\.?"],
            "RISK FACTORS",
        ),
        # MD&A patterns - multiple variations to catch different filing formats
        # 10-Q Item 2
        (
            r"ITEM\s+2[\.\s:]+(?:MANAGEMENT[''\u2019]?S?\s+DISCUSSION|MD&A)",
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
        return text[:effective_limit]

    # CRITICAL FALLBACK: If MD&A is missing but other sections were found,
    # append a large chunk of text to ensure the AI has context.
    has_mda = any(s.startswith("MANAGEMENT DISCUSSION & ANALYSIS") for s in sections)
    if not has_mda:
        print("⚠️ MD&A not found in extracted sections. Appending raw text fallback.")
        sections.append(
            f"FULL TEXT CONTEXT (MD&A MISSING FROM EXTRACTION)\n{text[:150000]}"
        )

    joined = "\n\n".join(sections).strip()

    # Add a compact set of KPI keyword windows from the *full* stripped text.
    #
    # Many filings disclose operational/usage KPIs in tables or notes that are not
    # reliably captured by the Item-based section extraction above. We always reserve
    # space for these snippets so they do not get truncated away.
    try:
        kpi_windows = (_build_company_kpi_context(text, max_chars=40_000) or "").strip()
    except Exception:  # noqa: BLE001
        kpi_windows = ""

    if effective_limit and effective_limit > 0:
        if kpi_windows:
            kpi_block = f"\n\nKPI KEYWORD SNIPPETS\n{kpi_windows}"
            # Reserve space for KPI snippets at the end.
            reserve = min(len(kpi_block), max(0, effective_limit - 5_000))
            head_limit = max(0, effective_limit - reserve)
            joined = joined[:head_limit].rstrip()
            joined = f"{joined}{kpi_block}"
        if len(joined) > effective_limit:
            joined = joined[:effective_limit].rstrip()
        return joined

    if kpi_windows:
        joined = f"{joined}\n\nKPI KEYWORD SNIPPETS\n{kpi_windows}"
    return joined


def _infer_local_document_mime_type(path: Path) -> str:
    """Best-effort MIME type for the local filing document.

    Important: some flows cache PDFs to a `.html` path, so we sniff magic bytes.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(5)
        if head == b"%PDF-":
            return "application/pdf"
    except Exception:  # noqa: BLE001
        pass

    suffix = str(path.suffix or "").lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in (".htm", ".html"):
        return "text/html"
    if suffix in (".txt", ".text"):
        return "text/plain"
    return "application/octet-stream"


def _load_document_full_text_for_spotlight(path: Path) -> str:
    """Load a broader Spotlight text context than `_load_document_excerpt`.

    `_load_document_excerpt` extracts investor-relevant sections and can miss
    exhibit KPIs / tables located outside those sections. For Spotlight KPI
    extraction we want coverage across the whole filing.
    """
    try:
        max_chars_raw = (os.getenv("SPOTLIGHT_FULL_TEXT_MAX_CHARS") or "").strip()
        max_chars = int(max_chars_raw) if max_chars_raw else 2_000_000
    except ValueError:
        max_chars = 2_000_000
    max_chars = max(200_000, int(max_chars))

    mime = _infer_local_document_mime_type(path)
    # For PDFs, prefer the excerpt extractor (PDF->text can be slow and noisy).
    if mime == "application/pdf":
        try:
            return _load_document_excerpt(
                path,
                limit=min(max_chars, _spotlight_document_excerpt_limit()),
                max_pages=_summary_pdf_max_pages(),
            )
        except Exception:  # noqa: BLE001
            return ""

    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            raw = path.read_text(errors="ignore")
        except Exception:  # noqa: BLE001
            raw = ""

    text = raw
    if mime == "text/html":
        try:
            text = _strip_html_to_text(raw)
        except Exception:  # noqa: BLE001
            text = raw

    text = (text or "").strip()
    if not text:
        return ""

    # If the file is very large, keep head+middle+tail so we still cover the whole doc.
    if len(text) > max_chars:
        third = max_chars // 3
        head = text[:third]
        mid_start = max(0, (len(text) // 2) - (third // 2))
        mid = text[mid_start : mid_start + third]
        tail = text[-third:]
        return f"{head}\n\n--- MIDDLE ---\n\n{mid}\n\n--- END ---\n\n{tail}".strip()

    return text


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
    rnd_expense = _extract_from_candidates(
        income_statement,
        [
            "rnd_expense",
            "research_and_development",
            "ResearchAndDevelopment",
            "ResearchAndDevelopmentExpense",
            "ResearchDevelopment",
            "ResearchDevelopmentExpense",
            "RAndD",
            "rAndD",
            "RDExpense",
        ],
    )
    if rnd_expense is not None:
        rnd_expense = abs(rnd_expense)
    sga_expense = _extract_from_candidates(
        income_statement,
        [
            "sga_expense",
            "selling_general_admin",
            "SellingGeneralAndAdministrative",
            "SellingGeneralAndAdministrativeExpense",
            "SellingAndMarketingExpense",
            "sellingGeneralAdministrative",
            "SellingGeneralAdministrative",
            "sellingGeneralAndAdministrative",
            "SGA",
        ],
    )
    if sga_expense is not None:
        sga_expense = abs(sga_expense)
    short_term_debt = _extract_from_candidates(
        balance_sheet,
        [
            "short_term_debt",
            "shortTermDebt",
            "ShortTermDebt",
            "ShortTermBorrowings",
        ],
    )
    long_term_debt = _extract_from_candidates(
        balance_sheet,
        [
            "long_term_debt",
            "longTermDebt",
            "LongTermDebt",
            "LongTermDebtNoncurrent",
        ],
    )
    total_debt = _extract_from_candidates(
        balance_sheet,
        [
            "total_debt",
            "totalDebt",
            "TotalDebt",
            "TotalDebtUSD",
            "ShortLongTermDebtTotal",
            "shortLongTermDebtTotal",
        ],
    )
    if total_debt is None and (
        short_term_debt is not None or long_term_debt is not None
    ):
        total_debt = float(short_term_debt or 0) + float(long_term_debt or 0)

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
        "rnd_expense": rnd_expense,
        "sga_expense": sga_expense,
        "total_debt": total_debt,
    }

    return {key: value for key, value in metrics.items() if value is not None}


def _safe_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _load_prior_statements_for_summary(
    *,
    filing: Dict[str, Any],
    context_source: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Load the immediately prior comparable filing + its stored financial statements.

    Comparable means same filing_type (10-Q vs 10-Q, 10-K vs 10-K) and filing_date strictly earlier.
    Returns (prior_filing, prior_statements) or (None, None) if unavailable.
    """
    filing_type = str(filing.get("filing_type") or "").strip()
    filing_date = str(filing.get("filing_date") or "").strip()
    company_id = filing.get("company_id")
    if not filing_type or not filing_date or not company_id:
        return None, None

    filing_date_key = filing_date[:10]
    filing_date_dt = _safe_iso_date(filing_date_key)

    prior_filing: Optional[Dict[str, Any]] = None

    if context_source == "supabase":
        try:
            supabase = get_supabase_client()
            response = (
                supabase.table("filings")
                .select("id, company_id, filing_type, filing_date")
                .eq("company_id", company_id)
                .eq("filing_type", filing_type)
                .lt("filing_date", filing_date_key)
                .order("filing_date", desc=True)
                .limit(1)
                .execute()
            )
            if response.data:
                prior_filing = response.data[0]
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Unable to load prior filing for %s: %s", filing.get("id"), exc
            )

    if prior_filing is None:
        company_key = str(company_id)
        candidates: List[Dict[str, Any]] = []
        for candidate in fallback_filings.get(company_key, []):
            if str(candidate.get("filing_type") or "").strip() != filing_type:
                continue
            candidate_date = str(candidate.get("filing_date") or "").strip()[:10]
            if not candidate_date:
                continue
            candidate_dt = _safe_iso_date(candidate_date)
            if filing_date_dt and candidate_dt:
                if candidate_dt >= filing_date_dt:
                    continue
            else:
                if candidate_date >= filing_date_key:
                    continue
            candidates.append(candidate)
        candidates.sort(
            key=lambda item: str(item.get("filing_date") or ""), reverse=True
        )
        prior_filing = candidates[0] if candidates else None

    if not prior_filing:
        return None, None

    prior_id = str(prior_filing.get("id") or "")
    if not prior_id:
        return prior_filing, None

    prior_statements = fallback_financial_statements.get(prior_id)
    if prior_statements is None and context_source == "supabase":
        try:
            supabase = get_supabase_client()
            statement_response = (
                supabase.table("financial_statements")
                .select("*")
                .eq("filing_id", prior_id)
                .limit(1)
                .execute()
            )
            if statement_response.data:
                prior_statements = statement_response.data[0]
                fallback_financial_statements[prior_id] = prior_statements
        except Exception as exc:  # noqa: BLE001
            logger.debug("Unable to load prior statements for %s: %s", prior_id, exc)

    return prior_filing, prior_statements


def _format_percent(value: Optional[float], *, decimals: int = 1) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.{decimals}f}%"
    except Exception:
        return None


def _format_pp_delta(current: Optional[float], prior: Optional[float]) -> Optional[str]:
    if current is None or prior is None:
        return None
    try:
        return f"{(float(current) - float(prior)):+.1f}pp"
    except Exception:
        return None


def _format_money_delta(
    current: Optional[float], prior: Optional[float]
) -> Optional[str]:
    if current is None or prior is None:
        return None
    try:
        cur = float(current)
        prev = float(prior)
    except Exception:
        return None
    delta = cur - prev
    pct = None
    if prev != 0:
        pct = (delta / abs(prev)) * 100
    delta_str = _format_dollar(delta) or str(delta)
    if pct is None:
        return delta_str
    return f"{delta_str} ({pct:+.1f}%)"


def _build_prior_period_delta_reference_block(
    *,
    filing_type: str,
    current_period_end: Optional[str],
    prior_period_end: Optional[str],
    current_metrics: Dict[str, Any],
    prior_metrics: Dict[str, Any],
) -> str:
    """Build a deterministic, compact delta bridge numbers block for Q/Q or Y/Y comparisons.

    This block is intended to be copied into the memo (then annotated with drivers).
    It MUST be internally consistent: Prior → Current ordering and deltas computed from displayed figures.
    """
    if not prior_metrics:
        return ""

    is_quarterly = filing_type.strip().upper().startswith("10-Q")
    label = "Q/Q" if is_quarterly else "Y/Y"
    current_label = (current_period_end or "").strip() or "current period"
    prior_label = (prior_period_end or "").strip() or "prior period"

    def _cash_and_securities(metrics: Dict[str, Any]) -> Optional[float]:
        cash = metrics.get("cash")
        sec = metrics.get("marketable_securities")
        if cash is None and sec is None:
            return None
        try:
            return float(cash or 0) + float(sec or 0)
        except Exception:
            return None

    current_cns = _cash_and_securities(current_metrics)
    prior_cns = _cash_and_securities(prior_metrics)

    def _money_pair_line(
        name: str, prior_value: Optional[float], current_value: Optional[float]
    ) -> Optional[str]:
        if prior_value is None or current_value is None:
            return None
        try:
            prev = float(prior_value)
            cur = float(current_value)
        except Exception:
            return None

        max_abs = max(abs(prev), abs(cur))
        if max_abs >= 1_000_000_000:
            scale = 1_000_000_000.0
            unit = "B"
            decimals = 2
        elif max_abs >= 1_000_000:
            scale = 1_000_000.0
            unit = "M"
            decimals = 2
        else:
            scale = 1.0
            unit = ""
            decimals = 0

        prev_scaled = round(prev / scale, decimals)
        cur_scaled = round(cur / scale, decimals)
        delta_scaled = round(cur_scaled - prev_scaled, decimals)

        pct = None
        if prev_scaled != 0:
            pct = (delta_scaled / abs(prev_scaled)) * 100

        if unit:
            prev_fmt = f"${prev_scaled:.{decimals}f}{unit}"
            cur_fmt = f"${cur_scaled:.{decimals}f}{unit}"
            delta_fmt = f"${delta_scaled:+.{decimals}f}{unit}"
        else:
            prev_fmt = f"${prev_scaled:,.0f}"
            cur_fmt = f"${cur_scaled:,.0f}"
            delta_fmt = f"${delta_scaled:+,.0f}"

        pct_fmt = f"{pct:+.1f}%" if pct is not None else None
        pct_clause = f", {pct_fmt}" if pct_fmt else ""
        return f"- {name}: {prev_fmt} → {cur_fmt} (Δ {delta_fmt}{pct_clause})"

    def _margin_pair_line(
        name: str, prior_value: Optional[float], current_value: Optional[float]
    ) -> Optional[str]:
        if prior_value is None or current_value is None:
            return None
        try:
            prev = float(prior_value)
            cur = float(current_value)
        except Exception:
            return None
        prev_fmt = f"{prev:.1f}%"
        cur_fmt = f"{cur:.1f}%"
        delta_pp = cur - prev
        return f"- {name}: {prev_fmt} → {cur_fmt} (Δ {delta_pp:+.1f}pp)"

    # Six-line bridge only (per user requirement).
    # Priority: Revenue, Op margin, OCF, Capex, FCF, Cash+securities (fallback to Cash or Total liabilities).
    rows: List[str] = []
    rows.append(
        _money_pair_line(
            "Revenue",
            prior_metrics.get("revenue"),
            current_metrics.get("revenue"),
        )
        or ""
    )
    rows.append(
        _margin_pair_line(
            "Operating margin",
            prior_metrics.get("operating_margin"),
            current_metrics.get("operating_margin"),
        )
        or ""
    )
    rows.append(
        _money_pair_line(
            "Operating cash flow",
            prior_metrics.get("operating_cash_flow"),
            current_metrics.get("operating_cash_flow"),
        )
        or ""
    )
    rows.append(
        _money_pair_line(
            "Capex",
            prior_metrics.get("capital_expenditures"),
            current_metrics.get("capital_expenditures"),
        )
        or ""
    )
    rows.append(
        _money_pair_line(
            "Free cash flow",
            prior_metrics.get("free_cash_flow"),
            current_metrics.get("free_cash_flow"),
        )
        or ""
    )
    cash_line = _money_pair_line("Cash + securities", prior_cns, current_cns)
    if not cash_line:
        cash_line = _money_pair_line(
            "Cash",
            prior_metrics.get("cash"),
            current_metrics.get("cash"),
        )
    if not cash_line:
        cash_line = _money_pair_line(
            "Total liabilities",
            prior_metrics.get("total_liabilities"),
            current_metrics.get("total_liabilities"),
        )
    rows.append(cash_line or "")

    rows = [row for row in rows if row.strip()]
    # Only emit the bridge when we can provide a clean, 6-line set (no invention).
    if len(rows) < 6:
        return ""

    return (
        "\n\nQ/Q DELTA BRIDGE NUMBERS (COPY THESE EXACTLY; APPEND 1 DRIVER + 1 SO-WHAT PER LINE):\n"
        f"- Comparison basis: {label}\n"
        f"- Prior period end: {prior_label}\n"
        f"- Current period end: {current_label}\n" + "\n".join(rows) + "\n"
    )


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
    """Build the deterministic Key Metrics data appendix block.

    The frontend parses the DATA_GRID_START/END block into a dedicated UI. Keep the
    content numeric and scannable; omit missing metrics rather than rendering zeros.
    """

    def _get(key: str) -> Any:
        return calculated_metrics.get(key)

    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _fmt_money(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        formatted = _format_dollar(value)
        return formatted or f"{value:,.2f}"

    def _fmt_pct(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        try:
            return f"{float(value):.1f}%"
        except Exception:
            return None

    def _fmt_ratio(value: Optional[float], *, decimals: int = 1) -> Optional[str]:
        if value is None:
            return None
        try:
            return f"{float(value):.{decimals}f}x"
        except Exception:
            return None

    revenue = _to_float(_get("revenue") or _get("total_revenue"))
    operating_income = _to_float(_get("operating_income"))
    operating_margin = _to_float(_get("operating_margin"))
    net_income = _to_float(_get("net_income"))
    net_margin = _to_float(_get("net_margin"))
    operating_cash_flow = _to_float(_get("operating_cash_flow"))
    capital_expenditures = _to_float(_get("capital_expenditures"))
    free_cash_flow = _to_float(_get("free_cash_flow"))

    cash_raw = _to_float(_get("cash"))
    securities_raw = _to_float(_get("marketable_securities"))
    cash_and_securities = None
    if cash_raw is not None or securities_raw is not None:
        cash_and_securities = float(cash_raw or 0.0) + float(securities_raw or 0.0)

    total_debt = _to_float(_get("total_debt"))
    total_assets = _to_float(_get("total_assets"))
    total_liabilities = _to_float(_get("total_liabilities"))
    current_assets = _to_float(_get("current_assets"))
    current_liabilities = _to_float(_get("current_liabilities"))
    inventory = _to_float(_get("inventory"))
    interest_expense = _to_float(_get("interest_expense"))
    dividends_paid = _to_float(_get("dividends_paid"))

    equity = None
    if total_assets is not None and total_liabilities is not None:
        equity = total_assets - total_liabilities

    working_capital = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities

    current_ratio = None
    if current_assets is not None and current_liabilities and current_liabilities != 0:
        current_ratio = current_assets / current_liabilities

    quick_ratio = None
    if (
        current_assets is not None
        and current_liabilities
        and current_liabilities != 0
        and inventory is not None
    ):
        quick_ratio = (current_assets - inventory) / current_liabilities

    net_debt = None
    if total_debt is not None and cash_and_securities is not None:
        net_debt = total_debt - cash_and_securities

    capex_pct_revenue = None
    if capital_expenditures is not None and revenue and revenue != 0:
        capex_pct_revenue = abs(capital_expenditures) / revenue * 100

    ocf_margin = None
    if operating_cash_flow is not None and revenue and revenue != 0:
        ocf_margin = operating_cash_flow / revenue * 100

    fcf_margin = None
    if free_cash_flow is not None and revenue and revenue != 0:
        fcf_margin = free_cash_flow / revenue * 100

    fcf_conversion = None
    if free_cash_flow is not None and operating_cash_flow and operating_cash_flow != 0:
        fcf_conversion = free_cash_flow / operating_cash_flow * 100

    interest_coverage = None
    if operating_income is not None and interest_expense and interest_expense != 0:
        interest_coverage = operating_income / abs(interest_expense)

    leverage_ratio = None
    if total_liabilities is not None and total_assets and total_assets != 0:
        leverage_ratio = total_liabilities / total_assets

    debt_to_equity = None
    if total_debt is not None and equity and equity != 0:
        debt_to_equity = total_debt / equity

    roe = None
    if net_income is not None and equity and equity > 0:
        roe = net_income / equity * 100

    def _row(label: str, value: Optional[str], icon: str) -> Optional[str]:
        if value is None or not str(value).strip():
            return None
        # Add whitespace around pipes so both backend and frontend word-count/tokenizers
        # treat the grid as multiple tokens (and never as a single "Revenue|$X|📈" word).
        return f"{label} | {value} | {icon}"

    rows: List[str] = []
    rows.extend(
        [
            _row("Revenue", _fmt_money(revenue), "📈"),
            _row("Operating Income", _fmt_money(operating_income), "🏭"),
            _row("Operating Margin", _fmt_pct(operating_margin), "📐"),
            _row("Net Income", _fmt_money(net_income), "💰"),
            _row("Net Margin", _fmt_pct(net_margin), "📐"),
            _row("Operating Cash Flow", _fmt_money(operating_cash_flow), "🧾"),
            _row(
                "Capex",
                _fmt_money(
                    abs(capital_expenditures)
                    if capital_expenditures is not None
                    else None
                ),
                "🏗️",
            ),
            _row("Free Cash Flow", _fmt_money(free_cash_flow), "💸"),
            _row("OCF Margin", _fmt_pct(ocf_margin), "🧾"),
            _row("FCF Margin", _fmt_pct(fcf_margin), "💸"),
            _row("FCF / OCF", _fmt_pct(fcf_conversion), "💸"),
            _row("Capex % Revenue", _fmt_pct(capex_pct_revenue), "🏗️"),
            _row("Cash + Securities", _fmt_money(cash_and_securities), "🏦"),
            _row("Total Debt", _fmt_money(total_debt), "📉"),
            _row("Net Debt", _fmt_money(net_debt), "🧱"),
            _row("Current Assets", _fmt_money(current_assets), "📦"),
            _row("Current Liabilities", _fmt_money(current_liabilities), "📦"),
            _row("Working Capital", _fmt_money(working_capital), "🧮"),
            _row("Current Ratio", _fmt_ratio(current_ratio), "📌"),
            _row("Quick Ratio", _fmt_ratio(quick_ratio), "📌"),
            _row("Total Assets", _fmt_money(total_assets), "🗂️"),
            _row("Total Liabilities", _fmt_money(total_liabilities), "📊"),
            _row("Equity", _fmt_money(equity), "📚"),
            _row("Liabilities / Assets", _fmt_ratio(leverage_ratio, decimals=2), "📊"),
            _row(
                "Interest Expense",
                _fmt_money(
                    abs(interest_expense) if interest_expense is not None else None
                ),
                "🏦",
            ),
            _row("Interest Coverage", _fmt_ratio(interest_coverage), "📌"),
            _row("Debt / Equity", _fmt_ratio(debt_to_equity, decimals=2), "📌"),
            _row("ROE", _fmt_pct(roe), "💰"),
            _row(
                "Dividends Paid",
                _fmt_money(abs(dividends_paid) if dividends_paid is not None else None),
                "💵",
            ),
        ]
    )

    rows = [row for row in rows if row]
    if not rows:
        return ""

    table_lines = ["DATA_GRID_START", *rows, "DATA_GRID_END"]
    return "\n".join(table_lines).strip()


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
                clauses.append(
                    f"negative free cash flow ({fcf_str}) limits flexibility"
                )
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
            clauses.append(
                f"net cash ({cash_str} vs {liab_str}) adds balance-sheet cushion"
            )
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
        or narrative_text.count(".")
        + narrative_text.count("!")
        + narrative_text.count("?")
        < 2
        or not re.search(r"\d", narrative_text)
        or re.search(r"Health\s+Score\s+Drivers", narrative_text, re.IGNORECASE)
        # The model often repeats this stock phrase; rebuild so the section reads fresh.
        or re.search(
            r"Under\s+a\s+value[-\s]investor\s+lens", narrative_text, re.IGNORECASE
        )
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
    revenue = calculated_metrics.get("revenue") or calculated_metrics.get(
        "total_revenue"
    )
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
        margin_clause = (
            f" (FCF margin {fcf_margin:.1f}%)" if fcf_margin is not None else ""
        )
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
                leverage_clause = (
                    ", with liabilities more than 3x cash and higher refinancing risk"
                )
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
                current_assets / current_liabilities
                if current_liabilities != 0
                else None
            )
        except Exception:
            current_ratio = None
        if current_ratio is not None:
            sentences.append(
                f"A current ratio around {current_ratio:.1f}x suggests near-term obligations are manageable."
            )

    # Add a forward-looking, underwriting-style linkage so this section doesn't read like
    # a disconnected list of metrics.
    if (
        operating_margin is not None
        and net_margin is not None
        and abs(net_margin - operating_margin) >= 5
    ):
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
        if fcf_str:
            sentences.append(
                "Free cash flow remains meaningful in absolute terms; the watch item is whether conversion trends with capex intensity rather than drifting lower."
            )
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


def _build_company_kpi_context(document_text: str, *, max_chars: int = 80_000) -> str:
    if not document_text:
        return ""
    head = (document_text or "").lstrip()[:8000]
    if head.startswith("{") and '"statements"' in head and '"filing_id"' in head:
        return ""

    blocks: List[str] = []
    # Extract labeled sections - prioritize sections most likely to contain KPIs
    for label, limit in (
        # Primary KPI sections
        ("KEY PERFORMANCE INDICATORS", 30_000),
        ("KEY OPERATING METRICS", 30_000),
        ("KEY METRICS", 30_000),
        ("OPERATING METRICS", 30_000),
        ("SELECTED OPERATING DATA", 40_000),
        ("SUPPLEMENTAL OPERATING DATA", 40_000),
        # Segment and product information
        ("SEGMENT INFORMATION", 50_000),
        ("SEGMENT REPORTING", 50_000),
        ("PRODUCTS AND SERVICES", 40_000),
        ("REPORTABLE SEGMENTS", 50_000),
        # Core filing sections
        ("BUSINESS OVERVIEW", 25_000),
        ("RESULTS OF OPERATIONS", 60_000),
        ("MANAGEMENT DISCUSSION & ANALYSIS", 80_000),
        ("MANAGEMENT'S DISCUSSION AND ANALYSIS", 80_000),
        # Financial data
        ("SELECTED FINANCIAL DATA", 40_000),
        # Include statements as a last resort, but keep the slice smaller to avoid drowning
        # out the higher-signal operational/segment sections.
        ("FINANCIAL STATEMENTS", 60_000),
    ):
        excerpt = _extract_labeled_excerpt(document_text, label, max_chars=limit) or ""
        if excerpt.strip():
            blocks.append(excerpt.strip())

    keywords = [
        "segment",
        "segments",
        "product line",
        "product lines",
        "reportable segment",
        "subscriber",
        "subscribers",
        "subscription",
        "monthly active",
        "mau",
        "daily active",
        "dau",
        "mapc",
        "mapcs",
        "monthly active platform consumers",
        "active platform consumers",
        "platform consumers",
        "active users",
        "customers",
        "customer accounts",
        "accounts",
        "active buyers",
        "active sellers",
        "active merchants",
        "active hosts",
        "active listings",
        "listings",
        "paid members",
        "premium subscribers",
        "family daily active",
        "family of apps",
        "arpu",
        "arpa",
        "arppu",
        "arpm",
        "average revenue per user",
        "average revenue per account",
        "average revenue per customer",
        "average revenue per member",
        "net revenue retention",
        "ndr",
        "churn",
        "churn rate",
        "take rate",
        "dollar retention",
        "gmv",
        "gms",
        "gtv",
        "gross bookings",
        "bookings",
        "tpv",
        "payment volume",
        "payments volume",
        "cross border",
        "transactions processed",
        "transactions",
        "orders",
        "aum",
        "assets under management",
        "backlog",
        "order book",
        "order backlog",
        "net interest income",
        "net interest margin",
        "nim",
        "loan growth",
        "deposit growth",
        "credit card spend",
        "revpar",
        "adr",
        "average daily rate",
        "occupancy",
        "occupancy rate",
        "load factor",
        "revenue passenger mile",
        "available seat mile",
        "room nights",
        "nights booked",
        "arr",
        "mrr",
        "recurring revenue",
        "annual recurring",
        "monthly recurring",
        "cloud revenue",
        "advertising revenue",
        "ad revenue",
        "ad impressions",
        "paid clicks",
        "cost per click",
        "cost-per-click",
        "cpc",
        "cost per impression",
        "cost-per-impression",
        "cpm",
        "traffic acquisition cost",
        "tac",
        "same store",
        "same-store",
        "comparable sales",
        "comp sales",
        "sales per square foot",
        "store count",
        "locations",
        "stores",
        "restaurants",
        "outlets",
        "warehouses",
        "new store openings",
        "deliveries",
        "rides",
        "trips",
        "shipments",
        "packages delivered",
        "vehicle deliveries",
        "units delivered",
        "units",
        "production volume",
        "capacity utilization",
        "patient visits",
        "member lives",
        "medical care ratio",
        "claims processed",
        "prescriptions",
        "barrels",
        "boe",
        "mwh",
        "energy storage",
        "installed capacity",
        "proved reserves",
        "employees",
        "headcount",
        "key performance indicator",
        "key operating metric",
        "operating metric",
        "key metric",
    ]

    if keywords:
        pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
        # Increased window size to capture more context around keywords
        window_size = 2000
        # Search both the head and tail of long texts so we don't miss KPI disclosures
        # that appear later in MD&A or supplemental sections. Also include a middle
        # slice for long filings where KPI tables often sit mid-document.
        head = document_text[:300_000]
        haystack = head
        if len(document_text) > 380_000:
            tail = document_text[-300_000:]
            mid_start = max(0, (len(document_text) // 2) - 150_000)
            mid = document_text[mid_start : mid_start + 300_000]
            haystack = f"{head}\n\n--- MIDDLE ---\n\n{mid}\n\n--- END ---\n\n{tail}"
        seen: set[str] = set()
        for match in pattern.finditer(haystack):
            start = max(0, match.start() - window_size)
            end = min(len(haystack), match.end() + window_size)
            snippet = haystack[start:end].strip()
            if not snippet:
                continue
            key = re.sub(r"\s+", " ", snippet)[:240]
            if key in seen:
                continue
            seen.add(key)
            blocks.append(snippet)
            # Allow more keyword matches for richer context
            if len(blocks) >= 12:
                break

    joined = "\n\n---\n\n".join(blocks).strip()
    if not joined:
        return document_text[:max_chars] if document_text else ""
    if max_chars and len(joined) > max_chars:
        return joined[:max_chars]
    return joined


def _clean_company_name(name: str) -> str:
    """Remove common security type suffixes from company names.

    E.g., "Ouster, Inc. Common Stock" -> "Ouster, Inc."
    """
    if not name:
        return name
    # Strip trailing ticker / exchange descriptors like "(GOOG)" or "(NASDAQ: GOOG)".
    result = re.sub(
        r"\s*\((?:NYSE|NASDAQ|AMEX|OTC|TSX|LSE)\s*[:\-]?\s*[A-Z0-9.\-]{1,12}\)\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\s*\([A-Z0-9.\-]{1,12}\)\s*$", "", result, flags=re.IGNORECASE)
    # Patterns to remove (case-insensitive)
    suffixes_to_remove = [
        r"\s+Common\s+Stock\s*$",
        r"\s+Ordinary\s+Shares?\s*$",
        r"\s+Class\s+[A-Z]\s+Stock\s*$",
        r"\s+Class\s+[A-Z]\s+Shares?\s*$",
        r"\s+Class\s+[A-Z]\s+Common\s+Stock\s*$",
        r"\s+Class\s+[A-Z]\s*$",
        r"\s+Series\s+[A-Z]\s*$",
        r"\s+ADR\s*$",
        r"\s+ADS\s*$",
        r"\s+Depositary\s+Shares?\s*$",
        r"\s+Units?\s*$",
        r"\s+Warrant\s*$",
        r"\s+Warrants?\s*$",
    ]
    for pattern in suffixes_to_remove:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result.strip()


def _extract_any_metric_from_summary(
    summary_text: str, company_name: str
) -> Optional[Dict[str, Any]]:
    """Last-resort extraction: find a meaningful metric from the summary text.

    Priority order:
    1. Named segment/product/region revenue - BEST
    2. Revenue with YoY growth context - GOOD
    3. Total revenue as absolute last resort - OK

    NEVER returns: Net Income, Operating Income, Earnings, or other generic financials.
    """
    if not summary_text or not company_name:
        return None

    # Clean the company name (remove security type suffixes)
    clean_name = _clean_company_name(company_name)

    # TIER 1: Generic patterns to find ANY named segment/product/region revenue
    # These patterns dynamically capture segment names without hardcoding companies.
    # Format: "<SegmentName> revenue of $X billion" or "$X billion in <SegmentName> revenue"

    generic_segment_patterns = [
        # Pattern: "SegmentName revenue/sales of/was/reached $X billion/million"
        # Captures: (segment_name, value, multiplier)
        (
            r"([A-Z][A-Za-z0-9\s&\-]{2,30}?)\s+(?:revenue|sales)\s+(?:of\s+|was\s+|reached\s+|totaled\s+|grew\s+to\s+)?(?:[$€£])?([\d,.]+)\s*(billion|million|B|M)\b",
            lambda m: m.group(1).strip(),
        ),
        # Pattern: "$X billion/million in SegmentName revenue/sales"
        # Captures: (value, multiplier, segment_name)
        (
            r"(?:[$€£])([\d,.]+)\s*(billion|million|B|M)\s+(?:in\s+)?([A-Z][A-Za-z0-9\s&\-]{2,30}?)\s+(?:revenue|sales)",
            lambda m: m.group(3).strip(),
        ),
        # Pattern: "SegmentName generated/posted $X billion/million"
        (
            r"([A-Z][A-Za-z0-9\s&\-]{2,30}?)\s+(?:generated|posted|contributed|delivered)\s+(?:[$€£])?([\d,.]+)\s*(billion|million|B|M)",
            lambda m: m.group(1).strip(),
        ),
    ]

    # Words that indicate this is NOT a segment name (generic terms)
    generic_terms = {
        "total",
        "revenue",
        "sales",
        "the",
        "company",
        "quarter",
        "year",
        "annual",
        "quarterly",
        "fiscal",
        "period",
        "reported",
        "net",
        "gross",
        "operating",
        clean_name.lower().split()[0] if clean_name else "",  # Skip company name itself
    }

    for pattern, name_extractor in generic_segment_patterns:
        for match in re.finditer(pattern, summary_text, re.IGNORECASE):
            try:
                segment_name = name_extractor(match)
                # Skip if it's a generic term or too short
                if not segment_name or len(segment_name) < 3:
                    continue
                if segment_name.lower().split()[0] in generic_terms:
                    continue
                # Skip if segment name is just the company name
                if segment_name.lower() in clean_name.lower():
                    continue

                # Extract value based on pattern structure
                groups = match.groups()
                if (
                    groups[0]
                    and not groups[0].replace(",", "").replace(".", "").isdigit()
                ):
                    # First group is name, second is value
                    value_str = groups[1].replace(",", "")
                    multiplier_str = groups[2].lower() if len(groups) > 2 else ""
                else:
                    # First group is value
                    value_str = groups[0].replace(",", "")
                    multiplier_str = groups[1].lower() if len(groups) > 1 else ""

                value = float(value_str)
                if multiplier_str in ("billion", "b"):
                    value *= 1_000_000_000
                elif multiplier_str in ("million", "m"):
                    value *= 1_000_000

                metric_name = f"{segment_name} Revenue"
                unit = _infer_currency_unit(match.group(0)) or "$"

                return {
                    "name": metric_name,
                    "value": value,
                    "prior_value": None,
                    "unit": unit,
                    "description": f"{metric_name} for {clean_name}",
                    "chart_type": "bar",
                    "company_specific": True,
                    "source_quote": match.group(0)[:200],
                }
            except (ValueError, IndexError, AttributeError):
                continue

    # TIER 2: Look for revenue with YoY growth context (still useful)
    revenue_growth_patterns = [
        (
            r"revenue\s+(?:of\s+)?(?:[$€£])?([\d,.]+)\s*(billion|million|B|M)[^.]*(?:up|grew|increased|growth)\s+(?:by\s+)?([\d.]+)\s*%",
            "Revenue",
        ),
        (
            r"(?:[$€£])([\d,.]+)\s*(billion|million|B|M)\s+(?:in\s+)?revenue[^.]*(?:up|grew|increased|growth)\s+(?:by\s+)?([\d.]+)\s*%",
            "Revenue",
        ),
    ]

    for pattern, metric_name in revenue_growth_patterns:
        match = re.search(pattern, summary_text, re.IGNORECASE)
        if match:
            value_str = match.group(1).replace(",", "")
            multiplier_str = match.group(2).lower()
            growth_pct = match.group(3)

            try:
                value = float(value_str)
                if multiplier_str in ("billion", "b"):
                    value *= 1_000_000_000
                elif multiplier_str in ("million", "m"):
                    value *= 1_000_000

                unit = _infer_currency_unit(match.group(0)) or "$"
                # Include growth in name for context
                display_name = f"{clean_name} Revenue (+{growth_pct}% YoY)"
                return {
                    "name": display_name,
                    "value": value,
                    "prior_value": None,
                    "unit": unit,
                    "description": f"Revenue with year-over-year growth for {clean_name}",
                    "chart_type": "bar",
                    "company_specific": False,
                }
            except (ValueError, IndexError):
                continue

    # TIER 3: Plain revenue as absolute last resort
    # Prioritize "revenue" over other generic terms
    plain_revenue_patterns = [
        (
            r"(?:total\s+)?revenue\s+(?:of\s+|was\s+|reached\s+)?(?:[$€£])?([\d,.]+)\s*(billion|million|B|M)",
            "Revenue",
        ),
        (
            r"(?:[$€£])([\d,.]+)\s*(billion|million|B|M)\s+(?:in\s+)?(?:total\s+)?revenue",
            "Revenue",
        ),
    ]

    for pattern, metric_name in plain_revenue_patterns:
        match = re.search(pattern, summary_text, re.IGNORECASE)
        if match:
            value_str = match.group(1).replace(",", "")
            multiplier_str = match.group(2).lower()

            try:
                value = float(value_str)
                if multiplier_str in ("billion", "b"):
                    value *= 1_000_000_000
                elif multiplier_str in ("million", "m"):
                    value *= 1_000_000

                unit = _infer_currency_unit(match.group(0)) or "$"
                return {
                    "name": f"{clean_name} Revenue",
                    "value": value,
                    "prior_value": None,
                    "unit": unit,
                    "description": f"Revenue reported by {clean_name}",
                    "chart_type": "bar",
                    "company_specific": False,
                }
            except ValueError:
                continue

    # NO TIER 4 - We deliberately do NOT fall back to Net Income, Operating Income, etc.
    # Those are generic financial metrics that don't belong in Company Spotlight.
    # If we can't find revenue, return None and let the UI show empty state.

    return None


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


def _looks_like_sec_cover_doc(
    text_head: str, *, file_size: Optional[int] = None
) -> bool:
    """Detect short SEC primary docs that are effectively cover pages.

    For some forms (notably 6-K and many 8-Ks), the `primaryDocument` is a short
    boilerplate cover sheet while the exhibit HTML (press release / results) contains
    the operational KPI disclosures we want for Spotlight.
    """
    if not text_head:
        return False
    size = int(file_size) if isinstance(file_size, int) else None
    # Cover docs are typically small; avoid touching large filings (10-Q/10-K).
    if size is not None and size > 80_000:
        return False

    upper = text_head.upper()
    if "INDICATE BY CHECK MARK" not in upper:
        return False
    if "FORM 6-K" not in upper and "FORM 8-K" not in upper:
        return False

    # If it already contains a likely operational KPI keyword, treat it as content.
    if re.search(
        r"\b("
        r"NET\s+BOOKINGS|BOOKINGS|BACKLOG|RPO|REMAINING\s+PERFORMANCE|"
        r"SUBSCRIB|MAU|DAU|USERS?|CUSTOMERS?|ACCOUNTS?|SHIPMENTS?|DELIVERIES?|"
        r"ORDERS?|TRANSACTIONS?|GMV|TPV|AUM|PAID\s+CLICKS|IMPRESSIONS"
        r")\b",
        upper,
        re.IGNORECASE,
    ):
        return False

    return True


def _looks_like_ixbrl_noise_document(text_head: str, *, file_size: Optional[int] = None) -> bool:
    """Heuristic: detect cached iXBRL-heavy SEC HTML that is mostly taxonomy noise."""
    if not text_head:
        return False

    head = (text_head or "")[:120_000]
    lower = head.lower()

    # Only consider it iXBRL-noisy if iXBRL markers appear early.
    if ("<ix:" not in lower) and ("xmlns:ix" not in lower) and ("ixt:" not in lower):
        return False

    # Avoid re-downloading very small docs (likely cover docs handled elsewhere).
    if isinstance(file_size, int) and file_size < 25_000:
        return False

    us_gaap = lower.count("us-gaap") + lower.count("ifrs-full") + lower.count("dei:")
    contextref = lower.count("contextref=")
    unitref = lower.count("unitref=")
    ix_tags = lower.count("<ix:") + lower.count("<ix ") + lower.count("xmlns:ix")
    tokens = us_gaap + contextref + unitref + ix_tags

    alpha = sum(1 for ch in lower if ch.isalpha())
    digit = sum(1 for ch in lower if ch.isdigit())

    if ix_tags >= 20 and tokens >= 140 and alpha < 80_000 and digit >= 400:
        return True
    if tokens >= 450 and alpha < 120_000:
        return True
    return False


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


def _persist_company_field_updates(
    context: Dict[str, Any], company_id: str, updates: Dict[str, Any]
) -> None:
    """Best-effort persistence for Supabase-backed companies."""
    if context.get("source") != "supabase":
        # Fallback cache path
        if company_id and company_id in fallback_companies and updates:
            fallback_companies[company_id].update(updates)
            try:
                save_fallback_companies()
            except Exception:  # noqa: BLE001
                pass
        return
    if not company_id or not updates:
        return
    try:
        supabase = get_supabase_client()
        supabase.table("companies").update(updates).eq("id", company_id).execute()
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            return
        logger.debug("Unable to persist company updates for %s: %s", company_id, exc)


def _normalize_cik_value(cik: Any) -> Optional[str]:
    digits = "".join(ch for ch in str(cik or "") if ch.isdigit())
    return digits.zfill(10) if digits else None


def _parse_iso_date(value: Any) -> Optional[date]:
    try:
        raw = str(value or "")[:10].strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


_SEC_PERIOD_OF_REPORT_PATTERN = re.compile(
    r"(?:CONFORMED\s+PERIOD\s+OF\s+REPORT|PERIOD\s+OF\s+REPORT)\s*[:=]\s*(\d{8})",
    re.IGNORECASE,
)

_AS_OF_DATE_YEAR_PATTERN = re.compile(
    r"\b(?:as\s+of|as\s+at)\s+[A-Za-z]{3,9}\s+\d{1,2},?\s+(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)


def _extract_sec_period_of_report(text: str) -> Optional[date]:
    """Best-effort parse of SEC header "period of report" from filing content."""
    if not text:
        return None
    match = _SEC_PERIOD_OF_REPORT_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1)
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except Exception:
        return None


def _extract_as_of_year(text: str) -> Optional[int]:
    """Best-effort extraction of an "As of Month Day, YYYY" year hint from a filing."""
    if not text:
        return None
    head = (text or "")[:120_000]
    match = _AS_OF_DATE_YEAR_PATTERN.search(head)
    if not match:
        return None
    try:
        year = int(match.group(1))
    except Exception:
        return None
    if year < 1900 or year > 2100:
        return None
    return year


def _pick_best_sec_filing_match(
    filings: List[Dict[str, Any]],
    *,
    target_date: Any,
    max_diff_days: int = 180,
) -> Optional[Dict[str, Any]]:
    """Pick the SEC filing record closest to `target_date`.

    Avoid defaulting to the newest filing when we cannot match; that can lead to
    "placeholder" KPIs that are actually sourced from a different (newer) period.
    """
    target_dt = _parse_iso_date(target_date)
    if not target_dt:
        return None

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for cand in filings or []:
        if not isinstance(cand, dict):
            continue
        # Use whichever date(s) exist on the SEC record. Prefer report/period end,
        # but consider filing date as a fallback.
        cand_dates = [
            _parse_iso_date(cand.get("period_end")),
            _parse_iso_date(cand.get("report_date")),
            _parse_iso_date(cand.get("filing_date")),
        ]
        cand_dates = [d for d in cand_dates if d is not None]
        if not cand_dates:
            continue
        diff = min(abs((d - target_dt).days) for d in cand_dates)
        scored.append((int(diff), cand))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    best_diff, best = scored[0]
    if int(best_diff) > int(max_diff_days):
        return None
    return best


def _looks_like_financial_statements_json(text_head: str) -> bool:
    """Detect our cached financial statements JSON blob.

    Some environments store a derived JSON document (with `"statements"`) as the
    filing's `local_document_path`. That blob is useful for calculated metrics but
    contains no filing narrative, so Spotlight KPI extraction will always fail.
    """
    head = (text_head or "").lstrip()[:9000]
    if not head.startswith("{"):
        return False
    lower = head.lower()
    return ('"statements"' in lower) and ('"filing_id"' in lower)


def _ensure_local_document(
    context: Dict[str, Any],
    settings,
    *,
    allow_network: bool = True,
) -> Optional[Path]:
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
            file_size: Optional[int] = None
            try:
                file_size = int(path_obj.stat().st_size)
            except Exception:
                file_size = None
            if _looks_like_cloud_run_console_page(head):
                if not allow_network:
                    # Spotlight calls this with allow_network=False and must not mutate
                    # persisted paths (nor attempt network recovery).
                    return None
                logger.warning(
                    "Cached filing document for %s looks like a Cloud Console page; ignoring %s",
                    filing_id_str or "<unknown>",
                    path_obj,
                )
                filing.pop("local_document_path", None)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": None}
                )
            elif _looks_like_financial_statements_json(head):
                if not allow_network:
                    return None
                # Spotlight requires the real filing narrative / exhibits, not the
                # derived statements JSON. Clear the cached path so we can resolve
                # and download the underlying SEC filing text.
                logger.info(
                    "Cached filing document for %s is statements JSON; re-resolving for Spotlight",
                    filing_id_str or "<unknown>",
                )
                filing.pop("local_document_path", None)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": None}
                )
            elif _looks_like_sec_cover_doc(head, file_size=file_size):
                if not allow_network:
                    return None
                # Upgrade in place: re-download from the SEC URL so our downloader can
                # select the best exhibit HTML (press release/results) for Spotlight.
                cached_url = filing.get("source_doc_url")
                if cached_url and _is_sec_document_url(str(cached_url)):
                    try:
                        if download_filing(str(cached_url), str(path_obj)):
                            upgraded_head = _read_text_head(path_obj)
                            try:
                                upgraded_size = int(path_obj.stat().st_size)
                            except Exception:
                                upgraded_size = None
                            if not _looks_like_sec_cover_doc(
                                upgraded_head, file_size=upgraded_size
                            ):
                                return path_obj
                    except Exception:  # noqa: BLE001
                        pass
                # If we can't upgrade, clear so we can re-resolve the SEC document URL.
                filing.pop("local_document_path", None)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": None}
                )
            elif _looks_like_ixbrl_noise_document(head, file_size=file_size):
                if not allow_network:
                    return None
                # Inline XBRL noise can dominate cached HTML, especially for older filings.
                # Re-download so our EDGAR fetcher can pick a better artifact (often .txt).
                cached_url = filing.get("source_doc_url")
                if cached_url and _is_sec_document_url(str(cached_url)):
                    try:
                        if download_filing(str(cached_url), str(path_obj)):
                            upgraded_head = _read_text_head(path_obj)
                            try:
                                upgraded_size = int(path_obj.stat().st_size)
                            except Exception:
                                upgraded_size = None
                            if not _looks_like_ixbrl_noise_document(
                                upgraded_head, file_size=upgraded_size
                            ):
                                return path_obj
                    except Exception:  # noqa: BLE001
                        pass

                filing.pop("local_document_path", None)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": None}
                )
            else:
                # Guard against stale/mismatched cached documents (common source of
                # "placeholder KPIs" where an older filing ID points at a newer document).
                expected_date = _parse_iso_date(
                    filing.get("period_end")
                    or filing.get("report_date")
                    or filing.get("filing_date")
                )
                doc_period = _extract_sec_period_of_report(head)
                if (
                    expected_date
                    and doc_period
                    and abs((doc_period - expected_date).days) > 370
                ):
                    if not allow_network:
                        return None
                    logger.warning(
                        "Cached filing document for %s appears to be a different period (expected=%s doc=%s); re-resolving",
                        filing_id_str or "<unknown>",
                        expected_date,
                        doc_period,
                    )
                    filing.pop("local_document_path", None)
                    filing.pop("source_doc_url", None)
                    _persist_filing_field_updates(
                        context,
                        filing_id_str,
                        {"local_document_path": None, "source_doc_url": None},
                    )
                elif expected_date and not doc_period:
                    # Some cached artifacts (exhibits/HTML) omit the SEC header, but we can
                    # still detect gross mismatches when an "as of" year is far from the
                    # filing's expected period (e.g., 2016 filing pointing to 2024 doc).
                    as_of_year = _extract_as_of_year(head)
                    if as_of_year and abs(int(as_of_year) - int(expected_date.year)) > 3:
                        if not allow_network:
                            return None
                        logger.warning(
                            "Cached filing document for %s appears to be a different period (expected_year=%s as_of_year=%s); re-resolving",
                            filing_id_str or "<unknown>",
                            expected_date.year,
                            as_of_year,
                        )
                        filing.pop("local_document_path", None)
                        filing.pop("source_doc_url", None)
                        _persist_filing_field_updates(
                            context,
                            filing_id_str,
                            {"local_document_path": None, "source_doc_url": None},
                        )
                    else:
                        return path_obj
                else:
                    return path_obj
        else:
            if not allow_network:
                # Spotlight calls this with allow_network=False and must not mutate
                # persisted paths (nor attempt network recovery).
                return None
            filing.pop("local_document_path", None)
            _persist_filing_field_updates(
                context, filing_id_str, {"local_document_path": None}
            )
    elif not allow_network:
        # Spotlight should be fast and must not trigger SEC resolution + downloads.
        return None

    # If the filing has a stored raw file in Supabase Storage (user-uploaded PDFs or
    # ingestion artifacts), download it as the local document for Spotlight.
    raw_file_path = filing.get("raw_file_path")
    if (
        allow_network
        and raw_file_path
        and context.get("source") == "supabase"
        and filing_id_str
    ):
        try:
            raw_str = str(raw_file_path).strip()
        except Exception:
            raw_str = ""

        if raw_str:
            # If `raw_file_path` is already a local path, use it directly.
            try:
                raw_local = Path(raw_str)
            except Exception:
                raw_local = None

            if raw_local is not None and raw_local.exists():
                filing["local_document_path"] = str(raw_local)
                _persist_filing_field_updates(
                    context, filing_id_str, {"local_document_path": str(raw_local)}
                )
                return raw_local

            # Avoid expensive storage calls for placeholder keys (e.g., `eodhd_*`).
            if "/" not in raw_str and "." not in raw_str:
                raw_str = ""

            if raw_str:
                try:
                    supabase = get_supabase_client()
                    blob = supabase.storage.from_("filings").download(raw_str)
                    if blob:
                        target_path = _build_local_document_path(
                            storage_dir, filing_id_str
                        )
                        try:
                            target_path.write_bytes(blob)
                            filing["local_document_path"] = str(target_path)
                            _persist_filing_field_updates(
                                context,
                                filing_id_str,
                                {"local_document_path": str(target_path)},
                            )
                            return target_path
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass

    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")
    period_end = filing.get("period_end") or filing.get("report_date")
    sec_max_results_raw = (os.getenv("SPOTLIGHT_SEC_MAX_RESULTS") or "").strip() or "1500"
    try:
        sec_max_results = int(sec_max_results_raw)
    except ValueError:
        sec_max_results = 1500
    sec_max_results = max(200, min(5000, int(sec_max_results)))

    def _infer_sec_filing_types() -> List[str]:
        """Infer SEC form types when our filing record isn't itself an SEC form.

        Many non-US / non-Supabase sources store filings with generic labels like
        "quarterly"/"annual". For Spotlight KPIs we still want to fetch *real* SEC
        filing text when the company has a CIK (e.g., ADRs like ASML).
        """
        ft = (filing.get("filing_type") or "").strip()
        ft_upper = ft.upper()

        # If it already looks like a SEC form code, use it directly.
        if re.fullmatch(
            r"(10-Q|10-K|8-K|20-F|6-K|40-F|10-Q/A|10-K/A|20-F/A|6-K/A)", ft_upper
        ):
            return [ft_upper]

        ft_lower = ft.lower()
        inferred_period: Optional[str] = None
        if any(
            token in ft_lower
            for token in ("quarter", "quarterly", "qtr", "interim", "q")
        ):
            inferred_period = "quarterly"
        elif any(token in ft_lower for token in ("annual", "year", "yearly", "fy")):
            inferred_period = "annual"

        # If we still can't infer from filing_type, use period_end month heuristics.
        expected_date = _parse_iso_date(
            filing.get("period_end")
            or filing.get("report_date")
            or filing.get("filing_date")
        )
        if inferred_period is None and expected_date:
            if expected_date.month in (3, 6, 9):
                inferred_period = "quarterly"
            elif expected_date.month in (12, 1):
                inferred_period = "annual"

        country = normalize_country(company.get("country")) if company else None
        is_us = bool(country == "US")

        if inferred_period == "quarterly":
            # US issuers: 10-Q. Foreign private issuers: often 6-K.
            return ["10-Q", "6-K"] if is_us else ["6-K", "10-Q"]
        if inferred_period == "annual":
            # US issuers: 10-K. Foreign private issuers: often 20-F.
            return ["10-K", "20-F"] if is_us else ["20-F", "10-K"]

        # Default: try a small set of common forms (bounded).
        return (
            ["10-Q", "10-K", "6-K", "20-F"]
            if is_us
            else ["6-K", "20-F", "10-Q", "10-K"]
        )

    source_doc_url = filing.get("source_doc_url")
    if not source_doc_url:
        # Some legacy rows store the SEC URL in `url` instead of `source_doc_url`.
        candidate_url = filing.get("url")
        if candidate_url and _is_sec_document_url(str(candidate_url)):
            source_doc_url = str(candidate_url)
            filing["source_doc_url"] = source_doc_url
            _persist_filing_field_updates(
                context, filing_id_str, {"source_doc_url": source_doc_url}
            )
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
        if not allow_network:
            return None
        # Avoid network lookups during unit tests.
        if os.getenv("PYTEST_CURRENT_TEST"):
            return None

        company_id = str(company.get("id") or "") if company else ""
        cik_value = _normalize_cik_value(company.get("cik") if company else None)
        if (not cik_value) and company and company.get("ticker"):
            cik_value = resolve_cik_from_ticker_sync(company.get("ticker"))
            if cik_value:
                company["cik"] = cik_value
                if company_id:
                    _persist_company_field_updates(
                        context, company_id, {"cik": cik_value}
                    )

        filing_types_to_try = _infer_sec_filing_types()
        if cik_value and filing_types_to_try and (filing_date or period_end):
            target = filing_date or period_end
            try:
                sec_filings = get_company_filings(
                    cik=cik_value,
                    filing_types=filing_types_to_try,
                    max_results=sec_max_results,
                    target_date=str(target) if target else None,
                    include_historical=False,
                )
                if sec_filings:
                    candidates = [
                        c
                        for c in sec_filings
                        if c.get("filing_type") in filing_types_to_try
                    ]
                    matched = _pick_best_sec_filing_match(
                        candidates, target_date=target
                    )

                    if matched:
                        source_doc_url = matched.get("url")
                        filing["source_doc_url"] = source_doc_url
                        if source_doc_url:
                            _persist_filing_field_updates(
                                context,
                                filing_id_str,
                                {"source_doc_url": source_doc_url},
                            )
            except Exception as sec_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to resolve SEC document for filing %s: %s",
                    filing_id_str,
                    sec_exc,
                )
    else:
        # If a source_doc_url is already set, still sanity-check that it matches the
        # requested filing period. Some pipelines mistakenly copy the latest SEC URL
        # onto older filing rows; correcting here avoids repeated placeholder KPIs.
        if not os.getenv("PYTEST_CURRENT_TEST"):
            cik_value = _normalize_cik_value(company.get("cik") if company else None)
            if (not cik_value) and company and company.get("ticker"):
                cik_value = resolve_cik_from_ticker_sync(company.get("ticker"))
            filing_types_to_try = _infer_sec_filing_types()
            if cik_value and filing_types_to_try and (filing_date or period_end):
                try:
                    target = filing_date or period_end
                    sec_filings = get_company_filings(
                        cik=cik_value,
                        filing_types=filing_types_to_try,
                        max_results=sec_max_results,
                        target_date=str(target) if target else None,
                        include_historical=False,
                    )
                    if sec_filings:
                        candidates = [
                            c
                            for c in sec_filings
                            if c.get("filing_type") in filing_types_to_try
                        ]
                        matched = _pick_best_sec_filing_match(
                            candidates, target_date=target
                        )
                        matched_url = (matched or {}).get("url")
                        if matched_url and matched_url != source_doc_url:
                            logger.info(
                                "Updating mismatched SEC document URL for filing %s",
                                filing_id_str or "<unknown>",
                            )
                            source_doc_url = matched_url
                            filing["source_doc_url"] = source_doc_url
                            _persist_filing_field_updates(
                                context,
                                filing_id_str,
                                {"source_doc_url": source_doc_url},
                            )
                except Exception as sec_exc:  # noqa: BLE001
                    logger.warning(
                        "Unable to sanity-check SEC document for filing %s: %s",
                        filing_id_str,
                        sec_exc,
                    )

    if not source_doc_url:
        return None

    target_path = _build_local_document_path(storage_dir, filing_id_str)

    if not allow_network:
        return None

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

    # Reset progress - Initialize time-based progress tracking
    start_summary_progress(filing_id, expected_total_seconds=120)
    set_summary_progress(filing_id, status="Initializing AI Agent...", stage_percent=5)

    usage_status = get_summary_usage_status(user.id)
    if usage_status.remaining <= 0:
        if usage_status.plan == "pro":
            reset_date = (
                usage_status.period_end.date().isoformat()
                if usage_status.period_end
                else "the next cycle"
            )
            detail = (
                f"Monthly summary limit reached (100/month). "
                f"Your limit resets on {reset_date}."
            )
        else:
            detail = (
                "Free trial summary already used. "
                "Upgrade to Pro to continue generating summaries."
            )
        set_summary_progress(
            filing_id, status="Monthly summary limit reached.", stage_percent=0
        )
        raise HTTPException(status_code=402, detail=detail)

    # Check cache first
    if (
        use_default_cache and False
    ):  # Cache disabled to force regeneration with new prompts
        cached_summary = fallback_filing_summaries.get(str(filing_id))
        if cached_summary:
            complete_summary_progress(filing_id)
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
        set_summary_progress(
            filing_id, status="Reading Filing Content...", stage_percent=15
        )
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
    set_summary_progress(
        filing_id, status="Extracting Financial Data...", stage_percent=30
    )
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
            document_text = _load_document_excerpt(
                local_document,
                limit=_summary_document_excerpt_limit(),
                max_pages=_summary_pdf_max_pages(),
            )
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
                        "company_id": str(company.get("id"))
                        if company.get("id")
                        else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Unable to set Gemini usage context: %s", exc)

        filing_type = filing.get("filing_type", "")
        filing_date = filing.get("filing_date", "")
        company_name = company.get("name", company.get("ticker", "Unknown"))

        financial_snapshot = _build_financial_snapshot(statements)
        calculated_metrics = _build_calculated_metrics(statements)

        prior_filing, prior_statements = _load_prior_statements_for_summary(
            filing=filing, context_source=str(context.get("source") or "")
        )
        prior_metrics: Dict[str, Any] = (
            _build_calculated_metrics(prior_statements) if prior_statements else {}
        )
        prior_period_delta_block = _build_prior_period_delta_reference_block(
            filing_type=str(filing_type or ""),
            current_period_end=str((statements or {}).get("period_end") or ""),
            prior_period_end=str((prior_statements or {}).get("period_end") or ""),
            current_metrics=calculated_metrics,
            prior_metrics=prior_metrics,
        )

        # Extract user's weighting preference from health_rating settings
        weighting_preset = None
        if preferences and preferences.health_rating:
            weighting_preset = preferences.health_rating.primary_factor_weighting

        # Optional AI growth assessment (disabled by default for speed)
        ai_growth_assessment = None
        if settings.enable_growth_assessment:
            try:
                set_summary_progress(
                    filing_id, status="Analyzing Growth Potential...", stage_percent=50
                )
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

        set_summary_progress(
            filing_id, status="Analyzing Risk Factors...", stage_percent=65
        )
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
        company_kpi_context = _build_company_kpi_context(
            document_text, max_chars=40_000
        )
        spotlight_block = ""
        if company_kpi_context and company_kpi_context not in context_excerpt:
            spotlight_block = (
                "\nCOMPANY SPOTLIGHT CONTEXT (use ONLY for the company-specific KPI lines):\n"
                f"{company_kpi_context}\n"
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
            set_summary_progress(
                filing_id, status="Computing Health Score...", stage_percent=75
            )

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

        # Section budgets (body words, headings excluded) are used as soft guidance.
        # Do NOT treat them as strict quotas; avoid padding and repetition to "hit" a number.
        section_budgets: Dict[str, int] = {}
        if target_length and target_length > 0:
            section_budgets = _calculate_section_word_budgets(
                int(target_length), include_health_rating=include_health_rating
            )

        health_budget = int(section_budgets.get("Financial Health Rating", 0) or 0)
        exec_budget = int(section_budgets.get("Executive Summary", 0) or 0)
        perf_budget = int(section_budgets.get("Financial Performance", 0) or 0)
        mdna_budget = int(
            section_budgets.get("Management Discussion & Analysis", 0) or 0
        )
        risk_budget = int(section_budgets.get("Risk Factors", 0) or 0)
        key_metrics_budget = int(section_budgets.get("Key Metrics", 0) or 0)
        closing_budget = int(section_budgets.get("Closing Takeaway", 0) or 0)

        def _budget_sentence(section: str, budget: int) -> str:
            if budget <= 0:
                return ""
            return (
                f"SUGGESTED LENGTH: up to ~{budget} words for this section body. "
                "It is acceptable to be shorter if additional words would be repetitive."
            )

        health_budget_sentence = _budget_sentence(
            "Financial Health Rating", health_budget
        )
        exec_budget_sentence = _budget_sentence("Executive Summary", exec_budget)
        perf_budget_sentence = _budget_sentence("Financial Performance", perf_budget)
        mdna_budget_sentence = _budget_sentence(
            "Management Discussion & Analysis", mdna_budget
        )
        risk_budget_sentence = _budget_sentence("Risk Factors", risk_budget)
        key_metrics_budget_sentence = _budget_sentence(
            "Key Metrics", key_metrics_budget
        )
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
                    "HIERARCHY (MANDATORY): Present decisions, not a narrated framework.\n"
                    "- One PRIMARY constraint dominates the memo (pick exactly one: margin structure, cash conversion quality, leverage/liquidity, or reinvestment intensity).\n"
                    "- Two to three SECONDARY risks are acknowledged.\n"
                    "- Everything else is subordinate or omitted.\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    "- Write 2 cohesive paragraphs (2-4 sentences each).\n"
                    "- Keep paragraphs short and breathable (aim for ≤4 sentences; if you need 'and also', split).\n"
                    "- Avoid one-sentence paragraphs and staccato one-liners.\n"
                    "- End with a sentence that sets up the Financial Performance section.\n\n"
                    "VOICE DISCIPLINE (MANDATORY):\n"
                    "- Do NOT describe your process ('this analysis/memo will...', 'I looked at...', 'the framework is...'). State conclusions; let structure imply process.\n"
                    "- Conviction tone MUST match the action: if the stance is HOLD/WAIT, explicitly name the blocking factor and the trigger that clears it.\n\n"
                    "CHANGE FOCUS (MANDATORY): State the single most important thing that changed versus the immediately prior comparable period (QoQ for 10-Q, YoY for 10-K) and why it matters.\n\n"
                    "NUMBERS DISCIPLINE (MANDATORY):\n"
                    "- Keep this section mostly qualitative. Use at most 1-2 anchor figures total.\n"
                    "- Do NOT stack multiple metrics in a single sentence; save density for Financial Performance / Key Metrics.\n\n"
                    "CONTENT (MANDATORY):\n"
                    "1) Stance + conviction (Bullish/Neutral/Bearish; High/Medium/Low).\n"
                    "2) Primary constraint (one sentence, with one anchor metric if relevant).\n"
                    "3) Two to three secondary risks (one sentence each; no re-explaining prior points).\n"
                    "4) What changes the stance (1-2 concrete triggers; measurable; time-bound).\n\n"
                    "CRITICAL: End with a clear, actionable stance. Do NOT use vague process phrases like 'I want to see...' or 'I need to determine...'.",
                ),
                (
                    "Financial Performance",
                    f"{perf_budget_sentence or 'Quantitative analysis (100-135 words).'}\n"
                    "Cover KEY metrics:\n"
                    "- Revenue with YoY% change and mix shifts (e.g., Mobility vs Delivery)\n"
                    "- Margin bridge: revenue growth → operating margin → net margin gap (explain drivers)\n"
                    "- Cash conversion: OCF to FCF, QoQ changes, capex context\n\n"
                    "COMPANY-SPOTLIGHT SIGNAL (MANDATORY): Include 1–2 *company-specific* operational lines pulled from the filing context (NOT generic finance). Prefer one of:\n"
                    "- Segment/product revenue lines (best): write them literally as '<Segment/Product> revenue was $X.XB' (or 'revenues were') for at least TWO segments when available.\n"
                    "- Business-model KPI lines: subscribers, MAU/DAU, ARPU, NDR, take rate, GMV/TPV, AUM, backlog, RevPAR, occupancy.\n"
                    "CRITICAL: Use ONLY numbers that appear in the CONTEXT or COMPANY SPOTLIGHT CONTEXT excerpts; do not invent or estimate.\n\n"
                    "Q/Q (OR Y/Y) DELTA BRIDGE (MANDATORY): Start this section with EXACTLY 6 short lines. Each line MUST start with '- ' and follow THIS exact pattern:\n"
                    "- Metric: Prior → Current (Δ ...) — Why it changed — Why it matters\n"
                    "Use ONLY the numbers in the 'Q/Q DELTA BRIDGE NUMBERS' reference block above. Do NOT hedge with words like 'hypothetical' or 'roughly'.\n"
                    "If that reference block is missing, SKIP the delta bridge entirely (do not invent prior-period numbers).\n\n"
                    "CHANGE FOCUS (MANDATORY): Use this section to explain WHAT CHANGED versus the immediately prior comparable period (QoQ for 10-Q, YoY for 10-K) — not to re-teach the thesis.\n"
                    "- Paragraph 1 should expand on the operating deltas (revenue/margins) with one driver per change.\n"
                    "- Paragraph 2 should expand on the cash deltas (OCF→FCF, capex/working capital) with one driver per change.\n\n"
                    "STRUCTURE (FLOW IS MANDATORY):\n"
                    "- After the delta bridge lines, write 2 paragraphs.\n"
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
                    "CHANGE FOCUS (MANDATORY): Explicitly call out what management changed versus the prior comparable period (guidance posture, capex pacing, cost discipline, capital return) and whether the numbers corroborate it.\n\n"
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
                    "FORMAT (MANDATORY): No intro paragraph and no closing recap. Output ONLY the 2-3 risks in the required format.\n"
                    "Each MUST:\n"
                    "1. Have a clear name that includes the *specific driver* (segment/product/platform/regulation). Avoid generic labels like 'Margin Compression Risk' unless you tie it to a named driver (e.g., TAC, capex, incentives, insurance).\n"
                    "2. Be 2-4 sentences with concrete mechanisms and quantified impact where possible\n"
                    "3. Be specific to THIS business model (not generic macro filler)\n"
                    "4. Be distinct: no duplicate names or overlapping drivers\n"
                    "5. Be grounded in the filing's RISK FACTORS excerpt in the prompt (do not invent risks)\n"
                    "6. For EACH risk, cite (a) a filing-specific driver from the excerpt and (b) at least one numeric metric from this memo (margins, OCF→FCF, capex, cash vs liabilities).\n"
                    "7. If a RISK FACTORS excerpt is provided above, include a SHORT verbatim quote (4-10 words) from that excerpt in quotation marks inside EACH risk to prove grounding.\n"
                    "8. WEIGHTING (MANDATORY): For each risk, explicitly state severity/likelihood (High/Med/Low) and include one sentence on why it does NOT dominate the thesis yet (and what would make it dominate).\n"
                    "9. CHANGE SIGNAL (MANDATORY): For each risk, name one concrete sign it is worsening versus the prior comparable period (a metric moving, a cost line drifting, a cash item deteriorating).\n"
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
- Any famous investor voices, names, or catchphrases
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
- 'This is a wonderful business with a wide moat.' (famous-investor catchphrase)
- 'Inverting the question, what could go wrong?' (famous-investor catchphrase)
=== END NO PERSONA MODE ===
"""

        # Build the opening identity based on whether a persona is selected
        if selected_persona_name:
            identity_block = f"""You are a senior analyst writing an institutional investment memo for portfolio managers.
You are filtering the analysis through the priorities of {selected_persona_name}, but you must NOT mimic catchphrases or produce self-referential manifesto language.
Your goal is to provide actionable, differentiated insight with clear hierarchy and minimal repetition."""
        elif investor_focus:
            identity_block = """You are a senior analyst writing an institutional investment memo for portfolio managers.
You are filtering the analysis through the investor persona described in the user customization requirements above, but you must NOT produce self-referential manifesto language.
Your goal is to provide actionable, differentiated insight with clear hierarchy and minimal repetition."""
        else:
            identity_block = """=== CRITICAL: NO PERSONA MODE ===
YOU ARE A NEUTRAL, OBJECTIVE FINANCIAL ANALYST. 

ABSOLUTE PROHIBITION - READ THIS FIRST:
- You have NOT been assigned any investor persona
- Do NOT adopt ANY famous investor's voice or perspective
- Do NOT use first-person language ('I', 'my view', 'I would', 'I believe', 'my conviction')
- Do NOT imitate or mention any famous investor

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

        anti_repetition_rules = (
            " - EDITING DISCIPLINE (MANDATORY): If an idea is stated clearly once, do NOT restate it later unless you add NEW information (new number, new mechanism, new trade-off, or a new conditional trigger).\n"
            " - ONE-TIME IDEAS RULE: Every idea earns exactly ONE paragraph in the entire memo. If you catch yourself repeating, delete the weaker instance or merge and move on.\n"
            " - BAN VERBATIM LOOPS: Do not repeat sentences or near-identical paragraph structure across sections.\n"
            " - BAN CATCHPHRASE SPAM: Mention each of these phrases at most ONCE in the entire memo (use synonyms or implicit references after):\n"
            "   'cash conversion vs margins', 'durability through the cycle', 'don\\'t buy growth with incentives', 'bridge from operating margin to free cash flow', 'margin for error'.\n"
            " - NO PROCESS NARRATION: Do NOT write meta-thinking lines (e.g., 'what I care about', 'what I watch', 'what could matter', 'this could be important', 'this could falsify the thesis'). Present conclusions and triggers.\n"
            " - FORBIDDEN DEFINITIONS: Do NOT explain basic definitions (e.g., 'Free cash flow is the difference between...'). Assume the reader is financially literate.\n"
            " - PRIOR-PERIOD INTEGRITY: If a 'Q/Q DELTA BRIDGE NUMBERS' block is present, you MUST use it for all sequential claims. NEVER use the words 'hypothetical', 'roughly', or 'assume' for those comparisons.\n"
            " - PARAGRAPH DISCIPLINE: One paragraph = one idea. If you need 'and also', start a new paragraph.\n"
            ' - OPINION-TO-EVIDENCE: Every strong opinion must (a) interpret a number, OR (b) explain a trade-off, OR (c) state a conditional ("if X, then Y breaks"). Otherwise, remove it.\n'
            " - STANCE CONSISTENCY: State the base stance ONCE (in Executive Summary). Do NOT oscillate between BUY/HOLD/constructive later.\n"
            ' - NO MINI-CONCLUSIONS: Avoid repeated "takeaway" or "so what" restatements. Use ONE forward-linking transition sentence at the end of each section, not a recap.\n'
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

        target_length_line = (
            f"4. Target Length: {target_length} words total (must land within ±10 words; no filler)"
            if target_length
            else "4. Target Length: keep concise; prioritize substance over length"
        )

        base_prompt = f"""
{identity_block}
Analyze the following filing for {company_name} ({filing_type}, {filing_date}).
{company_profile_block}

CONTEXT:
{context_excerpt}{truncated_note}{spotlight_block}

FINANCIAL SNAPSHOT (Reference only):
{financial_snapshot}

KEY METRICS (Use these for calculations and evidence):
{metrics_lines}
{prior_period_delta_block}
{risk_factors_block}

INSTRUCTIONS:
1. Tone: {tone.title()} (Professional, Insightful, Direct)
2. Detail Level: {detail_level.title()}
3. Output Style: {output_style.title()}
{target_length_line}

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
     - CHANGE FOCUS (MANDATORY): This memo is most useful as a "what changed" read.
       - If this is a quarterly filing (10-Q), explicitly compare THIS quarter vs the IMMEDIATELY PRIOR quarter throughout narrative sections.
       - If this is an annual filing (10-K), explicitly compare THIS year vs the PRIOR year throughout narrative sections.
       - Do not repeat the same comparison in multiple sections; each section should add a NEW angle on change (operating, cash, capital posture, risk).
       - If prior-period data is unavailable, DO NOT invent it and DO NOT say "hypothetical"; skip the comparison and focus on within-period drivers.
     - NO REDUNDANCY: Avoid repeating the same metric across multiple sections. Executive Summary should be mostly qualitative; keep dense figures in Financial Performance / Key Metrics.
    {anti_repetition_rules}
     - **SUSTAINABILITY**: Do NOT mention sustainability or ESG efforts unless they are a primary revenue driver (e.g., for a solar company). For most companies, this is fluff.
     - **MD&A**: Do NOT say "Management discusses..." or "In the MD&A section...". Just state the facts found there.
 - USE TRANSITIONS: Connect sections logically. Each section should flow naturally from the previous one.

=== #1 PRIORITY: SENTENCE COMPLETION (UNDER THE WORD CAP) ===
THIS IS YOUR SINGLE MOST IMPORTANT RULE.

FUNDAMENTAL PRINCIPLE: Never cut off mid-sentence. If you're near the word cap, finish the sentence, then tighten earlier sentences to stay under the cap. It is always acceptable to stop early rather than pad with filler.

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
- If you're near the cap and need a few words to finish a sentence, finish it — then tighten earlier sentences so the TOTAL stays under the cap.
- If you're at the cap, do NOT start a new thought you can't finish.
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
[ ] If you're near the cap, it's OK to be shorter — do NOT pad with filler; incomplete sentences are NOT OK
=== END CHECKLIST ===
"""

        # Enforce per-summary token budget by trimming the CONTEXT block (input)
        # before we make any Gemini calls.
        if token_budget:
            max_prompt_tokens = max(
                0, token_budget.remaining_tokens - max_output_tokens
            )
            max_prompt_chars = max_prompt_tokens * CHARS_PER_TOKEN_ESTIMATE
            if max_prompt_chars > 0 and len(base_prompt) > max_prompt_chars:
                base_prompt = _truncate_prompt_to_token_budget(
                    base_prompt,
                    max_prompt_chars=max_prompt_chars,
                    budget_note="\n\nNote: Filing text truncated to fit per-summary token budget.",
                )

        set_summary_progress(
            filing_id, status="Synthesizing Investor Insights...", stage_percent=85
        )
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
        quality_validators.append(_make_verbatim_repetition_validator())
        quality_validators.append(_make_phrase_limits_validator())
        quality_validators.append(_make_sentence_stem_repetition_validator())
        quality_validators.append(
            _make_period_delta_bridge_validator(
                require_bridge=bool((prior_period_delta_block or "").strip())
            )
        )
        quality_validators.append(
            _make_closing_recommendation_validator(
                persona_requested=bool(investor_focus), company_name=company_name
            )
        )
        quality_validators.append(
            _make_persona_exclusivity_validator(
                persona_requested=bool(investor_focus),
                selected_persona_name=selected_persona_name,
            )
        )
        quality_validators.append(_make_stance_consistency_validator())

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

        set_summary_progress(filing_id, status="Polishing Output...", stage_percent=95)
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
        summary_text = _dedupe_repeated_paragraphs(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)
        # Final pass to normalize headings and ensure required sections exist.
        if target_length:
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
            # Enforce a strict band (land within ±10 words).
            summary_text = _enforce_whitespace_word_band(
                summary_text, int(target_length), tolerance=10, allow_padding=True
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
        summary_text = _dedupe_repeated_paragraphs(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)

        # Fix health score if AI generated a different score than pre-calculated
        if pre_calculated_score is not None and pre_calculated_band:
            summary_text = _fix_health_score_in_summary(
                summary_text,
                pre_calculated_score,
                pre_calculated_band,
            )

            if target_length:
                summary_text = _enforce_whitespace_word_band(
                    summary_text, int(target_length), tolerance=10, allow_padding=True
                )

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
        summary_text = _dedupe_repeated_paragraphs(summary_text)
        summary_text = _normalize_casing(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)
        if target_length:
            # Hard cap only (never pad); additional cleanup below handles any artifacts from trimming.
            summary_text = _enforce_whitespace_word_band(
                summary_text, int(target_length), tolerance=10, allow_padding=True
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
                summary_text = _enforce_whitespace_word_band(
                    summary_text, int(target_length), tolerance=0, allow_padding=False
                )

        # Final quality cleanup before enforcing the user-visible band.
        # (Do this before the last word-band pass so any removals can be compensated.)
        summary_text = _normalize_underwriting_questions_formatting(summary_text)
        summary_text = _merge_underwriting_question_lines(summary_text)
        summary_text = _relocate_underwriting_questions_to_mdna(summary_text)
        summary_text = _remove_filler_phrases(summary_text)
        summary_text = _remove_generic_heuristic_paragraphs(summary_text)
        summary_text = _dedupe_consecutive_sentences(summary_text)
        summary_text = _dedupe_repeated_paragraphs(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)
        summary_text = _tone_down_emotive_adjectives(summary_text)
        summary_text = _cleanup_sentence_artifacts(summary_text)
        summary_text = _validate_complete_sentences(summary_text)

        # Final health-rating normalization (prevents dangling "68/100 -" lines and
        # keeps the section readable after trims/padding).
        if include_health_rating:
            # Even if the scorer failed (health_score_data empty), we should still fix
            # common formatting issues like "64/100 -" by inferring the band from the score.
            health_fix_data: Dict[str, Any] = dict(health_score_data or {})

            if (
                health_fix_data.get("overall_score") is None
                and pre_calculated_score is not None
            ):
                health_fix_data["overall_score"] = pre_calculated_score
            if (
                health_fix_data.get("score_band") is None
                or str(health_fix_data.get("score_band") or "").strip() == ""
            ) and pre_calculated_band:
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
                health_fix_data.get("score_band") is None
                or str(health_fix_data.get("score_band") or "").strip() == ""
            ) and health_fix_data.get("overall_score") is not None:
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
            # Enforce hard max (UI/tests count raw whitespace tokens too); allow padding for precision.
            summary_text = _enforce_whitespace_word_band(
                summary_text, int(target_length), tolerance=10, allow_padding=True
            )

        # Final backfill AFTER trimming to prevent missing sections from surviving caps.
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
                    summary_text, int(target_length), tolerance=0, allow_padding=False
                )

        # Post-band formatting: safe (does not change token/word counts meaningfully).
        summary_text = _normalize_underwriting_questions_formatting(summary_text)
        summary_text = _relocate_underwriting_questions_to_mdna(summary_text)
        summary_text = _merge_staccato_paragraphs(summary_text)

        # Do not enforce per-section padding/distribution here: it encourages repetition
        # and low-signal filler at high target lengths. We only enforce a hard max cap.

        # Final guard: ensure the document ends with punctuation for substantive outputs
        if (
            summary_text
            and _count_words(summary_text) >= 5
            and not summary_text.rstrip().endswith((".", "!", "?"))
        ):
            summary_text = summary_text.rstrip() + "."

        # Absolute last pass: enforce the hard max after *all* text mutations.
        if target_length:
            # Final polish AFTER trimming: remove any stray quote/fragments and re-validate
            # sentence completeness (trimming can reintroduce cut-offs near section boundaries).
            summary_text = _cleanup_sentence_artifacts(summary_text)
            summary_text = _validate_complete_sentences(summary_text)
            summary_text = _dedupe_consecutive_sentences(summary_text)
            summary_text = _deduplicate_sentences(summary_text)
            summary_text = _enforce_section_order(
                summary_text, include_health_rating=include_health_rating
            )
            # Final strict band: ensure we're within ±10 words of the requested target.
            summary_text = _enforce_strict_target_band(
                summary_text,
                int(target_length),
                calculated_metrics=calculated_metrics,
                company_name=company_name,
                include_health_rating=include_health_rating,
            )
        else:
            if _count_words(summary_text) > TARGET_LENGTH_MAX_WORDS:
                summary_text = _truncate_text_to_word_limit(
                    summary_text, TARGET_LENGTH_MAX_WORDS
                )

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

        # Build chart_data for frontend visualization
        if calculated_metrics or prior_metrics:
            # Determine period labels from filing type and dates
            is_quarterly = "10-Q" in str(filing_type or "").upper()
            current_period_end = str((statements or {}).get("period_end") or "")
            prior_period_end = str((prior_statements or {}).get("period_end") or "")

            # Build period labels (e.g., "Q3 2024" or "FY 2024")
            def _format_period_label(period_end: str, is_quarterly: bool) -> str:
                if not period_end:
                    return "Current" if is_quarterly else "FY"
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
                    if is_quarterly:
                        quarter = (dt.month - 1) // 3 + 1
                        return f"Q{quarter} {dt.year}"
                    return f"FY {dt.year}"
                except Exception:
                    return period_end[:10] if len(period_end) >= 10 else period_end

            current_label = _format_period_label(current_period_end, is_quarterly)
            prior_label = _format_period_label(prior_period_end, is_quarterly)

            # Additional metrics for inline charts (different from top summary card)
            additional_current = {
                "gross_profit": calculated_metrics.get("gross_profit"),
                "ebitda": calculated_metrics.get("ebitda"),
                "operating_cash_flow": calculated_metrics.get("operating_cash_flow")
                or calculated_metrics.get("cash_from_operations"),
                "capex": calculated_metrics.get("capex")
                or calculated_metrics.get("capital_expenditures"),
                "total_debt": calculated_metrics.get("total_debt"),
                "cash_and_equivalents": calculated_metrics.get("cash_and_equivalents")
                or calculated_metrics.get("cash"),
                "total_assets": calculated_metrics.get("total_assets"),
                "total_equity": calculated_metrics.get("total_equity")
                or calculated_metrics.get("shareholders_equity"),
                "eps": calculated_metrics.get("eps")
                or calculated_metrics.get("earnings_per_share"),
                "eps_diluted": calculated_metrics.get("eps_diluted")
                or calculated_metrics.get("diluted_eps"),
                "shares_outstanding": calculated_metrics.get("shares_outstanding")
                or calculated_metrics.get("weighted_avg_shares"),
                "rnd_expense": calculated_metrics.get("rnd_expense")
                or calculated_metrics.get("research_and_development"),
                "sga_expense": calculated_metrics.get("sga_expense")
                or calculated_metrics.get("selling_general_admin"),
                "interest_expense": calculated_metrics.get("interest_expense"),
                "tax_expense": calculated_metrics.get("tax_expense")
                or calculated_metrics.get("income_tax_expense"),
                "dividends_paid": calculated_metrics.get("dividends_paid"),
                "share_repurchases": calculated_metrics.get("share_repurchases")
                or calculated_metrics.get("stock_repurchases"),
                # Ratios
                "roe": calculated_metrics.get("roe")
                or calculated_metrics.get("return_on_equity"),
                "roa": calculated_metrics.get("roa")
                or calculated_metrics.get("return_on_assets"),
                "roic": calculated_metrics.get("roic")
                or calculated_metrics.get("return_on_invested_capital"),
                "debt_to_equity": calculated_metrics.get("debt_to_equity"),
                "current_ratio": calculated_metrics.get("current_ratio"),
                "quick_ratio": calculated_metrics.get("quick_ratio"),
                "asset_turnover": calculated_metrics.get("asset_turnover"),
                "inventory_turnover": calculated_metrics.get("inventory_turnover"),
                "days_sales_outstanding": calculated_metrics.get(
                    "days_sales_outstanding"
                )
                or calculated_metrics.get("dso"),
                "ebitda_margin": calculated_metrics.get("ebitda_margin"),
                "fcf_margin": calculated_metrics.get("fcf_margin")
                or calculated_metrics.get("free_cash_flow_margin"),
            }

            additional_prior = {}
            if prior_metrics:
                additional_prior = {
                    "gross_profit": prior_metrics.get("gross_profit"),
                    "ebitda": prior_metrics.get("ebitda"),
                    "operating_cash_flow": prior_metrics.get("operating_cash_flow")
                    or prior_metrics.get("cash_from_operations"),
                    "capex": prior_metrics.get("capex")
                    or prior_metrics.get("capital_expenditures"),
                    "total_debt": prior_metrics.get("total_debt"),
                    "cash_and_equivalents": prior_metrics.get("cash_and_equivalents")
                    or prior_metrics.get("cash"),
                    "total_assets": prior_metrics.get("total_assets"),
                    "total_equity": prior_metrics.get("total_equity")
                    or prior_metrics.get("shareholders_equity"),
                    "eps": prior_metrics.get("eps")
                    or prior_metrics.get("earnings_per_share"),
                    "eps_diluted": prior_metrics.get("eps_diluted")
                    or prior_metrics.get("diluted_eps"),
                    "shares_outstanding": prior_metrics.get("shares_outstanding")
                    or prior_metrics.get("weighted_avg_shares"),
                    "rnd_expense": prior_metrics.get("rnd_expense")
                    or prior_metrics.get("research_and_development"),
                    "sga_expense": prior_metrics.get("sga_expense")
                    or prior_metrics.get("selling_general_admin"),
                    "interest_expense": prior_metrics.get("interest_expense"),
                    "tax_expense": prior_metrics.get("tax_expense")
                    or prior_metrics.get("income_tax_expense"),
                    "dividends_paid": prior_metrics.get("dividends_paid"),
                    "share_repurchases": prior_metrics.get("share_repurchases")
                    or prior_metrics.get("stock_repurchases"),
                    "roe": prior_metrics.get("roe")
                    or prior_metrics.get("return_on_equity"),
                    "roa": prior_metrics.get("roa")
                    or prior_metrics.get("return_on_assets"),
                    "roic": prior_metrics.get("roic")
                    or prior_metrics.get("return_on_invested_capital"),
                    "debt_to_equity": prior_metrics.get("debt_to_equity"),
                    "current_ratio": prior_metrics.get("current_ratio"),
                    "quick_ratio": prior_metrics.get("quick_ratio"),
                    "asset_turnover": prior_metrics.get("asset_turnover"),
                    "inventory_turnover": prior_metrics.get("inventory_turnover"),
                    "days_sales_outstanding": prior_metrics.get(
                        "days_sales_outstanding"
                    )
                    or prior_metrics.get("dso"),
                    "ebitda_margin": prior_metrics.get("ebitda_margin"),
                    "fcf_margin": prior_metrics.get("fcf_margin")
                    or prior_metrics.get("free_cash_flow_margin"),
                }

            chart_data = {
                "current_period": {
                    "revenue": calculated_metrics.get("revenue")
                    or calculated_metrics.get("total_revenue"),
                    "operating_income": calculated_metrics.get("operating_income"),
                    "net_income": calculated_metrics.get("net_income"),
                    "free_cash_flow": calculated_metrics.get("free_cash_flow"),
                    "operating_margin": calculated_metrics.get("operating_margin"),
                    "net_margin": calculated_metrics.get("net_margin"),
                    "gross_margin": calculated_metrics.get("gross_margin"),
                    **{k: v for k, v in additional_current.items() if v is not None},
                },
                "prior_period": {
                    "revenue": prior_metrics.get("revenue")
                    or prior_metrics.get("total_revenue"),
                    "operating_income": prior_metrics.get("operating_income"),
                    "net_income": prior_metrics.get("net_income"),
                    "free_cash_flow": prior_metrics.get("free_cash_flow"),
                    "operating_margin": prior_metrics.get("operating_margin"),
                    "net_margin": prior_metrics.get("net_margin"),
                    "gross_margin": prior_metrics.get("gross_margin"),
                    **{k: v for k, v in additional_prior.items() if v is not None},
                }
                if prior_metrics
                else None,
                "period_type": "quarterly" if is_quarterly else "annual",
                "current_label": current_label,
                "prior_label": prior_label,
                "company_kpi": None,  # Will be populated if available
                "company_charts": [],  # Optional array of insight charts
            }

            # SKIP KPI EXTRACTION DURING SUMMARY GENERATION
            # The frontend should call the /spotlight endpoint separately to get the KPI.
            # This dramatically speeds up summary generation (was 10+ minutes, now <60s).
            # The company_kpi and company_charts fields will be populated by the spotlight endpoint.
            company_charts = []

            if company_charts:
                chart_data["company_charts"] = company_charts
                chart_data["company_kpi"] = company_charts[0]

            # Merge chart_data into response_data instead of returning separately
            response_data["chart_data"] = chart_data

        # Mark progress as complete
        complete_summary_progress(filing_id)

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error generating filing charts")
        raise HTTPException(status_code=500, detail=str(exc))


def _strip_directive_lines(text: str) -> str:
    """Remove residual directive/instructional lines that leaked from LLM prompts."""
    if not text:
        return text

    # Compile patterns for boilerplate directive phrases
    boilerplate_patterns = [
        re.compile(r"Highlight how revenue growth translated", re.IGNORECASE),
        re.compile(r"Call out whether cash conversion", re.IGNORECASE),
        re.compile(r"Note any mix shift between", re.IGNORECASE),
        re.compile(r"Address how leverage and liquidity", re.IGNORECASE),
        re.compile(r"Point to the key catalyst or risk", re.IGNORECASE),
        re.compile(r"Emphasize what the market may be underpricing", re.IGNORECASE),
        re.compile(r"^\s*SUGGESTED LENGTH:", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*MANDATORY:", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*CRITICAL:", re.IGNORECASE | re.MULTILINE),
        re.compile(
            r"^\s*STRUCTURE \(FLOW IS MANDATORY\):", re.IGNORECASE | re.MULTILINE
        ),
        re.compile(r"^\s*VOICE DISCIPLINE:", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*CHANGE FOCUS:", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*NUMBERS DISCIPLINE:", re.IGNORECASE | re.MULTILINE),
    ]

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        if any(p.search(line) for p in boilerplate_patterns):
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
        # Boilerplate slogans that sometimes leak mid-paragraph.
        r"The memo should\b[^.\n]{0,240}(?:\.|\n|$)",
        r"The analysis should\b[^.\n]{0,240}(?:\.|\n|$)",
        r"The best MD&A reads\b[^.\n]{0,240}(?:\.|\n|$)",
        r"The primary constraint\b[^.\n]{0,240}(?:\.|\n|$)",
        r"(?:(?:in\s+sum)\s+){5,}",
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


def _dedupe_repeated_paragraphs(text: str) -> str:
    """Remove repeated (or near-duplicate) narrative paragraphs across the memo.

    This targets a common failure mode where the model restates the same framing
    in multiple sections, which reads as loss of editorial control.
    """
    if not text:
        return text

    import difflib
    import string

    heading_regex = re.compile(r"^\s*##\s+.+")

    def _is_structured_paragraph(paragraph: str) -> bool:
        stripped = (paragraph or "").lstrip()
        return bool(stripped.startswith(("→", "- ", "* ", "• ", "**")))

    punct = string.punctuation + "“”’‘—–…"

    def _normalize_paragraph(paragraph: str) -> str:
        cleaned = (paragraph or "").replace("\u00a0", " ").strip()
        cleaned = " ".join(cleaned.split())
        cleaned = cleaned.strip(punct).lower()
        cleaned = re.sub(r"[^a-z0-9%$ ]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

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

    seen_norms: List[str] = []
    rebuilt_sections: List[str] = []

    for heading, body in sections:
        raw_body = (body or "").strip()
        if not raw_body:
            rebuilt_sections.append(heading.strip())
            continue

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw_body) if p.strip()]
        kept: List[str] = []
        for paragraph in paragraphs:
            if _is_structured_paragraph(paragraph):
                kept.append(paragraph)
                continue

            norm = _normalize_paragraph(paragraph)
            if not norm:
                continue

            # Avoid over-aggressive deletion for short paragraphs.
            if len(norm) < 140:
                kept.append(paragraph)
                continue

            is_duplicate = False
            for prior in seen_norms:
                max_len = max(1, max(len(norm), len(prior)))
                if abs(len(norm) - len(prior)) / max_len > 0.15:
                    continue
                ratio = difflib.SequenceMatcher(None, norm, prior).ratio()
                if ratio >= 0.93:
                    is_duplicate = True
                    break

            if is_duplicate:
                continue

            seen_norms.append(norm)
            kept.append(paragraph)

        rebuilt_body = "\n\n".join(kept).strip()
        rebuilt_sections.append(f"{heading}\n\n{rebuilt_body}".strip())

    rebuilt = "\n\n".join(
        [s for s in ([preamble_text] if preamble_text else []) + rebuilt_sections if s]
    ).strip()
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    return rebuilt


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
            stripped.startswith(("→", "- ", "* ", "• ")) or stripped.startswith("**")
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

    rebuilt = "\n\n".join(
        [s for s in ([preamble_text] if preamble_text else []) + rebuilt_sections if s]
    ).strip()
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
    if (
        persona_requested
        and persona_name
        and persona_name in PERSONA_CLOSING_INSTRUCTIONS
    ):
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

        trigger = (
            " and ".join(trigger_parts[:2])
            if trigger_parts
            else "durable cash conversion"
        )
        primary_concern = concerns[0] if concerns else "the weak spots"
        seed_material = "|".join(
            [persona_name, company_name, quality, trigger, primary_concern]
        )
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

        strengths_str = (
            " and ".join(strengths[:2]) if strengths else "limited visibility"
        )
        concerns_str = (
            " and ".join(concerns[:2]) if concerns else "no obvious red flags"
        )
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
            t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if t
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
            return bool(re.search(r"(?im)^\s*##\s*Key\s+Data\s+Appendix\b", text or ""))
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

    # IMPORTANT (quality over quantity):
    # `target_length` is treated as a hard maximum, not a quota. Avoid "topping up"
    # sections to hit proportional minimums because that pushes the system into
    # boilerplate/padding loops at high targets.
    min_words_by_section = dict(SUMMARY_SECTION_MIN_WORDS)
    if not include_health_rating:
        min_words_by_section.pop("Financial Health Rating", None)

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
            desc_norm = " ".join(re.sub(r"[^a-z0-9]+", " ", desc.lower()).split())
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
                desc_norm = " ".join(re.sub(r"[^a-z0-9]+", " ", desc.lower()).split())
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

        _append_section(
            "Financial Health Rating", f"{score_line}\n\n{narrative}".strip()
        )

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
        net_inc_str = (
            _format_metric_value_for_text("net_income", net_income)
            if net_income is not None
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
        capex_str = (
            _format_metric_value_for_text("capital_expenditures", capex)
            if capex is not None
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

        fcf_margin_pct = None
        if fcf is not None and revenue:
            try:
                fcf_margin_pct = (fcf / revenue) * 100
            except Exception:
                fcf_margin_pct = None

        intro = "The key question is whether"

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
            margin_clause = (
                f" (~{fcf_margin_pct:.1f}% FCF margin)"
                if fcf_margin_pct is not None
                else ""
            )
            capex_clause = f" after capex of {capex_str}" if capex_str else ""
            sentences.append(
                f"Cash conversion is the anchor: operating cash flow {ocf_str} converts to free cash flow {fcf_str}{margin_clause}{capex_clause}."
            )

        if cash_total_str and liabilities_str:
            sentences.append(
                f"Balance-sheet flexibility is {cash_total_str} cash and securities against {liabilities_str} liabilities, which keeps refinancing and downside scenarios in view."
            )

        if not sentences:
            return "The investment case hinges on whether operating profitability and cash conversion can compound without margin fragility or balance-sheet stress."

        return " ".join(sentences).strip()

    def _synthesize_financial_performance_addendum() -> str:
        """Add a short, concrete extension when the Financial Performance section is too thin."""

        revenue = calculated_metrics.get("revenue")
        operating_income = calculated_metrics.get("operating_income")
        net_income = calculated_metrics.get("net_income")
        operating_margin = calculated_metrics.get("operating_margin")
        net_margin = calculated_metrics.get("net_margin")
        ocf = calculated_metrics.get("operating_cash_flow")
        capex = calculated_metrics.get("capital_expenditures")
        fcf = calculated_metrics.get("free_cash_flow")

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
        net_inc_str = (
            _format_metric_value_for_text("net_income", net_income)
            if net_income is not None
            else None
        )
        ocf_str = (
            _format_metric_value_for_text("operating_cash_flow", ocf)
            if ocf is not None
            else None
        )
        capex_str = (
            _format_metric_value_for_text("capital_expenditures", capex)
            if capex is not None
            else None
        )
        fcf_str = (
            _format_metric_value_for_text("free_cash_flow", fcf)
            if fcf is not None
            else None
        )

        sentences: List[str] = []

        if rev_str and op_inc_str and operating_margin is not None:
            sentences.append(
                f"The run-rate engine is {rev_str} of revenue with operating income {op_inc_str}, implying an operating margin of {operating_margin:.1f}%."
            )

        if net_inc_str and net_margin is not None and operating_margin is not None:
            gap = net_margin - operating_margin
            if abs(gap) >= 5:
                sentences.append(
                    f"The spread to net income {net_inc_str} (net margin {net_margin:.1f}%) signals meaningful below-the-line items, so net margin should be treated cautiously when underwriting durability."
                )

        if ocf_str and fcf_str:
            capex_clause = f" after capex of {capex_str}" if capex_str else ""
            sentences.append(
                f"Operating cash flow {ocf_str} converts to free cash flow {fcf_str}{capex_clause}, which is the cash that can fund reinvestment, buybacks, or balance-sheet de-risking."
            )

        sentences.append(
            "Unit economics need to improve via pricing discipline and efficiency, rather than compressing under incentives, insurance, and regulatory costs."
        )

        return " ".join([s for s in sentences if s]).strip()

    def _synthesize_risk_factors_addendum() -> str:
        """Add concrete risk scenarios when Risk Factors is too thin.

        Goal: make Risk Factors feel like *underwriting*, not boilerplate.
        We keep this metric-anchored so it reads substantive.
        """

        lead = "A key risk is that"

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
            _format_metric_value_for_text("free_cash_flow", fcf)
            if fcf is not None
            else None
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
                implied_str = _format_metric_value_for_text(
                    "capital_expenditures", implied
                )
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
                "The management narrative is about converting scale into operating leverage while keeping growth investments disciplined."
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
        lead = "The key check is"

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
            sentence = (sentence or "").replace("\u00a0", " ")
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
            if re.search(
                r"\bmanagement\s+discussion\s+should\s+focus\b", body, re.IGNORECASE
            ) or re.search(
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

    _normalize_risk_factors_section()

    # 6. Key Metrics / Financial Snapshot
    # Always ensure a high-quality data appendix exists, even if the model tried to generate one.
    snapshot_header = "Key Metrics"
    has_metrics = _section_present("Key Metrics")
    has_snapshot = _section_present("Financial Snapshot")

    if target_length:
        # For long memos, we force our deterministic, high-fidelity data block.
        fresh_metrics = _build_key_metrics_block(
            calculated_metrics,
            target_length=target_length,
            include_health_rating=include_health_rating,
            health_score_data=health_score_data,
        )
        if fresh_metrics:
            if has_metrics:
                # Replace existing Key Metrics
                text = re.sub(
                    r"##\s*Key\s+Metrics\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                    lambda _m: f"## {snapshot_header}\n{fresh_metrics}\n",
                    text,
                    flags=re.IGNORECASE,
                )
            elif has_snapshot:
                # Replace existing Financial Snapshot
                text = re.sub(
                    rf"##\s*{re.escape(snapshot_header)}\s*\n+[\s\S]*?(?=\n##\s|\Z)",
                    lambda _m: f"## {snapshot_header}\n{fresh_metrics}\n",
                    text,
                    flags=re.IGNORECASE,
                )
            else:
                _append_section(snapshot_header, fresh_metrics)
    elif not (has_metrics or has_snapshot) and metrics_lines.strip():
        # Fallback for short memos without a target length
        _append_section("Key Metrics", metrics_lines.strip())

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
        elif (not persona_mode) and not _contains_objective_recommendation(
            existing_closing
        ):
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
        or not _closing_has_reasoned_takeaway(existing_closing or "")
        or (persona_mode and not _contains_personal_verdict(existing_closing or ""))
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
                    desired_body = _trim_appendix_preserving_rows(
                        desired_body, max_words
                    )
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
    snapshot = get_summary_progress_snapshot(filing_id)
    return {
        "status": snapshot.status,
        "percent": snapshot.percent,
        "percent_exact": snapshot.percent_exact,
        "eta_seconds": snapshot.eta_seconds,
    }


@router.get("/{filing_id}/spotlight")
async def get_filing_spotlight_kpi(
    filing_id: str,
    debug: bool = False,
    refresh: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    """Return the best available company-specific Spotlight KPI without regenerating the full summary."""
    settings = get_settings()
    context = _resolve_filing_context(filing_id, settings)
    filing = context["filing"]
    company = context["company"]

    # Spotlight needs the filing text. If the local document is missing, we must
    # resolve + download it (older filings often have no cached document yet).
    local_document = _ensure_local_document(context, settings, allow_network=False)

    allow_spotlight_network = str(os.getenv("SPOTLIGHT_ALLOW_NETWORK", "1") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if allow_spotlight_network and (not local_document or not local_document.exists()):
        try:
            raw_timeout = (os.getenv("SPOTLIGHT_DOCUMENT_RESOLVE_TIMEOUT_SECONDS") or "").strip() or "60"
            try:
                resolve_timeout = float(raw_timeout)
            except ValueError:
                resolve_timeout = 60.0
            resolve_timeout = max(5.0, float(resolve_timeout))

            with anyio.fail_after(resolve_timeout):
                local_document = await anyio.to_thread.run_sync(
                    lambda: _ensure_local_document(context, settings, allow_network=True),
                    cancellable=True,
                )
        except TimeoutError:
            local_document = None
        except Exception:  # noqa: BLE001
            local_document = None

    payload = await build_spotlight_payload_for_filing(
        str(filing_id),
        filing=filing,
        company=company,
        local_document_path=local_document if (local_document and local_document.exists()) else None,
        settings=settings,
        context_source=str(context.get("source") or ""),
        debug=debug,
        bypass_cache=bool(refresh),
    )
    return JSONResponse(content=jsonable_encoder(payload))


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
