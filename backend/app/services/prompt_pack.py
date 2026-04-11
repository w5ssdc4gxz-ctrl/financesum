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
FILING_CITATION_STYLE           → str   (injectable citation rules)
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
    "Executive Summary": 1,
    "Financial Performance": 4,
    "Management Discussion & Analysis": 2,
    "Risk Factors": 2,
    "Closing Takeaway": 1,
    "Financial Health Rating": 3,
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
5. NUMERIC DENSITY CAPS: Executive Summary and Closing Takeaway: at most
   1 number per 100 words.  MD&A: at most 1 number per 100 words — this
   section is about STRATEGY and MECHANISM, not metrics.  Risk Factors:
   at most 1 number per 100 words — risks are business events, not data
   points.  Financial Performance may go up to 4 per 100 words.  Every
   number must be followed by an interpretation sentence — never list
   figures without insight.
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
9. NO PADDING PHRASES: Banned endings — "and that matters," "and that still
   matters," "and that remains decisive," "which remains the real test,"
   "still decisive," "execution matters," "durability matters," "that remains
   the trigger," "that remains the hinge." These are empty filler. End each
   sentence with a specific, evidence-grounded conclusion instead.
10. NO SELF-REFERENTIAL STRUCTURE: Never write "This sets up the Risk Factors
    section," "as discussed in the Executive Summary," "the Key Metrics below
    show," "as tracked in the Key Metrics," or any reference to the memo's own
    structure. Just deliver the analysis — the reader knows what section they
    are reading.
11. COMPANY UNIQUENESS TEST: Before writing any sentence, apply this test —
    could this sentence appear in a summary of ANY other company in ANY
    industry?  If yes, rewrite it with company-specific details: name the
    product, segment, geography, customer, competitive dynamic, or strategy
    that makes this statement unique to this company.  Generic sentences like
    "Revenue grew driven by strong demand" FAIL this test.  Company-specific
    sentences like "Azure revenue grew 33% as enterprise migration workloads
    accelerated ahead of the hybrid-cloud deadline" PASS it.
12. CROSS-SECTION FIGURE EXCLUSIVITY: Each specific dollar figure (e.g.,
    "$397 million", "$4.218 billion") may appear in at most TWO sections.
    If a figure was already cited in an earlier section, do NOT repeat it —
    reference it by implication ("the credit revenue cited above") or use a
    DIFFERENT supporting figure.  The memo should feel like each section adds
    new evidence, not recycles the same numbers.
13. NO INCOHERENT ENDINGS: Every section must end with a complete, grammatical
    sentence that delivers a specific conclusion.  Fragments like "inventory
    confirms." or "The better." are REJECTED.  Trailing meta-sentences like
    "[Company] still matters" or "[Company] remains the proof point" are
    REJECTED — end with a specific, evidence-grounded claim instead.
14. GOLDEN THREAD: The central tension stated in the Executive Summary is the
    backbone of the entire memo.  Frame it as a TRADEOFF — what the company is
    gaining vs. what it is giving up.  Every section must advance, test, or
    resolve that tradeoff — never wander into standalone analysis.  Financial
    Performance tests the tradeoff with cash-flow reality.  MD&A reveals whether
    management's strategy addresses the tradeoff or ignores it.  Risk Factors
    identifies what could tip the tradeoff against the company.  Closing
    Takeaway resolves it with a clear verdict on which side wins.  The reader
    should feel each section is one step closer to the conclusion.
    REPETITION TEST: If you find yourself restating the same tension in different
    words across sections, you are repeating, not advancing.  Each section must
    ADD something new to the argument.
15. NO ANALYST FOG: Banned jargon phrases — "underwriting thread,"
    "capital absorption," "forward visibility constraints," "cash drag,"
    "the real frame for a quarter in which," "underwriting call,"
    "transmission path," "underwriting setup," "the setup remains,"
    "the cleanest read," "visibility inflection," "monetization runway,"
    "earnings power translation," "balance sheet optionality,"
    "the underwriting case," "margin absorption," "cash conversion optionality."
    These phrases sound sophisticated but communicate nothing.  Replace each
    with a plain-English explanation of the specific business dynamic.
    Test: if a non-finance reader would need to look up your phrase, rewrite it.
16. SHARP ENDINGS: Every section must end with a sentence that names a specific
    metric, trigger, threshold, or timeline.  Endings like "if that execution
    slips," "if that changes," "remains to be seen," or "time will tell" are
    REJECTED.  Instead end with something like: "Watch [specific metric] — if
    it drops below [threshold] for [timeframe], the view changes fast."
17. AHA REQUIREMENT: Each section must contain at least one insight that would
    surprise a generalist analyst — something non-obvious that only emerges from
    reading this specific filing.  If your section could have been written from
    a press release, it lacks an aha insight.  Surface the one thing the filing
    reveals that the market might be missing.
18. CONVERSATIONAL CLARITY: Write like a sharp analyst explaining the filing to
    a smart colleague over coffee — direct, specific, occasionally blunt.  Avoid
    institutional hedge language ("it is worth noting that…").  If you would
    not say it out loud, do not write it.
"""


# ---------------------------------------------------------------------------
# Quote behavior specification
# ---------------------------------------------------------------------------
QUOTE_BEHAVIOR_SPEC: str = """\
MANAGEMENT QUOTES — HANDLING RULES:
1. Include 0-3 high-signal direct quotes ONLY if they appear verbatim in the
   provided filing text or filing-language snippets. A direct quote earns its
   place only when it materially clarifies management's strategy, priorities,
   outlook, or the next operating checkpoint. If a quote does not do that,
   delete it and use attributed paraphrase instead.
