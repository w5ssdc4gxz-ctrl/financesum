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
from app.services.gemini_client import get_gemini_client, generate_growth_assessment
from app.services.health_scorer import calculate_health_score
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
    role_match = re.search(r'Role:\s*([^.]+)\.', investor_focus, re.IGNORECASE)
    if role_match:
        return role_match.group(1).strip()
    
    # Try to extract from "As [Name]," pattern
    as_match = re.search(r'As\s+([A-Z][a-z]+\s+[A-Z][a-z]+)', investor_focus)
    if as_match:
        return as_match.group(1).strip()
    
    # Common persona names to look for
    persona_names = [
        "Warren Buffett", "Charlie Munger", "Benjamin Graham", "Peter Lynch",
        "Ray Dalio", "Cathie Wood", "Joel Greenblatt", "John Bogle",
        "Howard Marks", "Bill Ackman"
    ]
    
    for name in persona_names:
        if name.lower() in investor_focus.lower():
            return name
    
    return None


def _build_closing_takeaway_description(persona_name: Optional[str], company_name: str) -> Tuple[str, str]:
    """Build a dynamic Closing Takeaway section description based on the selected persona.
    
    If a persona is selected, the instructions are specifically tailored to that persona's voice.
    If no persona is selected, generic instructions are provided.
    """
    title = "Closing Takeaway"
    
    base_requirements = (
        "MANDATORY SECTION - DO NOT OMIT UNDER ANY CIRCUMSTANCES.\n"
        "5-7 COMPLETE sentences (minimum 75 words). This is your FINAL INVESTMENT VERDICT.\n\n"
        "=== REQUIRED ELEMENTS (ALL 5 MUST BE PRESENT - CHECK EACH ONE) ===\n"
        "\n"
        "1. QUALITY ASSESSMENT (REQUIRED): Is this a high-quality, average, or poor business?\n"
        "   You MUST state this explicitly with reasoning.\n"
        "   Example: 'This is a wonderful business because...' or 'This is a mediocre business due to...'\n"
        "\n"
        "2. INVESTMENT STANCE (REQUIRED - CANNOT BE OMITTED):\n"
        "   You MUST state one of: BUY, HOLD, SELL, or WAIT\n"
        "   Use the persona's language but the verdict must be CLEAR.\n"
        "   Example: 'I would buy at these levels' or 'I would hold but not add' or 'I would pass/sell'\n"
        "\n"
        "3. KEY DRIVER (REQUIRED): What is the #1 factor behind your decision?\n"
        "   Be specific: 'The moat is...' or 'The valuation is...' or 'The risk is...'\n"
        "\n"
        "4. ACTIONABLE TRIGGER (REQUIRED): What would change your mind?\n"
        "   Example: 'I would reconsider if margins fell below 30%' or 'At a 20% pullback, I would buy'\n"
        "\n"
        "5. PERSONAL INVESTMENT OPINION (MANDATORY - THE FINAL SENTENCE):\n"
        "   Your LAST sentence MUST be a first-person investment recommendation.\n"
        "   You MUST use one of these EXACT formats:\n"
        "   - 'I personally would BUY/HOLD/SELL [Company] because [reason].'\n"
        "   - 'For my own portfolio, I would BUY/HOLD/SELL here.'\n"
        "   - 'My personal recommendation: BUY/HOLD/SELL.'\n"
        "   This is NON-NEGOTIABLE. The Closing Takeaway is INCOMPLETE without this final sentence.\n"
        "\n"
        "VERIFICATION: Before submitting, check: Does your FINAL sentence contain 'I personally would' or 'my personal recommendation'? If not, ADD IT.\n\n"
    )
    
    completion_requirements = (
        "\n=== ABSOLUTE SENTENCE COMPLETION REQUIREMENT ===\n"
        "EVERY sentence MUST end with a period, question mark, or exclamation point.\n"
        "DO NOT write 'I would...' and stop. COMPLETE IT: 'I would wait for a better entry point.'\n"
        "DO NOT trail off with '...' - FORBIDDEN.\n"
        "DO NOT end with incomplete phrases like 'which is...', 'and the...', 'but I...'\n"
        "READ YOUR LAST SENTENCE ALOUD. Does it sound complete? If not, REWRITE IT.\n"
        "=== END REQUIREMENT ===\n"
    )
    
    if persona_name and persona_name in PERSONA_CLOSING_INSTRUCTIONS:
        # Persona-specific instructions
        persona_instructions = PERSONA_CLOSING_INSTRUCTIONS[persona_name]
        description = (
            base_requirements +
            f"=== YOU ARE {persona_name.upper()} - WRITE EXACTLY AS THEY WOULD ===\n"
            f"This Closing Takeaway MUST sound like {persona_name} personally wrote it about {company_name}.\n"
            f"Use FIRST PERSON voice throughout ('I', 'my view', 'I would').\n\n"
            f"{persona_instructions}\n"
            f"\nDO NOT write a generic analyst conclusion. Sound EXACTLY like {persona_name}.\n"
            f"The reader should immediately recognize this as {persona_name}'s voice.\n"
            + completion_requirements +
            f"This section MUST provide CLOSURE as {persona_name} giving their final verdict on {company_name}."
        )
    else:
        # Generic instructions (no persona selected) - HIGH QUALITY OBJECTIVE ANALYSIS
        description = (
            base_requirements +
            "=== OBJECTIVE ANALYST MODE (NO PERSONA) ===\n"
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
            + completion_requirements +
            f"This section MUST provide CLOSURE for the analysis of {company_name} using NEUTRAL, OBJECTIVE language."
        )
    
    return (title, description)


