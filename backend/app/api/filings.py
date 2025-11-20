"""Filings API endpoints."""
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from uuid import uuid4
from app.models.database import get_supabase_client
from app.models.schemas import (
    Filing,
    FilingsFetchRequest,
    FilingsFetchResponse,
    FilingSummaryPreferences,
)
from app.tasks.fetch import fetch_filings_task, run_fetch_filings_inline
from app.config import get_settings
from app.api.companies import _supabase_configured
from app.services.eodhd_client import (
    get_eodhd_client,
    EODHDAccessError,
    EODHDClientError,
)
from app.services.edgar_fetcher import (
    download_filing,
    get_company_filings,
    search_company_by_ticker_or_cik,
)
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
from app.services.gemini_client import get_gemini_client
from app.services.sample_data import sample_filings_by_ticker
from app.utils.supabase_errors import is_supabase_table_missing_error

router = APIRouter()
logger = logging.getLogger(__name__)

# Gemini 2.0 Flash Lite supports up to ~1M tokens. We limit to keep requests manageable.
MAX_GEMINI_CONTEXT_CHARS = 600_000
MAX_SUMMARY_ATTEMPTS = 8
MAX_REWRITE_ATTEMPTS = 3

DETAIL_LEVEL_PROMPTS: Dict[str, str] = {
    "snapshot": "Keep analysis concise (1–2 short paragraphs) and only cite headline metrics that prove the main point.",
    "balanced": "Provide balanced coverage with equal weight on growth, profitability, balance sheet, and guidance.",
    "deep dive": "Offer exhaustive commentary with supporting data points for every section, including subtle nuances from management commentary.",
}

OUTPUT_STYLE_PROMPTS: Dict[str, str] = {
    "narrative": "Write in cohesive paragraphs with strong topic sentences and transitions. Avoid bullet lists except where explicitly required by the base template.",
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
    "score_plus_grade": "Present the 0–100 score plus a letter grade (A–F).",
    "score_plus_traffic_light": "Present the 0–100 score plus a traffic light (Green/Yellow/Red) indicator.",
    "score_plus_pillars": "Present the 0–100 score plus a four-pillar breakdown (Profitability | Risk | Liquidity | Growth).",
    "score_with_narrative": "Present the 0–100 score alongside a short narrative paragraph explaining the result.",
}


def _clamp_target_length(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(10, min(5000, value))


def _count_words(text: str) -> int:
    tokens = re.findall(r"\b\w+\b", text)
    return len(tokens)


def _truncate_text_to_word_limit(text: str, max_words: int) -> str:
    """Trim text so it contains at most `max_words` tokens while preserving original formatting."""
    if max_words <= 0:
        return ""

    matches = list(re.finditer(r"\b\w+\b", text))
    if len(matches) <= max_words:
        return text.rstrip()

    cutoff_index = matches[max_words - 1].end()
    truncated = text[:cutoff_index].rstrip()
    if cutoff_index >= len(text):
        return truncated

    # Try to backtrack to the nearest natural boundary (sentence end or paragraph break)
    boundary_index = None
    sentence_pattern = re.compile(r"[.!?](?:['\"\)\]]+)?")
    for match in sentence_pattern.finditer(truncated):
        boundary_index = match.end()
    if boundary_index is not None and boundary_index > max(0.6 * len(truncated), 0):
        truncated = truncated[:boundary_index].rstrip()
        if truncated:
            return truncated

    paragraph_break = truncated.rfind("\n\n")
    if paragraph_break != -1 and paragraph_break > len(truncated) * 0.4:
        return truncated[:paragraph_break].rstrip()

    # Fallback: look for any newline if double newline wasn't found
    single_newline = truncated.rfind("\n")
    if single_newline != -1 and single_newline > len(truncated) * 0.4:
        return truncated[:single_newline].rstrip()

    return truncated


def _needs_length_retry(text: str, target_length: int, cached_count: Optional[int] = None) -> Tuple[bool, int, int]:
    """Return tuple indicating if retry needed, actual count, tolerance band size."""
    words = cached_count if cached_count is not None else _count_words(text)
    tolerance = max(3, int(math.ceil(target_length * 0.02)))
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
) -> Tuple[str, Tuple[int, int]]:
    """
    Ask the model to rewrite an existing draft so it fits within the required length band
    while keeping every section intact. Returns the new draft and its (word_count, tolerance).
    """
    tolerance = max(3, int(math.ceil(target_length * 0.02)))
    lower = target_length - tolerance
    upper = target_length + tolerance
    corrections: List[str] = []
    working_draft = summary_text
    latest_words = current_words if current_words is not None else _count_words(working_draft)

    def _build_prompt() -> str:
        diff = latest_words - target_length
        abs_diff = abs(diff)
        
        if latest_words > upper:
            direction_instruction = (
                f"You are {abs_diff} words OVER the limit. \n"
                "ACTION: CONDENSE the text immediately. Remove filler words, merge sentences, and be more direct. "
                "Do not lose key metrics, but cut down on verbose explanations."
            )
        elif latest_words < lower:
            direction_instruction = (
                f"You are {abs_diff} words SHORT of the target. \n"
                "ACTION: EXPAND the content immediately. \n"
                "- Add 3-4 sentences of detailed analysis to 'Financial Performance'.\n"
                "- Add 2-3 sentences to 'Management Discussion & Analysis'.\n"
                "- Add 2-3 sentences to 'Strategic Initiatives'.\n"
                "- Elaborate on the implications of the risks and opportunities."
            )
        else:
            direction_instruction = "Ensure you stay within the target range."

        prompt = (
            f"You previously drafted an equity research memo containing {latest_words} words, which is outside the "
            f"required range of {lower}–{upper} words (target {target_length}). \n\n"
            f"{direction_instruction}\n\n"
            "Rewrite the entire memo so it fits the range while preserving every section and investor-specific instruction."
            "\n\nMANDATORY REQUIREMENTS:\n"
            "- Keep all existing section headings (Investor Lens, Executive Summary, Financial Performance, Management Discussion & Analysis, Risk Factors, "
            "Strategic Initiatives & Capital Allocation, Key Metrics/Others) unless they were absent in the draft. Do NOT drop sections to save space.\n"
            "- Retain the key figures, personas, and conclusions.\n"
            "- Ensure each paragraph ends on a complete sentence; do not stop mid-thought.\n"
            "- After rewriting, append a final line formatted exactly as `WORD COUNT: ###` (replace ### with the true count)."
            f"\n\nCRITICAL LENGTH CONSTRAINT:\nThe total output MUST be between {lower} and {upper} words."
        )
        if corrections:
            prompt += "\n\nADDITIONAL CORRECTIONS:\n" + "\n".join(corrections)
        prompt += "\n\nPREVIOUS DRAFT:\n" + working_draft
        return prompt

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        prompt = _build_prompt()
        response = gemini_client.model.generate_content(prompt)
        new_text, reported_count = _extract_word_count_control(response.text)
        if not new_text.strip():
            corrections.append("OUTPUT ISSUE: Draft was empty. Provide the full memo with all sections.")
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

    return working_draft, (latest_words, tolerance)


