"""
prompt_pack.py — Narrative-first prompt templates for filing summaries.

This module provides an outline-first, two-pass approach to generating
analyst-memo quality summaries with narrative arc.  It is industry-agnostic:
every template uses dynamic context variables filled at call time.

Public API
----------
build_outline_prompt(ctx)       → str   (Pass 1 — outline with claims + evidence anchors)
build_expansion_prompt(ctx, outline) → str   (Pass 2 — expand each section from the outline)
build_single_pass_prompt(ctx)   → str   (Combined prompt for single-call workflows)
build_prompt_from_legacy_args(...)  → str   (Drop-in replacement for old _build_summary_prompt)
parse_narrative_summary(raw_text)   → Dict[str, str]  (Parse LLM output → section dict)
score_to_band(score)            → str   (Health score → band label)
get_section_template(name)      → SectionTemplate
SECTION_TEMPLATES               → dict[str, SectionTemplate]
ANTI_BOREDOM_RULES              → str   (injectable constraint block)
QUOTE_BEHAVIOR_SPEC             → str   (injectable quote rules)
NUMERIC_DENSITY_CAPS            → dict[str, int]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.summary_budget_controller import (
    compute_depth_plan,
    describe_paragraph_range,
    describe_sentence_range,
    get_section_shape,
    section_budget_tolerance_words,
    total_word_tolerance_words,
)


# ---------------------------------------------------------------------------
# Numeric density caps — max numbers per 100 words by section
# ---------------------------------------------------------------------------
NUMERIC_DENSITY_CAPS: Dict[str, int] = {
    "Executive Summary": 2,
    "Financial Performance": 5,
    "Management Discussion & Analysis": 3,
    "Risk Factors": 2,
    "Closing Takeaway": 2,
    "Financial Health Rating": 4,
    "Key Metrics": 99,  # data block — no prose cap
}


# ---------------------------------------------------------------------------
# Anti-boredom constraint block — injected verbatim into every prompt
# ---------------------------------------------------------------------------
ANTI_BOREDOM_RULES: str = """\
ANTI-BOREDOM CONSTRAINTS (mandatory — violations will be rejected):
1. NO REPEATED SENTENCES OR PHRASES: Every sentence must introduce a new fact,
   mechanism, or conclusion.  If a sentence could be deleted without weakening
   the argument, delete it.  Never restate a point using synonyms
   (track/watch/monitor/verify/assess are the SAME word — pick ONE).
2. NO MECHANICAL PHRASING: Banned openers — "Additionally," "Furthermore,"
   "Moreover," "It is also worth noting," "It should be noted that,"
   "Importantly," "Notably," "Significantly," "Moving on to."  Start sentences
   with the subject of the claim instead.
3. CAUSAL STORYTELLING: Prefer cause-and-effect chains.  Use constructions
   like "This matters because…", "…which in turn…", "The implication is…",
   "That creates a setup where…", "…therefore…", "…setting up a tension
   between…".  Each paragraph should feel like it is *building* toward
   something, not listing observations.
4. VARY SENTENCE LENGTH: Mix short punchy sentences (6-10 words) with longer
   analytical ones (20-30 words).  Never put three sentences of similar length
   in a row.  Short sentences work best for verdicts; long ones for evidence.
5. NUMERIC DENSITY CAPS: In qualitative sections (Executive Summary, MD&A,
   Risk Factors, Closing Takeaway), use at most 3 numbers per 100 words.
   Financial Performance may go up to 5 per 100 words.  Every number must be
   followed by an interpretation sentence.
6. NO CORPORATE FLUFF: Banned phrases — "showcases its dominance,"
   "driving shareholder value," "incredibly encouraging," "clear indication,"
   "fueling future growth," "welcome addition," "poised for growth,"
   "testament to," "remains to be seen," "robust financial picture,"
   "leveraging synergies," "well-positioned."  Replace each with a specific,
   evidence-grounded claim.
7. NO PROCESS / META LANGUAGE: Never write "this memo will," "this section
   covers," "as instructed," "per the filing."  Just deliver the analysis.
8. SENTENCE-OPENING VARIETY: No two consecutive sentences may start with the
   same word.  Across the full memo, no opening word may appear more than
   three times.
"""


# ---------------------------------------------------------------------------
# Quote behavior specification
# ---------------------------------------------------------------------------
QUOTE_BEHAVIOR_SPEC: str = """\
MANAGEMENT QUOTES — HANDLING RULES:
1. Include 3-8 high-signal direct quotes ONLY if they appear verbatim in the
   provided filing text or filing-language snippets.  A "high-signal" quote
   reveals strategy, outlook, risk acknowledgment, or competitive positioning
   — not boilerplate legal language. Quote management verbatim to support claims.
2. Place quotes where they advance the argument: Executive Summary (1-2 max),
   MD&A (2-4 max), Risk Factors (0-2 max).  Never stack quotes back-to-back.
