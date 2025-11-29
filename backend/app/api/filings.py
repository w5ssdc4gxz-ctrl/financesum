"""Filings API endpoints."""
import json
import logging
import re
import string
import traceback
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
MAX_SUMMARY_ATTEMPTS = 12  # Increased to ensure length compliance
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


def _validate_complete_sentences(text: str) -> str:
    """Validate and fix incomplete sentences in the generated text.

    This function:
    1. Removes sentences that end with incomplete numbers (e.g., "revenue of $3.")
    2. Removes sentences that trail off with no verb or context
    3. Ensures each paragraph ends with proper punctuation
    4. Removes sentences that end mid-thought (e.g., "essential to determine if...")
    """
    if not text:
        return text

    lines = text.split('\n')
    validated_lines = []

    # Patterns for incomplete sentences
    incomplete_patterns = [
        # Number without unit at end: "revenue of $3." or "cash flow of $13.47"
        r'\$\d+(?:\.\d+)?\.?\s*$',
        # Trailing "of" or "at" with nothing after
        r'\s+(?:of|at|to|for|with)\s*[,.]?\s*$',
        # Sentence ending with comma or colon
        r'[,:]\s*$',
        # Blank amount placeholders
        r'(?:of|at|to)\s*,',
        # Sentences ending with "if", "whether", "that", "which" (incomplete thought)
        r'\s+(?:if|whether|that|which|when|where|how|what|why)\s*\.{0,3}\s*$',
        # Sentences ending with "to determine", "to assess", "to evaluate" (incomplete)
        r'\s+to\s+(?:determine|assess|evaluate|understand|analyze|see|know|find|verify|confirm)\s*\.{0,3}\s*$',
        # Sentences ending with "I need to", "I want to", "I would like to" (incomplete)
        r'I\s+(?:need|want|would like|have)\s+to\s*\.{0,3}\s*$',
        # Sentences ending with articles or prepositions
        r'\s+(?:the|a|an|in|on|at|by|for|with|from)\s*\.{0,3}\s*$',
        # Trailing ellipsis without prior complete sentence
        r'[^.!?]\s*\.{3}\s*$',
        # Sentences ending with semicolons followed by incomplete clause
        r';\s*(?:I|we|the|this|that|it)\s+\w*\s*\.{0,3}\s*$',
        # "My take is... I need to" pattern
        r'(?:my take is|I am|I\'m)\s+\w+[;,]\s*I\s+(?:need|want|have)\s+to\s*\.{0,3}\s*$',
    ]

    for line in lines:
        # Skip empty lines
        if not line.strip():
            validated_lines.append(line)
            continue

        # Check for incomplete sentence patterns
        is_incomplete = False
        for pattern in incomplete_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                is_incomplete = True
                # Try to fix by finding last complete sentence
                last_sentence = re.search(r'^(.*[.!?])\s*[^.!?]*$', line)
                if last_sentence:
                    line = last_sentence.group(1)
                    is_incomplete = False
                break

        if not is_incomplete:
            validated_lines.append(line)

    return '\n'.join(validated_lines)


def _truncate_text_to_word_limit(text: str, max_words: int) -> str:
    """Trim text so it contains at most `max_words` tokens while preserving original formatting."""
    if max_words <= 0:
        return ""

    matches = list(re.finditer(r"\b\w+\b", text))
    if len(matches) <= max_words:
        return text.rstrip()

    # Initial hard cutoff
    cutoff_index = matches[max_words - 1].end()
    truncated = text[:cutoff_index].rstrip()
    
    # Try to find the last sentence ending punctuation (.!?) within the last 15% of the text
    # This prevents cutting off in the middle of a sentence
    sentence_end = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
    
    if sentence_end != -1 and sentence_end > len(truncated) * 0.85:
        return truncated[:sentence_end + 1]
        
    # If no sentence end found nearby, try to cut at the last newline (paragraph)
    last_newline = truncated.rfind('\n')
    if last_newline != -1 and last_newline > len(truncated) * 0.85:
        return truncated[:last_newline]

    # If all else fails, append an ellipsis to indicate continuation is missing
    return truncated + "..."


def _build_padding_block(required_words: int) -> str:
    """DEPRECATED: Padding has been removed to prevent generic filler content.

    Returns empty string. Summaries should be complete from AI generation.
    If a summary is too short, it's better to accept the shorter length
    than to add generic filler that doesn't relate to the specific company.
    """
    return ""


def _trim_appendix_preserving_rows(body: str, max_words: int) -> str:
    """Trim Key Data Appendix body by removing rows from the bottom to avoid partial bullets."""
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