def _enforce_length_constraints(
    summary_text: str,
    target_length: int,
    gemini_client,
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
    last_word_stats: Optional[Tuple[int, int]],
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
    )
    summary_text = rewritten_text
    actual_words, tolerance = rewrite_stats
    lower = target_length - tolerance
    upper = target_length + tolerance

    if lower <= actual_words <= upper:
        return summary_text

    if actual_words > upper:
        trimmed_summary = _truncate_text_to_word_limit(summary_text, upper)
        trimmed_count = _count_words(trimmed_summary)
        logger.warning(
            "Summary remained above target range after rewrite fallback; trimmed from %s to %s words.",
            actual_words,
            trimmed_count,
        )
        return trimmed_summary

    logger.warning(
        "Summary remained below target range after rewrite fallback (got %s words; target %s±%s).",
        actual_words,
        target_length,
        tolerance,
    )
    return summary_text


def _generate_summary_with_length_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
) -> str:
    return _generate_summary_with_quality_control(gemini_client, base_prompt, target_length, None)


def _generate_summary_with_quality_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
) -> str:
    """
    Call Gemini up to MAX_SUMMARY_ATTEMPTS times, tightening instructions if word count or quality drifts.
    """
    corrections: List[str] = []
    prompt = base_prompt
    previous_draft: Optional[str] = None
    summary_text: str = ""
    last_word_stats: Optional[Tuple[int, int]] = None  # (actual_words, tolerance)

    def _rebuild_prompt() -> str:
        correction_block = ("\n\n".join(corrections)) if corrections else ""
        previous_block = (
            f"\n\nPrevious draft (for reference, do not copy verbatim):\n{previous_draft}\n"
            if previous_draft
            else ""
        )
        combined = base_prompt
        if correction_block:
            combined += "\n\n" + correction_block
        combined += previous_block
        combined += "\n\nRewrite the entire memo applying every instruction above."
        return combined

    for attempt in range(1, MAX_SUMMARY_ATTEMPTS + 1):
        response = gemini_client.model.generate_content(prompt)
        raw_text = response.text
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
            action = "CONDENSE the text immediately. Remove filler words and be more direct."
        else:
            action = (
                f"EXPAND the content immediately. You are {abs_diff} words short. "
                "1. Add 3-4 sentences of detailed analysis to 'Financial Performance'. "
                "2. Add 2-3 sentences to 'Management Discussion & Analysis'. "
                "3. Add 2-3 sentences to 'Strategic Initiatives'. "
                "Elaborate on the implications of every metric and risk."
            )

        corrections.append(
            f"LENGTH CORRECTION #{attempt}: Your last draft contained {prior_count} words, but the required range is "
            f"{target_length - tolerance}–{target_length + tolerance} words (target {target_length}). \n"
            f"You are {abs_diff} words {'OVER' if diff > 0 else 'SHORT'}. \n"
            f"ACTION: {action}\n"
            f"Recount while drafting and stop writing the moment you reach that window. If the next draft is outside the range, "
            "it will be rejected. Update the 'WORD COUNT: ###' control line to reflect the exact total."
        )
        prompt = _rebuild_prompt()

    if target_length and summary_text:
        summary_text = _enforce_length_constraints(
            summary_text,
            target_length,
            gemini_client,
            quality_validators,
            last_word_stats,
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
    ("Financial Health Rating", 35),
    ("Executive Summary", 45),
    ("Financial Performance", 60),
    ("Management Discussion & Analysis", 60),
    ("Risk Factors", 30),
    ("Strategic Initiatives & Capital Allocation", 45),
    ("Key Metrics Dashboard", 20),
]
SUMMARY_SECTION_MIN_WORDS = {title: minimum for title, minimum in SUMMARY_SECTION_REQUIREMENTS}

RATING_SCALE = [
    (90, "A+", "Exceptional"),
    (80, "A", "Strong Buy"),
    (70, "A-", "Outperform"),
    (60, "B+", "Accumulate"),
    (50, "B", "Market Perform"),
    (40, "C+", "Hold"),
    (30, "C", "Watchlist"),
    (0, "D", "High Risk"),
]


def _make_section_completeness_validator(include_health_rating: bool):
    required_titles = [
        title for title in SUMMARY_SECTION_REQUIREMENTS if title[0] != "Financial Health Rating"
    ]
    if include_health_rating:
        required_titles = SUMMARY_SECTION_REQUIREMENTS

    ordered_titles = [title for title, _ in required_titles]

    def _validator(text: str) -> Optional[str]:
        lower_text = text.lower()
        search_start = 0
        for idx, title in enumerate(ordered_titles):
            target = title.lower()
            heading_token = f"## {target}"
            match_index = lower_text.find(heading_token, search_start)
            if match_index == -1:
                return (
                    f"Missing the heading '## {title}'. Use that exact markdown heading (no prefixes) and include substantive content beneath it."
                )
            section_start = match_index + len(heading_token)
            next_section_index = len(text)
            for future_title in ordered_titles[idx + 1 :]:
                future_pos = lower_text.find(f"## {future_title.lower()}", section_start)
                if future_pos != -1:
                    next_section_index = future_pos
                    break
            section_body = text[section_start:next_section_index].strip()
            word_count = len(re.findall(r"\b\w+\b", section_body))
            min_words = SUMMARY_SECTION_MIN_WORDS.get(title, 25)
            if word_count < min_words:
                return (
                    f"The '{title}' section is too brief ({word_count} words). Expand it to at least {min_words} words "
                    "and ensure it concludes on a full sentence."
                )
            search_start = section_start
        return None

    return _validator