3. Keep each quote ≤25 words.  Introduce it with context ("Management
   acknowledged pricing headwinds, noting that…") rather than bare attribution
   ("The CEO said…").
4. FALLBACK when no filing snippets are available: paraphrase management's
   position with attribution — "Management indicated that…,"
   "The company described its strategy as…."  Use at least two such
   paraphrases across the memo.
5. NEVER FABRICATE QUOTES.  If the filing text does not contain a quotable
   statement on a topic, use paraphrase-with-attribution instead.
6. After the first use, vary attribution verbs: noted, acknowledged,
   emphasized, highlighted, cautioned, described, characterized, indicated.
   Never repeat the same verb within 200 words.
7. COPY QUOTES CHARACTER-BY-CHARACTER: When you include a direct quote, copy
   the exact words from the filing text with zero modifications.  Use standard
   straight quotes ("...") — never smart/curly quotes.
"""


# ---------------------------------------------------------------------------
# Section template dataclass
# ---------------------------------------------------------------------------
@dataclass
class SectionTemplate:
    """Prompt components for one section of the summary."""

    name: str
    system_guidance: str          # Injected into system prompt role framing
    user_prompt_template: str     # {}-style template filled with context vars
    do_rules: List[str]           # Positive instructions
    dont_rules: List[str]         # Prohibitions
    max_numeric_density: int      # Per 100 words
    outline_anchor: str           # What the outline step should produce for this section
    transition_into: str          # How the previous section should hand off
    transition_out: str           # How this section should hand off to the next


# ---------------------------------------------------------------------------
# Per-section templates
# ---------------------------------------------------------------------------
_EXECUTIVE_SUMMARY = SectionTemplate(
    name="Executive Summary",
    system_guidance=(
        "You are writing the thesis paragraph of an institutional investment memo. "
        "Your goal is to frame the ONE central tension this company faces — the "
        "strategic question that every subsequent section will test."
    ),
    user_prompt_template="""\
Write the Executive Summary for {company_name} ({filing_type}, {filing_period}).

COMPANY CONTEXT:
- Industry: {industry}
- Business model: {business_model}
- Key segments: {key_segments}

THE TASK: In 2-3 paragraphs of flowing prose, accomplish these goals:
1. Open with what this company IS and WHY it matters — its competitive position,
   its business model, and the market it serves.  Ground the reader in the
   business before any numbers.
2. Identify ONE central tension (e.g., "exceptional margin quality vs. rising
   reinvestment demands," or "accelerating revenue vs. deepening customer
   concentration").  State it clearly as a question the memo will answer.
3. Use at most 2 anchor figures total — this section is about framing, not
   measurement.
4. End with a forward-looking sentence that naturally raises the question
   Financial Performance will answer.

{quote_instruction}
{budget_instruction}
""",
    do_rules=[
        "Lead with the business story, not metrics",
        "State the central tension explicitly as a question or contrast",
        "Use one filing quote if snippets are available",
        "End with a bridge sentence into Financial Performance",
        "Make the reader feel they understand what the company does",
    ],
    dont_rules=[
        "Do NOT start with a revenue or income figure",
        "Do NOT list metrics — weave any numbers into narrative claims",
        "Do NOT use bullet points or numbered lists",
        "Do NOT use meta language ('this memo will…')",
        "Do NOT exceed 2 numeric anchors",
    ],
    max_numeric_density=2,
    outline_anchor="Central tension (1 sentence), thesis claim, 1-2 evidence anchors",
    transition_into="(first section — no inbound transition)",
    transition_out="Raise a question that Financial Performance will answer with data",
)

_FINANCIAL_PERFORMANCE = SectionTemplate(
    name="Financial Performance",
    system_guidance=(
        "You are the evidence section of an investment memo.  Your job is to "
        "test the thesis with the 2-3 most relevant numbers, interpreting each "
        "through a causal lens."
    ),
    user_prompt_template="""\
Write the Financial Performance section for {company_name}.

THESIS TENSION (from Executive Summary): {central_tension}

FINANCIAL DATA:
{financial_snapshot}
{metrics_lines}
{prior_period_delta}

THE TASK: In connected prose, accomplish these goals:
1. Pick the 2-3 numbers that most directly TEST the Executive Summary's tension.
2. Weave them into a causal argument: each number should answer "so what?" for
   the thesis.  Show cause-and-effect, not a metrics parade.
3. Compare latest period vs. prior comparable period (QoQ for 10-Q, YoY for 10-K)
   where data exists.
4. End by surfacing an execution or capital-allocation question that MD&A will
   address.

{budget_instruction}
""",
    do_rules=[
        "Start by answering the question the Executive Summary raised",
        "Interpret every number — what does it MEAN for the thesis?",
        "Use causal language (because, therefore, which implies)",
        "Include period-over-period comparison where data exists",
        "End with a management execution question leading into MD&A",
    ],
    dont_rules=[
        "Do NOT list metrics mechanically ('Revenue was $X, up Y%')",
        "Do NOT repeat figures from the Health Rating or Executive Summary",
        "Do NOT use more than 4-5 numeric anchors",
        "Do NOT include metrics without interpretation",
    ],
    max_numeric_density=5,
    outline_anchor="2-3 key metrics chosen, each with a 'so what' claim",
    transition_into="Answer the question the Executive Summary raised",
    transition_out="Surface a management execution question for MD&A",
)

_MDA = SectionTemplate(
    name="Management Discussion & Analysis",
    system_guidance=(
        "You are analyzing management's strategic choices and business mechanisms. "
        "This section is about UNDERSTANDING THE BUSINESS — why results changed, "
        "what management is doing about it, and whether their strategy supports "
        "or undermines the thesis.  Numbers are evidence, not structure."
    ),
    user_prompt_template="""\
Write the Management Discussion & Analysis section for {company_name}.

THESIS TENSION: {central_tension}
INDUSTRY CONTEXT: {industry}, {business_model}

MDA EXCERPT FROM FILING:
{mda_excerpt}

THE TASK: In 2-3 connected paragraphs, accomplish these goals:
1. Explain WHY results changed — business drivers, market shifts, management
   decisions.  Lead with qualitative insight, not metrics.
2. Quote or closely paraphrase management's own words about strategy, priorities,
   or outlook (at least one verbatim quote if snippets are available). Quote management verbatim to support claims.
3. Describe the business model mechanism: HOW does this company make money, and
   what is management doing to protect or grow that mechanism?
4. Discuss forward-looking plans: investments, market expansion, product roadmap,
   capital allocation philosophy.
5. Use at most 2 anchor figures — this section is about MECHANISM and STRATEGY.
6. End by surfacing what could go wrong with this strategy — a natural bridge
   into Risk Factors.

{quote_instruction}
{budget_instruction}
""",
    do_rules=[
        "Lead every paragraph with a business insight or strategic observation",
        "Include at least one verbatim management quote if filing snippets available",
        "Explain business model mechanics — how money is made and protected",
        "Discuss forward-looking plans and capital allocation",
        "End with a risk implication that bridges into Risk Factors",
    ],
    dont_rules=[
        "Do NOT lead with revenue or income figures",
        "Do NOT say 'Management discusses…' or 'In the MD&A section…'",
        "Do NOT restate metrics from Financial Performance",
        "Do NOT speculate beyond what the filing evidence supports",
        "Do NOT exceed 2 numeric anchors",
        "Do NOT write 'revenue was $X, up Y%' style sentences",
    ],
    max_numeric_density=3,
    outline_anchor="Key business driver, management quote anchor, strategy assessment",
    transition_into="Answer the execution question Financial Performance raised",
    transition_out="Surface what could go wrong → Risk Factors",
)

_RISK_FACTORS = SectionTemplate(
    name="Risk Factors",
    system_guidance=(
        "You are identifying genuine business risks — competitive, regulatory, "
        "technological, concentration, and market-dynamic threats.  Focus on "
        "mechanisms that could break the thesis, not symptoms like 'margins declined.'"
    ),
    user_prompt_template="""\
Write the Risk Factors section for {company_name}.

THESIS TENSION: {central_tension}
INDUSTRY: {industry}
BUSINESS MODEL: {business_model}

RISK FACTORS EXCERPT FROM FILING:
{risk_factors_excerpt}

SCHEMA (MANDATORY — violations will be rejected):
Write the exact number of risks required by the section budget. Each risk must follow this exact format:

**[Risk Name]:** budget-aware narrative describing the company-specific mechanism, financial impact pathway, and early-warning signal.

Rules:
- Risk Name must be specific to {company_name} — NOT a generic category label.
- Use the sentence range required by the section budget for each risk body.
- Sentence 1: the mechanism — how this risk operates for this specific company.
- The remaining sentences: the financial impact pathway — what breaks in the P&L, balance sheet, or competitive position.
- Include one concrete early-warning signal per risk.
- NO generic macro filler (e.g., "interest rate changes", "macroeconomic volatility" without company-specific context).
- NO merged risks (do not combine two distinct risks into one entry).
- NO thematic repetition — each risk must address a different mechanism.

{budget_instruction}
""",
    do_rules=[
        "Name specific, company-relevant risks — not generic categories",
        "Explain the causal mechanism for each risk",
        "Connect risks to vulnerabilities surfaced in preceding sections",
        "Include one early-warning signal per risk",
        "Use industry-specific knowledge to identify real threats",
        "Do NOT use generic risks; only highly company-specific risks",
    ],
    dont_rules=[
        "Do NOT list 'margin compression' or 'revenue deceleration' as risks — those are SYMPTOMS",
        "Do NOT use generic risks ('macroeconomic volatility') without company-specific context",
        "Do NOT turn this into another metrics section — numbers are optional support",
        "Do NOT repeat figures from earlier sections",
        "Do NOT use 'remains to be seen' or similar hedges",
    ],
    max_numeric_density=2,
    outline_anchor="2-3 named risks, each with mechanism and early-warning signal",
    transition_into="Pick up the strategic vulnerability MD&A surfaced",
    transition_out="Set up the verdict — what does this risk picture mean for the thesis?",
)

_CLOSING_TAKEAWAY = SectionTemplate(
    name="Closing Takeaway",
    system_guidance=(
        "You are delivering the verdict.  This section synthesizes the entire memo "
        "into a clear, forward-looking conclusion.  The reader should feel this "
        "verdict is the inevitable result of everything above."
    ),
    user_prompt_template="""\
Write the Closing Takeaway for {company_name}.

THESIS TENSION: {central_tension}
KEY EVIDENCE: {key_evidence_summary}

THE TASK:
1. Synthesize the story — connect the thesis tension, the evidence, management's
   response, and the risk picture into one coherent conclusion.
2. State a clear BUY / HOLD / SELL verdict that feels like the inevitable
   conclusion of everything above.
3. Follow the budget-specific trigger rules below. Short sections use one measurable
   trigger; long-form sections use one "what must stay true" trigger and one
   "what breaks the thesis" trigger.
4. Include one implication for capital allocation, cash generation, or valuation
   support when the budget allows it.
5. Make it forward-looking without introducing new facts.

{persona_instruction}
{budget_instruction}
""",
    do_rules=[
        "Open by connecting back to the central tension",
        "State a decisive verdict — BUY, HOLD, or SELL",
        "Name exactly ONE measurable trigger that changes the view",
        "Make it forward-looking",
        "Keep it tight — this is a destination, not a new section of analysis",
    ],
    dont_rules=[
        "Do NOT list multiple 'if X holds near Y' conditions — pick ONE trigger",
        "Do NOT introduce new facts not discussed earlier",
        "Do NOT use 'remains a key check/checkpoint'",
        "Do NOT use parenthetical asides or qualifier chains",
        "Do NOT use watch-list filler ('also monitor…', 'track…')",
        "Do NOT exceed 2 numeric anchors",
    ],
    max_numeric_density=2,
    outline_anchor="Verdict (BUY/HOLD/SELL), one-sentence rationale, one trigger",
    transition_into="This is the destination the entire memo has been building toward",
    transition_out="(final section — no outbound transition)",
)

_FINANCIAL_HEALTH_RATING = SectionTemplate(
    name="Financial Health Rating",
    system_guidance=(
        "You are establishing the financial baseline.  Present the pre-calculated "
        "health score and explain in budget-aware prose why the score is "
        "what it is and not higher or lower."
    ),
    user_prompt_template="""\
Write the Financial Health Rating for {company_name}.

PRE-CALCULATED SCORE: {health_score}/100 — {health_band}
KEY DRIVERS: {health_drivers}

THE TASK:
1. Start with: "{health_score}/100 — {health_band}."
2. In coherent prose sized to the section budget, explain why this score and not higher or lower.
   Anchor on the 2-3 metrics that most influenced the score.
3. End with one sentence that sets up the operating analysis that follows.

{budget_instruction}
""",
    do_rules=[
        "Use the exact pre-calculated score — never compute your own",
        "Anchor explanation on 2-3 key metric drivers",
        "End with a bridge into Executive Summary",
    ],
    dont_rules=[
        "Do NOT calculate a different score",
        "Do NOT use letter grades (A, B, C, D)",
        "Do NOT list every available metric — be selective",
        "Do NOT use markdown sub-headers (## or ###) within this section — use bold text (**Label**) for sub-categories instead",
    ],
    max_numeric_density=4,
    outline_anchor="Score, band label, 2-3 driver metrics",
    transition_into="(opening section — no inbound transition)",
    transition_out="Set up the thesis question Executive Summary will frame",
)

_KEY_METRICS = SectionTemplate(
    name="Key Metrics",
    system_guidance=(
        "Produce a scannable NUMERIC data appendix. EVERY line MUST start "
        "with '→ ' followed by a metric name, colon, and a NUMERIC value. "
        "NO prose sentences. NO narrative paragraphs. NO explanations. "
        "ONLY arrow-format data lines."
    ),
    user_prompt_template="""\
Write the Key Metrics data block for {company_name}.

FORMAT RULES (non-negotiable):
- EVERY line: → MetricName: NumericValue
- Examples of CORRECT:
  → Revenue: $21.7B
  → Operating Margin: 30.3%
  → Free Cash Flow: $8.21B | +188.1% QoQ
  → Current Ratio: 2.9x
- Examples of INCORRECT (NEVER do this):
  → The company's Revenue was $21.73B, which is a good sign.
  → We saw growth in revenue due to demand.

PRE-FORMATTED DATA (use these EXACT values):
{metrics_lines}

CRITICAL: Do NOT include blank lines between data rows. Wrap all rows with
DATA_GRID_START and DATA_GRID_END markers on their own lines.

{budget_instruction}
""",
    do_rules=[
        "Every line: → MetricName: NumericValue (with $, %, x formatting)",
        "Include period-over-period change where available (e.g., | +5.2% YoY)",
        "Group by category: Profitability, Liquidity, Leverage, Cash Flow",
        "Omit missing metrics — never write N/A or 'not available'",
    ],
    dont_rules=[
        "Do NOT add any narrative interpretation or prose sentences",
        "Do NOT write lines without actual numeric values",
        "Do NOT explain what metrics mean — just present the data",
        "Do NOT invent numbers not in the provided data",
    ],
    max_numeric_density=99,
    outline_anchor="List of available metrics in arrow format",
    transition_into="Data appendix — no narrative transition",
    transition_out="Data appendix — no narrative transition",
)


# ---------------------------------------------------------------------------
# Section template registry
# ---------------------------------------------------------------------------
SECTION_TEMPLATES: Dict[str, SectionTemplate] = {
    "Financial Health Rating": _FINANCIAL_HEALTH_RATING,
    "Executive Summary": _EXECUTIVE_SUMMARY,
    "Financial Performance": _FINANCIAL_PERFORMANCE,
    "Management Discussion & Analysis": _MDA,
    "Risk Factors": _RISK_FACTORS,
    "Key Metrics": _KEY_METRICS,
    "Closing Takeaway": _CLOSING_TAKEAWAY,
}

# Canonical section order
SECTION_ORDER: List[str] = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Key Metrics",
    "Closing Takeaway",
]


def get_section_template(name: str) -> Optional[SectionTemplate]:
    """Look up a section template by name (case-insensitive fuzzy match)."""
    if name in SECTION_TEMPLATES:
        return SECTION_TEMPLATES[name]
    lower = name.lower()
    for key, tmpl in SECTION_TEMPLATES.items():
        if key.lower() == lower:
            return tmpl
    return None


# ---------------------------------------------------------------------------
# Context dataclass — everything a prompt needs
# ---------------------------------------------------------------------------
@dataclass
class PromptContext:
    """All dynamic context variables needed to fill prompt templates.

    Every field is a plain string or int so callers do not need to import
    domain models.  Fields with defaults can be omitted for simpler calls.
    """

    company_name: str
    filing_type: str = ""              # e.g. "10-K", "10-Q"
    filing_period: str = ""            # e.g. "FY2024", "Q3 FY2025"
    filing_date: str = ""
    industry: str = "Not specified"
    business_model: str = "Not specified"
    key_segments: str = "Not specified"
    ticker: str = ""
    exchange: str = ""
    sector: str = ""
    country: str = ""

    # Financial data (pre-formatted strings)
    financial_snapshot: str = ""
    metrics_lines: str = ""
    prior_period_delta: str = ""

    # Filing excerpts
    context_excerpt: str = ""          # Main filing text
    mda_excerpt: str = ""              # MD&A section text
    risk_factors_excerpt: str = ""     # Risk Factors section text
    filing_language_snippets: str = "" # Pre-extracted quotable snippets

    # Health rating (pre-calculated)
    health_score: Optional[float] = None
    health_band: str = ""
    health_drivers: str = ""

    # Company research brief (hidden context)
    company_research_brief: str = ""

    # User preferences
    tone: str = "objective"
    detail_level: str = "comprehensive"
    output_style: str = "paragraph"
    complexity: str = "intermediate"
    target_length: Optional[int] = None
    investor_focus: str = ""

    # Persona
    persona_name: Optional[str] = None
    persona_requested: bool = False

    # Section budgets (pre-calculated)
    section_budgets: Dict[str, int] = field(default_factory=dict)

    # Include flags
    include_health_rating: bool = True

    # Continuous depth scaling: 0.0 = minimum depth (300 words), 1.0 = maximum (3000 words).
    # Drives DEPTH_INSTRUCTIONS injected into the prompt.
    scale_factor: float = 0.5
    depth_plan: Any = None

    # Agent pipeline context (optional — populated when 3-agent pipeline is used)
    company_intelligence: Optional[Dict[str, Any]] = None  # From Agent 1
    filing_analysis: Optional[Dict[str, Any]] = None  # From Agent 2


# ---------------------------------------------------------------------------
# Helper — build per-section instruction fragments
# ---------------------------------------------------------------------------

def _quote_instruction(ctx: PromptContext) -> str:
    """Return the quote instruction based on whether snippets are available."""
    if ctx.filing_language_snippets:
        return (
            "QUOTES: Include exactly 3 short direct quotes (≤25 words each) from the "
            "filing-language snippets provided.  COPY them EXACTLY character-by-character — "
            "do not change any words or punctuation.  Place at least 1 quote in Executive Summary "
            "and at least 1 in Management Discussion & Analysis.  Introduce "
            "each with context, not bare attribution."
        )
    return (
        "QUOTES: No filing-language snippets are available.  Paraphrase "
        "management's position with attribution ('Management indicated that…').  "
        "Do NOT invent quotes."
    )


def _budget_instruction(ctx: PromptContext, section_name: str) -> str:
    """Return the word-budget instruction for a section."""
    budget = ctx.section_budgets.get(section_name, 0)
    if budget and budget > 0:
        tol = section_budget_tolerance_words(section_name, int(budget))
        lower = max(1, int(budget) - int(tol))
        upper = int(budget) + int(tol)
        base = (
            f"SECTION WORD BUDGET: Write {lower}-{upper} words in this section body "
            f"(target {int(budget)}). Count body words only; the section heading does not count. "
            "Do not repeat the section title inside the body. If you need more words, add new analysis instead of restating prior claims."
        )
        shape = get_section_shape(section_name, int(budget))
        if not shape:
            return base

        if section_name == "Financial Health Rating":
            return (
                f"{base} "
                f"Shape: use {describe_sentence_range(shape.min_sentences, shape.max_sentences)} "
                f"across {describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs)}. "
                "Start with the exact pre-calculated score line, explain profitability/cash conversion and balance-sheet flexibility, "
                "then end with a bridge into Executive Summary."
            )
        if section_name == "Risk Factors":
            return (
                f"{base} "
                f"Shape: write exactly {int(shape.risk_count or 0)} structured risks. "
                f"Each risk should use {describe_sentence_range(int(shape.per_risk_min_sentences or 2), int(shape.per_risk_max_sentences or 3))} "
                "and must include the company-specific mechanism, financial transmission path, and one early-warning signal."
            )
        if section_name == "Closing Takeaway":
            return (
                f"{base} "
                f"Shape: use {describe_sentence_range(shape.min_sentences, shape.max_sentences)} "
                f"across {describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs, short=True)}. "
                "State exactly one stance. For budgets of 120 words or more, include one 'what must stay true' trigger, "
                "one 'what breaks the thesis' trigger, and one implication for capital allocation, cash generation, or valuation support."
            )
        return base
    return ""


def _depth_instructions(ctx: PromptContext) -> str:
    """Return evidence-gated depth instructions derived from a continuous DepthPlan."""
    depth_plan = getattr(ctx, "depth_plan", None) or compute_depth_plan(
        getattr(ctx, "scale_factor", 0.5)
    )
    depth_lines = [
        "DEPTH: Expand only with new, filing-grounded insight. If evidence is thin, compress instead of padding.",
    ]
    if depth_plan.yoy_score >= 0.35:
        depth_lines.append("Add year-over-year comparisons only where they change the conclusion.")
    if depth_plan.sequential_score >= 0.35:
        depth_lines.append("Add sequential comparisons only when they sharpen the current-period read.")
    if depth_plan.leverage_score >= 0.4:
        depth_lines.append("Explain operating leverage through price, volume, mix, or cost absorption.")
    if depth_plan.cash_conversion_score >= 0.4:
        depth_lines.append("Discuss cash conversion or working-capital mechanics only when evidence supports it.")
    if depth_plan.balance_sheet_score >= 0.4:
        depth_lines.append("Mention balance-sheet flexibility only if it changes the underwriting.")
    if depth_plan.capital_allocation_score >= 0.4:
        depth_lines.append("Add capital allocation implications only if management action or capacity is explicit.")
    if depth_plan.scenario_score >= 0.4:
        depth_lines.append("Frame one downside scenario with a measurable trigger if supported by the filing.")
    if depth_plan.example_score >= 0.45:
        depth_lines.append("Use one concrete example or evidence anchor per major claim when it adds new insight.")

    return (
        "\n".join(depth_lines)
        + "\nREPETITION CONTRACT (mandatory at all depth levels): "
        "Expansion means new insight, not restated insight. "
        "Never repeat a claim across sections — even with different wording. "
        "If a fact was stated in an earlier section, reference it by implication only."
    )


def _persona_instruction(ctx: PromptContext) -> str:
    """Return persona-related instruction."""
    if ctx.persona_requested and ctx.persona_name:
        return (
            f"Write with subtle {ctx.persona_name}-aligned framing for "
            f"{ctx.company_name}.  Avoid imitation catchphrases, role-play "
            "theatrics, and investor name-dropping."
        )
    if ctx.persona_requested:
        return "Write in first person using the selected persona lens, but keep the voice institutional."
    return f"Write in neutral third-person analyst voice focused on {ctx.company_name}."


def _identity_block(ctx: PromptContext) -> str:
    """Return the system identity/role framing."""
    if ctx.persona_name:
        return (
            f"You are a senior equity research analyst writing an institutional "
            f"investment memo for portfolio managers.  You are filtering the "
            f"analysis through the priorities of {ctx.persona_name}, but you must "
            f"NOT mimic catchphrases or produce self-referential manifesto language.  "
            f"Your goal is actionable, differentiated insight with clear hierarchy "
            f"and zero repetition."
        )
    return (
        "You are a senior equity research analyst writing an institutional "
        "investment memo for portfolio managers.  Write in third person, stay "
        "evidence-anchored, and produce actionable insight with clear hierarchy "
        "and zero repetition."
    )


def _company_profile_block(ctx: PromptContext) -> str:
    """Build a compact company profile block."""
    lines: List[str] = []
    if ctx.ticker:
        lines.append(f"Ticker: {ctx.ticker}")
    if ctx.exchange:
        lines.append(f"Exchange: {ctx.exchange}")
    if ctx.sector:
        lines.append(f"Sector: {ctx.sector}")
    if ctx.industry and ctx.industry != "Not specified":
        lines.append(f"Industry: {ctx.industry}")
    if ctx.country:
        lines.append(f"Country: {ctx.country}")
    if ctx.business_model and ctx.business_model != "Not specified":
        lines.append(f"Business model: {ctx.business_model}")
    if ctx.key_segments and ctx.key_segments != "Not specified":
        lines.append(f"Key segments: {ctx.key_segments}")
    if not lines:
        return ""
    return "COMPANY PROFILE:\n" + "\n".join(f"- {l}" for l in lines)


def _research_block(ctx: PromptContext) -> str:
    """Build the hidden company research context block.

    When the 3-agent pipeline is active and ``company_intelligence`` is
    available, formats the richer structured profile instead of the flat brief.
    """
    # Prefer structured intelligence from Agent 1 when available
    if ctx.company_intelligence and isinstance(ctx.company_intelligence, dict):
        ci = ctx.company_intelligence
        parts: List[str] = []
        parts.append(
            "\nCOMPANY INTELLIGENCE (internal reference — do NOT reproduce):"
        )
        if ci.get("business_identity"):
            parts.append(f"Business: {ci['business_identity']}")
        if ci.get("competitive_moat"):
            parts.append(f"Competitive Moat: {ci['competitive_moat']}")
        kpis = ci.get("primary_kpis") or []
        if kpis:
            kpi_names = ", ".join(
                str(k.get("name", "")) for k in kpis if isinstance(k, dict)
            )
            parts.append(f"Key KPIs: {kpi_names}")
        competitors = ci.get("key_competitors") or []
        if competitors:
            parts.append(f"Competitors: {', '.join(str(c) for c in competitors)}")
        focus = ci.get("investor_focus_areas") or []
        if focus:
            parts.append(
                "Focus Areas:\n" + "\n".join(f"- {f}" for f in focus)
            )
        if ci.get("industry_kpi_norms"):
            parts.append(f"Industry Norms: {ci['industry_kpi_norms']}")
        parts.append(
            "\nUse this intelligence to guide which metrics and dynamics to emphasize. "
            "Do NOT copy this section directly.\n"
        )
        return "\n".join(parts)

    # Fall back to flat brief
    if not ctx.company_research_brief or not ctx.company_research_brief.strip():
        return ""
    return (
        "\nCOMPANY BACKGROUND KNOWLEDGE (internal reference — do NOT reproduce "
        "this section in output):\n"
        f"{ctx.company_research_brief.strip()}\n\n"
        "Use this background to inform your analysis.  Ground every claim in the "
        "filing data provided, but let this context guide which aspects of the "
        "business to emphasize, which risks matter most, and what management's "
        "strategic direction means in the competitive landscape.\n"
        "Do NOT copy or paraphrase this background section directly.\n"
    )


def _narrative_arc_block(ctx: PromptContext) -> str:
    """Build the narrative-arc instruction."""
    health_line = (
        "  - Health Rating → establishes the financial baseline\n"
        if ctx.include_health_rating
        else ""
    )
    return (
        "NARRATIVE ARC (HIGHEST PRIORITY — read before anything else):\n"
        "Identify ONE central tension for this company — a strategic question "
        "that the filing evidence can answer.  Do NOT pick a generic tension; "
        "make it specific to {company_name}'s situation in {industry}.\n\n"
        "Every section must pull on this thread:\n"
        f"{health_line}"
        "  - Executive Summary → frames the tension as a thesis\n"
        "  - Financial Performance → tests the thesis with 2-3 key numbers\n"
        "  - MD&A → reveals whether management actions support or undermine it\n"
        "  - Risk Factors → names what could break it\n"
        "  - Closing Takeaway → resolves it with a verdict\n\n"
        "CRITICAL: The last sentence of each section must raise a question or "
        "implication that the NEXT section opens with.  The reader should never "
        "feel a 'topic change' between sections.\n"
    ).format(company_name=ctx.company_name, industry=ctx.industry)


def _section_budget_block(ctx: PromptContext) -> str:
    """Build the section-level word budget instruction."""
    if not ctx.section_budgets or not ctx.target_length:
        return ""
    lines = "\n".join(
        (
            f"- {section}: {max(1, int(words) - section_budget_tolerance_words(section, int(words)))}-"
            f"{int(words) + section_budget_tolerance_words(section, int(words))} body words "
            f"(target {int(words)})"
        )
        for section, words in ctx.section_budgets.items()
        if words > 0
    )
    return (
        f"\nSECTION LENGTH TARGETS:\n"
        f"{lines}\n"
        f"Total target: {ctx.target_length} words (allowed band ±{total_word_tolerance_words(int(ctx.target_length))}). Count section body words only; headings do not count. "
        f"Keep the memo balanced across sections, and do not front-load words into early sections or repeat titles inside section bodies.\n"
    )


# ---------------------------------------------------------------------------
# PASS 1 — Outline prompt
# ---------------------------------------------------------------------------

def build_outline_prompt(ctx: PromptContext) -> str:
    """Build the outline-generation prompt (Pass 1).

    The LLM produces a structured outline with:
    - Central tension (one sentence)
    - Per-section claims + evidence anchors
    - Transition sentences between sections
    - Quote placements
    """
    sections_list = SECTION_ORDER[:]
    if not ctx.include_health_rating:
        sections_list = [s for s in sections_list if s != "Financial Health Rating"]

    section_outline_instructions = []
    for s_name in sections_list:
        tmpl = SECTION_TEMPLATES.get(s_name)
        if not tmpl:
            continue
        section_outline_instructions.append(
            f"### {s_name}\n"
            f"- Outline anchor: {tmpl.outline_anchor}\n"
            f"- Transition IN: {tmpl.transition_into}\n"
            f"- Transition OUT: {tmpl.transition_out}\n"
            f"- Max numeric density: {tmpl.max_numeric_density} per 100 words"
        )

    outline_sections = "\n\n".join(section_outline_instructions)

    return f"""\
{_identity_block(ctx)}

You are performing PASS 1 of a two-pass summary generation.  Your job is to
produce a STRUCTURED OUTLINE — not the final summary.

Analyze the following filing for {ctx.company_name} ({ctx.filing_type}, {ctx.filing_period}).

{_company_profile_block(ctx)}
{_research_block(ctx)}

FILING CONTEXT:
{ctx.context_excerpt}

FINANCIAL SNAPSHOT:
{ctx.financial_snapshot}

KEY METRICS:
{ctx.metrics_lines}
{ctx.prior_period_delta}

{f"MDA EXCERPT:{chr(10)}{ctx.mda_excerpt}" if ctx.mda_excerpt else ""}

{f"RISK FACTORS EXCERPT:{chr(10)}{ctx.risk_factors_excerpt}" if ctx.risk_factors_excerpt else ""}

{f"FILING LANGUAGE SNIPPETS:{chr(10)}{ctx.filing_language_snippets}" if ctx.filing_language_snippets else ""}

=== OUTLINE TASK ===

Produce a structured outline with these components:

1. **CENTRAL TENSION** (one sentence): The single strategic question this
   memo will answer.  Make it specific to {ctx.company_name} in {ctx.industry}.

2. **PER-SECTION OUTLINE**:

{outline_sections}

3. **QUOTE PLACEMENTS**: For each management quote you plan to use, note:
   - The verbatim quote (from filing snippets only)
   - Which section it belongs in
   - What claim it supports

4. **KEY EVIDENCE MAP**: For each numeric anchor you plan to cite, note:
   - The figure
   - Which section it appears in (each figure in at most 2 sections)
   - What thesis-relevant claim it supports

Output the outline in the structured format above.  Do NOT write the final
summary yet — that happens in Pass 2.
"""


# ---------------------------------------------------------------------------
# PASS 2 — Expansion prompt
# ---------------------------------------------------------------------------

def build_expansion_prompt(ctx: PromptContext, outline: str) -> str:
    """Build the expansion prompt (Pass 2).

    Takes the outline from Pass 1 and expands each section into full prose.
    """
    sections_list = SECTION_ORDER[:]
    if not ctx.include_health_rating:
        sections_list = [s for s in sections_list if s != "Financial Health Rating"]

    section_instructions = []
    for s_name in sections_list:
        tmpl = SECTION_TEMPLATES.get(s_name)
        if not tmpl:
            continue

        do_block = "\n".join(f"  DO: {r}" for r in tmpl.do_rules)
        dont_block = "\n".join(f"  DON'T: {r}" for r in tmpl.dont_rules)

        filled_user_prompt = tmpl.user_prompt_template.format(
            company_name=ctx.company_name,
            filing_type=ctx.filing_type,
            filing_period=ctx.filing_period,
            industry=ctx.industry,
            business_model=ctx.business_model,
            key_segments=ctx.key_segments,
            central_tension="(see outline above)",
            financial_snapshot=ctx.financial_snapshot,
            metrics_lines=ctx.metrics_lines,
            prior_period_delta=ctx.prior_period_delta,
            mda_excerpt=ctx.mda_excerpt,
            risk_factors_excerpt=ctx.risk_factors_excerpt,
            quote_instruction=_quote_instruction(ctx),
            budget_instruction=_budget_instruction(ctx, s_name),
            health_score=ctx.health_score or "",
            health_band=ctx.health_band,
            health_drivers=ctx.health_drivers,
            key_evidence_summary="(see outline above)",
            persona_instruction=_persona_instruction(ctx),
        )

        section_instructions.append(
            f"## {s_name}\n"
            f"{filled_user_prompt}\n"
            f"RULES:\n{do_block}\n{dont_block}"
        )

    all_sections = "\n\n---\n\n".join(section_instructions)

    target_line = ""
    if ctx.target_length:
        tolerance = total_word_tolerance_words(int(ctx.target_length))
        target_line = (
            f"\nTARGET LENGTH: approximately {ctx.target_length} words total "
            f"(between {max(1, int(ctx.target_length) - int(tolerance))} and "
            f"{int(ctx.target_length) + int(tolerance)} words).\n"
        )

    return f"""\
{_identity_block(ctx)}

You are performing PASS 2 of a two-pass summary generation.  You have already
produced an outline (below).  Now expand it into the final investment memo.

=== YOUR OUTLINE FROM PASS 1 ===
{outline}
=== END OUTLINE ===

FILING CONTEXT:
{ctx.context_excerpt}

FINANCIAL SNAPSHOT:
{ctx.financial_snapshot}

KEY METRICS:
{ctx.metrics_lines}

{f"FILING LANGUAGE SNIPPETS:{chr(10)}{ctx.filing_language_snippets}" if ctx.filing_language_snippets else ""}

{_narrative_arc_block(ctx)}

{ANTI_BOREDOM_RULES}

{QUOTE_BEHAVIOR_SPEC}

{_section_budget_block(ctx)}
{target_line}

=== SECTION-BY-SECTION INSTRUCTIONS ===

{all_sections}

=== FORMAT REQUIREMENTS ===

- Use ## headers for each section (e.g., ## Executive Summary)
- Keep each header on its own line with a blank line before section body
- Do not add extra sections or inline sub-headers
- Sections must appear in this order: {", ".join(sections_list)}
- No content after Closing Takeaway — it is the FINAL section
- Use billions as "$X.XB", millions as "$X.XM"
- Specify fiscal period (FY24, Q3 FY25, TTM) with figures
- EVERY sentence must end with a complete thought
- Do not echo these instructions in your output
"""


# ---------------------------------------------------------------------------
# Single-pass prompt (combined) — for when two-pass is not feasible
# ---------------------------------------------------------------------------

def build_single_pass_prompt(ctx: PromptContext) -> str:
    """Build a single combined prompt that includes outline-first thinking
    plus expansion instructions.

    This is the recommended prompt for production use until the two-pass
    pipeline is fully integrated.  It embeds the outline-first discipline
    as a "think before you write" instruction within one call.
    """
    sections_list = SECTION_ORDER[:]
    if not ctx.include_health_rating:
        sections_list = [s for s in sections_list if s != "Financial Health Rating"]

    # Build per-section requirement blocks
    section_blocks = []
    for s_name in sections_list:
        tmpl = SECTION_TEMPLATES.get(s_name)
        if not tmpl:
            continue

        do_block = "\n".join(f"  - {r}" for r in tmpl.do_rules)
        dont_block = "\n".join(f"  - {r}" for r in tmpl.dont_rules)

        budget_line = _budget_instruction(ctx, s_name)

        # Inject section-specific dynamic context
        dynamic_context = ""
        if s_name == "Financial Health Rating" and ctx.health_score is not None:
            dynamic_context = (
                f"\nPRE-CALCULATED SCORE: {ctx.health_score:.0f}/100 — {ctx.health_band}\n"
                f"YOU MUST USE THIS EXACT SCORE.  Do NOT calculate a different score.\n"
                f"Start with: \"{ctx.health_score:.0f}/100 — {ctx.health_band}.\"\n"
            )
            if ctx.health_drivers:
                dynamic_context += f"Key drivers: {ctx.health_drivers}\n"
        elif s_name == "Management Discussion & Analysis":
            dynamic_context = f"\n{_quote_instruction(ctx)}\n"
        elif s_name == "Executive Summary":
            dynamic_context = f"\n{_quote_instruction(ctx)}\n"

        section_blocks.append(
            f"## {s_name}\n"
            f"{tmpl.system_guidance}\n"
            f"{dynamic_context}\n"
            f"{budget_line}\n"
            f"DO:\n{do_block}\n"
            f"DON'T:\n{dont_block}\n"
            f"Max numeric density: {tmpl.max_numeric_density} per 100 words.\n"
            f"Transition OUT: {tmpl.transition_out}"
        )

    all_section_blocks = "\n\n".join(section_blocks)

    # Complexity instruction
    complexity_map = {
        "simple": "Use plain English and avoid jargon.  Explain financial concepts simply.",
        "expert": "Use sophisticated financial terminology.  Assume the reader is an expert investor.",
        "intermediate": "Use standard financial analysis language.",
    }
    complexity_line = complexity_map.get(ctx.complexity, complexity_map["intermediate"])

    # Target length
    target_block = ""
    if ctx.target_length:
        tolerance = total_word_tolerance_words(int(ctx.target_length))
        target_block = (
            f"\nTARGET LENGTH: approximately {ctx.target_length} words total "
            f"(between {max(1, int(ctx.target_length) - int(tolerance))} and "
            f"{int(ctx.target_length) + int(tolerance)} words). "
            f"Stay inside the band without padding, repetition, or cut-off sentences.\n"
        )

    # Filing language block
    snippets_block = ""
    if ctx.filing_language_snippets:
        snippets_block = (
            f"\nFILING LANGUAGE SNIPPETS (verbatim lines available for quotes):\n"
            f"{ctx.filing_language_snippets}\n"
        )

    # MDA + Risk blocks
    mda_block = (
        f"\nMDA EXCERPT (use for MD&A section — quote selectively):\n"
        f"{ctx.mda_excerpt}\n"
    ) if ctx.mda_excerpt else ""

    risk_block = (
        f"\nRISK FACTORS EXCERPT (use for Risk Factors section):\n"
        f"{ctx.risk_factors_excerpt}\n"
    ) if ctx.risk_factors_excerpt else ""

    return f"""\
{_identity_block(ctx)}

Analyze the following filing for {ctx.company_name} ({ctx.filing_type}, {ctx.filing_date}).
{complexity_line}

{_company_profile_block(ctx)}
{_research_block(ctx)}

FILING CONTEXT:
{ctx.context_excerpt}

FINANCIAL SNAPSHOT:
{ctx.financial_snapshot}

KEY METRICS:
{ctx.metrics_lines}
{ctx.prior_period_delta}
{mda_block}{risk_block}{snippets_block}

INSTRUCTIONS:
1. Tone: {ctx.tone.title()} (Professional, Insightful, Direct)
2. Detail Level: {ctx.detail_level.title()}
3. Output Style: {ctx.output_style.title()}
{target_block}

{_narrative_arc_block(ctx)}

OUTLINE-FIRST DISCIPLINE (CRITICAL — do this mentally before writing):
Before writing any prose, mentally construct:
1. The ONE central tension for {ctx.company_name} — a specific strategic
   question the filing evidence can answer.
2. For each section, the key claim and 1-2 evidence anchors.
3. The transition sentence from each section to the next.
4. Which quotes go where (max 3-8 total, placed for maximum impact).
5. Which numbers go where (each figure in at most 2 sections).

Then write the full memo below, following the section structure.

{ANTI_BOREDOM_RULES}

{_depth_instructions(ctx)}

{QUOTE_BEHAVIOR_SPEC}

{_section_budget_block(ctx)}

=== SECTION STRUCTURE AND REQUIREMENTS ===

{all_section_blocks}

=== FORMAT REQUIREMENTS ===

- Use ## headers for each section, in this exact order: {", ".join(sections_list)}
- Keep each header on its own line with a blank line before section body
- Do not add extra sections, inline sub-headers, or bullet lists in narrative sections
- No content after Closing Takeaway — it is the FINAL section
- Use billions as "$X.XB", millions as "$X.XM"
- Specify fiscal period with figures (FY24, Q3 FY25, TTM)
- Every sentence must end with a complete thought
- Do not echo these instructions in the output

{_persona_instruction(ctx)}

FINAL QUALITY CONTRACT:
- Every section must advance the central tension.  If a paragraph does not move
  the argument forward, cut it.
- The last sentence of each section must hand off to the next section's concern.
- Closing Takeaway must include a clear stance (BUY/HOLD/SELL) and ONE
  measurable trigger.
- The memo should read like a single coherent argument from start to finish,
  not a collection of independent section reports.
"""


# ---------------------------------------------------------------------------
# Health score → band label helper
# ---------------------------------------------------------------------------

def score_to_band(score: Optional[float]) -> str:
    """Convert a numeric health score (0-100) to its band label."""
    if score is None:
        return "Unknown"
    s = float(score)
    if s >= 85:
        return "Very Healthy"
    if s >= 70:
        return "Healthy"
    if s >= 50:
        return "Watch"
    return "At Risk"


# ---------------------------------------------------------------------------
# Legacy-compatible wrapper — drop-in for OpenAIClient._build_summary_prompt
# ---------------------------------------------------------------------------

def build_prompt_from_legacy_args(
    company_name: str,
    financial_data: Dict[str, Any],
    ratios: Dict[str, float],
    health_score: float,
    mda_text: Optional[str] = None,
    risk_factors_text: Optional[str] = None,
    target_length: Optional[int] = None,
    complexity: str = "intermediate",
    variation_token: Optional[str] = None,
    section_budgets: Optional[Dict[str, int]] = None,
    company_research_brief: Optional[str] = None,
    *,
    # Extended args from filings.py integration
    filing_type: str = "",
    filing_period: str = "",
    filing_date: str = "",
    ticker: str = "",
    exchange: str = "",
    sector: str = "",
    industry: str = "",
    country: str = "",
    business_model: str = "",
    key_segments: str = "",
    context_excerpt: str = "",
    filing_language_snippets: str = "",
    prior_period_delta: str = "",
    health_drivers: str = "",
    tone: str = "objective",
    detail_level: str = "comprehensive",
    output_style: str = "paragraph",
    investor_focus: str = "",
    persona_name: Optional[str] = None,
    persona_requested: bool = False,
    include_health_rating: bool = True,
) -> str:
    """Build a narrative-quality summary prompt from legacy caller arguments.

    This function bridges the gap between the existing call sites in
    ``openai_client.py`` / ``filings.py`` and the new prompt pack.  Callers
    can swap a single function call without restructuring their code.

    Returns the full prompt string ready to send to the LLM.
    """
    import json as _json

    # Format ratios into readable lines
    ratios_lines = "\n".join(
        f"- {key}: {value:.2%}"
        if isinstance(value, float) and abs(value) < 10
        else f"- {key}: {value:.2f}"
        for key, value in ratios.items()
        if value is not None
    )

    # Format financial data snapshot
    financial_snapshot = ""
    if financial_data:
        try:
            financial_snapshot = _json.dumps(financial_data, indent=2, default=str)[:5000]
        except (TypeError, ValueError):
            financial_snapshot = str(financial_data)[:5000]

    ctx = PromptContext(
        company_name=company_name,
        filing_type=filing_type,
        filing_period=filing_period,
        filing_date=filing_date,
        industry=industry or "Not specified",
        business_model=business_model or "Not specified",
        key_segments=key_segments or "Not specified",
        ticker=ticker,
        exchange=exchange,
        sector=sector,
        country=country,
        financial_snapshot=financial_snapshot,
        metrics_lines=ratios_lines,
        prior_period_delta=prior_period_delta,
        context_excerpt=context_excerpt,
        mda_excerpt=(mda_text[:8000] if mda_text else ""),
        risk_factors_excerpt=(risk_factors_text[:5000] if risk_factors_text else ""),
        filing_language_snippets=filing_language_snippets,
        health_score=health_score,
        health_band=score_to_band(health_score),
        health_drivers=health_drivers,
        company_research_brief=company_research_brief or "",
        tone=tone,
        detail_level=detail_level,
        output_style=output_style,
        complexity=complexity,
        target_length=target_length,
        investor_focus=investor_focus,
        persona_name=persona_name,
        persona_requested=persona_requested,
        section_budgets=section_budgets or {},
        include_health_rating=include_health_rating,
    )

    return build_single_pass_prompt(ctx)


# ---------------------------------------------------------------------------
# Response parser — extract sections from LLM output
# ---------------------------------------------------------------------------

# Fuzzy header → canonical section name mapping.
# Keys are lowercase; values match SECTION_ORDER exactly.
_HEADER_TO_SECTION: Dict[str, str] = {
    # Exact matches
    "financial health rating": "Financial Health Rating",
    "executive summary": "Executive Summary",
    "financial performance": "Financial Performance",
    "management discussion & analysis": "Management Discussion & Analysis",
    "management discussion and analysis": "Management Discussion & Analysis",
    "md&a": "Management Discussion & Analysis",
    "risk factors": "Risk Factors",
    "key metrics": "Key Metrics",
    "closing takeaway": "Closing Takeaway",
    # Common LLM variations
    "health rating": "Financial Health Rating",
    "financial health": "Financial Health Rating",
    "health score": "Financial Health Rating",
    "mda": "Management Discussion & Analysis",
    "management discussion": "Management Discussion & Analysis",
    "management analysis": "Management Discussion & Analysis",
    "risks": "Risk Factors",
    "key risk factors": "Risk Factors",
    "metrics": "Key Metrics",
    "financial metrics": "Key Metrics",
    "takeaway": "Closing Takeaway",
    "closing": "Closing Takeaway",
    "conclusion": "Closing Takeaway",
    "investment recommendation": "Closing Takeaway",
    "overall takeaway": "Closing Takeaway",
    "verdict": "Closing Takeaway",
}


def _match_header_to_section(header_text: str) -> Optional[str]:
    """Match a raw header string to a canonical SECTION_ORDER name.

    Uses exact lookup first, then substring matching for robustness.
    Returns None if no match is found.
    """
    import re as _re

    # Clean the header: strip markdown formatting, numbering, whitespace
    cleaned = _re.sub(r"[*#`]+", "", header_text).strip()
    # Remove leading numbers like "1." or "2."
    cleaned = _re.sub(r"^\d+\.?\s*", "", cleaned).strip()
    lower = cleaned.lower()

    # 1. Exact match
    if lower in _HEADER_TO_SECTION:
        return _HEADER_TO_SECTION[lower]

    # 2. Substring match — check if any known pattern is contained in the header
    for pattern, section_name in _HEADER_TO_SECTION.items():
        if pattern in lower:
            return section_name

    # 3. Check if the header contains a known section name
    for section_name in SECTION_ORDER:
        if section_name.lower() in lower:
            return section_name

    return None


def parse_narrative_summary(raw_text: str) -> Dict[str, str]:
    """Parse LLM output with ``##`` headers into a section dict.

    Returns a dict mapping canonical section names (matching ``SECTION_ORDER``)
    to their body text.  Handles the 7 standard sections:

    - ``"Financial Health Rating"``
    - ``"Executive Summary"``
    - ``"Financial Performance"``
    - ``"Management Discussion & Analysis"``
    - ``"Risk Factors"``
    - ``"Key Metrics"``
    - ``"Closing Takeaway"``

    Sections not found in the output are returned as empty strings.
    Unrecognized headers are preserved under their original header text
    (stripped of markdown).  The key ``"_raw"`` contains the unmodified
    input for downstream processing.

    Parameters
    ----------
    raw_text : str
        Raw markdown output from the LLM.

    Returns
    -------
    Dict[str, str]
        Section name → section body text.  All 7 standard sections are
        always present as keys (empty string if missing).
    """
    import re as _re

    result: Dict[str, str] = {"_raw": (raw_text or "").strip()}

    # Pre-populate every standard section with empty string
    for section_name in SECTION_ORDER:
        result[section_name] = ""

    if not raw_text or not raw_text.strip():
        return result

    # Parse into (header, body) pairs
    current_section: Optional[str] = None
    current_lines: List[str] = []

    for line in raw_text.split("\n"):
        stripped = line.strip()

        # Match # / ## / ### headers, optionally with numbering
        header_match = _re.match(r"^#{1,3}\s+(.*)", stripped)
        if header_match:
            # Flush previous section
            if current_section is not None:
                body = "\n".join(current_lines).strip()
                if body:
                    result[current_section] = body

            header_text = header_match.group(1).strip()
            matched_section = _match_header_to_section(header_text)

            if matched_section:
                current_section = matched_section
            else:
                # Preserve unrecognized headers under cleaned text
                cleaned = _re.sub(r"[*#`]+", "", header_text).strip()
                current_section = cleaned or header_text

            current_lines = []
        elif current_section is not None:
            current_lines.append(line)

    # Flush final section
    if current_section is not None:
        body = "\n".join(current_lines).strip()
        if body:
            result[current_section] = body

    return result


def build_structured_output_contract(
    *,
    section_budgets: Dict[str, int],
    include_health_rating: bool,
    target_length: Optional[int],
) -> str:
    """Build the JSON output contract for structured section generation."""
    sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    budget_lines = "\n".join(
        (
            f'- "{section_name}": target {int(section_budgets.get(section_name, 0) or 0)} body words '
            f"(allowed range "
            f"{max(1, int(section_budgets.get(section_name, 0) or 0) - section_budget_tolerance_words(section_name, int(section_budgets.get(section_name, 0) or 0)))}-"
            f"{int(section_budgets.get(section_name, 0) or 0) + section_budget_tolerance_words(section_name, int(section_budgets.get(section_name, 0) or 0))})"
        )
        for section_name in sections
    )
    total_line = (
        f"- Total memo target: {int(target_length)} words (backend enforces ±{total_word_tolerance_words(int(target_length))})."
        if target_length
        else "- Total memo target: concise, but complete."
    )
    return (
        "STRUCTURED OUTPUT CONTRACT:\n"
        "Return a single JSON object only. No markdown headings outside JSON. No prose before or after JSON.\n"
        "Schema:\n"
        "{\n"
        '  "central_tension": "string",\n'
        '  "reported_total_word_count": 1234,\n'
        '  "sections": {\n'
        '    "Executive Summary": {"body": "string", "reported_word_count": 150}\n'
        "  }\n"
        "}\n"
        "Rules:\n"
        f"{total_line}\n"
        "- Each section body must contain section prose only. Do not include markdown headings, repeated section titles, or the section label inside section bodies.\n"
        "- Count words on the section body only; the markdown heading does not count toward the section budget.\n"
        "- Use these exact section keys and budget ranges:\n"
        f"{budget_lines}\n"
        "- Report each section's reported_word_count using the final body text. If any section is outside its allowed range, rewrite that section and recalculate the total before returning.\n"
        "- Before finalizing, use code to calculate the word count and adjust the output until you get it right.\n"
        "- Once done, produce the full output below as the JSON object only."
    )


def _extract_json_candidate(raw_text: str) -> Optional[str]:
    if not raw_text or not raw_text.strip():
        return None
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = raw_text[start : end + 1].strip()
    return candidate if candidate.startswith("{") and candidate.endswith("}") else None


def _normalize_structured_section_body(body: str, section_name: str) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
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
        if text == prior:
            break
    if section_name != "Key Metrics":
        text = re.sub(r"^\s*#.+$", "", text, flags=re.MULTILINE).strip()
    return text


def extract_structured_section_payload(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse a structured JSON response from the model if present."""
    candidate = _extract_json_candidate(raw_text)
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    sections = payload.get("sections")
    if not isinstance(sections, dict) or not sections:
        return None
    return payload


def assemble_structured_summary(
    payload: Dict[str, Any], *, include_health_rating: bool
) -> Optional[str]:
    """Assemble canonical markdown from a structured sections payload."""
    if not isinstance(payload, dict):
        return None
    section_payload = payload.get("sections")
    if not isinstance(section_payload, dict):
        return None

    normalized_sections: Dict[str, str] = {}
    for raw_name, raw_value in section_payload.items():
        canonical_name = _match_header_to_section(str(raw_name or ""))
        if not canonical_name:
            continue
        body: str
        if isinstance(raw_value, dict):
            body = str(
                raw_value.get("body")
                or raw_value.get("text")
                or raw_value.get("content")
                or ""
            )
        else:
            body = str(raw_value or "")
        body = _normalize_structured_section_body(body, canonical_name)
        if body and canonical_name not in normalized_sections:
            normalized_sections[canonical_name] = body

    sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    parts: List[str] = []
    for section_name in sections:
        body = (normalized_sections.get(section_name) or "").strip()
        if not body:
            continue
        parts.append(f"## {section_name}\n{body}")
    assembled = "\n\n".join(parts).strip()
    return assembled or None