def _trim_preserving_headings(text: str, max_words: int) -> str:
    """
    Deterministically trim the memo while keeping every section heading present.
    This avoids chopping off the Key Data Appendix or other trailing sections.
    """
    heading_regex = re.compile(r"^\s*##\s+.+")
    sections: List[Tuple[str, str]] = []
    current_heading: Optional[str] = None
    buffer: List[str] = []

    for line in text.splitlines():
        if heading_regex.match(line):
            if current_heading is not None:
                sections.append((current_heading, "\n".join(buffer).strip()))
            current_heading = line.strip()
            buffer = []
        elif current_heading is not None:
            buffer.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(buffer).strip()))

    if not sections:
        return _truncate_text_to_word_limit(text, max_words)

    min_words_per_section = max(6, min(20, max_words // max(1, len(sections))))
    section_word_counts = [max(min_words_per_section, _count_words(body)) for _, body in sections]
    total_words = sum(section_word_counts)
    if total_words == 0:
        return _truncate_text_to_word_limit(text, max_words)

    appendix_index = next((i for i, (h, _) in enumerate(sections) if "key data appendix" in h.lower()), None)
    protected_words = section_word_counts[appendix_index] if appendix_index is not None else 0

    # Allocate budget prioritizing Key Data Appendix if present
    allocations = [0] * len(sections)
    remaining_budget = max_words

    if appendix_index is not None:
        allocations[appendix_index] = protected_words
        remaining_budget -= protected_words

    flexible_indices = [i for i in range(len(sections)) if i != appendix_index]
    flexible_total = sum(section_word_counts[i] for i in flexible_indices)

    if flexible_total == 0 and appendix_index is not None and allocations[appendix_index] > max_words:
        # Trim appendix itself if it's the only section and too long
        allocations[appendix_index] = max_words
    elif flexible_total > 0:
        flex_scale = min(1.0, max(0, remaining_budget) / flexible_total) if remaining_budget > 0 else 0
        for i in flexible_indices:
            allocations[i] = max(min_words_per_section, int(section_word_counts[i] * flex_scale))

    allocated_total = sum(allocations)
    # Adjust if we over- or under-allocated
    if allocated_total > max_words:
        overflow = allocated_total - max_words
        # Reduce from flexible sections first, leaving the appendix untouched if possible
        adjustable = [i for i in flexible_indices if allocations[i] > min_words_per_section]
        while overflow > 0 and adjustable:
            idx = adjustable[0]
            allocations[idx] -= 1
            overflow -= 1
            if allocations[idx] <= min_words_per_section:
                adjustable.pop(0)
    elif allocated_total < max_words and allocations:
        remaining = max_words - allocated_total
        idx = 0
        while remaining > 0:
            target_idx = flexible_indices[idx % len(flexible_indices)] if flexible_indices else idx % len(allocations)
            allocations[target_idx] += 1
            remaining -= 1
            idx += 1

    trimmed_sections = []
    for idx, ((heading, body), allowed) in enumerate(zip(sections, allocations)):
        if allowed <= 0:
            continue
        if appendix_index is not None and idx == appendix_index:
            trimmed_body = _trim_appendix_preserving_rows(body, allowed)
        else:
            trimmed_body = _truncate_text_to_word_limit(body, allowed)
        trimmed_sections.append(f"{heading}\n{trimmed_body}".rstrip())

    return "\n\n".join(trimmed_sections).strip()


def _finalize_length_band(summary_text: str, target_length: int, tolerance: int = 10) -> str:
    """
    Hard guardrail to guarantee the final text lands within the requested band,
    even if the model repeatedly ignores instructions.
    """
    if not summary_text or target_length is None:
        return summary_text

    lower = target_length - tolerance
    upper = target_length + tolerance
    word_count = _count_words(summary_text)

    if lower <= word_count <= upper:
        return summary_text

    # Over target: trim deterministically while keeping headings present
    if word_count > upper:
        trimmed = _trim_preserving_headings(summary_text, upper)
        trimmed_words = _count_words(trimmed)
        if trimmed_words < lower:
            trimmed = _truncate_text_to_word_limit(summary_text, lower)
            trimmed_words = _count_words(trimmed)
        if trimmed_words > upper:
            trimmed = _truncate_text_to_word_limit(trimmed, upper)
        if trimmed and not trimmed.rstrip().endswith((".", "!", "?")):
            trimmed = trimmed.rstrip() + "."
        return trimmed

    # Under target: append additional content seamlessly (no label)
    deficit = lower - word_count
    padding_block = _build_padding_block(deficit)
    base = summary_text.rstrip()
    if base and not base.endswith((".", "!", "?")):
        base += "."
    # Append padding without a label - it should flow naturally
    padded = f"{base} {padding_block}"
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
        padded += " " + _build_padding_block(shortfall)
        padded_words = _count_words(padded)
        if padded_words > upper:
            padded = _truncate_text_to_word_limit(padded, upper)
    return padded


def _force_final_band(summary_text: str, target_length: int, tolerance: int = 10) -> str:
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
        padding_block = _build_padding_block(deficit)
        if summary_text and not summary_text.rstrip().endswith((".", "!", "?")):
            summary_text = summary_text.rstrip() + "."
        # Append padding seamlessly without a label
        summary_text = f"{summary_text} {padding_block}"

    # Final safety net
    final_words = _count_words(summary_text)
    if final_words > upper:
        summary_text = _truncate_text_to_word_limit(summary_text, upper)
    elif final_words < lower:
        shortfall = lower - final_words
        padding_block = _build_padding_block(shortfall)
        summary_text = summary_text.rstrip()
        if summary_text and not summary_text.endswith((".", "!", "?")):
            summary_text += "."
        # Append padding seamlessly without a label
        summary_text = f"{summary_text} {padding_block}"
    return summary_text


def _needs_length_retry(text: str, target_length: int, cached_count: Optional[int] = None) -> Tuple[bool, int, int]:
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
    latest_words = current_words if current_words is not None else _count_words(working_draft)
    best_valid_draft = working_draft
    best_stats: Tuple[int, int] = (latest_words, tolerance)

    def _build_prompt() -> str:
        diff = latest_words - target_length
        abs_diff = abs(diff)
        
        if latest_words > upper:
            direction_instruction = (
                f"You are {abs_diff} words OVER the limit. \n"
                "ACTION: CONDENSE the text immediately. \n"
                f"1. CUT at least {int(abs_diff * 1.2)} words. Be aggressive.\n"
                "2. Keep the 'Key Data Appendix' but make it purely tabular/bulleted.\n"
                "3. Shorten the 'Risk Factors' and 'Strategic Initiatives' sections.\n"
                "4. Remove adjectives, adverbs, and filler words. Merge sentences.\n"
                "5. DO NOT append any new summary at the end. Just rewrite the existing sections to be shorter."
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
            "Strategic Initiatives & Capital Allocation, Key Data Appendix) unless they were absent in the draft. Do NOT drop sections to save space.\\n"
            "- Retain the key figures, personas, and conclusions.\n"
            "- Ensure each paragraph ends on a complete sentence; do not stop mid-thought.\n"
            "- ENSURE THE OUTPUT IS COMPLETE. Do not cut off the last section.\n"
            "- After rewriting, append a final line formatted exactly as `WORD COUNT: ###` (replace ### with the true count)."
            f"\n\nCRITICAL LENGTH CONSTRAINT:\nThe total output MUST be between {lower} and {upper} words. This is a HARD REQUIREMENT."
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
        logger.warning(
            "Summary remained above target range after rewrite fallback (got %s words; target %s±%s). Applying hard clamp.",
            actual_words,
            target_length,
            tolerance,
        )
        summary_text = _trim_preserving_headings(summary_text, upper)

    # If still under length, force one more aggressive expansion
    if actual_words < lower:
        logger.warning(
            "Summary is critically short (%s words; minimum %s). Forcing emergency expansion.",
            actual_words,
            lower,
        )
        shortfall = lower - actual_words
        emergency_prompt = (
            f"The following summary is {shortfall} words SHORT of the ABSOLUTE MINIMUM requirement of {lower} words.\n\n"
            f"You MUST expand this summary by adding AT LEAST {int(shortfall * 1.2)} words of substantive analysis.\n\n"
            "CRITICAL EXPANSION REQUIREMENTS:\n"
            "- Add detailed analysis to 'Financial Performance' (margins, cash flow quality, sustainability).\n"
            "- Expand 'Management Discussion & Analysis' with strategic insights and forward guidance.\n"
            "- Elaborate 'Risk Factors' with specific scenarios and quantified impact estimates.\n"
            "- Enhance 'Strategic Initiatives' with ROI expectations and timeline milestones.\n"
            "- Keep all existing sections intact. Only ADD content, do not remove anything.\n\n"
            "MANDATORY: Append a final line 'WORD COUNT: ###' with the actual count after expansion.\n\n"
            f"SUMMARY TO EXPAND:\n{summary_text}"
        )
        response = gemini_client.model.generate_content(emergency_prompt)
        expanded_text, reported_count = _extract_word_count_control(response.text)
        expanded_words = _count_words(expanded_text)
        
        if expanded_words >= lower:
            logger.info("Emergency expansion successful: %s words (minimum %s)", expanded_words, lower)
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
            sentences_to_cut = max(1, int(abs_diff / 10))
            action = (
                f"CONDENSE the text immediately. You are {abs_diff} words OVER. "
                f"Remove approximately {sentences_to_cut} sentences of fluff or repetitive content. "
                "Merge short sentences. Do not lose key metrics, but be ruthless with adjectives. "
                "DO NOT add a summary at the end."
            )
        else:
            # Much more aggressive expansion
            sentences_to_add = max(2, int(abs_diff / 12))
            action = (
                f"EXPAND the content immediately. You are {abs_diff} words SHORT of the MINIMUM requirement. "
                f"Add AT LEAST {sentences_to_add} sentences of substantive analysis. This is CRITICAL.\n"
                "SPECIFIC ACTIONS:\n"
                "1. Add 2-3 sentences to 'Financial Performance' with deeper margin analysis and trend interpretation.\n"
                "2. Add 2-3 sentences to 'Management Discussion & Analysis' with forward-looking strategic commentary.\n"
                "3. Add 2-3 sentences to 'Risk Factors' with specific scenario analysis (e.g., 'If X happens, then Y').\n"
                "4. Add 1-2 sentences to 'Strategic Initiatives' explaining WHY these initiatives matter.\n"
                f"You MUST reach at least {target_length - tolerance} words. Do NOT stop until you hit this minimum."
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
                minimum_acceptable
            )
            
            # One final, extremely forceful expansion attempt
            shortfall = minimum_acceptable - final_word_count
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
            
            response = gemini_client.model.generate_content(final_expansion_prompt)
            expanded_text, _ = _extract_word_count_control(response.text)
            expanded_count = _count_words(expanded_text)
            
            if expanded_count >= minimum_acceptable:
                logger.info("Final expansion successful: %s words (minimum %s)", expanded_count, minimum_acceptable)
                return expanded_text
            else:
                # Still too short - log and return the best we have
                logger.error(
                    "FAILED to meet minimum word count after all attempts. "
                    "Returning %s words (minimum %s).",
                    expanded_count if expanded_count > final_word_count else final_word_count,
                    minimum_acceptable
                )
                return expanded_text if expanded_count > final_word_count else summary_text
        
        # If over minimum, apply the usual length constraints
        summary_text = _enforce_length_constraints(
            summary_text,
            target_length,
            gemini_client,
            quality_validators,
            last_word_stats,
        )
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
    ("Financial Health Rating", 35),
    ("Executive Summary", 45),
    ("Financial Performance", 60),
    ("Management Discussion & Analysis", 60),
    ("Risk Factors", 30),
    ("Strategic Initiatives & Capital Allocation", 45),
    ("Key Data Appendix", 20),
    ("Closing Takeaway", 40),  # Increased min words for proper verdict
]
SUMMARY_SECTION_MIN_WORDS = {title: minimum for title, minimum in SUMMARY_SECTION_REQUIREMENTS}

# Rating scale - using dashboard-aligned labels only (no letter grades per user decision)
# Scale: 0-49 = At Risk, 50-69 = Watch, 70-84 = Healthy, 85-100 = Very Healthy
# NO letter grades (A, B, C, D) - numeric score + descriptive label only
RATING_SCALE = [
    (85, "VH", "Very Healthy"),
    (70, "H", "Healthy"),
    (50, "W", "Watch"),
    (0, "AR", "At Risk"),
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
            f"- Investor brief (absolute priority): {focus_clause}. You MUST adopt this persona completely. Use strong first-person language ('I', 'me', 'my view')."
        )
        instructions.append(
            "- Begin the memo with a labeled 'Investor Lens' paragraph. Start strictly with a first-person statement identifying your persona (e.g., 'As Peter Lynch, I...'). Restate the methodology and what you are looking for. Do NOT summarize results here."
        )
        instructions.append(
            "- In the 'Executive Summary', provide your decisive verdict. Use phrases like 'I like...', 'I am concerned about...', 'My take is...'. Be opinionated based on the persona's criteria."
        )
        instructions.append(
            "- In every major section, include at least one sentence explaining why the content matters to YOU (the persona) before citing generic takeaways."
        )
        instructions.append(
            "- CRITICAL FOR MD&A: If the 'Management Discussion & Analysis' section is not explicitly labeled or appears missing, you MUST infer management's perspective from the 'FULL TEXT CONTEXT' provided at the end of the input. Do NOT state that the section is missing. Extract insights on strategy, R&D, and future outlook from the available text."
        )
    else:
        instructions.append(
            "- No persona name was provided, but treat the investor brief text as the governing viewpoint and reference it explicitly in the Investor Lens (methodology) and Executive Summary (findings)."
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

    instructions.append(
        """
CRITICAL COMPLETENESS INSTRUCTION:
- You MUST complete all sections. Do not stop mid-sentence.
- Allocate your word count wisely. Do not spend too many words on early sections if it means cutting off the end.
- The 'Key Data Appendix' MUST be included at the end.
"""
    )

    # Add mandatory closing verdict for persona-based analyses
    if investor_focus:
        instructions.append(
            """
CLOSING VERDICT REQUIREMENT (MANDATORY):
After the 'Key Data Appendix', you MUST include a '## Closing Takeaway' section with your final investment verdict.
This section should be 2-4 sentences that:
1. Summarize what you (the persona) think about this company based on ALL the data analyzed
2. Give a clear stance: Would you invest? Wait for a better price? Pass entirely?
3. State the key factor driving your decision
4. Be written in first-person voice consistent with your persona

Example for John Bogle: "My conclusion: This is a fine business with exceptional profitability and strong balance sheet metrics. But the prudent course is to own the entire market, keep your costs near zero, stay the course for decades, and let compounding work for you. At current valuations, the index fund remains the wiser choice."

Example for Peter Lynch: "I'd buy this one. The story is simple - they make products everyone needs, growth is accelerating, and the PEG ratio says it's cheap for what you're getting. This is exactly the kind of opportunity I look for."

DO NOT end the analysis without this closing verdict section. It is the most important part for the reader.
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
    # If mode is default, we force the default health rating configuration
    if preferences and preferences.mode == "default":
        return dict(DEFAULT_HEALTH_RATING_CONFIG)

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
        "- The rating MUST be calculated using a transparent formula. Show the calculation:",
        "  HEALTH RATING FORMULA (use this exact weighting):",
        "  - Profitability (30%): Net Margin > 15% = 30pts, 10-15% = 20pts, 5-10% = 10pts, <5% = 0pts",
        "  - Cash Flow Quality (25%): FCF/Net Income > 1.0 = 25pts, 0.7-1.0 = 18pts, 0.4-0.7 = 10pts, <0.4 = 0pts",
        "  - Leverage (20%): Debt/Equity < 0.5 = 20pts, 0.5-1.0 = 15pts, 1.0-2.0 = 8pts, >2.0 = 0pts",
        "  - Liquidity (15%): Current Ratio > 2.0 = 15pts, 1.5-2.0 = 12pts, 1.0-1.5 = 7pts, <1.0 = 0pts",
        "  - Growth (10%): Revenue Growth > 20% = 10pts, 10-20% = 7pts, 0-10% = 4pts, <0% = 0pts",
        "- Show each component score and sum to total. Example: 'Profitability: 20/30 + Cash Flow: 18/25 + Leverage: 15/20 + Liquidity: 12/15 + Growth: 7/10 = 72/100'",
        "- FOR EACH COMPONENT: Briefly justify the score with the actual metric value.",
        "  Example: 'Growth: 0/10 (Revenue grew only 3% YoY, below the 10% threshold for 4pts)'",
        "  Example: 'Profitability: 30/30 (Net margin of 55% exceeds the 15% threshold for full points)'",
        "- After the score, provide the rating label: 85-100 = Very Healthy, 70-84 = Healthy, 50-69 = Watch, 0-49 = At Risk",
        "- Do NOT use arbitrary scores without showing the calculation. Every score must be justified by the formula AND the underlying metric.",
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
    # Removed redundant instruction to append rating to Key Metrics section


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
        (r"ITEM\s+1A\.?\s+RISK\s+FACTORS", [r"ITEM\s+1B\.?", r"ITEM\s+2\.?"], "RISK FACTORS"),
        # MD&A patterns - multiple variations to catch different filing formats
        # 10-Q Item 2
        (r"ITEM\s+2[\.\s:]+(?:MANAGEMENT['']?S?\s+DISCUSSION|MD&A)", [r"ITEM\s+3\.?", r"ITEM\s+4\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        # 10-K Item 7
        (r"ITEM\s+7[\.\s:]+(?:MANAGEMENT['']?S?\s+DISCUSSION|MD&A)", [r"ITEM\s+7A\.?", r"ITEM\s+8\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        # Standalone MD&A header (no Item number)
        (r"MANAGEMENT[''\u2019]?S?\s+DISCUSSION\s+AND\s+ANALYSIS\s+OF\s+FINANCIAL\s+CONDITION", [r"ITEM\s+7A\.?", r"ITEM\s+8\.?", r"QUANTITATIVE\s+AND\s+QUALITATIVE", r"ITEM\s+3\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        # Alternative: Just "MANAGEMENT DISCUSSION" without possessive
        (r"MANAGEMENT\s+DISCUSSION\s+AND\s+ANALYSIS", [r"ITEM\s+7A\.?", r"ITEM\s+8\.?", r"QUANTITATIVE\s+AND\s+QUALITATIVE", r"ITEM\s+3\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        # NVIDIA-specific patterns (often uses dashes)
        (r"MANAGEMENT[''\u2019]?S?\s+DISCUSSION\s+AND\s+ANALYSIS\s+[-–—]", [r"ITEM\s+7A\.?", r"ITEM\s+8\.?", r"QUANTITATIVE", r"ITEM\s+3\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        # Results of Operations (often part of MD&A)
        (r"RESULTS\s+OF\s+OPERATIONS", [r"LIQUIDITY\s+AND\s+CAPITAL", r"ITEM\s+3\.?", r"ITEM\s+7A\.?", r"ITEM\s+8\.?"], "MANAGEMENT DISCUSSION & ANALYSIS"),
        (r"ITEM\s+7A\.?\s+QUANTITATIVE", [r"ITEM\s+8\.?"], "MARKET RISK"),
        (r"ITEM\s+8\.?\s+FINANCIAL\s+STATEMENTS", [r"ITEM\s+9\.?"], "FINANCIAL STATEMENTS"),
    ]

    for start_pat, end_pats, header in extraction_rules:
        section = _extract_section(text, start_pat, end_pats)
        if section:
            # Avoid duplicate MD&A if multiple patterns match
            if header == "MANAGEMENT DISCUSSION & ANALYSIS" and any(s.startswith("MANAGEMENT DISCUSSION & ANALYSIS") for s in sections):
                continue
            # Log success for debugging
            if header == "MANAGEMENT DISCUSSION & ANALYSIS":
                print(f"✅ MD&A extracted using pattern: {start_pat[:50]}... ({len(section)} chars)")
            sections.append(f"{header}\n{section}")
            
    # Fallback: if no sections found, return a generous chunk of the start
    if not sections:
        return text[:100000]

    # CRITICAL FALLBACK: If MD&A is missing but other sections were found, 
    # append a large chunk of text to ensure the AI has context.
    has_mda = any(s.startswith("MANAGEMENT DISCUSSION & ANALYSIS") for s in sections)
    if not has_mda:
        print("⚠️ MD&A not found in extracted sections. Appending raw text fallback.")
        sections.append(f"FULL TEXT CONTEXT (MD&A MISSING FROM EXTRACTION)\n{text[:150000]}")

    return "\n\n".join(sections)



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
    
    if preferences.mode == "default":
        include_health_rating = True
    else:
        include_health_rating = bool(preferences.health_rating and preferences.health_rating.enabled)

    # Reset progress
    progress_cache[str(filing_id)] = "Initializing AI Agent..."

    # Check cache first
    if use_default_cache and False: # Cache disabled to force regeneration with new prompts
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
                    "Provide the 0-100 score with descriptive label (Very Healthy 85-100, Healthy 70-84, Watch 50-69, At Risk 0-49). "
                    "NO letter grades (A, B, C, D). Format: '72/100 - Healthy'. "
                    "Explain why the score landed there with specific metrics: margins, cash flow, leverage, liquidity. "
                    "A company with 60%+ margins should score 70+ unless there are severe balance sheet issues.",
                )
            )
        section_descriptions.extend(
            [
                (
                    "Executive Summary",
                    "2-3 COMPLETE sentences ONLY. Synthesize your investment view: bullish/bearish/neutral with clear reasoning. "
                    "Focus on strategic implications (e.g., 'AI pivot validated by 3x Data Center growth'). "
                    "NO specific numbers here - save those for Financial Performance. "
                    "CRITICAL: End with a COMPLETE stance statement. Do NOT write 'I need to determine...' or 'I want to see...' - "
                    "these are incomplete thoughts. End with 'I am bullish/bearish/neutral because [complete reason].' "
                    "VERIFY: Read your last sentence aloud. If it trails off or sounds incomplete, rewrite it.",
                ),
                (
                    "Financial Performance",
                    "MINIMUM 80 words. This is the ONLY section for quantitative analysis. Required elements:\n"
                    "- Revenue with YoY% change and time period (e.g., '$26.0B Q3 FY25, +94% YoY')\n"
                    "- Operating margin with trend (expanding/compressing vs prior period)\n"
                    "- Net income and EPS with comparisons\n"
                    "- Cash flow quality: OCF vs Net Income ratio, FCF generation\n"
                    "- Working capital: Distinguish between ENDING BALANCES (how much inventory/AR exists) vs CHANGES (how much it increased/decreased). A positive change means increase, negative means decrease.\n"
                    "ALWAYS specify the fiscal period for EVERY number. Use consistent period (all Q3 FY25 or all FY24).",
                ),
                (
                    "Management Discussion & Analysis",
                    "MINIMUM 80 words. This section MUST contain ACTUAL MANAGEMENT COMMENTARY from the MD&A section of the filing.\n"
                    "CRITICAL: The filing DOES contain an MD&A section (Item 7 for 10-K, Item 2 for 10-Q). You MUST extract and cite from it.\n"
                    "SEC-COMPLIANT REQUIREMENTS:\n"
                    "- Quote or closely paraphrase ACTUAL statements from the filing's MD&A section\n"
                    "- Use attributions like 'management stated', 'the company disclosed', 'according to the filing'\n"
                    "- Include SPECIFIC commentary on: revenue drivers, segment performance, margin trends, outlook\n"
                    "- Reference the ACTUAL forward guidance if provided (e.g., 'Q4 revenue expected to be $X')\n"
                    "REQUIRED CONTENT (find these in the MD&A section):\n"
                    "- Revenue/margin guidance or outlook statements\n"
                    "- Segment performance commentary (e.g., Data Center grew X%, Gaming declined Y%)\n"
                    "- Capacity/supply chain updates from management\n"
                    "- Competitive positioning statements\n"
                    "- Key drivers of results explained by management\n"
                    "DO NOT write 'management commentary is limited' - the MD&A section contains substantive commentary. Extract it.",
                ),
                (
                    "Risk Factors",
                    "EXACTLY 5 risks. Each risk MUST follow this format:\n"
                    "**[Risk Name]**: [1-2 sentences explaining the risk with specific, quantified details]\n\n"
                    "REQUIRED RISKS FOR SEMICONDUCTORS (include ALL that apply):\n"
                    "1. **TSMC Manufacturing Dependency**: X% of chips manufactured by TSMC in Taiwan. Geopolitical risk, natural disaster, or capacity constraints could halt production.\n"
                    "2. **China Export Restrictions**: US export controls limit sales to China, representing $X.XB (X%) of revenue. Further restrictions could expand.\n"
                    "3. **Customer Concentration**: Top 3-5 customers represent X% of revenue. Name them if known (Microsoft, Amazon, Google, Tesla, Meta).\n"
                    "4. **Cyclical Demand**: Semiconductor demand is cyclical. Current AI/datacenter demand could normalize or decline.\n"
                    "5. **Competition**: AMD, Intel, and custom ASICs from hyperscalers threaten market share. Quantify if possible.\n\n"
                    "FOR OTHER INDUSTRIES: Identify 5 company-specific risks with similar specificity.\n"
                    "FORBIDDEN: Generic risks without quantification or company-specific context.",
                ),
                (
                    "Competitive Landscape",
                    "MINIMUM 40 words. Analyze competitive positioning:\n"
                    "- Key competitors and market share dynamics\n"
                    "- Competitive advantages/moats (or lack thereof)\n"
                    "- Emerging threats (e.g., for NVIDIA: AMD, custom ASICs from hyperscalers, Intel)\n"
                    "- Barriers to entry\n"
                    "Be specific to the industry and company.",
                ),
                (
                    "Strategic Initiatives & Capital Allocation",
                    "MINIMUM 50 words. Analyze how the company deploys capital with SPECIFIC numbers:\n"
                    "- R&D: $X.XB (X% of revenue) - what it funds\n"
                    "- Capex: $X.XB - capacity expansion, infrastructure\n"
                    "- Buybacks: $X.XB (X% of FCF) - dilution offset vs return of capital\n"
                    "- Dividends: $X.XB yield\n"
                    "- M&A: Recent deals and strategic rationale\n"
                    "Assess: Is capital allocation value-accretive or value-destructive?",
                ),
                (
                    "Key Data Appendix",
                    "Bullet list format ONLY. No narrative. ALL figures must be from the SAME fiscal period:\n"
                    "- Fiscal Period: [Q# FY## or FY##] - STATE THIS FIRST\n"
                    "- Revenue: $X.XB\n"
                    "- Operating Margin: X.X%\n"
                    "- Net Income: $X.XB\n"
                    "- EPS: $X.XX\n"
                    "- FCF: $X.XB\n"
                    "- Cash: $X.XB\n"
                    "- Debt: $X.XB\n"
                    "- P/E (if available): X.Xx\n"
                    "- FCF Yield (if available): X.X%\n"
                    "CRITICAL: Appendix numbers MUST match the numbers cited in narrative sections above. Do not mix periods.",
                ),
                (
                    "Closing Takeaway",
                    "2-4 sentences. This is your FINAL INVESTMENT VERDICT - the most important section for the reader.\n"
                    "Synthesize everything into ONE clear, actionable conclusion that reflects your persona's perspective.\n"
                    "REQUIRED ELEMENTS:\n"
                    "1. Overall assessment of the company's quality based on the data analyzed\n"
                    "2. Clear investment stance: Would you buy? Hold? Pass? Wait for better price?\n"
                    "3. The key factor driving your decision\n"
                    "If using a persona, write in FIRST PERSON voice consistent with that persona.\n"
                    "Examples:\n"
                    "- John Bogle: 'My conclusion: This is a fine business. But the prudent course is to own the entire market at 0.03% cost. "
                    "Stay the course with the index fund.'\n"
                    "- Peter Lynch: 'I'd buy this one. The story is simple, the PEG is attractive, and we're only in the 4th inning of growth.'\n"
                    "- Warren Buffett: 'This is a wonderful business at a fair price. The moat is wide, the economics are durable, "
                    "and I'd be comfortable holding for decades.'\n"
                    "DO NOT end without providing a clear stance. This section provides CLOSURE for the entire analysis.",
                ),
            ]
        )
        section_requirements = "\n".join(
            f"## {title}\n{description}" for title, description in section_descriptions
        )
        
        tone = preferences.tone or "objective"
        detail_level = preferences.detail_level or "comprehensive"
        output_style = preferences.output_style or "paragraph"

        base_prompt = f"""
You are a senior analyst at a top-tier hedge fund writing a high-conviction briefing for portfolio managers.
Your goal is to provide actionable, differentiated insight, not just a summary of facts.
Analyze the following filing for {company_name} ({filing_type}, {filing_date}).

CONTEXT:
{context_excerpt}{truncated_note}

FINANCIAL SNAPSHOT (Reference only):
{financial_snapshot}

KEY METRICS (Use these for calculations and evidence):
{metrics_lines}

INSTRUCTIONS:
1. Tone: {tone.title()} (Professional, Insightful, Direct)
2. Detail Level: {detail_level.title()}
3. Output Style: {output_style.title()}
4. Target Length: {target_length} words (approx)

STRUCTURE & CONTENT REQUIREMENTS:
{section_requirements}
{health_directives_section}
{preference_block}

CRITICAL RULES:
- MAINTAIN CONSISTENT TONE throughout. If using a persona (e.g., Graham, Lynch), stay in that voice for ALL sections.
- Do NOT use markdown bolding (**) within the text body. Only use it for section headers if needed.
- Ensure every claim is backed by the provided text or metrics.
- If data is missing, omit that data point rather than saying "not disclosed" or "not available".
- SYNTHESIZE, DO NOT SUMMARIZE. Tell us what the numbers mean, not just what they are.
- SPECIFY TIME PERIODS: Always label figures with their time period (FY24, Q3 FY25, TTM, etc.).
- NO REDUNDANCY: Each number should appear in only ONE section. Executive Summary = qualitative view. Financial Performance = all numbers.
- **SUSTAINABILITY**: Do NOT mention sustainability or ESG efforts unless they are a primary revenue driver (e.g., for a solar company). For most companies, this is fluff.
- **MD&A**: Do NOT say "Management discusses..." or "In the MD&A section...". Just state the facts found there.
- USE TRANSITIONS: Connect sections logically. Each section should flow naturally from the previous one.
- COMPLETE ALL SENTENCES: Every sentence MUST end with proper punctuation. Never leave a thought unfinished.
- VERIFY ENDING: Before submitting, check the last sentence of EVERY section. If it ends mid-thought (e.g., "essential to determine if..."), DELETE it and end on the previous complete sentence.

NARRATIVE QUALITY:
- Start each section with a clear topic sentence that states the key insight.
- End each section with a forward-looking implication or action item that is COMPLETE.
- Avoid starting consecutive sentences with the same word.
- Vary sentence length and structure for readability.
- THE LAST SENTENCE OF EACH SECTION MUST BE A COMPLETE THOUGHT ending in a period, question mark, or exclamation point.

FREE CASH FLOW RECONCILIATION:
- If FCF exceeds Net Income, you MUST explain why (e.g., working capital release, D&A exceeds capex, deferred revenue)
- If FCF < Net Income, explain the cash consumption (e.g., inventory build, receivables growth, capex expansion)
- Never present FCF > Net Income as normal without explanation

NEGATIVE CONSTRAINTS:
- Do NOT repeat the Financial Health Rating in the Key Data Appendix.
- Do NOT repeat the same metrics across multiple sections.
- Do NOT use generic filler phrases like "management remains focused" or "the company continues to execute".
- Do NOT include placeholder text like "not extracted", "see above", "not available" - if you lack information, omit it.
- Do NOT switch between personal opinion and neutral analyst tone mid-document.
- Do NOT end any section with an incomplete sentence. If unsure, read the last sentence aloud.
"""
        progress_cache[str(filing_id)] = "Synthesizing Investor Insights..."
        print("DEBUG: Calling _generate_summary_with_quality_control")
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
        summary_text = _validate_complete_sentences(summary_text)  # Fix incomplete sentences
        summary_text = _ensure_required_sections(
            summary_text,
            include_health_rating=include_health_rating,
            metrics_lines=metrics_lines,
            calculated_metrics=calculated_metrics,
            company_name=company_name,
            health_rating_config=health_config,
        )
        if target_length:
            summary_text = _enforce_length_constraints(
                summary_text,
                target_length,
                gemini_client,
                quality_validators=[_make_section_completeness_validator(include_health_rating)],
                last_word_stats=None,
            )
            summary_text = _finalize_length_band(summary_text, target_length, tolerance=10)
            # Re-validate required sections after any trimming/padding, then clamp again
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                company_name=company_name,
                health_rating_config=health_config,
            )
            summary_text = _finalize_length_band(summary_text, target_length, tolerance=10)
            # Final pass to normalize headings and length in case prior rewrites removed structure
            summary_text = _normalize_section_headings(summary_text, include_health_rating)
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                company_name=company_name,
                health_rating_config=health_config,
            )
            summary_text = _finalize_length_band(summary_text, target_length, tolerance=10)
            summary_text = _force_final_band(summary_text, target_length, tolerance=10)

        # Cache result
        if use_default_cache:
            fallback_filing_summaries[str(filing_id)] = summary_text
        return JSONResponse(content={
            "filing_id": filing_id,
            "summary": summary_text,
            "cached": False
        })
        
    except Exception as gemini_exc:
        with open("debug_error.txt", "w") as f:
            f.write(f"ERROR: {gemini_exc}\n")
            traceback.print_exc(file=f)
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
    """Format a number or return 'not disclosed' if missing."""
    if value is None:
        return "not disclosed"
    formatted = _format_dollar(value)
    if formatted:
        return formatted
    return f"{value:,.2f}"


def _ensure_required_sections(
    summary_text: str,
    *,
    include_health_rating: bool,
    metrics_lines: str,
    calculated_metrics: Dict[str, Any],
    company_name: str,
    health_rating_config: Optional[Dict[str, Any]] = None,
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

    def _section_present(title: str) -> bool:
        return f"## {title}" in text

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

    # 1. Financial Health Rating - only add if we have actual data
    if include_health_rating and not _section_present("Financial Health Rating"):
        score_match = re.search(r"Financial Health Rating[:\s]+(\d{1,3})", text, re.IGNORECASE)
        if score_match:
            health_score_val = float(score_match.group(1))
        else:
            health_score_val = _estimate_health_score(calculated_metrics)

        label = _get_score_label(health_score_val)
        fcf_str = _format_number_or_default(calculated_metrics.get("free_cash_flow"))
        cash_str = _format_number_or_default(calculated_metrics.get("cash"))
        liabilities_str = _format_number_or_default(calculated_metrics.get("total_liabilities"))

        body = f"{company_name} receives a Financial Health Rating of {health_score_val:.0f}/100 - {label}."

        supporting_facts = []
        if _has_valid_data(fcf_str):
            supporting_facts.append(f"free cash flow of {fcf_str}")
        if _has_valid_data(cash_str):
            supporting_facts.append(f"cash of {cash_str}")
        if _has_valid_data(liabilities_str):
            supporting_facts.append(f"total liabilities of {liabilities_str}")

        if supporting_facts:
            body += f" This rating reflects {', '.join(supporting_facts)}."

        _append_section("Financial Health Rating", body)

    # 2-6: For other sections, we do NOT add placeholder content.
    # If the AI failed to generate these sections, we skip them entirely.
    # The prompt should be strong enough to ensure the AI generates all sections.
    # Adding "not extracted" or "see above" placeholders degrades quality.

    # 7. Key Data Appendix - this is just raw metrics, always useful to include
    if not _section_present("Key Data Appendix") and metrics_lines.strip():
        _append_section("Key Data Appendix", metrics_lines.strip())

    # 8. Closing Takeaway - ensure there's a closing verdict if missing
    if not _section_present("Closing Takeaway"):
        # Check if the analysis was persona-based by looking for "Investor Lens" section
        has_persona = "## Investor Lens" in text or "As " in text[:500]
        if has_persona:
            # Generate a generic closing for persona analyses if missing
            _append_section(
                "Closing Takeaway",
                f"Based on the analysis above, {company_name} presents a mixed investment picture. "
                "Investors should weigh the company's financial strengths against the identified risks "
                "before making investment decisions."
            )

    return text


@router.get("/{filing_id}/progress")
async def get_filing_summary_progress(filing_id: str):
    """Get real-time progress of summary generation."""
    status = progress_cache.get(str(filing_id), "Initializing...")
    return {"status": status}