# Persona-specific closing templates for dynamic generation
# CRITICAL: All personas MUST end with "I personally would buy/hold/sell" statement
PERSONA_CLOSING_INSTRUCTIONS = {
    "Warren Buffett": (
        "As Warren Buffett, your closing MUST:\n"
        "- Use phrases like 'wonderful business', 'moat', 'owner earnings', 'circle of competence'\n"
        "- Reference whether you'd 'hold for decades'\n"
        "- Assess the moat (wide/narrow/non-existent)\n"
        "- Use folksy language and analogies\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell [Company] because...' or 'For my own portfolio, I would buy/hold/sell here.'\n"
        "EXAMPLE: 'This is a wonderful business with a wide moat built on [specific advantage]. "
        "The economics are durable, and I would be comfortable holding for decades. "
        "At current prices, Mr. Market is offering a fair deal for patient capital. I personally would buy and hold for the long term.'"
    ),
    "Charlie Munger": (
        "As Charlie Munger, your closing MUST:\n"
        "- Use inversion: 'What would make this a terrible investment?'\n"
        "- Discuss incentives alignment\n"
        "- Be blunt and pithy\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' or similar personal stance.\n"
        "EXAMPLE: 'Inverting the question: what would make this a disaster? [Answer]. "
        "The incentives are properly aligned. The economics make sense. I personally would buy at these levels.'"
    ),
    "Benjamin Graham": (
        "As Benjamin Graham, your closing MUST:\n"
        "- Reference 'margin of safety' explicitly\n"
        "- Discuss intrinsic value vs market price\n"
        "- Use 'intelligent investor' language\n"
        "- Be quantitative and methodical\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' or 'For the intelligent investor, I would...' with clear action.\n"
        "EXAMPLE: 'The margin of safety at current prices is [adequate/insufficient]. "
        "For the intelligent investor, this represents [investment/speculation]. "
        "The balance sheet strength [supports/undermines] the thesis. I personally would hold until a larger margin of safety appears.'"
    ),
    "Peter Lynch": (
        "As Peter Lynch, your closing MUST:\n"
        "- Tell 'the story' in simple terms\n"
        "- Reference PEG ratio if applicable\n"
        "- Classify as stalwart/fast grower/turnaround/cyclical\n"
        "- Be enthusiastic if bullish\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' with enthusiasm if bullish.\n"
        "EXAMPLE: 'Here's the story: [simple explanation]. The PEG of [X] says this is [cheap/fair/expensive]. "
        "This is a [category] that I would [verdict]. You don't need an MBA to understand this one. I personally would buy this stalwart and hold on.'"
    ),
    "Ray Dalio": (
        "As Ray Dalio, your closing MUST:\n"
        "- Reference 'where we are in the cycle'\n"
        "- Discuss risk parity considerations\n"
        "- Mention correlation to macro factors\n"
        "- Use systems thinking language\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' with sizing rationale.\n"
        "EXAMPLE: 'At this point in the cycle, [assessment]. The risk parity consideration suggests [sizing]. "
        "Understanding the machine, I personally would hold with a moderate position size given the current cycle position.'"
    ),
    "Cathie Wood": (
        "As Cathie Wood, your closing MUST:\n"
        "- Reference 'disruptive innovation'\n"
        "- Mention Wright's Law or S-curves if relevant\n"
        "- Give a 5-year or 2030 vision\n"
        "- Express high conviction in innovation\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' with conviction.\n"
        "EXAMPLE: 'The disruptive innovation potential here is [assessment]. "
        "Wright's Law suggests costs will [trajectory]. By 2030, [vision]. I personally would buy with high conviction for the next 5 years.'"
    ),
    "Joel Greenblatt": (
        "As Joel Greenblatt, your closing MUST:\n"
        "- Reference return on capital and earnings yield\n"
        "- Give a clear Magic Formula verdict: Is it GOOD (high ROC), CHEAP (high earnings yield), or BOTH? The Magic Formula works best when a stock is BOTH good AND cheap.\n"
        "- Be quantitative and direct - cite specific numbers\n"
        "- Assess if this is a 'clean situation' or if there are complications\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY - FINAL SENTENCE):\n"
        "  Your VERY LAST sentence MUST be one of these exact formats:\n"
        "  * 'I personally would buy [Company] at these levels.'\n"
        "  * 'I personally would hold [Company] but not add here.'\n"
        "  * 'I personally would sell/pass on [Company].'\n"
        "  This is NON-NEGOTIABLE. Without this sentence, your analysis is INCOMPLETE.\n"
        "EXAMPLE: 'Return on capital is 25%, earnings yield is 8%. By the Magic Formula, this is a good business at a fair price - but not clearly cheap. "
        "The cash generation is strong, but leverage concerns limit the margin of safety. I personally would hold UBER and wait for a better entry point.'"
    ),
    "John Bogle": (
        "As John Bogle, your closing MUST:\n"
        "- Reference 'stay the course' and 'costs matter'\n"
        "- Compare individual stock to index fund approach\n"
        "- Use 'haystack vs needle' analogy\n"
        "- Be humble and prudent\n"
        "- STATE A CLEAR VERDICT: Even as an index advocate, give your assessment\n"
        "- Include what would change your view (e.g., 'valuation, competitive threats')\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' or 'For those who insist on individual stocks, I personally would...'.\n"
        "EXAMPLE: 'This is a fine business with exceptional profitability. But why own one needle when you can own the haystack? "
        "Costs matter, and 90% of active managers fail. If valuation became more attractive, I might reconsider. I personally would hold this one but still prefer the index fund.'"
    ),
    "Howard Marks": (
        "As Howard Marks, your closing MUST:\n"
        "- Reference 'second-level thinking'\n"
        "- Discuss 'where we are in the cycle' and 'the pendulum'\n"
        "- Assess risk-reward asymmetry\n"
        "- Consider 'what's priced in'\n"
        "- STATE A CLEAR VERDICT: BUY, HOLD, SELL, or WAIT with your reasoning\n"
        "- Include what would change your view (cycle shift, valuation change)\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...'.\n"
        "EXAMPLE: 'Where are we in the cycle? The optimism is elevated but not extreme. Second-level thinking suggests the market is not fully pricing in competitive risks. "
        "The risk-reward asymmetry favors caution. I personally would hold at these levels and wait for a better entry point.'"
    ),
    "Bill Ackman": (
        "As Bill Ackman, your closing MUST:\n"
        "- Assess if business is 'simple, predictable, free-cash-flow generative'\n"
        "- Identify 'the catalyst' for value creation\n"
        "- State what 'management MUST' do\n"
        "- Express conviction level\n"
        "- STATE A CLEAR VERDICT: BUY, HOLD, or SELL with conviction level\n"
        "- Include what would change your view (catalyst, management action)\n"
        "- END WITH PERSONAL RECOMMENDATION (MANDATORY): Your FINAL sentence MUST be 'I personally would buy/hold/sell...' with conviction.\n"
        "EXAMPLE: 'This is simple, predictable, and free-cash-flow generative—exactly what I look for. "
        "The catalyst for value creation is clear. Management MUST maintain discipline. I personally would buy with high conviction at these levels.'"
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
        "Key Data Appendix",
        "Closing Takeaway",
    ]
    
    result = text
    
    for header in section_headers:
        # Pattern 1: Header appears after punctuation on same line
        # e.g., "...business. ## Executive Summary As Bill..."
        pattern1 = re.compile(
            rf'([.!?])\s*(?:##?\s*)?({re.escape(header)})\s+(\S)',
            re.IGNORECASE
        )
        result = pattern1.sub(
            lambda m: f'{m.group(1)}\n\n## {header}\n\n{m.group(3)}',
            result
        )
        
        # Pattern 2: Header appears mid-sentence without punctuation
        # e.g., "some text ## Executive Summary more text"
        # Only add period if the character before isn't already punctuation
        pattern2 = re.compile(
            rf'([^.!?\s])\s+(?:##?\s*)({re.escape(header)})\s+(\S)',
            re.IGNORECASE
        )
        result = pattern2.sub(
            lambda m: f'{m.group(1)}.\n\n## {header}\n\n{m.group(3)}',
            result
        )
        
        # Pattern 3: Header at very start of text without ##
        pattern3 = re.compile(
            rf'^(?:##?\s*)?({re.escape(header)})\s*\n?',
            re.IGNORECASE | re.MULTILINE
        )
        if re.match(pattern3, result):
            result = pattern3.sub(f'## {header}\n\n', result, count=1)
    
    # Clean up excessive newlines
    result = re.sub(r'\n{4,}', '\n\n\n', result)
    
    # Ensure ## headers are properly formatted
    result = re.sub(r'(\n|^)#+\s+', r'\1## ', result)
    
    # Clean up double periods that might have been introduced
    result = re.sub(r'\.{2,}', '.', result)
    
    return result


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
        (r'I would\s*\.{2,}\s*$', 'I would proceed with caution given current valuations.'),
        (r'I would\s*$', 'I would proceed with caution given current valuations.'),
        (r'I need to\s*\.{2,}', 'I need to see clearer evidence before committing capital.'),
        (r'I believe\s*\.{2,}', 'I believe caution is warranted at current levels.'),
        (r'I am concerned about\s*\.{2,}', 'I am concerned about the sustainability of current trends.'),
        (r'Given my focus on\s*\.{2,}', 'Given my focus on risk-reward asymmetry, I remain cautious.'),
        (r'I prefer to\s*\.{2,}', 'I prefer to wait for a more favorable entry point.'),
        
        # ===== EXECUTIVE SUMMARY / CLOSING PATTERNS =====
        (r'sustainability and the\s*\.{2,}', 'sustainability and the long-term durability of these exceptional margins.'),
        (r'sustainability and the\s*$', 'sustainability and the long-term durability of these exceptional margins.'),
        (r'and the\s*\.{2,}\s*$', 'and the implications for long-term value creation.'),
        (r'and the\s*$', 'and the implications for long-term value creation.'),
        (r'but the current\s*\.{2,}', 'but the current valuation leaves limited margin of safety.'),
        (r'raises concerns about\s*\.{2,}', 'raises concerns about the sustainability of exceptional results.'),
        
        # ===== MD&A / MANAGEMENT PATTERNS =====
        (r'uncertainties in global\s*\.{2,}', 'uncertainties in the global supply chain and macroeconomic environment.'),
        (r'uncertainties in global\s*$', 'uncertainties in the global supply chain and macroeconomic environment.'),
        (r'in global\s*\.{2,}', 'in global markets and supply chains.'),
        (r'in global\s*$', 'in global markets and supply chains.'),
        (r'supply chain complexities\s*\.{2,}', 'supply chain complexities that require ongoing attention.'),
        (r'strategic agility\s*\.{2,}', 'strategic agility to maintain market leadership.'),
        
        # ===== RISK FACTOR PATTERNS =====
        (r'a geopolitical\s*\.{2,}', 'a geopolitical risk that warrants close monitoring.'),
        (r'a geopolitical\s*$', 'a geopolitical risk that warrants close monitoring.'),
        (r', a geopolitical\s*\.{2,}', ', a geopolitical concern that warrants attention.'),
        (r', a geopolitical\s*$', ', a geopolitical concern that warrants attention.'),
        (r'in a key market\s*\.{2,}', 'in a key market that could materially impact results.'),
        (r'in a key market\s*$', 'in a key market that could materially impact results.'),
        (r'materially affecting\s*\.{2,}', 'materially affecting the company\'s financial performance.'),
        (r'geopolitical instability\s*\.{2,}', 'geopolitical instability that could disrupt operations.'),
        (r'capacity constraints\s*\.{2,}', 'capacity constraints that could limit production.'),
        
        # ===== COMPETITIVE LANDSCAPE PATTERNS =====
        (r"NVIDIA's\s*\.{2,}", "NVIDIA's competitive positioning and pricing power."),
        (r"NVIDIA's\s*$", "NVIDIA's competitive positioning and pricing power."),
        (r"reliance on NVIDIA's\s*\.{2,}", "reliance on NVIDIA's chips and potentially developing alternatives."),
        (r"reliance on NVIDIA's\s*$", "reliance on NVIDIA's chips and potentially developing alternatives."),
        (r'competitive\s+strategies\s*\.{2,}', 'competitive strategies and market positioning.'),
        (r'competitive\s+strategies\s*$', 'competitive strategies and market positioning.'),
        (r'potentially reducing their\s*\.{2,}', 'potentially reducing their dependency on external suppliers.'),
        (r'potentially reducing their\s*$', 'potentially reducing their dependency on external suppliers.'),
        (r'hyperscalers like\s*\.{2,}', 'hyperscalers like Google, Amazon, and Microsoft.'),
        (r'eroding\s*\.{2,}', 'eroding market share over time.'),
        (r'concentration\s*\.{2,}', 'concentration risk that investors should monitor.'),
        
        # ===== STRATEGIC INITIATIVES PATTERNS =====
        (r'technological\s+advancements\s*\.{2,}', 'technological advancements and market adoption milestones.'),
        (r'technological\s+advancements\s*$', 'technological advancements and market adoption milestones.'),
        (r'product launches and\s*\.{2,}', 'product launches and technological innovations.'),
        (r'product launches and\s*$', 'product launches and technological innovations.'),
        (r'articulated, along with\s*\.{2,}', 'articulated, along with clear performance metrics and timelines.'),
        (r'articulated, along with\s*$', 'articulated, along with clear performance metrics and timelines.'),
        (r'value creation and\s*\.{2,}', 'value creation and shareholder returns.'),
        
        # ===== GENERIC TRAILING PATTERNS =====
        (r'global\s*\.{2,}', 'global market dynamics and competitive pressures.'),
        (r'reliance on\s*\.{2,}', 'reliance on key suppliers and partners.'),
        (r'securing and\s*\.{2,}', 'securing and maintaining market position.'),
        (r'potentially hindering\s*\.{2,}', 'potentially hindering future growth.'),
        (r'potentially eroding\s*\.{2,}', 'potentially eroding competitive advantages.'),
        (r'driven by the\s*\.{2,}', 'driven by strong demand and operational execution.'),
        (r'driven by\s*\.{2,}', 'driven by favorable market conditions.'),
        
        # ===== PATTERNS ENDING WITH PREPOSITIONS/ARTICLES =====
        (r',\s+but\s+the\s*\.{2,}\s*$', ', but the risks remain manageable for long-term investors.'),
        (r',\s+but\s+the\s*$', ', but the risks remain manageable for long-term investors.'),
        (r',\s+although\s+the\s*\.{2,}', ', although the outlook remains uncertain.'),
        (r',\s+although\s+the\s*$', ', although the outlook remains uncertain.'),
        (r',\s+while\s+the\s*\.{2,}', ', while the opportunity set remains compelling.'),
        (r',\s+while\s+the\s*$', ', while the opportunity set remains compelling.'),
        (r',\s+however\s+the\s*\.{2,}', ', however the valuation provides some cushion.'),
        (r',\s+however\s+the\s*$', ', however the valuation provides some cushion.'),
        
        # ===== INCOMPLETE ARTICLE/PREPOSITION ENDINGS =====
        (r'\bthe\s*\.{2,}\s*$', 'the implications for investors.'),
        (r'\ba\s*\.{2,}\s*$', 'a material consideration for investors.'),
        (r'\ban\s*\.{2,}\s*$', 'an important factor to monitor.'),
        (r'\bto\s*\.{2,}\s*$', 'to monitor closely.'),
        (r'\bof\s*\.{2,}\s*$', 'of significant importance.'),
        (r'\bfor\s*\.{2,}\s*$', 'for careful consideration.'),
        (r'\bwith\s*\.{2,}\s*$', 'with appropriate risk management.'),
        (r'\bin\s*\.{2,}\s*$', 'in the current market environment.'),
    ]
    
    result = text
    
    # Apply pattern-based fixes to the full text
    for pattern, replacement in ellipsis_fixes:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE | re.MULTILINE)
    
    # Handle any remaining ellipsis by finding and fixing them
    # Split into paragraphs (double newline) to preserve structure
    paragraphs = re.split(r'(\n\n+)', result)
    fixed_paragraphs = []
    
    for para in paragraphs:
        # Skip paragraph separators
        if re.match(r'^\n+$', para):
            fixed_paragraphs.append(para)
            continue
            
        # Check for remaining ellipsis in this paragraph
        if re.search(r'\.{2,}', para):
            # Find all ellipsis positions and fix each
            while re.search(r'\.{2,}', para):
                match = re.search(r'\.{2,}', para)
                if not match:
                    break
                    
                pos = match.start()
                # Get text before ellipsis
                before = para[:pos].rstrip()
                after = para[match.end():].lstrip()
                
                # Find the last complete sentence before this point
                last_punct = max(before.rfind('. '), before.rfind('! '), before.rfind('? '), before.rfind('.\n'))
                
                if last_punct > len(before) * 0.3:
                    # Truncate to last complete sentence
                    para = before[:last_punct + 1]
                    if after and not after.startswith('\n'):
                        para += ' ' + after
                    else:
                        para += after
                else:
                    # Add a contextual completion based on surrounding text
                    completion = _get_contextual_completion(before)
                    para = before + completion + (' ' + after if after and not after.startswith('\n') else after)
        
        fixed_paragraphs.append(para)
    
    return ''.join(fixed_paragraphs)