def _build_preference_instructions(
    preferences: Optional[FilingSummaryPreferences],
    company_name: Optional[str] = None,
) -> str:
    """Convert user-provided preferences into prompt guidance."""
    if not preferences or preferences.mode == "default":
        return "- Use the standard structure below with a balanced, neutral tone suitable for institutional investors."

    instructions: List[str] = ["- Absolute priority: satisfy the investor's custom brief before any boilerplate."]

    investor_focus = preferences.investor_focus.strip() if preferences.investor_focus else None
    if investor_focus:
        focus_clause = (
            f"{investor_focus} as it relates to {company_name}" if company_name else investor_focus
        )
        instructions.append(
            f"- Investor brief (absolute priority): {focus_clause}. Mirror this persona's diction, risk tolerance, and valuation discipline throughout."
        )
        instructions.append(
            "- Begin the memo with a labeled 'Investor Lens' paragraph that restates this brief and explains how the filing will be evaluated through it."
        )
        instructions.append(
            "- In both the Investor Lens and Executive Summary sections, explicitly reference this persona by name (or title) and show how each takeaway maps to their checklist."
        )
        instructions.append(
            "- In every major section, include at least one sentence explaining why the content matters to this investor profile before citing generic takeaways."
        )
    else:
        instructions.append(
            "- No persona name was provided, but treat the investor brief text as the governing viewpoint and reference it explicitly in the Investor Lens and Executive Summary."
        )

    if preferences.focus_areas:
        joined = ", ".join(preferences.focus_areas)
        instructions.append(
            f"- Primary focus areas (cover strictly in this order, dedicating at least one labeled paragraph or subsection to each): {joined}."
        )
        instructions.append(
            "- Do not introduce unrelated themes unless they reinforce the requested focus areas; if information is missing, explicitly state the gap and why."
        )
        ordered_lines = "\n".join(f"   {idx + 1}. {area}" for idx, area in enumerate(preferences.focus_areas))
        instructions.append("  Focus area execution order:\n" + ordered_lines)

    if preferences.tone:
        instructions.append(f"- Tone must remain {preferences.tone.lower()} throughout.")

    detail_prompt = DETAIL_LEVEL_PROMPTS.get((preferences.detail_level or "").lower())
    if detail_prompt:
        instructions.append(f"- Detail expectation: {detail_prompt}")

    output_prompt = OUTPUT_STYLE_PROMPTS.get((preferences.output_style or "").lower())
    if output_prompt:
        instructions.append(f"- Output style: {output_prompt}")

    complexity_prompt = COMPLEXITY_LEVEL_PROMPTS.get((preferences.complexity or "intermediate").lower())
    if complexity_prompt:
        instructions.append(f"- Complexity: {complexity_prompt}")

    target_length = _clamp_target_length(preferences.target_length)
    if target_length:
        min_words = target_length - 10
        max_words = target_length + 10
        instructions.append(
            f"""
CRITICAL LENGTH CONSTRAINT:
The total output MUST be between {min_words} and {max_words} words.
This is a HARD REQUIREMENT. Do not write less than {min_words} words. Do not write more than {max_words} words.
Check your word count before finishing.
"""
        )
        if target_length > 450:
            instructions.append(
                "- To meet this high word count, you MUST provide extensive detail, historical context, and deep analysis in every section. "
                "Do NOT be concise. Elaborate on every point."
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
    for key in ("framework", "primary_factor_weighting", "risk_tolerance", "analysis_depth", "display_style"):
        value = pref_data.get(key)
        if value:
            config[key] = value
    return config


def _build_health_rating_instructions(
    preferences: Optional[FilingSummaryPreferences],
    company_name: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    config = _resolve_health_rating_config(preferences)
    if not config:
        return None, None

    directives = [
        f"- Produce a Financial Health Rating for {company_name} on a 0–100 scale (100 = exceptional strength).",
        "- Cite at least three concrete metrics (profitability, cash flow, leverage, liquidity, or execution) that justify the score.",
        "- Mention the single most important risk or catalyst that pushed the score higher or lower.",
    ]

    framework_prompt = HEALTH_FRAMEWORK_PROMPTS.get(config.get("framework"))
    if framework_prompt:
        directives.append(f"- Framework: {framework_prompt}")

    weighting_prompt = HEALTH_WEIGHTING_PROMPTS.get(config.get("primary_factor_weighting"))
    if weighting_prompt:
        directives.append(f"- Primary factor weighting: {weighting_prompt}")

    risk_prompt = HEALTH_RISK_PROMPTS.get(config.get("risk_tolerance"))
    if risk_prompt:
        directives.append(f"- Risk tolerance: {risk_prompt}")

    depth_prompt = HEALTH_ANALYSIS_DEPTH_PROMPTS.get(config.get("analysis_depth"))
    if depth_prompt:
        directives.append(f"- Analysis depth: {depth_prompt}")

    display_prompt = HEALTH_DISPLAY_PROMPTS.get(config.get("display_style"))
    if display_prompt:
        directives.append(f"- Output format: {display_prompt}")
        if config.get("display_style") == "score_only":
            directives.append("- Do not append letter grades, colors, pillar labels, or narrative badges; output only the 0–100 score and the supporting explanation.")

    directives.append("- Present the Financial Health Rating as the first section before the Executive Summary.")
    directives.append("- ALSO include the 'Financial Health Rating: X/100' line in the 'Key Metrics/Others' section at the end of the memo.")

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
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    # Remove HTML tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Unescape HTML entities
    cleaned = unescape(cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\\s+", " ", cleaned)
    return cleaned.strip()


def _extract_section(text: str, start_marker: str, end_markers: List[str]) -> str:
    """Extract a section from text bounded by start and optional end markers."""
    upper_text = text.upper()
    start_upper = start_marker.upper()
    start_idx = upper_text.find(start_upper)
    if start_idx == -1:
        return ""

    end_idx = len(text)
    for marker in end_markers:
        marker_upper = marker.upper()
        candidate = upper_text.find(marker_upper, start_idx + len(start_upper))
        if candidate != -1 and candidate < end_idx:
            end_idx = candidate

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
    for start, ends, header in [
        ("ITEM 1. BUSINESS", ["ITEM 1A.", "ITEM 1B."], "BUSINESS OVERVIEW"),
        ("ITEM 1A. RISK FACTORS", ["ITEM 1B.", "ITEM 2."], "RISK FACTORS"),
        ("ITEM 7. MANAGEMENT'S DISCUSSION", ["ITEM 7A.", "ITEM 8."], "MANAGEMENT DISCUSSION & ANALYSIS"),
        ("MANAGEMENT'S DISCUSSION AND ANALYSIS", ["ITEM 7A.", "ITEM 8."], "MANAGEMENT DISCUSSION & ANALYSIS"),
        ("ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK", ["ITEM 8."], "MARKET RISK"),
        ("ITEM 8. FINANCIAL STATEMENTS", ["ITEM 9.", "ITEM 9A."], "FINANCIAL STATEMENTS"),
    ]:
        section = _extract_section(text, start, ends)
        if section:
            sections.append(f"{header}\n{section}")

    combined = "\n\n".join(sections) if sections else text
    if limit is None:
        return combined
    return combined[:limit]


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
        sorted_entries = sorted(line_item.items(), key=lambda itm: str(itm[0]), reverse=True)
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

    revenue = _extract_latest_numeric(income_statement.get("totalRevenue") or income_statement.get("Revenue"))
    operating_income = _extract_latest_numeric(income_statement.get("OperatingIncomeLoss") or income_statement.get("OperatingIncome"))
    net_income = _extract_latest_numeric(income_statement.get("NetIncomeLoss") or income_statement.get("NetIncome"))
    eps = _extract_latest_numeric(income_statement.get("DilutedEPS"))

    total_assets = _extract_latest_numeric(balance_sheet.get("TotalAssets"))
    total_liabilities = _extract_latest_numeric(balance_sheet.get("TotalLiabilities"))
    cash = _extract_latest_numeric(balance_sheet.get("CashAndCashEquivalentsAtCarryingValue") or balance_sheet.get("CashAndCashEquivalents"))

    operating_cash_flow = _extract_latest_numeric(cash_flow.get("NetCashProvidedByUsedInOperatingActivities"))
    capex = _extract_latest_numeric(cash_flow.get("PaymentsToAcquirePropertyPlantAndEquipment"))
    free_cash_flow = (
        operating_cash_flow - capex if operating_cash_flow is not None and capex is not None else None
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


def _build_calculated_metrics(statements: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Derive key metrics from financial statements for AI guidance."""
    if not statements or not isinstance(statements, dict):
        return {}

    data = statements.get("statements") or {}

    income_statement = data.get("income_statement", {})
    balance_sheet = data.get("balance_sheet", {})
    cash_flow = data.get("cash_flow", {})

    def _extract_from_candidates(source: Dict[str, Any], candidates: List[str]) -> Optional[float]:
        for key in candidates:
            value = source.get(key)
            result = _extract_latest_numeric(value)
            if result is not None:
                return result
        return None

    revenue = _extract_from_candidates(
        income_statement,
        [
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
        ["NetIncomeLoss", "NetIncome", "netIncome", "netIncomeLoss", "NetIncomeApplicableToCommonShares"],
    )
    operating_income = _extract_from_candidates(
        income_statement,
        ["OperatingIncomeLoss", "OperatingIncome", "operatingIncome", "OperatingIncomeLossUSD"],
    )
    eps = _extract_from_candidates(
        income_statement,
        ["DilutedEPS", "dilutedEPS", "EPSDiluted", "epsDiluted"],
    )

    operating_cash_flow = _extract_from_candidates(
        cash_flow,
        [
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
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CapitalExpenditures",
            "capitalExpenditures",
            "CapitalExpenditure",
            "PurchaseOfPPE",
        ],
    )
    capex = abs(capex_raw) if capex_raw is not None else None
    free_cash_flow = (
        operating_cash_flow - capex if operating_cash_flow is not None and capex is not None else None
    )

    cash = _extract_from_candidates(
        balance_sheet,
        [
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
        ["TotalAssets", "totalAssets", "TotalAssetsUSD"],
    )
    total_liabilities = _extract_from_candidates(
        balance_sheet,
        ["TotalLiabilities", "totalLiabilities", "TotalLiabilitiesNetMinorityInterest"],
    )

    operating_margin = (
        (operating_income / revenue) * 100 if operating_income is not None and revenue else None
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
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "dividends_paid": dividends_paid,
        "share_repurchases": share_repurchases,
    }

    return {key: value for key, value in metrics.items() if value is not None}


def _format_metric_value(key: str, value: float) -> str:
    if key == "diluted_eps":
        return f"${value:.2f}"
    if key in {"operating_margin", "net_margin"}:
        return f"{value:.1f}%"
    return _format_dollar(value) or f"{value:,.2f}"


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

    def _resolve_from_fallback() -> Dict[str, Any]:
        filing = fallback_filings_by_id.get(filing_key)
        if not filing:
            raise HTTPException(status_code=404, detail="Filing not found")

        company_id = str(filing.get("company_id"))
        company = fallback_companies.get(company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found for filing")

        return {
            "filing": filing,
            "company": company,
            "source": "fallback",
        }

    if not _supabase_configured(settings):
        return _resolve_from_fallback()

    supabase = get_supabase_client()

    try:
        filing_response = supabase.table("filings").select("*").eq("id", filing_key).execute()
        if not filing_response.data:
            raise HTTPException(status_code=404, detail="Filing not found")

        filing = filing_response.data[0]
        company_id = filing.get("company_id")

        company_response = (
            supabase.table("companies")
            .select("id, ticker, exchange, cik")
            .eq("id", company_id)
            .execute()
        )
        if not company_response.data:
            raise HTTPException(status_code=404, detail="Company not found for filing")

        company = company_response.data[0]

        return {
            "filing": filing,
            "company": company,
            "source": "supabase",
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_supabase_table_missing_error(exc):
            return _resolve_from_fallback()
        raise HTTPException(status_code=500, detail=f"Error resolving filing context: {exc}")


def _fetch_eodhd_document(ticker: str, exchange: Optional[str] = None, filter_param: Optional[str] = None) -> Dict[str, Any]:
    client = get_eodhd_client()
    exchange_code = (exchange or "US") or "US"
    return client.get_fundamentals(ticker, exchange=exchange_code, filter_param=filter_param)


def _ensure_storage_dir(settings) -> Path:
    storage_dir = Path(settings.data_dir).expanduser().resolve() / "filings"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _build_local_document_path(storage_dir: Path, filing_id: str) -> Path:
    return storage_dir / f"{filing_id}.html"


def _ensure_local_document(context: Dict[str, Any], settings) -> Optional[Path]:
    filing = context["filing"]
    company = context["company"]
    storage_dir = _ensure_storage_dir(settings)

    existing_path = filing.get("local_document_path")
    if existing_path:
        path_obj = Path(existing_path)
        if path_obj.exists():
            return path_obj

    filing_id = filing.get("id")
    filing_id_str = str(filing_id)
    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")

    source_doc_url = filing.get("source_doc_url")

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

                    if candidate.get("filing_date") == filing_date or candidate.get("period_end") == filing_date:
                        source_doc_url = candidate.get("url")
                        filing["source_doc_url"] = source_doc_url
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
            return target_path
    except Exception as download_exc:  # noqa: BLE001
        logger.warning(
            "Failed to download SEC filing %s: %s",
            source_doc_url,
            download_exc,
        )

    return None


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

    fallback_companies[company_key] = company
    save_fallback_companies()

    ticker = company.get("ticker")
    if not ticker:
        raise HTTPException(status_code=400, detail="Company is missing a ticker symbol")

    entries_to_ingest: List[Dict[str, Any]] = []

    try:
        financial_data = get_eodhd_client().get_financial_statements(ticker, exchange="US")
        eodhd_url = f"https://eodhd.com/api/fundamentals/{ticker}.US"

        quarterly_income = financial_data.get("income_statement", {}).get("quarterly", {})
        for date_str, statement in quarterly_income.items():
            entries_to_ingest.append(
                {
                    "filing_type": "10-Q",
                    "date_str": date_str,
                    "income_statement": statement,
                    "balance_sheet": financial_data.get("balance_sheet", {}).get("quarterly", {}).get(date_str, {}),
                    "cash_flow": financial_data.get("cash_flow", {}).get("quarterly", {}).get(date_str, {}),
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
                    "balance_sheet": financial_data.get("balance_sheet", {}).get("yearly", {}).get(date_str, {}),
                    "cash_flow": financial_data.get("cash_flow", {}).get("yearly", {}).get(date_str, {}),
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
        logger.warning("No sample filings available for %s; continuing with empty dataset.", ticker)

    cutoff_date = None
    if request.max_history_years:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=365 * request.max_history_years)

    company_filings = fallback_filings.setdefault(company_key, [])
    existing_pairs = {(filing["filing_type"], filing["filing_date"]) for filing in company_filings}
    saved_count = 0

    for existing in company_filings:
        fallback_filings_by_id.setdefault(str(existing["id"]), existing)

    storage_dir = _ensure_storage_dir(settings)
    sec_filings_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    cik_value = company.get("cik")
    ticker_symbol = company.get("ticker")

    if (not cik_value or not str(cik_value).isdigit()) and ticker_symbol:
        try:
            general_info = get_eodhd_client().get_company_info(ticker_symbol, exchange=company.get("exchange") or "US")
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
        cik_digits = ''.join(ch for ch in cik_value if ch.isdigit())
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
                    sec_filings_map[(filing_type_value, filing_date_value, "filing_date")] = entry
                if filing_type_value and period_end_value:
                    sec_filings_map[(filing_type_value, period_end_value, "period_end")] = entry
        except Exception as sec_exc:  # noqa: BLE001
            logger.warning(
                "Unable to retrieve SEC filings for CIK %s: %s",
                cik_value,
                sec_exc,
            )
    else:
        logger.warning("CIK not available for company %s; SEC document download skipped", company_key)

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
        return await _start_fetch_with_fallback_company(company_key, company, request, settings)

    supabase = get_supabase_client()
    
    # Verify company exists
    try:
        company_response = supabase.table("companies").select("*").eq("id", str(request.company_id)).execute()
        if not company_response.data:
            raise HTTPException(status_code=404, detail="Company not found")
        
        company = company_response.data[0]
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
        raise HTTPException(status_code=500, detail=f"Error verifying company: {str(e)}")
    
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

    if not raw and local_exists:
        return RedirectResponse(url=f"/api/{settings.api_version}/filings/{filing_id}/document?raw=1")

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
        raise HTTPException(status_code=502, detail="Unable to retrieve filing document from provider")


@router.get("/{filing_id}", response_model=Filing)
async def get_filing(filing_id: str):
    """Get filing details by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filing = fallback_filings_by_id.get(filing_id) or fallback_filings_by_id.get(str(filing_id))
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
            filing = fallback_filings_by_id.get(filing_id) or fallback_filings_by_id.get(str(filing_id))
            if filing:
                return _prepare_filing_response(filing, settings)
            raise HTTPException(status_code=404, detail="Filing not found (Supabase tables missing and no cached filing).")
        raise HTTPException(status_code=500, detail=f"Error retrieving filing: {str(e)}")


@router.get("/company/{company_id}", response_model=List[Filing])
async def list_company_filings(
    company_id: str,
    filing_type: str = None,
    limit: int = 50,
    offset: int = 0
):
    """List filings for a specific company."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filings = fallback_filings.get(company_id, [])
        if filing_type:
            filings = [filing for filing in filings if filing["filing_type"] == filing_type]
        sliced = filings[offset:offset + limit]
        return [_prepare_filing_response(filing, settings) for filing in sliced]

    supabase = get_supabase_client()
    
    try:
        query = supabase.table("filings").select("*").eq("company_id", company_id)
        
        if filing_type:
            query = query.eq("filing_type", filing_type)
        
        response = query.order("filing_date", desc=True).range(offset, offset + limit - 1).execute()
        
        return [_prepare_filing_response(filing, settings) for filing in response.data]
    
    except Exception as e:
        if is_supabase_table_missing_error(e):
            filings = fallback_filings.get(company_id, [])
            if filing_type:
                filings = [filing for filing in filings if filing["filing_type"] == filing_type]
            sliced = filings[offset:offset + limit]
            return [_prepare_filing_response(filing, settings) for filing in sliced]
        raise HTTPException(status_code=500, detail=f"Error listing filings: {str(e)}")


@router.post("/{filing_id}/summary")
async def generate_filing_summary(
    filing_id: str,
    preferences: Optional[FilingSummaryPreferences] = Body(default=None),
):
    """
    Returns cached summary if already generated.
    """
    settings = get_settings()
    preferences = preferences or FilingSummaryPreferences()
    target_length = _clamp_target_length(preferences.target_length)
    use_default_cache = preferences.mode == "default"
    include_health_rating = bool(preferences.health_rating and preferences.health_rating.enabled)

    # Reset progress
    progress_cache[str(filing_id)] = "Initializing AI Agent..."

    # Check cache first
    if use_default_cache:
        cached_summary = fallback_filing_summaries.get(str(filing_id))
        if cached_summary:
            progress_cache[str(filing_id)] = "Complete"
            return JSONResponse(content={"filing_id": filing_id, "summary": cached_summary, "cached": True})
    
    # Get filing context
    try:
        progress_cache[str(filing_id)] = "Reading Filing Content..."
        context = _resolve_filing_context(filing_id, settings)
        filing = context["filing"]
        company = context["company"]
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
                logger.warning("Unable to load Supabase financial statements for %s: %s", filing_id, stmt_exc)

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
                document_text = json.dumps(jsonable_encoder({"statements": statements}), indent=2)
        else:
            raise HTTPException(status_code=400, detail="No document content available for summarization")
    
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
        
        filing_type = filing.get("filing_type", "")
        filing_date = filing.get("filing_date", "")
        company_name = company.get("name", company.get("ticker", "Unknown"))
        
        financial_snapshot = _build_financial_snapshot(statements)
        calculated_metrics = _build_calculated_metrics(statements)
        progress_cache[str(filing_id)] = "Analyzing Risk Factors..."
        metrics_lines = "\n".join(
            f"- {label}: {_format_metric_value(key, calculated_metrics[key])}"
            for key, label in [
                ("revenue", "Revenue"),
                ("operating_income", "Operating Income"),
                ("net_income", "Net Income"),
                ("diluted_eps", "Diluted EPS"),
                ("operating_cash_flow", "Operating Cash Flow"),
                ("capital_expenditures", "Capital Expenditures"),
                ("free_cash_flow", "Free Cash Flow"),
                ("cash", "Cash"),
                ("marketable_securities", "Marketable Securities"),
                ("total_assets", "Total Assets"),
                ("total_liabilities", "Total Liabilities"),
                ("operating_margin", "Operating Margin"),
                ("net_margin", "Net Margin"),
                ("dividends_paid", "Dividends Paid"),
                ("share_repurchases", "Share Repurchases"),
            ]
            if key in calculated_metrics
        ) or "- No structured metrics extracted; rely on filing text."
        total_liquidity = None
        if "cash" in calculated_metrics or "marketable_securities" in calculated_metrics:
            cash_val = calculated_metrics.get("cash") or 0
            securities_val = calculated_metrics.get("marketable_securities") or 0
            total_liquidity = cash_val + securities_val
            formatted_liquidity = _format_dollar(total_liquidity) or f"${total_liquidity:,.2f}"
            metrics_lines += f"\n- Liquidity (Cash + Marketable Securities): {formatted_liquidity}"
        context_excerpt = (
            document_text
            if len(document_text) <= MAX_GEMINI_CONTEXT_CHARS
            else document_text[:MAX_GEMINI_CONTEXT_CHARS]
        )
        truncated_note = "" if len(context_excerpt) == len(document_text) else "\n\nNote: Filing text truncated to fit model context."
        company_label = company.get("name") or company.get("ticker") or "the company"
        preference_block = _build_preference_instructions(preferences, company_label)
        
        if include_health_rating:
             progress_cache[str(filing_id)] = "Computing Health Score..."
        
        health_config, health_rating_block = _build_health_rating_instructions(preferences, company_label)
        health_directives_section = ""
        if health_rating_block:
            health_directives_section = f"\n HEALTH RATING DIRECTIVES\n {health_rating_block}\n"
        section_descriptions: List[Tuple[str, str]] = []
        if health_rating_block:
            section_descriptions.append(
                (
                    "Financial Health Rating",
                    "Provide the configured 0–100 score plus the requested visualization (letter grade, traffic light, or pillar breakdown). "
                    "Explain why the score landed there and cite the exact metrics (margins, cash flow, leverage, liquidity, execution) driving it.",
                )
            )
        section_descriptions.extend(
            [
                (
                    "Executive Summary",
                    "Two short paragraphs addressing company-specific highlights, growth drivers, profitability, and cash generation. Tie each point back to the investor's stated focus.",
                ),
                (
                    "Financial Performance",
                    "Narrative explanation using actual figures (revenue, margins, EPS, balance sheet strength, cash flow) from the metrics above or the filing text. Compute missing numbers (e.g., free cash flow) when inputs are supplied.",
                ),
                (
                    "Management Discussion & Analysis",
                    "Detailed rundown of strategy, competitive position, management priorities, and MD&A highlights. If the excerpt lacks direct quotes, infer management’s likely emphasis based on the provided metrics and initiatives; never claim information is missing.",
                ),
                (
                    "Risk Factors",
                    "Bullet list of the top 5 company-specific risks mentioned in the filing.",
                ),
                (
                    "Strategic Initiatives & Capital Allocation",
                    "Paragraph-style discussion of investments, acquisitions, buybacks, and R&D priorities, explaining how each decision aligns with investor expectations.",
                ),
                (
                    "Key Metrics Dashboard",
                    "Bullet list that enumerates every metric from the calculated metrics block with its corresponding value; if a metric is unavailable, state why.",
                ),
            ]
        )
        section_requirements = "\n".join(
            f"## {title}\n{description}" for title, description in section_descriptions
        )
        
        tone = preferences.tone or "neutral"
        detail_level = preferences.detail_level or "comprehensive"
        output_style = preferences.output_style or "paragraph"

        base_prompt = f"""
You are an expert financial analyst writing a briefing for {tone} investors.
Analyze the following filing for {company_name} ({filing_type}, {filing_date}).

CONTEXT:
{context_excerpt}{truncated_note}

FINANCIAL SNAPSHOT (Use these numbers if not found in text):
{financial_snapshot}

KEY METRICS (Use these for calculations):
{metrics_lines}

INSTRUCTIONS:
1. Tone: {tone.title()}
2. Detail Level: {detail_level.title()}
3. Output Style: {output_style.title()}
4. Target Length: {target_length} words (approx)

STRUCTURE & CONTENT REQUIREMENTS:
{section_requirements}
{health_directives_section}
{preference_block}

CRITICAL RULES:
- Do NOT use markdown bolding (**) within the text body. Only use it for section headers if needed.
- Ensure every claim is backed by the provided text or metrics.
- If data is missing, state "not disclosed" rather than hallucinating.
"""
        progress_cache[str(filing_id)] = "Synthesizing Investor Insights..."
        summary_text = _generate_summary_with_quality_control(
            gemini_client,
            base_prompt,
            target_length=target_length,
            quality_validators=[
                _make_section_completeness_validator(include_health_rating)
            ],
        )
        
        progress_cache[str(filing_id)] = "Polishing Output..."
        # Post-processing to ensure structure
        summary_text = _normalize_section_headings(summary_text, include_health_rating)
        summary_text = _ensure_required_sections(
            summary_text,
            include_health_rating=include_health_rating,
            metrics_lines=metrics_lines,
            calculated_metrics=calculated_metrics,
            company_name=company_name,
            health_rating_config=health_config,
        )

        # Cache result
        if use_default_cache:
            fallback_filing_summaries[str(filing_id)] = summary_text
        return JSONResponse(content={
            "filing_id": filing_id,
            "summary": summary_text,
            "cached": False
        })
        
    except Exception as gemini_exc:
        logger.exception(f"Gemini summarization error for filing {filing_id}: {gemini_exc}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {gemini_exc}")


@router.post("/{filing_id}/parse")
async def parse_filing(filing_id: str):
    """
    Initiate background task to parse a filing.
    Returns a task ID for tracking progress.
    """
    from app.tasks.parse import parse_document_task
    
    settings = get_settings()

    if not _supabase_configured(settings):
        raise HTTPException(status_code=404, detail="Filings not available without Supabase configuration")

    supabase = get_supabase_client()
    
    # Verify filing exists
    try:
        filing_response = supabase.table("filings").select("*").eq("id", filing_id).execute()
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
            "progress": 0
        }
        supabase.table("task_status").insert(task_data).execute()
        
        return {
            "task_id": task.id,
            "message": f"Started parsing filing {filing_id}"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting parse task: {str(e)}")

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


def _normalize_section_headings(text: str, include_health_rating: bool) -> str:
    """Ensure each required section begins with the expected markdown heading."""
    required_titles = [
        title for title, _ in SUMMARY_SECTION_REQUIREMENTS if title != "Financial Health Rating"
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
                (heading for heading in required_titles if next_line.lower().startswith(heading.lower())),
                None,
            )
            if target_match:
                line = f"## {target_match}"
                idx += 1
        normalized_lines.append(line)
        idx += 1

    normalized_text = "\n".join(normalized_lines)
    for title in required_titles:
        pattern = re.compile(rf"(^|\n)\s*(?:##\s*)?{re.escape(title)}\b", re.IGNORECASE)
        normalized_text = pattern.sub(lambda _: f"\n## {title}", normalized_text, count=1)
    return normalized_text


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
    if value is None:
        return "data unavailable"
    return _format_dollar(value) or f"{value:,.2f}"


def _ensure_required_sections(
    summary_text: str,
    *,
    include_health_rating: bool,
    metrics_lines: str,
    calculated_metrics: Dict[str, Any],
    company_name: str,
    health_rating_config: Optional[Dict[str, Any]] = None,
) -> str:
    text = summary_text

    def _section_present(title: str) -> bool:
        return f"## {title}" in text

    def _append_section(title: str, body: str) -> None:
        nonlocal text
        text = text.rstrip() + f"\n\n## {title}\n{body.strip()}\n"

    # 1. Ensure Financial Health Rating section exists
    health_score_val = None
    health_grade_val = None
    
    if include_health_rating:
        # Try to find existing score in the text first
        # Look for "Financial Health Rating: 85/100" or similar patterns
        score_match = re.search(r"Financial Health Rating[:\s]+(\d{1,3})", text, re.IGNORECASE)
        if score_match:
            health_score_val = float(score_match.group(1))
            grade, _ = _score_to_grade(health_score_val)
            health_grade_val = grade
        
        if not _section_present("Financial Health Rating"):
            # Fallback generation if missing
            if health_score_val is None:
                health_score_val = _estimate_health_score(calculated_metrics)
                health_grade_val, label = _score_to_grade(health_score_val)
            else:
                _, label = _score_to_grade(health_score_val)

            display_pref = (health_rating_config or {}).get("display_style", "score_plus_grade")
            include_grade = display_pref != "score_only"
            
            # Helper to get metric from calculated or extract from text
            def _get_metric(key: str, pattern: str) -> str:
                val = calculated_metrics.get(key)
                if val is not None:
                    return _format_number_or_default(val)
                # Try extraction
                match = re.search(pattern, text, re.IGNORECASE)
                return match.group(1) if match else "data unavailable"

            cash_str = _get_metric("cash", r"cash position of\s+([$€£¥]?\d+(?:,\d{3})*(?:\.\d+)?[MB]?)")
            liabilities_str = _get_metric("total_liabilities", r"total liabilities of\s+([$€£¥]?\d+(?:,\d{3})*(?:\.\d+)?[MB]?)")
            fcf_str = _get_metric("free_cash_flow", r"free cash flow of\s+([$€£¥]?\d+(?:,\d{3})*(?:\.\d+)?[MB]?)")
            
            body = f"{company_name} receives a Financial Health Rating of {health_score_val:.0f}/100"
            if include_grade and health_grade_val:
                body += f" ({health_grade_val})"
            body += (
                f". {label if include_grade else 'Financial'} fundamentals are supported by free cash flow of {fcf_str}, "
                f"a cash position of {cash_str}, and total liabilities of {liabilities_str}. Maintain disciplined capital deployment "
                "and monitor leverage trends."
            )
            _append_section("Financial Health Rating", body)

    # 2. Ensure other required sections
    if not _section_present("Executive Summary"):
        revenue = _format_number_or_default(calculated_metrics.get("revenue"))
        net_income = _format_number_or_default(calculated_metrics.get("net_income"))
        fcf_str = _format_number_or_default(calculated_metrics.get("free_cash_flow"))
        body = (
            f"{company_name} reported revenue of {revenue} with net income of {net_income}. "
            f"Free cash flow of {fcf_str} highlights the company's ability to fund growth and shareholder returns."
        )
        _append_section("Executive Summary", body)

    if not _section_present("Financial Performance"):
        ocf_str = _format_number_or_default(calculated_metrics.get("operating_cash_flow"))
        capex_str = _format_number_or_default(calculated_metrics.get("capital_expenditures"))
        margin = calculated_metrics.get("operating_margin")
        margin_text = f"{margin:.1f}%" if margin is not None else "healthy margins"
        body = (
            f"Operating cash flow reached {ocf_str}, and capital expenditures were {capex_str}. "
            f"Operating margin held at {margin_text}. Liquidity is supported by cash of {_format_number_or_default(calculated_metrics.get('cash'))}."
        )
        _append_section("Financial Performance", body)

    if not _section_present("Management Discussion & Analysis"):
        body = (
            f"Management remains focused on scaling durable revenue streams while funding innovation. "
            f"Investment discipline, evidenced by {_format_number_or_default(calculated_metrics.get('capital_expenditures'))} in capex, "
            "supports long-term initiatives while maintaining operating leverage."
        )
        _append_section("Management Discussion & Analysis", body)

    if not _section_present("Risk Factors"):
        risk_points = [
            "Macroeconomic volatility could weigh on demand and advertising budgets.",
            "Competitive intensity in core and cloud markets requires continued product investment.",
            "Regulatory scrutiny around data privacy and antitrust enforcement remains elevated.",
            "Supply chain or infrastructure expansion delays could slow growth initiatives.",
            "Large-scale acquisitions or capital projects carry execution risk.",
        ]
        body = "\n".join(f"- {point}" for point in risk_points)
        _append_section("Risk Factors", body)

    if not _section_present("Strategic Initiatives & Capital Allocation"):
        ocf_str = _format_number_or_default(calculated_metrics.get("operating_cash_flow"))
        capex_str = _format_number_or_default(calculated_metrics.get("capital_expenditures"))
        fcf_str = _format_number_or_default(calculated_metrics.get("free_cash_flow"))
        body = (
            f"The company deploys operating cash flow of {ocf_str} toward {capex_str} of reinvestment while retaining "
            f"{fcf_str} in free cash flow. This supports buybacks, dividends, and targeted acquisitions without straining liquidity."
        )
        _append_section("Strategic Initiatives & Capital Allocation", body)

    # 3. Update Key Metrics Dashboard
    # Prepare the authoritative dashboard content
    dashboard_content = metrics_lines.strip() if metrics_lines.strip() else "- Metric data unavailable"
    
    # If health rating is enabled, ensure it's in the dashboard
    if include_health_rating and health_score_val is not None:
        # Check if it's already in the metrics lines (unlikely given current logic, but safe to check)
        if "Financial Health Rating" not in dashboard_content:
            rating_line = f"- Financial Health Rating: {health_score_val:.0f}/100"
            dashboard_content = f"{rating_line}\n{dashboard_content}"

    if not _section_present("Key Metrics Dashboard"):
        _append_section("Key Metrics Dashboard", dashboard_content)
    else:
        # Force update the Key Metrics Dashboard to ensure it's never truncated
        # and always contains the authoritative calculated metrics + health score
        pattern = re.compile(r"## Key Metrics Dashboard.*?(?=\n## |\Z)", re.DOTALL)
        text = pattern.sub(f"## Key Metrics Dashboard\n{dashboard_content}", text)

    return text
@router.get("/{filing_id}/progress")
async def get_filing_summary_progress(filing_id: str):
    """Get real-time progress of summary generation."""
    status = progress_cache.get(str(filing_id), "Initializing...")
    return {"status": status}