1a. REJECT BOILERPLATE QUOTES — NEVER quote any of these:
   - Investment classification or maturity policies ("Investments with maturities
     beyond one year may be classified as short-term...")
   - Forward-looking statement disclaimers ("forward-looking statements involve
     risks and uncertainties...")
   - Accounting standard adoption language ("We adopted ASU...")
   - Risk factor boilerplate repeated across multiple filings unchanged
   - Fair value or carrying value definitions
   - Tax/accounting footnotes such as "federal foreign tax credits," "excess tax
     benefits," effective tax-rate disclosures, or deferred-tax language
   - Disclosure-rule, registry, transfer-restriction, anti-takeover, or similar
     legal/governance boilerplate unless it directly changes strategy or the
     next 12 months
   A high-signal quote explains a DECISION, STRATEGY, PRIORITY, or FORWARD
   DIRECTION specific to THIS filing period. If you cannot find one, use
   paraphrase-with-attribution instead. Zero quotes is better than boilerplate
   quotes.
2. Place quotes only where they genuinely advance the argument. Executive
   Summary and MD&A are the most natural homes; Risk Factors and Closing should
   usually rely on attribution unless a direct quote materially sharpens the
   point. Never stack quotes back-to-back.
3. Keep each quote ≤25 words.  Introduce it with context ("Management
   acknowledged pricing headwinds, noting that…") rather than bare attribution
   ("The CEO said…").
4. FALLBACK when no filing snippets are available: paraphrase management's
   position with attribution — "Management indicated that…,"
   "The company described its strategy as…."  Use at least two such
   paraphrases across the memo.
5. NEVER FABRICATE QUOTES. If the filing text does not contain a quotable
   strategy/outlook statement on a topic, use paraphrase-with-attribution
   instead.
6. After the first use, vary attribution verbs: noted, acknowledged,
   emphasized, highlighted, cautioned, described, characterized, indicated.
   Never repeat the same verb within 200 words.
7. COPY QUOTES CHARACTER-BY-CHARACTER: When you include a direct quote, copy
   the exact words from the filing text with zero modifications.  Use standard
   straight quotes ("...") — never smart/curly quotes.
"""


# ---------------------------------------------------------------------------
# Filing citation style — injected into every prompt
# ---------------------------------------------------------------------------
FILING_CITATION_STYLE: str = """\
FILING CITATION STYLE (mandatory — every section must feel grounded in THIS filing):
1. QUOTE INTEGRATION: When citing management, use contextual attribution:
   "Management acknowledged pricing headwinds, noting that '[exact quote from filing],'
   which creates a setup where..."  NOT bare attribution like "The CEO said..."
2. RISK CITATION: When describing a risk, tie it to filing language:
   "The filing identifies [specific exposure], warning that '[exact risk language],'
   an effect that could..."
3. PARAPHRASE WITH ATTRIBUTION: When no verbatim quote fits, still cite:
   "Management characterized the demand environment as [paraphrase], suggesting that..."
4. EVERY PARAGRAPH must contain at least one of: a direct quote, a paraphrase
   with attribution, or a specific filing data point. Paragraphs of pure analyst
   commentary without filing grounding FAIL the quality test.
5. ZERO GENERIC CONTENT: If a sentence could appear in ANY other company's filing
   summary, it is REJECTED. Every claim must name a specific product, segment,
   geography, customer, regulation, or competitive dynamic from THIS filing.
"""


# ---------------------------------------------------------------------------
# Clarity-first writing directive — injected into every prompt
# ---------------------------------------------------------------------------
CLARITY_FIRST_DIRECTIVE: str = """\
CLARITY-FIRST WRITING RULES (override all other style guidance on conflict):
1. WRITE TO BE UNDERSTOOD, NOT TO IMPRESS: Use plain, direct language a smart
   non-expert can follow. If a sentence requires a finance degree to parse,
   rewrite it. "They are spending heavily now and we do not yet see the return"
   beats "forward visibility constraints limit capital absorption clarity."
2. FEWER NUMBERS, MORE MEANING: Cite 3-5 key numbers across the entire memo.
   Every number MUST be followed by a plain-English "so what" sentence.
   "Operating income was $27B" is useless. "Operating income hit $27B, but
   that required $14B in capex — nearly double last year — so the business
   is working harder to earn the same profit" is useful.
3. THINK IN TRADEOFFS: The best analysis frames what the company is giving up
   to get what it wants. "They are trading short-term cash flow for long-term
   AI positioning" is a tradeoff. "Revenue grew and capex rose" is two
   disconnected observations. Every narrative section should contain at least
   one explicit tradeoff framing.
4. SEPARATE SIGNAL FROM STORY: Distinguish what management says from what the
   numbers show. Use constructions like "Management says demand is strong, but
   cash conversion is weakening" or "The filing claims margin expansion, yet
   capex is consuming a growing share of operating cash flow." Where story and
   signal align, say so. Where they diverge, name the gap — that is where
   the real insight lives.
5. END WITH CLEAR JUDGMENT: Replace hedging language ("suggests," "indicates,"
   "may imply," "remains to be seen," "appears to reflect") with direct
   assessment. Use "The evidence shows...", "The risk is...", "This means...",
   "The result is...", "So far...". A reader should finish each section
   knowing exactly what you think.
6. FOCUS ON CASH REALITY: For every growth claim, test it against cash flow.
   Is cash flow tracking earnings? Is growth consuming or generating cash?
   If revenue is up but free cash flow is flat, say so plainly — that is
   the most important signal in the filing.
7. ONE IDEA, ONE STATEMENT: Say it once, say it clearly, move on. If you have
   made the point that AI investment is a risk, do not restate it as "AI is a
   pressure point," "AI is a key risk," "AI determines balance sheet
   flexibility," and "AI still matters." That is one idea stated four ways.
   Each section must ADD something new to the argument, not rephrase what
   came before.
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
        "Your goal is to frame the ONE central tension this company faces — stated "
        "as a clear tradeoff (what the company gains vs. what it gives up), not just "
        "an observation.  Write so a smart non-expert understands the company's "
        "situation in one read."
    ),
    user_prompt_template="""\
Write the Executive Summary for {company_name} ({filing_type}, {filing_period}).

COMPANY CONTEXT:
- Industry: {industry}
- Business model: {business_model}
- Key segments: {key_segments}

THE TASK: In 2-3 paragraphs of flowing prose, accomplish these goals:
0. DECISION FRAMING: The very first sentence must state the single most
   important takeaway from this filing — the one thing a portfolio manager
   needs to know RIGHT NOW.  The reader should understand the verdict within
   2 sentences before any context or explanation.
0a. OPENING BLOCK CONTRACT: Within the first 2 sentences, you MUST also state
   the non-obvious insight from this filing and the single operating proof
   point investors should watch next. The opening should work like a decision
   tool, not a scene-setter.
0b. AHA RULE: State the non-obvious insight as a clear contrast between the old
   read and the new read from this filing. Make it memorable, but keep it
   company-specific and grounded in the evidence rather than in slogans.
1. Open with management's key message or thesis about the company's direction
   — what leadership believes matters most right now.  Ground the reader in
   management's worldview before any numbers.  Then establish what this company
   IS and WHY it matters (competitive position, business model, market served).
2. State where the business stands in this filing period and how management
   sounds about what comes next.
3. Identify ONE central tension (e.g., "exceptional margin quality vs. rising
   reinvestment demands," or "accelerating revenue vs. deepening customer
   concentration").  State it clearly as a question the memo will answer.
4. Prefer company-specific operating KPIs over generic Revenue/EPS when available.
5. Use at most 1 anchor figure total — this section is about framing, not
   measurement.  Lead with narrative, not numbers.
6. End with a forward-looking sentence that naturally raises the question
   Financial Performance will answer. Name the single proof point, metric, or
   operating checkpoint that will answer it — this handoff IS the golden thread.
7. Use one verbatim management quote ONLY if the filing snippets contain a
   high-signal statement about strategy, outlook, or what happens next.
   Integrate it with context: "Management [verb] that '[quote],' which
   [interpretation]." If the available quotes are legal, tax, or accounting
   boilerplate, use strong paraphrase with attribution instead. Management's
   voice MUST appear within the first 3 sentences, but it does NOT have to be
   a direct quote.

{quote_instruction}
{budget_instruction}
""",
    do_rules=[
        "Open with management's stated thesis or priority, not metrics",
        "Make the first 2 sentences do the main work: takeaway, aha insight, and next proof point",
        "State the aha as a concrete contrast in the business, not as a vague theme",
        "State what changed in this filing period before drifting into generic finance language",
        "Frame the central tension as a tradeoff — what the company is giving up to get what it wants",
        "Use one filing quote only when it adds strategic or forward-looking context; otherwise use attributed paraphrase",
        "Prefer company-specific operating KPIs over generic Revenue/EPS when available",
        "End with a bridge sentence into Financial Performance",
        "Make the reader feel they understand what the company does",
        "Write so a non-finance reader understands the company's situation",
    ],
    dont_rules=[
        "Do NOT start with a revenue or income figure",
        "Do NOT list metrics — weave any numbers into narrative claims",
        "Do NOT use bullet points or numbered lists",
        "Do NOT use meta language ('this memo will…')",
        "Do NOT exceed 1 numeric anchor — this section frames the story, numbers come later",
        "Do NOT use analyst jargon ('underwriting thread', 'capital absorption', 'forward visibility')",
        "Do NOT hedge with 'suggests', 'indicates', 'may imply' — state your assessment directly",
    ],
    max_numeric_density=1,
    outline_anchor="Central tension (1 sentence), thesis claim, 1 evidence anchor max",
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
1. Pick ONLY the 2-3 numbers that most directly TEST the Executive Summary's tension.
   HARD CAP: 2-3 numeric figures total. Each figure gets ONE interpretation —
   do not restate the same insight in different words.  If margin strength is
   funding investment, say it once; do not rephrase as "cash generation supporting
   the buildout" or "operating leverage enabling reinvestment."  Remaining metrics
   belong in Key Metrics.
2. Prefer company-specific operating KPIs first; use generic financial metrics
   only when they are the real driver of the investment question.
3. Weave them into a causal argument: each number should answer "so what?" for
   the thesis.  Show cause-and-effect, not a metrics parade.
4. Compare latest period vs. prior comparable period (QoQ for 10-Q, YoY for 10-K)
   where data exists.
5. End by surfacing an execution or capital-allocation question that MD&A will
   address. The final sentence must name the metric, trigger, threshold, or
   timeline that would answer it first.

{budget_instruction}
""",
    do_rules=[
        "Start by answering the question the Executive Summary raised",
        "Prefer company-specific KPI findings over generic metrics when they exist",
        "Interpret every number — what does it MEAN for the thesis?",
        "Keep it tight: 2-3 interpreted metrics, then move on",
        "Use causal language (because, therefore, which implies)",
        "Include period-over-period comparison where data exists",
        "End with a management execution question leading into MD&A",
        "Test each number against cash flow — is growth generating or consuming cash?",
        "Frame findings as tradeoffs, not disconnected observations",
        "Separate what management claims from what the numbers show",
    ],
    dont_rules=[
        "Do NOT list metrics mechanically ('Revenue was $X, up Y%')",
        "Do NOT repeat any specific dollar figure already cited in the Health Rating or Executive Summary — use different evidence",
        "Do NOT use more than 4-5 numeric anchors",
        "Do NOT include metrics without interpretation — every number needs a plain-English 'so what'",
        "Do NOT end with a fragment or meta-sentence — end with a specific analytical conclusion",
        "Do NOT use analyst fog phrases — explain in plain English what is happening",
    ],
    max_numeric_density=4,
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
        "or undermines the thesis.  Numbers are evidence, not structure.  "
        "Separate what management SAYS from what the numbers SHOW.  Where they "
        "diverge, name the gap explicitly — that is where the real insight lives."
    ),
    user_prompt_template="""\
Write the Management Discussion & Analysis section for {company_name}.

THESIS TENSION: {central_tension}
INDUSTRY CONTEXT: {industry}, {business_model}

MDA EXCERPT FROM FILING:
{mda_excerpt}

THE TASK: In 2-3 connected paragraphs, accomplish these goals:
1. LEAD with management's stated strategy, priorities, or key decisions BEFORE
   any metrics.  The first paragraph must be about what management is doing and
   why — not what the numbers show.  Only after establishing the strategic
   context should you use metrics as evidence.
2. Quote or closely paraphrase management's own words about strategy, priorities,
   or outlook. Use up to TWO verbatim quotes if filing snippets contain genuinely
   high-signal strategy or outlook language. Every claim about management's strategy MUST be supported by
   a direct quote or explicit paraphrase with attribution.  Use the citation style:
   "Management noted that '[exact quote],' which means [interpretation]" or
   "Management characterized [topic] as [paraphrase], indicating that [implication]."
   The reader should feel they are hearing management's actual voice throughout,
   not reading generic analyst commentary.
3. Describe the business model mechanism: HOW does this company make money, and
   what is management doing to protect or grow that mechanism?
4. State what changed in this filing period and what management thinks is likely
   to happen next.
5. Discuss forward-looking plans: investments, market expansion, product roadmap,
   capital allocation philosophy.
6. Use at most 1 anchor figure — this section is about MECHANISM and STRATEGY,
   not data.  Let management's own words carry the argument, not numbers.
7. End by surfacing what could go wrong with this strategy — a natural bridge
   into Risk Factors.  The final sentence must name the specific checkpoint,
   trigger, timeline, or metric that would show the strategy is slipping. This
   handoff must flow from the strategy discussion, not feel bolted on.
8. PROMISE VS. DELIVERY: Identify what management guided, promised, or
   prioritized in prior periods.  State whether this filing shows delivery,
   progress, or miss on those commitments.  If no prior guidance is available,
   state what management is now committing to for the next period.  This is
   the most company-specific content you can write.

{quote_instruction}
{budget_instruction}
""",
    do_rules=[
        "Lead FIRST with management's stated strategy or priorities before any numbers",
        "Use direct quotes only when they add strategic or forward-looking context; otherwise paraphrase with attribution",
        "Support every strategy claim with a direct quote or explicit attribution",
        "Explain business model mechanics — how money is made and protected",
        "State what management expects, plans, or is prioritizing next",
        "Assess whether management delivered on prior promises or guidance",
        "End with a risk implication that bridges into Risk Factors",
        "Identify where management's narrative diverges from the financial evidence",
        "Frame management's strategy as a tradeoff — what are they betting on and what are they giving up?",
    ],
    dont_rules=[
        "Do NOT lead with revenue or income figures",
        "Do NOT say 'Management discusses…' or 'In the MD&A section…'",
        "Do NOT restate metrics or dollar figures from Financial Performance or earlier sections — use fresh evidence",
        "Do NOT speculate beyond what the filing evidence supports",
        "Do NOT exceed 1 numeric anchor — this section is about strategy and mechanism, not metrics",
        "Do NOT write 'revenue was $X, up Y%' style sentences",
        "Do NOT end with a fragment or meta-sentence — end with a specific risk implication",
    ],
    max_numeric_density=1,
    outline_anchor="Key business driver, management quote anchor, strategy assessment, promise-vs-delivery",
    transition_into="Answer the execution question Financial Performance raised",
    transition_out="Surface what could go wrong → Risk Factors",
)

_RISK_FACTORS = SectionTemplate(
    name="Risk Factors",
    system_guidance=(
        "You are identifying the 1-2 risks most LIKELY to actually affect this "
        "company's results in the next 4 quarters. Rank by PROBABILITY first, "
        "then magnitude. A probable risk with moderate impact beats a dramatic "
        "risk with low likelihood. Write clearly — a non-finance reader should "
        "understand each risk in one read. Ground every risk in specific "
        "evidence from the filing. Prefer 2 sharply differentiated risks over "
        "a crowded list. Never write more than 2 risks."
    ),
    user_prompt_template="""\
Write the Risk Factors section for {company_name}.

THESIS TENSION: {central_tension}
INDUSTRY: {industry}
BUSINESS MODEL: {business_model}

RISK FACTORS EXCERPT FROM FILING:
{risk_factors_excerpt}

EXCERPT QUALITY CHECK:
If the RISK FACTORS EXCERPT above appears to contain table-of-contents entries,
page numbers, ITEM references without substantive content, or other non-risk text,
IGNORE those fragments entirely and identify risks based on the company context and
financial data provided elsewhere in the prompt. Never fabricate risks from section
headers or page numbers.

SCHEMA (MANDATORY — violations will be rejected):
Write the exact number of risks required by the section budget. Keep the section visually clean: each risk must be its own standalone paragraph separated by a blank line. Each risk must follow this format:

[Risk Name]: In 2-3 clear sentences, explain what could go wrong, why it
matters for this company specifically, and what investors should watch for.

Rules:
- Risk Name must name a specific exposure unique to {company_name} — a product,
  customer, regulation, geography, or competitive dynamic. NOT a generic category.
- Test: if another company in a different industry could have the same risk name,
  it is too generic.
- DO NOT prepend {company_name} to a generic category — "{company_name} Margin Risk"
  is just "Margin Risk" with a label. Instead name the specific exposure, e.g.,
  "High-NA EUV Yield Ramp Risk" or "TSMC/Samsung Concentration Risk."
- Ground each risk in SPECIFIC facts from the filing excerpt above. If the excerpt
  mentions a product, customer, regulation, or market — use that in the risk name.
- Write each risk as natural flowing prose. Do NOT use a rigid template structure
  with separate mechanism / impact / signal sentences.
- Weave filing evidence naturally into your explanation — e.g., "management noted
  that..." or include a direct quote (8+ words) as part of the sentence flow.
- Do NOT use mechanical phrasing like "The filing warns that [quote]." as a
  standalone sentence. Integrate evidence into the explanation.
- NO generic macro filler (e.g., "interest rate changes", "macroeconomic volatility"
  without company-specific context).

FORWARD-LOOKING REQUIREMENT:
- Each risk must describe something that COULD HAPPEN in the next 4 quarters,
  not something that already happened in this filing period.
- A risk is NOT "margins declined 200bp" — that is history.
- A risk IS "if [specific customer/supplier/regulator] does X, then [specific P&L impact]."
- REJECT generic category labels like "cybersecurity risk", "margin risk",
  "qualitative disclosures risk" — name the SPECIFIC exposure unique to this company.
- Reject filing-structure debris such as "General Instruction," item headers, or
  other parsing fragments. Those are source artifacts, not investor risks.
- A named accounting convention only counts as a real risk if the filing ties it
  to debt, funding, liquidity, customer demand, product rollout, or another
  consequence that would hit the business first.
- NO merged risks (do not combine two distinct risks into one entry).
- NO thematic repetition — each risk must address a COMPLETELY DIFFERENT mechanism,
  impact pathway, and business area.  If two risks could be summarized as "the same
  thing with different wording," merge them or replace one.  The validator will reject
  risks with overlapping names, overlapping mechanisms, or similar sentence structures.

MATERIALITY RANKING (mandatory — rank by this order):
1. PROBABILITY (most important): Pick risks with the strongest evidence of
   actually happening in the next 4 quarters. A probable risk with moderate
   impact beats a dramatic risk with low likelihood.
2. MAGNITUDE: Name the P&L line items affected.
3. ASYMMETRY: Why the downside is not already priced in.

PRIORITIZATION REQUIREMENT:
- Open each risk with WHY IT MATTERS NOW — state the specific timeline or
  catalyst (e.g., "Q2 2026 regulatory ruling," "contract renewal in October,"
  "pricing reset at fiscal year-end") before explaining the mechanism.
- Each risk must pass the "position-size test": would a portfolio manager
  adjust their position based on this risk alone?  If not, it is too generic.
- End each risk body with the first metric, operating checkpoint, threshold, or
  dated catalyst that would show the downside is actually forming.

DISCARD BOILERPLATE LEGAL RISKS:
- "We may face competition" — unless a specific competitor action is named
- "Regulatory changes may affect" — unless a specific regulation/proceeding is named with a timeline
- "We depend on key personnel" — almost never investment-relevant
- "Cybersecurity threats" — unless a specific incident occurred in this filing period
- Foreign securities registry, transfer-restriction, anti-takeover, or similar holder-rights boilerplate unless the filing ties it to a live transaction, ruling, or capital-markets event
- Anything that has been in every filing for 3+ years without materializing

REJECTED RISK NAMES (dressed-up generic — will fail validation):
- "Cost-to-Serve and Pricing Pressure Risk" — name WHAT product/service costs are rising
- "Asset Deployment and Returns Risk" — name WHICH assets and WHAT return is at risk
- "Delivery and Conversion Timing Risk" — name WHAT is being delivered and to WHOM
- "Unit-Economics Reset Risk" — name the specific unit economics at risk
- "Infrastructure Utilization Risk" — name WHICH infrastructure
- "Capital Allocation Constraint Risk" — name the specific constraint
- "Operating Model Leverage Risk" — name the specific lever
- "Revenue Concentration Risk" — name the customer, segment, or geography
- "Cybersecurity Risk" — too generic unless the filing describes a specific cyber incident
- ANY name built from financial metrics (margin, cash flow, FCF, returns, conversion)
  rather than a business event (customer loss, regulation, product delay, competition)

GOOD RISK NAMES (pass validation):
- "High-NA EUV Yield Ramp Risk" (ASML — names the product)
- "TSMC/Samsung Concentration Risk" (ASML — names the customers)
- "AWS Compute Pricing Pressure Risk" (Amazon — names the segment)

RISK ≠ METRIC (CRITICAL):
- A RISK is a business event: customer loss, regulation change, competitor
  launch, supply disruption, patent expiration, contract non-renewal.
- Financial figures (operating margin %, FCF, cash balance) are EVIDENCE
  you cite inside a risk body, NOT the risk itself.
- WRONG: "The 24.5% operating margin leaves less cushion if costs rise"
- RIGHT: "TSMC allocation constraints could delay HBM3e volume ramps,
  compressing the margin as fixed wafer commitments outpace sellable output"
- Start each risk with the BUSINESS EVENT, not a financial number.

BANNED BOILERPLATE PHRASES (will fail validation — never use these):
- "pricing, demand, or cost-to-serve pressure can flow into"
- "the transmission path runs through weaker unit economics"
- "the transmission path runs through reduced flexibility"
- "current cash conversion proves more cyclical than durable"
- "management should monitor those indicators and adjust execution"
- "the mechanism is that pricing, demand, or cost-to-serve"
Use company-specific language instead of these template phrases.

FILING GROUNDING (mandatory — validation will reject if missing):
- Each risk MUST reference specific language, data, or disclosures from the RISK
  FACTORS EXCERPT above. If the excerpt mentions a specific product, customer,
  regulation, market, or exposure — that becomes the risk name and mechanism.
- At least one risk must include either a direct quote (8+ words in double
  quotes) OR a natural filing attribution ("management noted that...",
  "the company disclosed...", "according to the filing...").
- Weave evidence into the explanation. DO NOT use the mechanical pattern
  "The filing warns that [quote]." followed by a separate sentence.
- If no filing excerpt is available, ground each risk in specific financial data
  from this company's filing — never write a risk that could apply to any company.

{budget_instruction}
""",
    do_rules=[
        "Name specific, company-relevant risks — not generic categories",
        "Rank risks by probability of actually happening, not dramatic impact",
        "Prefer 1-2 sharply differentiated risks over a crowded list",
        "Keep each risk as its own clean paragraph with no stacked labels or inline bolding",
        "Reject filing-structure debris and low-value legal boilerplate even if it appears in the excerpt",
        "Explain the causal mechanism for each risk",
        "Connect risks to vulnerabilities surfaced in preceding sections",
        "Write naturally — do not use a rigid mechanism/impact/signal template",
        "Use industry-specific knowledge to identify real threats",
        "Do NOT use generic risks; only highly company-specific risks",
        "State an approximate probability or likelihood for each risk — not just impact",
        "Make each risk actionable — what specifically should the investor watch for?",
    ],
    dont_rules=[
        "Do NOT list 'margin compression' or 'revenue deceleration' as risks — those are SYMPTOMS",
        "Do NOT use generic risks ('macroeconomic volatility') without company-specific context",
        "Do NOT turn this into another metrics section — numbers are optional support",
        "Do NOT repeat figures from earlier sections",
        "Do NOT use 'remains to be seen' or similar hedges",
        "Do NOT build a risk from a financial metric — margin %, FCF, cash balance are evidence, not risks",
        "Do NOT start a risk body with a financial number — start with the business event",
        "Do NOT use 'The filing warns that [quote]' as a mechanical sentence opener",
        "Do NOT separate mechanism, impact, and signal into rigid sentence slots",
        "Do NOT write vague monitoring language like 'The clearest indicators to monitor are X and Y' — state what could break and why",
    ],
    max_numeric_density=1,
    outline_anchor="2-3 named risks ranked by probability, each with a specific business exposure",
    transition_into="Pick up the strategic vulnerability MD&A surfaced — continue the golden thread",
    transition_out="Set up the verdict — what does this risk picture mean for the thesis?",
)

_CLOSING_TAKEAWAY = SectionTemplate(
    name="Closing Takeaway",
    system_guidance=(
        "You are delivering the verdict.  This section synthesizes the entire memo "
        "into a clear, forward-looking conclusion.  The reader should feel this "
        "verdict is the inevitable result of everything above.  No hedging, no "
        "equivocation — state what you think and why."
    ),
    user_prompt_template="""\
Write the Closing Takeaway for {company_name}.

THESIS TENSION: {central_tension}
KEY EVIDENCE: {key_evidence_summary}

THE TASK:
1. Synthesize the GOLDEN THREAD — pull together the central tension (Exec Summary),
   the evidence that tested it (Financial Performance), management's strategic
   response (MD&A), and what could break it (Risk Factors) into one inevitable
   conclusion.  The reader should feel this verdict was building from sentence one.
2. State a clear BUY / HOLD / SELL verdict that feels like the inevitable
   conclusion of everything above.
3. Emphasize current state, management credibility (did they deliver on prior
   promises or guidance?), and the forward setup rather than recapping the same
   figures already used above.  The verdict must connect to whether management
   has earned trust through execution.
4. Follow the budget-specific trigger rules below. Short sections use one measurable
   trigger; long-form sections use one "what must stay true" trigger and one
   "what breaks the thesis" trigger.
5. Include one implication for capital allocation, cash generation, or valuation
   support when the budget allows it.
6. Make it forward-looking without introducing new facts.
7. The final sentence must name the single metric, threshold, or dated trigger
   that would change the stance first.
8. Justify the verdict with the same drivers already established earlier in the
   memo. Do not introduce a new generic balance-sheet reason at the end unless
   that was one of the main analytical drivers above.

{persona_instruction}
{budget_instruction}
""",
    do_rules=[
        "Open by connecting back to the central tension",
        "Make the first sentence read like a decision plus the reason",
        "State a decisive verdict — BUY, HOLD, or SELL",
        "Connect verdict to management's credibility on previous commitments",
        "Tie the verdict to the same business drivers the memo already established",
        "Name exactly ONE measurable trigger that changes the view",
        "Make the trigger business-specific, not a generic margin recap",
        "Make it forward-looking",
        "Keep it tight — this is a destination, not a new section of analysis",
    ],
    dont_rules=[
        "Do NOT list multiple 'if X holds near Y' conditions — pick ONE trigger",
        "Do NOT introduce new facts not discussed earlier",
        "Do NOT use 'remains a key check/checkpoint'",
        "Do NOT use parenthetical asides or qualifier chains",
        "Do NOT use watch-list filler ('also monitor…', 'track…')",
        "Do NOT exceed 1 numeric anchor — this is a verdict, not a data section",
        "Do NOT repeat any specific dollar figure from earlier sections — synthesize conclusions, not numbers",
        "Do NOT end with meta-sentences like '[Company] matters' or '[Company] remains the proof point' — end with a specific forward-looking claim",
        "Do NOT hedge the verdict with 'suggests', 'indicates', or 'remains to be seen' — be direct",
        "Do NOT restate risks already covered — synthesize into a single clear judgment",
        "Do NOT justify the verdict with a generic cash-versus-liabilities line unless that balance-sheet tension was central to the earlier analysis",
    ],
    max_numeric_density=1,
    outline_anchor="Verdict (BUY/HOLD/SELL), one-sentence rationale, one trigger",
    transition_into="This is the destination the entire memo has been building toward",
    transition_out="(final section — no outbound transition)",
)

_FINANCIAL_HEALTH_RATING = SectionTemplate(
    name="Financial Health Rating",
    system_guidance=(
        "You are establishing the financial baseline.  Present the pre-calculated "
        "health score and explain in clear, plain language why the score is "
        "what it is and not higher or lower.  Frame it as: what is strong, "
        "what is weakening, and what the tradeoff is."
    ),
    user_prompt_template="""\
Write the Financial Health Rating for {company_name}.

PRE-CALCULATED SCORE: {health_score}/100 — {health_band}
KEY DRIVERS: {health_drivers}

THE TASK:
1. Start with: "{health_score}/100 — {health_band}."
2. In coherent prose sized to the section budget, explain why this score and not higher or lower.
   Anchor on the 2-3 metrics that most influenced the score.
   Use business-model-specific cash, capital intensity, funding, reserve, or working-capital dynamics instead of generic profitability/liquidity filler.
3. End with one sentence that raises the central operating question — a natural
   bridge into the deeper analysis that follows.  Do NOT name any section
   ("the Executive Summary will explore...").  Instead, pose or imply the
   question: "The score is strong, but the real test is whether [specific
   operating dynamic] holds up under [specific pressure]."

{budget_instruction}
""",
    do_rules=[
        "Use the exact pre-calculated score — never compute your own",
        "Anchor explanation on 2-3 key metric drivers",
        "End with a bridge into Executive Summary",
        "Explain in plain English what the score means for the business — not just which metrics drove it",
        "Frame the rating in terms of a tradeoff: what is strong vs. what is weakening",
    ],
    dont_rules=[
        "Do NOT calculate a different score",
        "Do NOT use letter grades (A, B, C, D)",
        "Do NOT list every available metric — be selective",
        "Do NOT use markdown sub-headers (## or ###) within this section — use bold text (**Label**) for sub-categories instead",
        "Do NOT reference other sections by name ('which is the key issue for the Executive Summary') — just deliver the analysis",
        "Do NOT end with meta-sentences like '[Company] matters' or 'The rating still depends on whether' — end with a specific forward-looking claim",
    ],
    max_numeric_density=3,
    outline_anchor="Score, band label, 2-3 driver metrics",
    transition_into="(opening section — no inbound transition)",
    transition_out="Set up the thesis question Executive Summary will frame",
)

_KEY_METRICS = SectionTemplate(
    name="Key Metrics",
    system_guidance=(
        "Produce a scannable numeric appendix with a short 'What Matters:' "
        "intro block above a deterministic data grid. The intro should prioritize "
        "which rows matter and why before the raw numbers. The grid itself must use "
        "pipe-separated numeric rows only."
    ),
    user_prompt_template="""\
Write the Key Metrics data block for {company_name}.

FORMAT RULES (non-negotiable):
- Required intro block:
  What Matters:
  - short analytical bullet
  - short analytical bullet
- Use the bullets to answer: what should the reader watch first, and why?
- After the intro, output ONLY a numeric data grid using pipe rows.
- Every grid row: Metric Name | Numeric Value
- Examples of CORRECT grid rows:
  Revenue | $21.7B
  Operating Margin | 30.3%
  Free Cash Flow | $8.21B | +188.1% QoQ
  Current Ratio | 2.9x
- Examples of INCORRECT grid rows:
  The company's Revenue was $21.73B, which is a good sign.
  We saw growth in revenue due to demand.

PRE-FORMATTED DATA (use these EXACT values):
{metrics_lines}

CRITICAL: Do NOT include blank lines between data rows. Wrap all rows with
DATA_GRID_START and DATA_GRID_END markers on their own lines.
When company-specific operating KPIs are available, place them before the generic
financial rows.

{budget_instruction}
""",
    do_rules=[
        "Use 'What Matters:' only for the short intro bullets above the grid",
        "Use the intro bullets to tell the reader which rows matter most and why",
        "Make the first bullet the single best row to watch first and what it confirms or breaks",
        "Make the second bullet explain the next most important operating confirmation or downside buffer",
        "Write the bullets like a watchlist, not like labels pasted above the grid",
        "Every data row: MetricName | NumericValue (with $, %, x formatting)",
        "Include period-over-period change where available (e.g., | +5.2% YoY)",
        "Group by category: Profitability, Liquidity, Leverage, Cash Flow",
        "Omit missing metrics — never write N/A or 'not available'",
    ],
    dont_rules=[
        "Do NOT add narrative paragraphs below the optional 'What Matters:' intro",
        "Do NOT write data rows without actual numeric values",
        "Do NOT explain what metrics mean — just present the data",
        "Do NOT invent numbers not in the provided data",
    ],
    max_numeric_density=99,
    outline_anchor="Optional 'What Matters' bullets plus a pipe-format data grid",
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
    section_instructions: Dict[str, str] = field(default_factory=dict)

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
        target = int(ctx.target_length or 0)
        if target and target < 180:
            return (
                "QUOTES: Filing-language snippets are available, but direct quotes are optional at this micro length. "
                "Use management attribution unless one quote materially sharpens the analysis."
            )
        if target and target < 400:
            return (
                "QUOTES: You may use at most 1 short direct quote (≤25 words) from the "
                "filing-language snippets provided, and only if it materially sharpens "
                "strategy, outlook, or the next operating checkpoint. Otherwise use "
                "attributed paraphrase."
            )
        if target and target < 1200:
            return (
                "QUOTES: Use 0-2 short direct quotes (≤25 words each) from the "
                "filing-language snippets provided, only when they materially add "
                "strategy, outlook, or what-happens-next context. If a quote feels "
                "legal, tax, accounting, or governance-heavy, skip it and paraphrase "
                "with attribution instead."
            )
        return (
            "QUOTES: Use 0-3 short direct quotes (≤25 words each) from the "
            "filing-language snippets provided, only when they materially add "
            "strategy, outlook, or what-happens-next context. COPY any direct quote "
            "EXACTLY character-by-character, but default to attributed paraphrase "
            "when the available quote is low-signal."
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
            risk_count = int(shape.risk_count or 0)
            per_risk_budget = max(1, budget // max(1, risk_count)) if risk_count else budget
            brevity_note = ""
            if per_risk_budget < 70:
                brevity_note = (
                    f" Budget is tight at ~{per_risk_budget} words per risk"
                    " — keep each risk concise, natural, and focused on the specific"
                    " exposure, why it matters, and what investors should watch."
                )
            return (
                f"{base} "
                f"Shape: write up to {risk_count} named risks from the strongest source-backed exposures. "
                f"Each risk should use {describe_sentence_range(int(shape.per_risk_min_sentences or 2), int(shape.per_risk_max_sentences or 3))} "
                f"(~{per_risk_budget} words each) "
                "and should read as natural prose grounded in a specific business exposure, "
                "ranked by probability first rather than forced mechanism/impact/signal slots. "
                "If fewer risks are genuinely supportable, keep fewer instead of padding the section."
                f"{brevity_note}"
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
        "If a fact was stated in an earlier section, reference it by implication only. "
        "Each specific dollar figure ($X.XXM/B) may appear in at most 2 sections — "
        "later sections must use different supporting evidence."
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
            f"You are a senior equity research analyst writing a clear, direct "
            f"investment memo.  Write like a sharp analyst explaining the filing to "
            f"a smart colleague — evidence-anchored, occasionally blunt, never stiff "
            f"or hedging.  You are filtering the analysis through the priorities of "
            f"{ctx.persona_name}, but you must NOT mimic catchphrases or produce "
            f"self-referential manifesto language.  Your goal is actionable, "
            f"differentiated insight with clear hierarchy and zero repetition."
        )
    return (
        "You are a senior equity research analyst writing a clear, direct "
        "investment memo.  Write like a sharp analyst explaining the filing to "
        "a smart colleague — third person, evidence-anchored, occasionally blunt, "
        "never stiff or hedging.  Produce actionable insight with clear hierarchy "
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

    expansion_section_blocks = []
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

        # Inject user's per-section custom instruction
        user_directive = ""
        if ctx.section_instructions and s_name in ctx.section_instructions:
            directive_text = ctx.section_instructions[s_name].strip()
            if directive_text:
                user_directive = (
                    f"\nUSER INSTRUCTION FOR THIS SECTION (absolute priority):\n"
                    f"{directive_text}\n"
                )

        expansion_section_blocks.append(
            f"## {s_name}\n"
            f"{filled_user_prompt}\n"
            f"{user_directive}"
            f"RULES:\n{do_block}\n{dont_block}"
        )

    all_sections = "\n\n---\n\n".join(expansion_section_blocks)

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

{CLARITY_FIRST_DIRECTIVE}

{FILING_CITATION_STYLE}

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

        # Inject user's per-section custom instruction
        user_section_directive = ""
        if ctx.section_instructions and s_name in ctx.section_instructions:
            directive_text = ctx.section_instructions[s_name].strip()
            if directive_text:
                user_section_directive = (
                    f"\nUSER INSTRUCTION FOR THIS SECTION (absolute priority):\n"
                    f"{directive_text}\n"
                )

        section_blocks.append(
            f"## {s_name}\n"
            f"{tmpl.system_guidance}\n"
            f"{dynamic_context}\n"
            f"{user_section_directive}\n"
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

{CLARITY_FIRST_DIRECTIVE}

{FILING_CITATION_STYLE}

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
    section_instructions: Optional[Dict[str, str]] = None,
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
        section_instructions=section_instructions or {},
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