def _get_contextual_completion(text: str) -> str:
    """Generate a contextual completion for incomplete text based on keywords."""
    text_lower = text.lower()
    
    # Risk-related context
    if any(kw in text_lower for kw in ['risk', 'concern', 'threat', 'vulnerable', 'exposure']):
        return ', which warrants careful monitoring by investors.'
    
    # Competition-related context
    if any(kw in text_lower for kw in ['compet', 'rival', 'market share', 'amd', 'intel']):
        return ', presenting ongoing competitive challenges.'
    
    # Financial/valuation context
    if any(kw in text_lower for kw in ['margin', 'profit', 'revenue', 'growth', 'valuation']):
        return ', which impacts the investment thesis.'
    
    # Management/strategy context
    if any(kw in text_lower for kw in ['management', 'strategy', 'initiative', 'capital']):
        return ', requiring continued execution from management.'
    
    # Geopolitical context
    if any(kw in text_lower for kw in ['geopolitical', 'china', 'taiwan', 'export']):
        return ', a factor that requires ongoing monitoring.'
    
    # Supply chain context
    if any(kw in text_lower for kw in ['supply', 'manufacturing', 'tsmc', 'production']):
        return ', impacting production capabilities.'
    
    # Default completion
    return ', which warrants careful consideration.'


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
        r'(##\s*Financial Health Rating\s*\n+'  # Section header
        r'[^#]*?)'  # Any content before the score
        r'(\d{1,3}(?:\.\d+)?/100\s*'  # The score (e.g., "1/100" or "62/100" or "51.1/100")
        r'(?:\([A-Z]\)\s*)?'  # Optional letter grade in parens
        r'-?\s*(?:Very Healthy|Healthy|Watch|At Risk)?)',  # Optional band
        re.IGNORECASE | re.DOTALL
    )
    
    # Also match arrow-prefixed scores like "→\n1/100 (F) - Watch"
    arrow_score_pattern = re.compile(
        r'(→\s*\n?\s*)'  # Arrow prefix
        r'(\d{1,3}(?:\.\d+)?/100\s*'  # Score
        r'(?:\([A-Z]\)\s*)?'  # Optional letter grade
        r'-?\s*(?:Very Healthy|Healthy|Watch|At Risk)?)',
        re.IGNORECASE
    )
    
    # Format the correct score - using band first letter only, no full letter grade
    band_abbrev = pre_calculated_band[0] if pre_calculated_band else 'W'
    correct_score = f"{pre_calculated_score:.0f}/100 ({band_abbrev}) - {pre_calculated_band}"
    
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
        logger.info(f"Fixed health score mismatch: replaced AI-generated score with {pre_calculated_score:.1f}/100 - {pre_calculated_band}")
    
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

    lines = text.split('\n')
    validated_lines = []

    # Patterns that indicate incomplete sentences (more comprehensive)
    incomplete_patterns = [
        # Number without unit at end: "revenue of $3." or "cash flow of $13.47"
        r'\$\d+(?:\.\d+)?\.?\s*$',
        # Trailing "of" or "at" with nothing after (not followed by ellipsis)
        r'\s+(?:of|at|to|for|with)\s*[,]?\s*$',
        # Sentence ending with just comma or colon (not ellipsis)
        r'[,:]$',
        # Blank amount placeholders
        r'(?:of|at|to)\s*,',
        # Ends with articles without noun
        r'\s+(?:the|a|an)\s*$',
        # Ends with conjunctions without completion
        r'\s+(?:and|but|or|while|although|however|which)\s*$',
        # Ends with possessive without noun (e.g., "NVIDIA's")
        r"[A-Za-z]+['']s\s*$",
        # Ends with "that" without clause
        r'\s+that\s*$',
        # Ends with incomplete comparisons
        r'\s+(?:than|as)\s*$',
    ]

    # Completions for various trailing patterns
    trailing_completions = {
        r'\s+the\s*$': ' the implications for investors.',
        r'\s+a\s*$': ' a key consideration.',
        r'\s+an\s*$': ' an important factor.',
        r'\s+and\s*$': ' and other relevant factors.',
        r'\s+but\s*$': ' but caution is warranted.',
        r'\s+or\s*$': ' or alternative approaches.',
        r'\s+while\s*$': ' while maintaining focus on fundamentals.',
        r'\s+although\s*$': ' although uncertainties remain.',
        r'\s+however\s*$': ' however the outlook remains positive.',
        r'\s+which\s*$': ' which impacts the investment case.',
        r'\s+that\s*$': ' that warrants attention.',
        r"[A-Za-z]+['']s\s*$": "'s strategic positioning.",
    }

    for line in lines:
        # Skip empty lines or section headers
        if not line.strip() or line.strip().startswith('#'):
            validated_lines.append(line)
            continue

        # Skip bullet points that might intentionally be brief
        if line.strip().startswith('- ') or line.strip().startswith('→'):
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
                last_punct = max(line.rfind('.'), line.rfind('!'), line.rfind('?'))
                if last_punct > len(line) * 0.5:  # Only cut if we keep at least 50%
                    line = line[:last_punct + 1]
                    is_incomplete = False
                elif last_punct > len(line) * 0.3:
                    # If we can keep at least 30%, cut and add a generic completion
                    line = line[:last_punct + 1]
                    is_incomplete = False
                break

        if not is_incomplete:
            validated_lines.append(line)
        else:
            # If still incomplete, try to add generic completion instead of dropping
            line_stripped = original_line.rstrip()
            if line_stripped and not line_stripped[-1] in '.!?':
                # Add a contextual ending
                if 'risk' in line_stripped.lower():
                    validated_lines.append(line_stripped + ', which warrants monitoring.')
                elif 'compet' in line_stripped.lower():
                    validated_lines.append(line_stripped + ', presenting competitive challenges.')
                elif 'growth' in line_stripped.lower() or 'margin' in line_stripped.lower():
                    validated_lines.append(line_stripped + ', impacting the investment thesis.')
                else:
                    validated_lines.append(line_stripped + ', which requires attention.')
            else:
                validated_lines.append(original_line)

    return '\n'.join(validated_lines)


def _truncate_text_to_word_limit(text: str, max_words: int) -> str:
    """Trim text so it contains at most `max_words` tokens while preserving complete sentences.
    
    CRITICAL: This function NEVER returns incomplete sentences. It will always
    cut back to the last complete sentence, even if that means going significantly
    under the word limit. Complete sentences are more important than hitting word count.
    """
    if max_words <= 0:
        return ""

    matches = list(re.finditer(r"\b\w+\b", text))
    if len(matches) <= max_words:
        return text.rstrip()

    # Initial hard cutoff
    cutoff_index = matches[max_words - 1].end()
    truncated = text[:cutoff_index].rstrip()
    
    # ALWAYS find the last complete sentence - don't allow incomplete sentences
    # Look for sentence-ending punctuation (.!?) that's NOT followed by a digit
    # (to avoid cutting after "$1." in "$1.2B")
    sentence_endings = []
    for i, char in enumerate(truncated):
        if char in '.!?':
            # Check it's not a decimal point (e.g., "$1.2B" or "3.5%")
            if i + 1 < len(truncated) and truncated[i + 1].isdigit():
                continue
            # Check it's not an abbreviation mid-sentence
            if i + 1 < len(truncated) and truncated[i + 1] not in ' \n\t"\'':
                continue
            sentence_endings.append(i)
    
    if sentence_endings:
        # Use the last complete sentence
        last_sentence_end = sentence_endings[-1]
        result = truncated[:last_sentence_end + 1].rstrip()
        
        # Verify the result ends with proper punctuation
        if result and result[-1] in '.!?':
            return result
    
    # If we still can't find a good sentence ending, look in the ENTIRE text
    # for the last sentence ending before our word limit
    for i in range(len(truncated) - 1, -1, -1):
        if truncated[i] in '.!?':
            # Verify it's not a decimal
            if i + 1 < len(truncated) and truncated[i + 1].isdigit():
                continue
            return truncated[:i + 1].rstrip()
    
    # Absolute last resort: find ANY sentence ending in the original text
    # and cut there, even if it's much shorter
    for i in range(cutoff_index - 1, 0, -1):
        if text[i] in '.!?':
            if i + 1 < len(text) and text[i + 1].isdigit():
                continue
            result = text[:i + 1].rstrip()
            if result:
                return result
    
    # If there's truly no sentence ending (shouldn't happen), return what we have
    # but ensure it ends with a period
    if truncated and not truncated.rstrip().endswith(('.', '!', '?')):
        truncated = truncated.rstrip() + "."
    return truncated


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
                "ACTION: CONDENSE the text PROPORTIONALLY across ALL sections. \n"
                f"1. CUT approximately {int(abs_diff * 1.2)} words total.\n"
                "2. PROPORTIONAL CUTS - reduce EACH section by a similar percentage:\n"
                "   - Financial Health Rating: ~10% of cuts\n"
                "   - Executive Summary: ~15% of cuts\n"
                "   - Financial Performance: ~20% of cuts\n"
                "   - Management Discussion & Analysis: ~18% of cuts\n"
                "   - Risk Factors: ~10% of cuts\n"
                "   - Strategic Initiatives: ~12% of cuts\n"
                "   - Closing Takeaway: ~10% of cuts (KEEP AT LEAST 50 words)\n"
                "3. DO NOT take all cuts from one section (especially Closing Takeaway).\n"
                "4. Remove adjectives, adverbs, and filler words. Merge sentences.\n"
                "5. Keep 'Key Data Appendix' compact but complete.\n"
                "6. DO NOT append any new summary. Just condense existing sections."
            )
        elif latest_words < lower:
            words_needed = lower - latest_words
            direction_instruction = (
                f"You are {abs_diff} words SHORT of the MINIMUM requirement ({lower} words). \n"
                f"ACTION: EXPAND the content NOW. You MUST add AT LEAST {int(words_needed * 1.2)} words.\n\n"
                f"MANDATORY EXPANSION (add exactly these words per section):\n"
                f"- Financial Performance: Add {max(2, int(words_needed * 0.30))} words (deeper margin analysis)\n"
                f"- Management Discussion & Analysis: Add {max(2, int(words_needed * 0.25))} words (strategic insights)\n"
                f"- Risk Factors: Add {max(2, int(words_needed * 0.20))} words (specific scenarios)\n"
                f"- Strategic Initiatives: Add {max(2, int(words_needed * 0.15))} words (ROI expectations)\n"
                f"- Competitive Landscape: Add {max(1, int(words_needed * 0.10))} words (moat analysis)\n\n"
                f"You MUST reach at least {lower} words. Count before finishing."
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
            "2. Keep all existing section headings (Investor Lens, Executive Summary, Financial Performance, Management Discussion & Analysis, Risk Factors, "
            "Strategic Initiatives & Capital Allocation, Key Data Appendix) unless they were absent in the draft. Do NOT drop sections to save space.\n"
            "3. Retain the key figures, personas, and conclusions.\n"
            "4. EVERY sentence MUST end with proper punctuation. No cutting off with 'and the...', 'which is...', or incomplete numbers like '$1.'.\n"
            "5. ENSURE THE OUTPUT IS COMPLETE. Do not cut off the last section or the Closing Takeaway.\n"
            "6. After rewriting, append a final line formatted exactly as `WORD COUNT: ###` (replace ### with the true count)."
            f"\n\nLENGTH TARGET:\nAim for {lower}–{upper} words. BUT if you must choose between hitting the word count OR completing sentences, "
            f"ALWAYS complete your sentences. Going slightly over/under is acceptable; incomplete sentences are NOT."
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
    filing_id: Optional[str] = None,
) -> str:
    """
    Call Gemini up to MAX_SUMMARY_ATTEMPTS times, tightening instructions if word count or quality drifts.
    Uses streaming for real-time progress updates when filing_id is provided.
    """
    corrections: List[str] = []
    prompt = base_prompt
    previous_draft: Optional[str] = None
    summary_text: str = ""
    last_word_stats: Optional[Tuple[int, int]] = None  # (actual_words, tolerance)

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
        if correction_block:
            combined += "\n\n" + correction_block
        combined += previous_block
        combined += "\n\nRewrite the entire memo applying every instruction above."
        return combined

    for attempt in range(1, MAX_SUMMARY_ATTEMPTS + 1):
        if filing_id:
            attempt_label = f"Generating Summary (attempt {attempt}/{MAX_SUMMARY_ATTEMPTS})"
            progress_cache[str(filing_id)] = f"{attempt_label}... 0%"
            raw_text = gemini_client.stream_generate_content(
                prompt,
                progress_callback=_progress_callback,
                stage_name=attempt_label,
                expected_tokens=target_length * 2 if target_length else 4000
            )
        else:
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
            # Much more aggressive expansion with specific word targets
            min_words = target_length - tolerance
            action = (
                f"EXPAND the content immediately. You are {abs_diff} words SHORT of the MINIMUM ({min_words} words).\n"
                f"You MUST add AT LEAST {int(abs_diff * 1.2)} words total.\n\n"
                f"MANDATORY WORD ADDITIONS:\n"
                f"- Financial Performance: +{max(3, int(abs_diff * 0.30))} words (margin analysis, trend interpretation)\n"
                f"- Management Discussion & Analysis: +{max(2, int(abs_diff * 0.25))} words (strategic commentary)\n"
                f"- Risk Factors: +{max(2, int(abs_diff * 0.20))} words (specific 'if-then' scenarios)\n"
                f"- Strategic Initiatives: +{max(2, int(abs_diff * 0.15))} words (ROI, timelines)\n"
                f"- Competitive Landscape: +{max(1, int(abs_diff * 0.10))} words (moat sustainability)\n\n"
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
    ("Financial Health Rating", 30),
    ("Executive Summary", 100),  # HERO section - premium insight users pay for
    ("Financial Performance", 50),
    ("Management Discussion & Analysis", 50),
    ("Risk Factors", 25),
    ("Strategic Initiatives & Capital Allocation", 35),
    ("Key Data Appendix", 20),
    ("Closing Takeaway", 50),  # Must be substantive verdict - never over-shorten
]
SUMMARY_SECTION_MIN_WORDS = {title: minimum for title, minimum in SUMMARY_SECTION_REQUIREMENTS}

# Section proportional weights for distributing word budgets
# These represent relative importance/length of each section
# Sum = 100 (percentages)
# Executive Summary is the HERO section - the premium insight users pay for
SECTION_PROPORTIONAL_WEIGHTS: Dict[str, int] = {
    "Financial Health Rating": 8,
    "Executive Summary": 25,
    "Financial Performance": 16,
    "Management Discussion & Analysis": 14,
    "Risk Factors": 10,
    "Strategic Initiatives & Capital Allocation": 10,
    "Key Data Appendix": 5,
    "Closing Takeaway": 12,
}


def _calculate_section_word_budgets(
    target_length: int,
    include_health_rating: bool = True,
) -> Dict[str, int]:
    """
    Calculate proportional word budgets for each section based on target length.
    
    Distributes words across sections using weights, ensuring:
    1. Each section gets at least its minimum word count
    2. Remaining words are distributed proportionally
    3. The Closing Takeaway maintains adequate length (not over-shortened)
    """
    # Determine which sections to include
    sections_to_use = list(SECTION_PROPORTIONAL_WEIGHTS.keys())
    if not include_health_rating:
        sections_to_use = [s for s in sections_to_use if s != "Financial Health Rating"]
    
    # Calculate total weight for active sections
    total_weight = sum(
        SECTION_PROPORTIONAL_WEIGHTS.get(s, 10) for s in sections_to_use
    )
    
    # Calculate budgets
    budgets: Dict[str, int] = {}
    for section in sections_to_use:
        weight = SECTION_PROPORTIONAL_WEIGHTS.get(section, 10)
        min_words = SUMMARY_SECTION_MIN_WORDS.get(section, 25)
        
        # Calculate proportional allocation
        proportional_words = int((weight / total_weight) * target_length)
        
        # Ensure minimum is respected
        budgets[section] = max(proportional_words, min_words)
    
    return budgets


def _format_section_word_budgets(
    target_length: int,
    include_health_rating: bool = True,
) -> str:
    """
    Format section word budgets as a readable instruction string.
    """
    budgets = _calculate_section_word_budgets(target_length, include_health_rating)
    
    lines = [
        "=== SECTION WORD BUDGETS (PROPORTIONAL DISTRIBUTION) ===",
        "CRITICAL: Distribute your words PROPORTIONALLY across ALL sections.",
        f"Do NOT take all reduction from one section (especially Closing Takeaway).",
        "",
        "TARGET WORD ALLOCATION PER SECTION:",
    ]
    
    for section, budget in budgets.items():
        lines.append(f"  • {section}: ~{budget} words")
    
    total_budgeted = sum(budgets.values())
    lines.extend([
        "",
        f"  TOTAL: ~{total_budgeted} words",
        "",
        "IMPORTANT:",
        "- These are TARGET budgets, not hard limits",
        "- If you need to reduce length, reduce EACH section proportionally",
        "- The Closing Takeaway should be a COMPLETE verdict (50-80 words typical)",
        "- Do NOT sacrifice one section to make room for another",
        "=== END SECTION WORD BUDGETS ===",
    ])
    
    return "\n".join(lines)


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
        return (
            "- Use the standard structure below with a balanced, neutral tone suitable for institutional investors.\n"
            "- NO PERSONA: Write as a neutral professional analyst. Use third-person language ('The company...', 'The data indicates...').\n"
            "- FORBIDDEN: First-person language ('I', 'my view'), famous investor voices (Buffett, Munger, Graham, etc.), folksy analogies.\n"
            "- FOCUS ON: Quantitative metrics, objective analysis, evidence-based conclusions."
        )

    instructions: List[str] = [
        "=== USER CUSTOMIZATION REQUIREMENTS (MANDATORY - ZERO TOLERANCE FOR DEVIATION) ===",
        "The user has provided SPECIFIC customization preferences. You MUST follow ALL of these exactly:",
        "",
        "CRITICAL: These user preferences OVERRIDE any default behavior. Failure to comply = invalid output.",
        "",
    ]

    investor_focus = preferences.investor_focus.strip() if preferences.investor_focus else None
    if investor_focus:
        focus_clause = (
            f"{investor_focus} as it relates to {company_name}" if company_name else investor_focus
        )
        instructions.append(
            f"=== INVESTOR BRIEF (HIGHEST PRIORITY) ===\n"
            f"{focus_clause}\n"
            f"You MUST adopt this persona/viewpoint COMPLETELY. This is not optional.\n"
            f"Use STRONG first-person language ('I', 'me', 'my view', 'from my perspective').\n"
            f"EVERY section must reflect this viewpoint - not just the intro."
        )
        instructions.append(
            "\n- Begin the memo with a labeled 'Investor Lens' paragraph. Start strictly with a first-person statement identifying your persona (e.g., 'As Peter Lynch, I...'). Restate the methodology and what you are looking for. Do NOT summarize results here."
        )
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
            f"\n=== MANDATORY FOCUS AREAS (USER-SPECIFIED) ===\n"
            f"The user REQUIRES these specific topics to be covered IN THIS ORDER:\n"
            f"{joined}\n"
            f"EACH focus area MUST have its own dedicated paragraph or subsection.\n"
            f"Do NOT skip any. Do NOT add unrelated topics unless they support these."
        )
        ordered_lines = "\n".join(f"   {idx + 1}. {area}" for idx, area in enumerate(preferences.focus_areas))
        instructions.append("  Required execution order:\n" + ordered_lines)

    if preferences.tone:
        tone_upper = preferences.tone.upper()
        instructions.append(
            f"\n=== TONE REQUIREMENT (USER-SPECIFIED) ===\n"
            f"Tone: {tone_upper}\n"
            f"This tone must be CONSISTENT throughout the ENTIRE document.\n"
            f"Do NOT switch between tones. Do NOT be neutral if user specified otherwise."
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

    complexity_prompt = COMPLEXITY_LEVEL_PROMPTS.get((preferences.complexity or "intermediate").lower())
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
        include_health = bool(preferences and _resolve_health_rating_config(preferences))
        
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
- The 'Key Data Appendix' MUST be included at the end.
"""
    )

    # Add mandatory closing verdict for persona-based analyses
    if investor_focus:
        instructions.append(
            """
=== CLOSING TAKEAWAY - PERSONA VOICE REQUIREMENT (CRITICAL) ===

After the 'Key Data Appendix', you MUST include a '## Closing Takeaway' section.

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
6. PERSONAL CLOSING (MANDATORY): End with a first-person statement like "I personally would [buy/hold/sell]..." or "For my own portfolio, I would..." - this should feel like genuine advice from the persona to a friend

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

    display_style = config.get("display_style", "score_plus_grade")
    is_custom_mode = preferences and preferences.mode == "custom"
    
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
        directives.extend([
            "USER SELECTED: Score + 4 Pillars breakdown",
            "",
            "The rating MUST be calculated using a transparent formula. Show the calculation:",
            "  HEALTH RATING FORMULA (use this exact weighting):",
            "  - Profitability (30%): Net Margin > 15% = 30pts, 10-15% = 20pts, 5-10% = 10pts, <5% = 0pts",
            "  - Cash Flow Quality (25%): FCF/Net Income > 1.0 = 25pts, 0.7-1.0 = 18pts, 0.4-0.7 = 10pts, <0.4 = 0pts",
            "  - Leverage (20%): Debt/Equity < 0.5 = 20pts, 0.5-1.0 = 15pts, 1.0-2.0 = 8pts, >2.0 = 0pts",
            "  - Liquidity (15%): Current Ratio > 2.0 = 15pts, 1.5-2.0 = 12pts, 1.0-1.5 = 7pts, <1.0 = 0pts",
            "  - Growth (10%): Revenue Growth > 20% = 10pts, 10-20% = 7pts, 0-10% = 4pts, <0% = 0pts",
            "",
            "Show each component score and sum to total.",
            "Example format: 'Profitability: 20/30 + Cash Flow: 18/25 + Leverage: 15/20 + Liquidity: 12/15 + Growth: 7/10 = 72/100'",
            "",
            "FOR EACH COMPONENT: Justify the score with the actual metric value.",
            "  Example: 'Growth: 0/10 (Revenue grew only 3% YoY, below the 10% threshold for 4pts)'",
            "  Example: 'Profitability: 30/30 (Net margin of 55% exceeds the 15% threshold for full points)'",
        ])
    elif display_style == "score_only":
        directives.extend([
            "USER SELECTED: 0-100 Score Only",
            "",
            "CRITICAL - SCORE ONLY FORMAT:",
            "- Present ONLY a single overall score (e.g., '78/100').",
            "- Add a brief 1-2 sentence explanation of what drove the score.",
            "- DO NOT show letter grades, traffic lights, or pillar breakdowns.",
            "- DO NOT use the format 'Category: X/Y'.",
            "",
            f"CORRECT FORMAT: '{company_name} receives a Financial Health Rating of 78/100. Strong profitability and cash generation are offset by elevated leverage.'",
        ])
    elif display_style == "score_plus_grade":
        directives.extend([
            "USER SELECTED: Score + Letter Grade",
            "",
            "MANDATORY FORMAT (YOU MUST INCLUDE ALL ELEMENTS):",
            "1. The score (0-100) with letter grade: 90-100=A, 80-89=B, 70-79=C, 60-69=D, <60=F",
            "2. The rating label (Very Healthy/Healthy/Watch/At Risk)",
            "3. A MANDATORY explanation of 3-5 sentences (50-100 words minimum)",
            "",
            "YOUR EXPLANATION MUST COVER:",
            "- What drove the score (specific metrics with values)",
            "- Key strength(s) identified",
            "- Key concern(s) or risk(s)",
            "- How the user's selected framework influenced the assessment",
            "",
            "DO NOT just write the score and stop. The explanation is REQUIRED.",
            "",
            f"CORRECT FORMAT EXAMPLE:",
            f"'{company_name} receives a Financial Health Rating of 78/100 (C) - Healthy. The score reflects strong profitability ",
            f"with a 56% net margin and robust free cash flow generation of $22B. The balance sheet is conservatively managed ",
            f"with minimal debt relative to cash holdings. However, applying the user's value investor framework, I note concerns ",
            f"about customer concentration and cyclical demand patterns that could impact the durability of these margins. ",
            f"The score would be higher but for these risk factors that warrant monitoring.'",
            "",
            "FORBIDDEN: Just writing '{company_name} receives a Financial Health Rating of 78/100 (C) - Healthy.' and stopping.",
        ])
    elif display_style == "score_plus_traffic_light":
        directives.extend([
            "USER SELECTED: Score + Traffic Light",
            "",
            "MANDATORY FORMAT (YOU MUST INCLUDE ALL ELEMENTS):",
            "1. The score (0-100) with traffic light: 70-100=GREEN, 50-69=YELLOW, 0-49=RED",
            "2. A MANDATORY explanation of 3-5 sentences (50-100 words minimum)",
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
        ])
    elif display_style == "score_with_narrative":
        directives.extend([
            "USER SELECTED: Score + Narrative",
            "",
            "MANDATORY FORMAT - EXTENDED NARRATIVE REQUIRED:",
            "1. The score (0-100) with rating label",
            "2. A DETAILED narrative paragraph of 6-8 sentences (100-150 words minimum)",
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
        ])
    else:
        # Fallback for any other display style
        directives.extend([
            "MANDATORY FORMAT - EXPLANATION REQUIRED:",
            "1. The score (0-100) with rating label",
            "2. A MANDATORY explanation of 3-5 sentences (50-100 words minimum)",
            "",
            "YOUR EXPLANATION MUST COVER:",
            "- What metrics drove the score",
            "- Key strengths identified",
            "- Key concerns or risks",
            "",
            f"CORRECT FORMAT: '{company_name} receives a Financial Health Rating of 78/100 - Healthy. The score reflects [specific metrics]. Key strengths include [details]. However, [concerns]. Overall, [assessment].'",
            "",
            "FORBIDDEN: Just writing the score without explanation.",
        ])
    
    # Common directives for all display styles
    directives.extend([
        "",
        "RATING LABELS (MANDATORY - include with score):",
        "- 85-100 = Very Healthy",
        "- 70-84 = Healthy", 
        "- 50-69 = Watch",
        "- 0-49 = At Risk",
        "",
        "=== MINIMUM CONTENT REQUIREMENT (CRITICAL) ===",
        "The Financial Health Rating section MUST be at least 50 words.",
        "A single line like 'Company receives 78/100 - Healthy.' is INVALID.",
        "You MUST explain WHY the company received this score with specific metrics.",
        "",
        "REQUIRED ELEMENTS IN YOUR EXPLANATION:",
        "1. At least ONE specific profitability metric (e.g., 'net margin of 56%')",
        "2. At least ONE cash flow metric (e.g., 'FCF of $22B')",
        "3. At least ONE balance sheet observation (e.g., 'conservative debt levels')",
        "4. At least ONE risk or concern that impacts the score",
        "",
        "If you write fewer than 50 words for this section, your output is INVALID.",
        "=== END MINIMUM CONTENT REQUIREMENT ===",
    ])

    # Custom framework/weighting instructions for CUSTOM mode
    if is_custom_mode:
        framework = config.get("framework")
        weighting = config.get("primary_factor_weighting")
        risk = config.get("risk_tolerance")
        depth = config.get("analysis_depth")
        
        directives.append("")
        directives.append("=== USER-SPECIFIED HEALTH SCORE PARAMETERS (MUST FOLLOW) ===")
        
        framework_prompt = HEALTH_FRAMEWORK_PROMPTS.get(framework)
        if framework_prompt:
            directives.append(f"")
            directives.append(f"FRAMEWORK (User selected: {framework}):")
            directives.append(f"  {framework_prompt}")
            directives.append(f"  You MUST evaluate the company through this specific lens.")

        weighting_prompt = HEALTH_WEIGHTING_PROMPTS.get(weighting)
        if weighting_prompt:
            directives.append(f"")
            directives.append(f"PRIMARY FACTOR WEIGHTING (User selected: {weighting}):")
            directives.append(f"  {weighting_prompt}")
            directives.append(f"  This factor should have the MOST influence on the final score.")

        risk_prompt = HEALTH_RISK_PROMPTS.get(risk)
        if risk_prompt:
            directives.append(f"")
            directives.append(f"RISK TOLERANCE (User selected: {risk}):")
            directives.append(f"  {risk_prompt}")
            directives.append(f"  Apply this risk tolerance when penalizing or rewarding factors.")

        depth_prompt = HEALTH_ANALYSIS_DEPTH_PROMPTS.get(depth)
        if depth_prompt:
            directives.append(f"")
            directives.append(f"ANALYSIS DEPTH (User selected: {depth}):")
            directives.append(f"  {depth_prompt}")
            directives.append(f"  Your analysis must reach this level of depth.")
        
        directives.append("")
        directives.append("COMPLIANCE CHECK: The health score MUST reflect ALL user-specified parameters above.")
        directives.append("If the score doesn't align with user's framework, weighting, and risk tolerance, REVISE IT.")

    directives.append("")
    directives.append("PLACEMENT: The Financial Health Rating section MUST appear FIRST, before the Executive Summary.")

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
        ["net_income", "NetIncomeLoss", "NetIncome", "netIncome", "netIncomeLoss", "NetIncomeApplicableToCommonShares"],
    )
    operating_income = _extract_from_candidates(
        income_statement,
        ["operating_income", "OperatingIncomeLoss", "OperatingIncome", "operatingIncome", "OperatingIncomeLossUSD"],
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
        ["total_liabilities", "totalLiab", "TotalLiabilities", "totalLiabilities", "TotalLiabilitiesNetMinorityInterest"],
    )

    current_assets = _extract_from_candidates(
        balance_sheet,
        ["current_assets", "CurrentAssets", "TotalCurrentAssets", "totalCurrentAssets", "AssetsCurrent"],
    )
    current_liabilities = _extract_from_candidates(
        balance_sheet,
        ["current_liabilities", "CurrentLiabilities", "TotalCurrentLiabilities", "totalCurrentLiabilities", "LiabilitiesCurrent"],
    )
    inventory = _extract_from_candidates(
        balance_sheet,
        ["inventories", "Inventory", "InventoryNet", "inventory", "Inventories"],
    )
    interest_expense = _extract_from_candidates(
        income_statement,
        ["interest_expense", "InterestExpense", "interestExpense", "InterestAndDebtExpense", "InterestIncomeExpense"],
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


def _compute_health_score_data(calculated_metrics: Dict[str, Any], weighting_preset: Optional[str] = None, ai_growth_assessment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    
    total_equity = (total_assets - total_liabilities) if total_assets and total_liabilities else None
    
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
        logger.info(f"Health score ratios being passed: fcf={ratios.get('fcf')}, net_income={ratios.get('net_income')}, operating_cash_flow={ratios.get('operating_cash_flow')}, operating_margin={ratios.get('operating_margin')}, debt_to_equity={ratios.get('debt_to_equity')}, weighting_preset={weighting_preset}")
        health_data = calculate_health_score(ratios, weighting_preset=weighting_preset, ai_growth_assessment=ai_growth_assessment)
        logger.info(f"Health score component scores: {health_data.get('component_scores', {})}, weights: {health_data.get('component_weights', {})}")
        return health_data
    except Exception as e:
        logger.warning(f"Health score calculation failed: {e}")
        return {}


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
        
        # Extract user's weighting preference from health_rating settings
        weighting_preset = None
        if preferences and preferences.health_rating:
            weighting_preset = preferences.health_rating.primary_factor_weighting

        # Generate AI growth assessment based on management perspective and sector context
        ai_growth_assessment = None
        try:
            progress_cache[str(filing_id)] = "Analyzing Growth Potential..."
            # Build comprehensive ratios dict for growth context
            ratios_for_growth = {}
            if calculated_metrics.get("operating_margin") is not None:
                ratios_for_growth["operating_margin"] = calculated_metrics["operating_margin"] / 100
            if calculated_metrics.get("net_margin") is not None:
                ratios_for_growth["net_margin"] = calculated_metrics["net_margin"] / 100
            if calculated_metrics.get("revenue_growth_yoy") is not None:
                ratios_for_growth["revenue_growth_yoy"] = calculated_metrics["revenue_growth_yoy"] / 100
            if calculated_metrics.get("fcf_margin") is not None:
                ratios_for_growth["fcf_margin"] = calculated_metrics["fcf_margin"] / 100
            if calculated_metrics.get("gross_margin") is not None:
                ratios_for_growth["gross_margin"] = calculated_metrics["gross_margin"] / 100
            ai_growth_assessment = generate_growth_assessment(
                filing_text=document_text,
                company_name=company_name,
                weighting_preference=weighting_preset,
                ratios=ratios_for_growth
            )
            logger.info(f"AI growth assessment: score={ai_growth_assessment.get('score')}, description={ai_growth_assessment.get('description')}")
        except Exception as growth_err:
            logger.warning(f"AI growth assessment failed: {growth_err}")
            ai_growth_assessment = None

        # Pre-calculate health score BEFORE generating summary so we can inject it into the prompt
        print(f"DEBUG: calculated_metrics keys = {list(calculated_metrics.keys())}")
        print(f"DEBUG: free_cash_flow = {calculated_metrics.get('free_cash_flow')}, net_income = {calculated_metrics.get('net_income')}, operating_cash_flow = {calculated_metrics.get('operating_cash_flow')}")
        print(f"DEBUG: weighting_preset = {weighting_preset}")
        pre_calculated_health = _compute_health_score_data(calculated_metrics, weighting_preset=weighting_preset, ai_growth_assessment=ai_growth_assessment)
        print(f"DEBUG: pre_calculated_health component_scores = {pre_calculated_health.get('component_scores', {})}")
        print(f"DEBUG: pre_calculated_health component_weights = {pre_calculated_health.get('component_weights', {})}")
        pre_calculated_score = pre_calculated_health.get("overall_score") if pre_calculated_health else None
        pre_calculated_band = pre_calculated_health.get("score_band") if pre_calculated_health else None
        
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
        
        # Extract persona name if a persona is selected
        investor_focus = preferences.investor_focus.strip() if preferences and preferences.investor_focus else None
        selected_persona_name = _extract_persona_name(investor_focus)
        
        if include_health_rating:
             progress_cache[str(filing_id)] = "Computing Health Score..."
        
        health_config, health_rating_block = _build_health_rating_instructions(preferences, company_label)
        health_directives_section = ""
        if health_rating_block:
            health_directives_section = f"\n HEALTH RATING DIRECTIVES\n {health_rating_block}\n"
        section_descriptions: List[Tuple[str, str]] = []
        if health_rating_block:
            if pre_calculated_score is not None and pre_calculated_band:
                health_rating_description = (
                    f"!!! MANDATORY SCORE - DO NOT CHANGE !!!\n"
                    f"THE FINANCIAL HEALTH SCORE IS PRE-CALCULATED: {pre_calculated_score:.1f}/100 - {pre_calculated_band}\n\n"
                    f"YOU MUST WRITE EXACTLY: '{pre_calculated_score:.0f}/100 ({pre_calculated_band[0] if pre_calculated_band else 'W'}) - {pre_calculated_band}'\n\n"
                    f"CRITICAL RULES:\n"
                    f"1. The score is {pre_calculated_score:.1f} - DO NOT calculate a different score\n"
                    f"2. DO NOT write 1/100, 62/100, or ANY other score - ONLY {pre_calculated_score:.0f}/100\n"
                    f"3. The band is '{pre_calculated_band}' - use this EXACT label\n"
                    f"4. Start the section with: '{pre_calculated_score:.0f}/100 ({pre_calculated_band[0] if pre_calculated_band else 'W'}) - {pre_calculated_band}. ...'\n"
                    f"5. Then EXPLAIN why this score was assigned based on the metrics.\n\n"
                    f"FORBIDDEN: Calculating your own score. The score {pre_calculated_score:.1f} is mathematically computed from actual financial ratios.\n"
                    f"NO letter grades (A, B, C, D, F). Use the numeric score and band label only."
                )
            else:
                health_rating_description = (
                    "Provide the 0-100 score with descriptive label (Very Healthy 85-100, Healthy 70-84, Watch 50-69, At Risk 0-49). "
                    "NO letter grades (A, B, C, D). Format: '72/100 - Healthy'. "
                    "Explain why the score landed there with specific metrics: margins, cash flow, leverage, liquidity. "
                    "A company with 60%+ margins should score 70+ unless there are severe balance sheet issues."
                )
            section_descriptions.append(("Financial Health Rating", health_rating_description))
        section_descriptions.extend(
            [
                (
                    "Executive Summary",
                    "THIS IS THE HERO SECTION - the premium insight users pay for. MINIMUM 100 WORDS.\n\n"
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
                    "Concise quantitative overview (50-70 words). Include ONLY the most critical metrics:\n"
                    "- Revenue with YoY% change and period\n"
                    "- Operating margin and net margin\n"
                    "- Net income and FCF\n"
                    "- Cash flow quality (OCF vs Net Income ratio)\n"
                    "Keep it tight - the Executive Summary carries the narrative weight. "
                    "Focus on numbers that support your thesis, not exhaustive data.",
                ),
                (
                    "Management Discussion & Analysis",
                    "Critical management insights (50-70 words). Extract ONLY the most important forward-looking statements:\n"
                    "- Key revenue drivers or segment trends management highlighted\n"
                    "- Forward guidance or outlook if provided\n"
                    "- Strategic priorities or investments mentioned\n"
                    "Use attributions: 'management stated', 'according to the filing'. "
                    "Focus on what moves the stock, not comprehensive MD&A coverage.",
                ),
                (
                    "Risk Factors",
                    "Top 2-3 MATERIAL risks only (25-40 words total). Focus on thesis-critical risks:\n"
                    "**[Risk Name]**: [1 sentence with quantified impact if possible]\n\n"
                    "Only include risks SPECIFIC to THIS company's actual business model and industry. "
                    "Skip generic risks. What could actually break the investment thesis?",
                ),
                (
                    "Strategic Initiatives & Capital Allocation",
                    "Brief capital deployment overview (35-50 words). Key items only:\n"
                    "- R&D, CapEx, or major investments if material\n"
                    "- Buybacks/dividends if significant\n"
                    "- M&A activity if relevant\n"
                    "Assess: Is capital allocation value-accretive? Skip if nothing material to report.",
                ),
                (
                    "Key Data Appendix",
                    "Quick reference bullets (arrow format). Core metrics only:\n"
                    "→ Revenue: $X.XB | Operating Income: $X.XB | Net Income: $X.XB\n"
                    "→ Capital Expenditures: $X.XM | Total Assets: $X.XB\n"
                    "→ Operating Margin: X.X% | Net Margin: X.X%\n"
                    "Keep it scannable. Numbers MUST match narrative sections.",
                ),
                _build_closing_takeaway_description(selected_persona_name, company_name),
            ]
        )
        section_requirements = "\n".join(
            f"## {title}\n{description}" for title, description in section_descriptions
        )
        
        tone = preferences.tone or "objective"
        detail_level = preferences.detail_level or "comprehensive"
        output_style = preferences.output_style or "paragraph"

        # Build no-persona block for objective analysis when no persona is selected
        if selected_persona_name:
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
- Quantitative focus: revenue growth %, margins, ROE, debt/equity, valuation multiples
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
        else:
            identity_block = """=== CRITICAL: NO PERSONA MODE ===
YOU ARE A NEUTRAL, OBJECTIVE FINANCIAL ANALYST. 

ABSOLUTE PROHIBITION - READ THIS FIRST:
- You have NOT been assigned any investor persona
- Do NOT adopt ANY famous investor's voice or perspective
- Do NOT use first-person language ('I', 'my view', 'I would', 'I believe')
- Do NOT imitate: Warren Buffett, Charlie Munger, Peter Lynch, Benjamin Graham, Howard Marks, Bill Ackman, Ray Dalio, Cathie Wood, John Bogle, Joel Greenblatt, or ANY other investor
- Do NOT use phrases like 'As a value investor...', 'As an investor, I...'

REQUIRED WRITING STYLE:
- Write in THIRD PERSON only ('The analysis indicates...', 'The data suggests...', 'The company demonstrates...')
- Use professional equity research tone (like Goldman Sachs or Morgan Stanley analyst reports)
- Focus on quantitative metrics and evidence-based conclusions
- Provide objective, data-driven analysis

THIS IS YOUR PRIMARY DIRECTIVE. VIOLATION = INVALID OUTPUT.
=== END CRITICAL INSTRUCTION ===

You are a professional equity research analyst writing a financial briefing.
Your goal is to provide actionable, differentiated insight, not just a summary of facts."""

        base_prompt = f"""
{identity_block}
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

EVERY section header (Financial Health Rating, Executive Summary, Financial Performance, etc.) MUST:
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
- Do NOT use markdown bolding (**) within the text body. Only use it for section headers if needed.
- Ensure every claim is backed by the provided text or metrics.
- If data is missing, omit that data point rather than saying "not disclosed" or "not available".
- SYNTHESIZE, DO NOT SUMMARIZE. Tell us what the numbers mean, not just what they are.
- SPECIFY TIME PERIODS: Always label figures with their time period (FY24, Q3 FY25, TTM, etc.).
- NO REDUNDANCY: Each number should appear in only ONE section. Executive Summary = qualitative view. Financial Performance = all numbers.
- **SUSTAINABILITY**: Do NOT mention sustainability or ESG efforts unless they are a primary revenue driver (e.g., for a solar company). For most companies, this is fluff.
- **MD&A**: Do NOT say "Management discusses..." or "In the MD&A section...". Just state the facts found there.
- USE TRANSITIONS: Connect sections logically. Each section should flow naturally from the previous one.

=== #1 PRIORITY: SENTENCE COMPLETION (OVERRIDES WORD COUNT) ===
THIS IS YOUR SINGLE MOST IMPORTANT RULE. IT TAKES PRIORITY OVER WORD COUNT.

FUNDAMENTAL PRINCIPLE: It is ALWAYS better to exceed the word count by 100 words than to cut off a single sentence.

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
   - Any sentence ending with: the, a, an, of, to, for, with, in, and, but, or, while, although, I

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
- If you're at 640 words and need to finish a sentence, FINISH IT (even if you go to 700 words)
- If you're at the limit, DO NOT start a new thought you can't finish
- Plan your sections so you have room to complete the Closing Takeaway fully

BEFORE SUBMITTING - MANDATORY CHECK:
Read the LAST WORD of EVERY sentence. If it's an article, preposition, conjunction, pronoun, or incomplete number, REWRITE IT.

=== END SENTENCE COMPLETION REQUIREMENT ===

NARRATIVE QUALITY:
- Start each section with a clear topic sentence that states the key insight.
- End each section with a forward-looking implication or action item that is COMPLETE.
- Avoid starting consecutive sentences with the same word.
- Vary sentence length and structure for readability.
- THE LAST SENTENCE OF EACH SECTION MUST BE A COMPLETE THOUGHT ending in a period, question mark, or exclamation point.
- If you write a subordinate clause (starting with "which", "that", "although", "while", "but"), you MUST complete it.
- BUDGET YOUR WORDS: If the target is 650 words, plan to write ~550 words for main sections, leaving 100 words for proper conclusions.

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
- Do NOT cut off numbers mid-way (e.g., "$1." instead of "$1.2B") - always write complete figures.

=== FINAL PRE-SUBMISSION CHECKLIST (MANDATORY) ===
Before you output anything, verify:
[ ] Every sentence ends with . ? or ! (not with "and", "the", "of", "I", etc.)
[ ] All dollar amounts are complete (e.g., "$18.77B" not "$18.")
[ ] The Closing Takeaway section is FULLY complete with a clear verdict
[ ] No section ends mid-thought
[ ] If you're over the word count, that's OK - incomplete sentences are NOT OK
=== END CHECKLIST ===
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
            filing_id=filing_id,
        )
        
        progress_cache[str(filing_id)] = "Polishing Output..."
        # Post-processing to ensure structure
        summary_text = _fix_inline_section_headers(summary_text)  # CRITICAL: Fix headers appearing inline first
        summary_text = _normalize_section_headings(summary_text, include_health_rating)
        summary_text = _fix_trailing_ellipsis(summary_text)  # Fix sentences ending with ...
        summary_text = _validate_complete_sentences(summary_text)  # Fix other incomplete sentences
        summary_text = _ensure_required_sections(
            summary_text,
            include_health_rating=include_health_rating,
            metrics_lines=metrics_lines,
            calculated_metrics=calculated_metrics,
            company_name=company_name,
            health_rating_config=health_config,
            persona_name=selected_persona_name,
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
                persona_name=selected_persona_name,
            )
            summary_text = _finalize_length_band(summary_text, target_length, tolerance=10)
            # Final pass to normalize headings and length in case prior rewrites removed structure
            summary_text = _fix_inline_section_headers(summary_text)
            summary_text = _normalize_section_headings(summary_text, include_health_rating)
            summary_text = _ensure_required_sections(
                summary_text,
                include_health_rating=include_health_rating,
                metrics_lines=metrics_lines,
                calculated_metrics=calculated_metrics,
                company_name=company_name,
                health_rating_config=health_config,
                persona_name=selected_persona_name,
            )
            summary_text = _finalize_length_band(summary_text, target_length, tolerance=10)
            summary_text = _force_final_band(summary_text, target_length, tolerance=10)

        # Final ellipsis cleanup after all length adjustments
        summary_text = _fix_trailing_ellipsis(summary_text)
        
        # Fix health score if AI generated a different score than pre-calculated
        if pre_calculated_score is not None and pre_calculated_band:
            summary_text = _fix_health_score_in_summary(
                summary_text,
                pre_calculated_score,
                pre_calculated_band,
            )

        # Use pre-calculated health score data (computed before summary generation)
        health_score_data = pre_calculated_health

        # Cache result
        if use_default_cache:
            fallback_filing_summaries[str(filing_id)] = summary_text
        
        response_data = {
            "filing_id": filing_id,
            "summary": summary_text,
            "cached": False
        }
        
        if health_score_data:
            response_data["health_score"] = health_score_data.get("overall_score")
            response_data["health_band"] = health_score_data.get("score_band")
            response_data["health_components"] = health_score_data.get("component_scores")
            response_data["health_component_weights"] = health_score_data.get("component_weights")
            response_data["health_component_descriptions"] = health_score_data.get("component_descriptions")
            response_data["health_component_metrics"] = health_score_data.get("component_metrics")
        
        return JSONResponse(content=response_data)
        
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
    """Ensure each required section begins with the expected markdown heading on its own line.
    
    This handles cases where:
    1. Headers appear inline with content (e.g., "...business. ## Executive Summary As Bill...")
    2. Headers are missing the ## prefix
    3. Headers have extra whitespace or formatting issues
    """
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
    
    # CRITICAL: First, handle INLINE section headers (headers appearing mid-line)
    # This catches patterns like "...business. ## Executive Summary As Bill..."
    # and splits them into proper separate lines
    for title in required_titles:
        # Pattern to find inline headers: text before + ## Title + text after (all on same conceptual line)
        # We need to insert newlines before and after the header
        inline_pattern = re.compile(
            rf'([.!?])\s*(?:##?\s*)?({re.escape(title)})\s*',
            re.IGNORECASE
        )
        # Replace with: punctuation + double newline + ## Title + double newline
        normalized_text = inline_pattern.sub(
            lambda m: f'{m.group(1)}\n\n## {title}\n\n',
            normalized_text
        )
    
    # Also handle cases where the header appears without preceding punctuation but inline
    # e.g., "some text ## Executive Summary more text"
    for title in required_titles:
        inline_no_punct_pattern = re.compile(
            rf'(\S)\s+(?:##?\s*)({re.escape(title)})\s+(\S)',
            re.IGNORECASE
        )
        normalized_text = inline_no_punct_pattern.sub(
            lambda m: f'{m.group(1)}\n\n## {title}\n\n{m.group(3)}',
            normalized_text
        )

    # Now normalize headers that are on their own lines but might be missing ##
    for title in required_titles:
        pattern = re.compile(rf"(^|\n)\s*(?:##\s*)?{re.escape(title)}\s*(?:\n|$)", re.IGNORECASE | re.MULTILINE)
        normalized_text = pattern.sub(lambda _: f"\n\n## {title}\n\n", normalized_text, count=1)
    
    # Clean up any excessive newlines (more than 2 consecutive)
    normalized_text = re.sub(r'\n{4,}', '\n\n\n', normalized_text)
    
    # Ensure headers have exactly one blank line before and after
    for title in required_titles:
        # Fix cases where header doesn't have proper spacing
        header_spacing_pattern = re.compile(
            rf'([^\n])(\n*)(\s*##\s*{re.escape(title)})(\n*)([^\n])',
            re.IGNORECASE
        )
        def ensure_spacing(m):
            before_char = m.group(1)
            before_newlines = '\n\n' if before_char not in '\n' else ''
            after_newlines = '\n\n'
            after_char = m.group(5)
            return f'{before_char}{before_newlines}## {title}{after_newlines}{after_char}'
        
        normalized_text = header_spacing_pattern.sub(ensure_spacing, normalized_text)
    
    # Final cleanup: ensure no header is followed immediately by another header without content
    normalized_text = re.sub(r'(## [^\n]+)\n\n(## )', r'\1\n\n[Section content pending]\n\n\2', normalized_text)
    
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


def _generate_fallback_closing_takeaway(
    company_name: str,
    calculated_metrics: Dict[str, Any],
    persona_name: Optional[str] = None,
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
    total_debt = calculated_metrics.get("total_debt") or calculated_metrics.get("total_liabilities")
    revenue = calculated_metrics.get("revenue") or calculated_metrics.get("total_revenue")
    
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
    if persona_name and persona_name in PERSONA_CLOSING_INSTRUCTIONS:
        return _generate_persona_flavored_closing(
            persona_name, company_name, strengths, concerns, 
            quality, is_positive, is_mixed, revenue, operating_margin
        )
    
    # Generic fallback (no persona selected) - concise but complete (~40-50 words)
    sentences = []
    
    if strengths and not concerns:
        sentences.append(f"{company_name} demonstrates {quality} financial characteristics with {' and '.join(strengths[:2])}.")
        sentences.append("The fundamentals support a constructive long-term outlook.")
        sentences.append("Consider initiating positions on valuation pullbacks.")
    elif concerns and not strengths:
        sentences.append(f"{company_name} faces financial headwinds including {' and '.join(concerns[:2])}.")
        sentences.append("These challenges warrant caution until management demonstrates tangible operational improvement.")
        sentences.append("Monitor for margin expansion and improved cash conversion before committing capital.")
    elif strengths and concerns:
        sentences.append(f"{company_name} presents a mixed picture: {strengths[0]} offset by {concerns[0]}.")
        sentences.append("A neutral stance is appropriate until greater clarity emerges.")
        sentences.append("Watch for an inflection point in the problem areas.")
    else:
        # Minimal data available
        if revenue:
            rev_str = _format_dollar(revenue)
            sentences.append(f"{company_name}, with {rev_str} in revenue, requires deeper analysis.")
        else:
            sentences.append(f"{company_name} requires deeper due diligence to form a definitive view.")
        sentences.append("Evaluate strategic initiatives and management's capital allocation before investing.")
    
    return " ".join(sentences)


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
    
    strengths_str = " and ".join(strengths[:2]) if strengths else "limited visibility into fundamentals"
    concerns_str = " and ".join(concerns[:2]) if concerns else "no major red flags"
    margin_str = f"{operating_margin:.1f}%" if operating_margin else "undisclosed margins"
    
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
            return (
                f"Where are we in the cycle? {company_name} presents {strengths[0] if strengths else 'some positives'} "
                f"alongside {concerns[0] if concerns else 'risks'}. The correlation to macro factors warrants careful position sizing."
            )
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
            return (
                f"{company_name} has innovation potential, but {concerns[0] if concerns else 'execution risk'} creates uncertainty. "
                f"I am watching for Wright's Law dynamics to emerge before increasing conviction."
            )
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
            return (
                f"Where are we in the cycle? {company_name} has {strengths[0] if strengths else 'positives'} but {concerns[0] if concerns else 'risks'}. "
                f"Second-level thinking suggests waiting for the pendulum to swing further before committing capital."
            )
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
    company_name: str,
    health_rating_config: Optional[Dict[str, Any]] = None,
    persona_name: Optional[str] = None,
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

    # 8. Closing Takeaway - ensure there's a closing verdict if missing OR too short
    # Generate a data-driven closing takeaway if the AI failed to include one
    # Also replace if the existing one is under the minimum word count
    # Pass persona_name to maintain persona voice in fallback
    min_closing_words = SUMMARY_SECTION_MIN_WORDS.get("Closing Takeaway", 75)
    
    # Check if closing takeaway exists and count its words
    existing_closing = None
    closing_match = re.search(r'##\s*Closing\s+Takeaway\s*\n+([\s\S]*?)(?=\n##\s|\Z)', text, re.IGNORECASE)
    if closing_match:
        existing_closing = closing_match.group(1).strip()
        existing_word_count = len(existing_closing.split())
    else:
        existing_word_count = 0
    
    if not _section_present("Closing Takeaway") or existing_word_count < min_closing_words:
        closing_body = _generate_fallback_closing_takeaway(company_name, calculated_metrics, persona_name)
        if existing_closing and existing_word_count < min_closing_words:
            # Remove the short closing takeaway and replace it
            text = re.sub(r'##\s*Closing\s+Takeaway\s*\n+[\s\S]*?(?=\n##\s|\Z)', '', text, flags=re.IGNORECASE)
        _append_section("Closing Takeaway", closing_body)

    return text


@router.get("/{filing_id}/progress")
async def get_filing_summary_progress(filing_id: str):
    """Get real-time progress of summary generation."""
    status = progress_cache.get(str(filing_id), "Initializing...")
    return {"status": status}
