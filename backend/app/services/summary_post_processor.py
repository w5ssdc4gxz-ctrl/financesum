"""Validator-driven post-processing pipeline for filing summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from app.services.repetition_guard import RepetitionReport, check_repetition, detect_cross_section_dollar_figures, strip_repeated_sentences
from app.services.risk_evidence import assess_risk_overlap
from app.services.summary_budget_controller import (
    CANONICAL_SECTION_ORDER,
    describe_sentence_range,
    get_risk_factors_shape,
    risk_budget_target_count,
    SHORT_FORM_SECTIONED_TARGET_MAX_WORDS,
    SHORT_FORM_SECTIONED_TARGET_MIN_WORDS,
    section_budget_tolerance_words,
    total_word_tolerance_words,
)
from app.services.word_surgery import clean_ending, count_words

_SECTION_PATTERN = re.compile(r"##\s+(.+?)\s*\n(.*?)(?=\n##\s+|\Z)", re.DOTALL)
_HEADING_PATTERN = re.compile(r"^\s*##\s+(.+?)\s*$", re.MULTILINE)
_END_PUNCT_RE = re.compile(r'[.!?](?:["\')\]]+)?$')
_RISK_ITEM_RE = re.compile(
    r"\*\*(?P<name>[^*:\n]{2,120}?):?\*\*\s*:?\s*(?P<body>.+?)(?=(?:\s+\*\*[^*]+?\*\*\s*:?)|\Z)",
    re.DOTALL,
)
_GENERIC_RISK_NAME_RE = re.compile(
    r"\b("
    r"macro(?:economic)?|competition|competitive pressure|"
    r"regulatory risk|margin compression|liquidity risk|"
    r"cash flow risk|reinvestment risk|"
    r"margin\s*/?\s*reinvestment risk|cash conversion\s*/?\s*(?:capex )?risk|"
    r"capex risk|funding risk|margin risk|cost absorption risk|"
    r"payback risk|funding flexibility risk|demand conversion risk|"
    r"earnings quality(?: / normalization)? risk|"
    r"pricing\s*/?\s*competitive(?: spend| position)? risk|"
    r"liquidity\s*/?\s*funding risk|execution risk|"
    r"demand risk|growth risk|valuation risk|"
    r"unit[- ]economics reset risk|infrastructure utilization risk|"
    r"capital allocation constraint risk|"
    r"operating model \w+ risk|"
    r"revenue (?:concentration|mix|diversification) risk|"
    r"margin durability risk|cash conversion sustainability risk|"
    r"balance sheet flexibility risk|operating leverage risk|"
    r"cost[- ]to[- ]serve\b.*\brisk|"
    r"asset deployment\b.*\brisk|"
    r"delivery and conversion\b.*\brisk|"
    r"pricing pressure risk|"
    r"returns? risk|"
    r"conversion timing risk|"
    r"cash generation risk|"
    r"working[- ]capital risk|"
    r"cost structure risk|"
    r"profitability risk|"
    r"capital intensity risk|"
    r"financial flexibility risk|"
    r"debt service risk|"
    r"interest rate risk|"
    r"cybersecurity risk"
    r")\b",
    re.IGNORECASE,
)
# Catch risk names that are purely financial-metric-derived with no company nouns.
# Only matches when ALL significant words are financial terms.
# Deliberately excludes "working" and "timing" (used in legitimate fallback names
# like "Working-Capital Timing Risk") to avoid false positives on our own output.
_METRIC_ONLY_RISK_WORDS = frozenset({
    "cost", "costs", "margin", "margins", "pricing", "revenue", "cash", "capital",
    "asset", "assets", "debt", "leverage", "return", "returns", "conversion",
    "funding", "profitability", "liquidity", "deployment", "delivery",
    "earnings", "operating", "financial", "interest", "capex", "opex",
    "serve", "pressure", "risk", "constraint",
    "exposure", "sensitivity", "structure", "intensity", "generation",
    "rate", "and", "the", "of", "to", "a", "an",
})


def _is_metric_only_risk_name(name: str) -> bool:
    """True when every significant word in the risk name is a financial term."""
    words = re.findall(r"[a-z]+", name.lower())
    if len(words) < 3:
        return False
    return all(w in _METRIC_ONLY_RISK_WORDS for w in words)
_MECHANISM_RE = re.compile(
    r"\b(because|driven by|if|unless|leads to|results in|pressure|compress|dilute|erode|funding|liquidity|working capital|pricing|churn|renewal|mix shift|substitution|execution slip)\b",
    re.IGNORECASE,
)
_TRANSMISSION_RE = re.compile(
    r"\b(revenue|pricing|volume|mix|margins?|gross margins?|operating margins?|"
    r"cash flow|cash conversion|free cash flow|liquidity|refinancing|debt|"
    r"balance sheet|working capital|capex|opex|demand|backlog|bookings|shipments?)\b",
    re.IGNORECASE,
)
_EARLY_WARNING_RE = re.compile(
    r"\b(early[- ]warning|watch|signal|trigger|threshold|leading indicator|renewal|bookings|backlog|churn|pipeline|pricing|utilization|adoption|attrition|downtime|default|refinancing)\b",
    re.IGNORECASE,
)
_BOILERPLATE_EARLY_WARNING_RE = re.compile(
    r"("
    r"the mechanism is that pricing,?\s*demand,?\s*or cost-to-serve|"
    r"pricing,?\s*demand,?\s*or cost-to-serve pressure can flow into|"
    r"the transmission path runs through weaker unit economics|"
    r"the transmission path runs through reduced flexibility|"
    r"current cash conversion proves more cyclical than durable|"
    r"management should monitor those indicators and adjust execution before pressure"
    r")",
    re.IGNORECASE,
)
_COMMON_FINANCIAL_TOKENS = frozenset({
    "revenue", "margin", "margins", "operating", "income", "earnings",
    "growth", "profit", "costs", "expenses", "capital", "investment",
    "financial", "quarter", "fiscal", "annual", "company", "business",
    "market", "increase", "decrease", "higher", "lower", "period",
    "results", "performance", "management", "operations", "total",
    "compared", "primarily", "driven", "impact", "related", "during",
    "including", "approximately", "billion", "million", "percent",
    "respectively", "significant", "certain", "general", "based",
    "expected", "continued", "reported", "reflects", "affected",
})
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
SHORT_SECTIONED_WORD_BAND_TOLERANCE = 40

_SECTION_MEMORY_THEME_PATTERNS: Dict[str, re.Pattern[str]] = {
    "cash conversion": re.compile(
        r"cash\s+(?:conversion|converts?|converting)|operating[- ]cash[- ]flow\s+to\s+free[- ]cash[- ]flow",
        re.IGNORECASE,
    ),
    "free cash flow": re.compile(r"free\s+cash\s+flow|\bfcf\b", re.IGNORECASE),
    "reinvestment": re.compile(
        r"reinvestment|capex|capital\s+intensity|infrastructure\s+spend|capacity\s+build",
        re.IGNORECASE,
    ),
    "margin durability": re.compile(
        r"operating\s+margin|margin\s+(?:durability|retention|profile)|operating\s+leverage|profitability",
        re.IGNORECASE,
    ),
    "balance-sheet flexibility": re.compile(
        r"balance[- ]sheet|liquidity|funding|refinancing|cash\s+cushion|optionality",
        re.IGNORECASE,
    ),
    "capital allocation": re.compile(
        r"capital[- ]allocation|buybacks?|dividends?|m&a|shareholder\s+returns?",
        re.IGNORECASE,
    ),
    "management credibility": re.compile(
        r"credibility|on\s+track|delivered|missed|commitment|commitments|execution\s+quality",
        re.IGNORECASE,
    ),
    "guidance": re.compile(
        r"guidance|outlook|expects?|plans?|targets?|next\s+(?:quarter|period|half|year)|ahead",
        re.IGNORECASE,
    ),
}

_SECTION_MEMORY_METRIC_PATTERNS: Dict[str, re.Pattern[str]] = {
    "Revenue": re.compile(r"\brevenue\b", re.IGNORECASE),
    "Operating Income": re.compile(r"\boperating\s+income\b|\bebit\b", re.IGNORECASE),
    "Operating Margin": re.compile(r"\boperating\s+margin\b", re.IGNORECASE),
    "Net Margin": re.compile(r"\bnet\s+margin\b", re.IGNORECASE),
    "Operating Cash Flow": re.compile(
        r"\boperating\s+cash\s+flow\b|\bocf\b", re.IGNORECASE
    ),
    "Free Cash Flow": re.compile(r"\bfree\s+cash\s+flow\b|\bfcf\b", re.IGNORECASE),
    "Capex": re.compile(
        r"\bcapex\b|capital\s+expenditures?|property\s+and\s+equipment",
        re.IGNORECASE,
    ),
    "Cash": re.compile(r"\bcash(?:\s+\+\s+securities)?\b", re.IGNORECASE),
    "Debt": re.compile(r"\bdebt\b", re.IGNORECASE),
    "Liabilities": re.compile(r"\bliabilit(?:y|ies)\b", re.IGNORECASE),
}

_SECTION_MEMORY_MANAGEMENT_RE = re.compile(
    r"\b(management|leadership|executives?|ceo|cfo|board|capital allocation|pricing|guidance|outlook|roadmap|priority|priorities)\b",
    re.IGNORECASE,
)

_SECTION_MEMORY_PROMISE_RE = re.compile(
    r"\b(delivered|on track|missed|new commitment|commitment|commitments|credibility|proof point|watchpoint)\b",
    re.IGNORECASE,
)
_EXPLICIT_STANCE_RE = re.compile(
    r"(?<![A-Za-z-])(buy|hold|sell)(?![A-Za-z-])",
    re.IGNORECASE,
)
_HEALTH_NEGATIVE_RE = re.compile(
    r"\b(pressure|strained|weak(?:ening)?|deteriorat(?:e|es|ing|ion)|funding risk|liquidity risk|refinancing|leverage pressure|balance-sheet pressure)\b",
    re.IGNORECASE,
)
_HEALTH_POSITIVE_RE = re.compile(
    r"\b(healthy|resilien(?:t|ce)|durable|flexib(?:le|ility)|cushion|ample liquidity|strong cash|strong balance sheet)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionValidationFailure:
    section_name: str
    code: str
    message: str
    actual_words: int = 0
    budget_words: int = 0
    severity: float = 0.0


@dataclass
class SummaryValidationReport:
    passed: bool
    total_words: int
    lower_bound: int
    upper_bound: int
    global_failures: List[str] = field(default_factory=list)
    section_failures: List[SectionValidationFailure] = field(default_factory=list)
    repetition_report: RepetitionReport = field(default_factory=RepetitionReport)
    risk_count: int = 0


@dataclass
class PostProcessResult:
    """Result of the post-processing pipeline."""

    text: str
    passed: bool
    violations: List[str] = field(default_factory=list)
    retries: int = 0
    validation_report: Optional[SummaryValidationReport] = None


def _required_sections(include_health_rating: bool) -> List[str]:
    return [
        section_name
        for section_name in CANONICAL_SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]


def _extract_sections(text: str) -> Dict[str, str]:
    return {
        (match.group(1) or "").strip(): (match.group(2) or "").strip()
        for match in _SECTION_PATTERN.finditer(text or "")
    }


def _extract_section_body(text: str, section_name: str) -> str:
    return _extract_sections(text).get(section_name, "")


def _replace_section_body(text: str, section_name: str, new_body: str) -> str:
    escaped = re.escape(section_name)
    pattern = re.compile(
        r"(##\s+" + escaped + r"\s*\n)(.*?)(?=\n##\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    replacement = r"\g<1>" + new_body.strip() + "\n"
    return pattern.sub(replacement, text, count=1)


def _previous_section_text(text: str, section_name: str, include_health_rating: bool) -> str:
    ordered = _required_sections(include_health_rating)
    if section_name not in ordered:
        return ""
    idx = ordered.index(section_name)
    if idx <= 0:
        return ""
    return _extract_section_body(text, ordered[idx - 1])


def _normalize_memory_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _dedupe_memory_values(items: List[str], *, limit: Optional[int] = None) -> List[str]:
    results: List[str] = []
    seen: Set[str] = set()
    max_items = int(limit or 0)
    for raw in items:
        value = _normalize_memory_value(raw)
        if not value:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(value)
        if max_items > 0 and len(results) >= max_items:
            break
    return results


def _collect_section_memory(
    text: str,
    include_health_rating: bool,
) -> Dict[str, List[str]]:
    claims: List[str] = []
    used_theme_keys: List[str] = []
    used_anchor_metrics: List[str] = []
    used_company_terms: List[str] = []
    used_management_topics: List[str] = []
    used_promise_items: List[str] = []
    used_dollar_figures: List[str] = []
    skip_phrases = {
        "financial health rating",
        "executive summary",
        "financial performance",
        "management discussion analysis",
        "risk factors",
        "closing takeaway",
        "key metrics",
    }
    _dollar_re = re.compile(
        r"\$[\d,]+(?:\.\d+)?\s*(?:billion|million|thousand|[BMKbmk])?"
    )
    for section_name in _required_sections(include_health_rating):
        body = _extract_section_body(text, section_name)
        if not body:
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        if sentences:
            claims.append(sentences[0])
        for theme_name, pattern in _SECTION_MEMORY_THEME_PATTERNS.items():
            if pattern.search(body):
                used_theme_keys.append(theme_name)
        for label, pattern in _SECTION_MEMORY_METRIC_PATTERNS.items():
            if pattern.search(body):
                used_anchor_metrics.append(label)
        if _SECTION_MEMORY_MANAGEMENT_RE.search(body):
            topic_sentences = [
                sentence
                for sentence in sentences[:3]
                if _SECTION_MEMORY_MANAGEMENT_RE.search(sentence)
            ]
            used_management_topics.extend(topic_sentences[:2] or sentences[:1])
        if _SECTION_MEMORY_PROMISE_RE.search(body):
            promise_sentences = [
                sentence
                for sentence in sentences[:3]
                if _SECTION_MEMORY_PROMISE_RE.search(sentence)
            ]
            used_promise_items.extend(promise_sentences[:2] or sentences[:1])

        capitalized_phrases = re.findall(
            r"\b(?:[A-Z]{2,}(?:\s+[A-Z]{2,})*|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b",
            body,
        )
        for phrase in capitalized_phrases:
            normalized = _normalize_memory_value(phrase)
            lowered = normalized.lower()
            if lowered in skip_phrases or len(normalized) < 3:
                continue
            if normalized.isdigit():
                continue
            used_company_terms.append(normalized)

        # Track dollar figures used in earlier sections
        if section_name != "Key Metrics":
            for match in _dollar_re.finditer(body):
                fig = match.group(0).strip()
                if len(fig) >= 3:
                    used_dollar_figures.append(fig)

    return {
        "used_claims": _dedupe_memory_values(claims, limit=8),
        "used_theme_keys": _dedupe_memory_values(used_theme_keys, limit=10),
        "used_anchor_metrics": _dedupe_memory_values(used_anchor_metrics, limit=10),
        "used_company_terms": _dedupe_memory_values(used_company_terms, limit=10),
        "used_management_topics": _dedupe_memory_values(
            used_management_topics, limit=8
        ),
        "used_promise_items": _dedupe_memory_values(used_promise_items, limit=6),
        "used_dollar_figures": _dedupe_memory_values(used_dollar_figures, limit=15),
    }


def _collect_used_claims(text: str, include_health_rating: bool) -> List[str]:
    return list(
        _collect_section_memory(text, include_health_rating).get("used_claims") or []
    )


def _has_terminal_punctuation(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped) and bool(_END_PUNCT_RE.search(stripped))


def _has_dangling_ending(text: str) -> bool:
    stripped = str(text or "").rstrip()
    for pattern in _DANGLING_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    return False


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()])


def _strip_company_prefix_from_risk_name(name: str, company_name: str) -> str:
    """Strip a leading company name from a risk name so generic suffixes are caught.

    E.g., "ASML Margin Risk" → "Margin Risk" for re-checking against the
    generic risk name regex.
    """
    if not company_name:
        return name
    stripped = name.strip()
    # Try removing the full company name or its first token (ticker/short name)
    for prefix in (company_name.strip(), company_name.strip().split()[0]):
        if stripped.lower().startswith(prefix.lower()):
            remainder = stripped[len(prefix):].strip().lstrip("'s").strip()
            if remainder:
                return remainder
    return stripped


def _repair_punctuation(text: str) -> str:
    """Fix common LLM punctuation artifacts."""
    result = text
    # Fix ".:","!:","?:" → just the first punctuation
    result = re.sub(r'([.!?])\s*:', r'\1', result)
    # Fix double periods ".."
    result = re.sub(r'\.{2,}', '.', result)
    # Fix ". ." with space
    result = re.sub(r'\.\s+\.', '.', result)
    # Fix ",." or ",!"
    result = re.sub(r',\s*([.!?])', r'\1', result)
    return result


_SELF_REFERENCE_RE = re.compile(
    r"(?:"
    r"[Tt]his sets up the \w[\w\s]* section"
    r"|[Aa]s (?:discussed|noted|tracked|outlined|covered) in the [\w\s]+ (?:section|above|below)"
    r"|[Tt]he Key Metrics (?:below |above |section )?show"
    r"|[Tt]hese [\w\s]* (?:are|is) tracked in"
    r"|[Aa]s the [\w\s]+ section (?:shows|details|explores|covers)"
    r")",
    re.IGNORECASE,
)

# Instruction leakage — prompt/meta-instructions echoed verbatim in output.
# Catches patterns that slip through all three pipeline paths.
_INSTRUCTION_LEAK_RE = re.compile(
    r"\b(?:"
    # "should frame X as" / "should highlight" / "should emphasize"
    r"(?:you\s+)?should\s+(?:frame|highlight|emphasize|focus\s+on|prioritize|avoid|include|mention|note)\b"
    r"|(?:this|the)\s+(?:financial health rating|executive summary|financial performance|"
    r"management discussion(?:\s*&\s*analysis)?|md&a|risk factors|closing takeaway|key metrics)\s+"
    r"(?:should|must|needs?\s+to)\b"
    # Direct prompt artifacts
    r"|user instruction|section focus|style contract|quote mandate|citation mandate"
    r"|return only the section body|per the guidelines|as instructed"
    # "trying to accomplish" / "this memo will/should/must"
    r"|trying to accomplish|this memo (?:will|should|must)\b"
    r"|the summary should\b"
    r")",
    re.IGNORECASE,
)

# Aha insight signal — Executive Summary should contain a non-obvious reframe.
_AHA_SIGNAL_RE = re.compile(
    r"\b("
    r"the real implication|the underappreciated point|"
    r"what the market may be missing|what matters now|"
    r"this means|that means|implies that|"
    r"most investors would|counter-?intuitiv|"
    r"what .{0,20}actually shows|"
    r"the non-obvious|the overlooked|the hidden"
    r")\b",
    re.IGNORECASE,
)

# Casual first-person framing in Closing Takeaway (banned without explicit persona).
_CASUAL_FIRST_PERSON_RE = re.compile(
    r"\b(?:"
    r"for my (?:own )?portfolio|in my portfolio|"
    r"I would (?:buy|sell|hold)|"
    r"I.{0,10}(?:am|would be) (?:buying|selling|holding)"
    r")\b",
    re.IGNORECASE,
)

# Regulatory minutiae in risk factors — disclosure/compliance/accounting that
# doesn't affect revenue, margins, or growth.
_REGULATORY_MINUTIAE_RE = re.compile(
    r"\b("
    r"fin[- ]?sa|fin[- ]?ia|"
    r"disclosure\s+requirements?|"
    r"income\s+tax\s+(?:reporting|disclosure)|"
    r"accounting\s+standard\s+(?:adoption|change)|"
    r"internal[- ]control\s+(?:over\s+financial\s+reporting|deficienc)|"
    r"audit\s+committee|"
    r"forward[- ]looking\s+statement\s+disclaimer|"
    r"asu\s+\d{4}|asc\s+\d{3}|ifrs\s+\d+"
    r")\b",
    re.IGNORECASE,
)


def _detect_self_references(text: str) -> List[str]:
    """Return self-referential meta-language phrases found in *text*."""
    return [m.group(0) for m in _SELF_REFERENCE_RE.finditer(text or "")]


def _detect_instruction_leakage(text: str) -> List[str]:
    """Return instruction-leakage phrases found in *text*."""
    return [m.group(0) for m in _INSTRUCTION_LEAK_RE.finditer(text or "")]


def _validate_risk_factors(
    text: str,
    *,
    risk_budget_words: int,
    risk_factors_excerpt: str = "",
    company_name: str = "",
) -> tuple[int, list[tuple[str, str]]]:
    """Return (count, [(code, message), ...]).

    Codes are either ``"risk_schema"`` (structural count/shape failures),
    ``"risk_specificity"`` (generic, ungrounded, or boilerplate risks that
    should never ship), or ``"risk_quality"`` for softer advisories.
    """
    body = _extract_section_body(text, "Risk Factors")
    if not body:
        return 0, [("risk_schema", "Risk Factors section is missing.")]

    shape = get_risk_factors_shape(risk_budget_words)
    expected_count = int(shape.risk_count or 0)
    items = list(_RISK_ITEM_RE.finditer(body))
    # At borderline budgets where per-risk allocation is tight (<65 words),
    # generating expected_count - 1 risks is a recoverable shortfall.
    # Continue to quality-check the risks that ARE present rather than
    # returning immediately; the count mismatch is appended at the end.
    _deferred_count_mismatch: list[tuple[str, str]] = []
    if len(items) != expected_count:
        min_viable_items = min(2, max(1, int(expected_count or 0)))
        can_quality_check_present_items = len(items) >= int(min_viable_items)
        if can_quality_check_present_items:
            _deferred_count_mismatch.append((
                "risk_schema",
                f"Risk Factors has {len(items)} structured risk(s); expected exactly {expected_count} for this budget.",
            ))
        else:
            return len(items), [
                ("risk_schema", f"Risk Factors has {len(items)} structured risk(s); expected exactly {expected_count} for this budget.")
            ]

    per_risk_budget = max(1, int(risk_budget_words) // max(1, expected_count))
    excerpt_terms = {
        token
        for token in re.findall(r"[a-z]{5,}", (risk_factors_excerpt or "").lower())
        if token not in {"about", "their", "which", "these", "those", "other"}
    }
    seen_names: set[str] = set()
    _risk_name_tokens: list[str] = []
    _risk_body_tokens: list[str] = []
    failures: list[tuple[str, str]] = []

    for item in items:
        name = (item.group("name") or "").strip()
        body_text = (item.group("body") or "").strip()
        canon_name = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if canon_name in seen_names:
            failures.append(("risk_schema", "Risk Factors contain duplicate risk names. Use distinct, non-overlapping drivers."))
            continue
        seen_names.add(canon_name)

        for prev_name, prev_body in zip(_risk_name_tokens, _risk_body_tokens):
            overlap = assess_risk_overlap(
                risk_name=name,
                risk_body=body_text,
                other_risk_name=prev_name,
                other_risk_body=prev_body,
            )
            if overlap.names_overlap:
                overlapping = ", ".join(overlap.shared_name_tokens)
                failures.append((
                    "risk_schema",
                    f"Risk name '{name}' overlaps too much with a previous risk "
                    f"(shared tokens: {overlapping}). "
                    "Each risk must address a completely different mechanism and business area.",
                ))
                break
            if overlap.bodies_overlap:
                failures.append((
                    "risk_quality",
                    f"Risk '{name}' body is too similar to a previous risk body. "
                    "Each risk must describe a distinct mechanism and impact pathway.",
                ))
                break
        _risk_name_tokens.append(name)
        _risk_body_tokens.append(body_text)

        if _GENERIC_RISK_NAME_RE.search(name):
            failures.append((
                "risk_specificity",
                f"Risk name '{name}' is too generic. Name the specific exposure, counterparty, regulation, or product/segment at risk.",
            ))
        elif company_name:
            stripped_name = _strip_company_prefix_from_risk_name(name, company_name)
            if stripped_name != name and _GENERIC_RISK_NAME_RE.search(stripped_name):
                failures.append((
                    "risk_specificity",
                    f"Risk name '{name}' is a generic category with the company name prepended. "
                    f"Name the specific exposure, product, segment, or competitive dynamic at risk.",
                ))
        if _is_metric_only_risk_name(name):
            failures.append((
                "risk_specificity",
                f"Risk name '{name}' is built from financial metrics, not a business event. "
                f"Name a specific product, segment, customer, geography, or regulation at risk.",
            ))
        # Regulatory minutiae that doesn't affect revenue/margins/growth
        if _REGULATORY_MINUTIAE_RE.search(name) or _REGULATORY_MINUTIAE_RE.search(body_text):
            has_business_impact = bool(re.search(
                r"\b(revenue|margin|growth|earnings|cash flow|profitability|competitive)\b",
                body_text, re.IGNORECASE,
            ))
            if not has_business_impact:
                failures.append((
                    "risk_specificity",
                    f"Risk '{name}' is regulatory minutiae (disclosure/compliance/accounting) "
                    f"that does not affect revenue, margins, or growth. Replace with a "
                    f"business-critical risk.",
                ))
        sentence_count = _sentence_count(body_text)
        if not (
            int(shape.per_risk_min_sentences or 2)
            <= sentence_count
            <= int(shape.per_risk_max_sentences or 3)
        ):
            failures.append((
                "risk_schema",
                f"Risk Factors under '{name}' must contain "
                + describe_sentence_range(
                    int(shape.per_risk_min_sentences or 2),
                    int(shape.per_risk_max_sentences or 3),
                )
                + ".",
            ))
        # Risk bodies that open with a financial number are metric recaps, not risks.
        _first_sentence = (body_text.split(".")[0] or "").strip()
        if re.match(
            r"(?:[\"“][^\"”]{0,80}[\"”]\s*,?\s*)?"
            r"(?:A key risk is that |The risk is that |With )?"
            r"(?:the )?(?:current |trailing )?(?:operating |net |gross )?"
            r"(?:margin|cash flow|revenue|FCF|EBITDA|OCF|capex|cash|debt|"
            r"free cash flow|conversion|liquidity)\b",
            _first_sentence,
            re.IGNORECASE,
        ):
            failures.append((
                "risk_specificity",
                f"Risk Factors under '{name}' opens with a financial metric recap. "
                f"Start with the business event (customer loss, regulation change, "
                f"supply disruption), not a number.",
            ))
        if re.match(r'^(?:["“][^"”]{0,120}["”]\s*,?\s*)?(?:\$|\d)', _first_sentence):
            failures.append((
                "risk_specificity",
                f"Risk Factors under '{name}' opens with a numeric or metric-led setup. "
                f"Open with the business event first, then use figures only as supporting evidence.",
            ))
        if len(body_text.split()) < 18:
            failures.append((
                "risk_schema",
                f"Risk Factors are too thin under '{name}'. Expand each risk with enough sentence depth to explain the exposure and business impact.",
            ))
        # At tight per-risk budgets (<90 words), mechanism/transmission misses
        # are advisory — the LLM may not have enough room to hit every keyword.
        _depth_code = "risk_quality" if per_risk_budget < 90 else "risk_schema"
        if not _MECHANISM_RE.search(body_text):
            failures.append((
                _depth_code,
                f"Risk Factors under '{name}' need a concrete mechanism (what causes the risk and how it hits the business).",
            ))
        if not _TRANSMISSION_RE.search(body_text):
            failures.append((
                _depth_code,
                f"Risk Factors under '{name}' must explain the financial impact path into revenue, margins, cash flow, or balance-sheet flexibility.",
            ))
        if not _EARLY_WARNING_RE.search(body_text):
            failures.append((
                "risk_quality",
                f"Risk Factors under '{name}' would be stronger with a concrete early-warning signal.",
            ))
        if _BOILERPLATE_EARLY_WARNING_RE.search(body_text):
            failures.append((
                "risk_specificity",
                f"Risk Factors under '{name}' contain boilerplate language. Replace templated risk language with company-specific analysis.",
            ))
        if excerpt_terms:
            body_tokens = set(re.findall(r"[a-z]{5,}", body_text.lower()))
            overlap = body_tokens & excerpt_terms
            if not overlap:
                failures.append((
                    "risk_specificity",
                    f"Risk Factors under '{name}' are too generic relative to the filing excerpt. Tie the mechanism to company-specific terms.",
                ))
            elif not (overlap - _COMMON_FINANCIAL_TOKENS):
                failures.append((
                    "risk_specificity",
                    f"Risk Factors under '{name}' overlap with the filing only on generic financial terms. "
                    f"Reference a specific product, segment, customer, or technology from the filing.",
                ))

    # Append deferred count mismatch (borderline budgets) after quality checks.
    if _deferred_count_mismatch:
        failures = _deferred_count_mismatch + failures

    return len(items), failures


def _is_advisory_risk_quality_failure(failure: SectionValidationFailure) -> bool:
    return bool(
        getattr(failure, "code", "") == "risk_quality"
        and "early-warning signal" in str(getattr(failure, "message", "")).lower()
    )


def _validation_section_budget_tolerance_words(
    section_name: str,
    budget_words: int,
    *,
    target_words: int,
) -> int:
    """Use wider section bands only for high-budget long-form validation."""
    base_tolerance = section_budget_tolerance_words(section_name, budget_words)
    if section_name == "Key Metrics":
        return int(base_tolerance)

    budget = max(0, int(budget_words or 0))
    target = max(0, int(target_words or 0))
    if target >= 1500 and budget >= 350:
        return max(int(base_tolerance), int(round(budget * 0.05)))
    return int(base_tolerance)


def _validation_total_tolerance_words(target_words: int) -> int:
    target = max(0, int(target_words or 0))
    if (
        int(SHORT_FORM_SECTIONED_TARGET_MIN_WORDS)
        <= target
        < int(SHORT_FORM_SECTIONED_TARGET_MAX_WORDS)
    ):
        return int(SHORT_SECTIONED_WORD_BAND_TOLERANCE)
    if target >= int(SHORT_FORM_SECTIONED_TARGET_MAX_WORDS):
        return min(15, int(total_word_tolerance_words(target_words)))
    return int(total_word_tolerance_words(target_words))


def _validate_stance_location_and_consistency(
    sections: Dict[str, str],
) -> List[SectionValidationFailure]:
    failures: List[SectionValidationFailure] = []
    closing_body = str(sections.get("Closing Takeaway") or "").strip()
    if not closing_body:
        return failures

    closing_tokens = [
        (match.group(1) or "").lower()
        for match in _EXPLICIT_STANCE_RE.finditer(closing_body)
    ]
    distinct_closing_tokens = list(dict.fromkeys(closing_tokens))
    if len(set(distinct_closing_tokens)) > 1:
        failures.append(
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="conflicting_stance",
                message=(
                    "Closing Takeaway contains conflicting stances. Use exactly one explicit BUY/HOLD/SELL verdict."
                ),
                severity=3.2,
            )
        )
    closing_stance = distinct_closing_tokens[-1] if distinct_closing_tokens else ""

    for section_name, body in sections.items():
        if section_name == "Closing Takeaway":
            continue
        stance_tokens = [
            (match.group(1) or "").lower()
            for match in _EXPLICIT_STANCE_RE.finditer(str(body or ""))
        ]
        if not stance_tokens:
            continue
        failures.append(
            SectionValidationFailure(
                section_name=section_name,
                code="stance_outside_closing",
                message=(
                    f"{section_name} uses explicit BUY/HOLD/SELL language. Only Closing Takeaway may state the recommendation."
                ),
                severity=3.0,
            )
        )
        if closing_stance and any(token != closing_stance for token in stance_tokens):
            failures.append(
                SectionValidationFailure(
                    section_name=section_name,
                    code="conflicting_stance",
                    message=(
                        f"{section_name} contradicts the Closing Takeaway stance '{closing_stance.upper()}'."
                    ),
                    severity=3.1,
                )
            )
    return failures


def _validate_health_closing_alignment(
    sections: Dict[str, str],
) -> Optional[SectionValidationFailure]:
    health_body = str(sections.get("Financial Health Rating") or "").strip()
    closing_body = str(sections.get("Closing Takeaway") or "").strip()
    if not health_body or not closing_body:
        return None
    closing_tokens = [
        (match.group(1) or "").lower()
        for match in _EXPLICIT_STANCE_RE.finditer(closing_body)
    ]
    if not closing_tokens:
        return None
    stance = closing_tokens[-1]
    negative_hits = len(_HEALTH_NEGATIVE_RE.findall(health_body))
    positive_hits = len(_HEALTH_POSITIVE_RE.findall(health_body))
    if stance == "buy" and negative_hits >= 2 and positive_hits == 0:
        return SectionValidationFailure(
            section_name="Closing Takeaway",
            code="health_closing_misalignment",
            message=(
                "Closing Takeaway is more constructive than the Financial Health Rating supports. Align the recommendation with the health/risk posture."
            ),
            severity=2.9,
        )
    if stance == "sell" and positive_hits >= 2 and negative_hits == 0:
        return SectionValidationFailure(
            section_name="Closing Takeaway",
            code="health_closing_misalignment",
            message=(
                "Closing Takeaway is more negative than the Financial Health Rating supports. Align the recommendation with the health/risk posture."
            ),
            severity=2.7,
        )
    return None


def validate_summary(
    text: str,
    *,
    target_words: int,
    section_budgets: Dict[str, int],
    include_health_rating: bool = True,
    risk_factors_excerpt: str = "",
    total_tolerance_words: Optional[int] = None,
    company_name: str = "",
) -> SummaryValidationReport:
    working_text = str(text or "").strip()
    total_words = count_words(working_text)
    target = int(target_words or 0)
    if total_tolerance_words is not None:
        tolerance_words = max(0, int(total_tolerance_words))
    else:
        tolerance_words = _validation_total_tolerance_words(target_words)
    lower = max(1, int(target_words) - int(tolerance_words))
    upper = int(target_words) + int(tolerance_words)

    report = SummaryValidationReport(
        passed=True,
        total_words=total_words,
        lower_bound=lower,
        upper_bound=upper,
    )

    if total_words < lower:
        report.global_failures.append(
            f"Under word target: {total_words} words (need ≥{lower}). Regeneration required — do not pad with filler."
        )
    elif total_words > upper:
        report.global_failures.append(
            f"Over word target: {total_words} words (need ≤{upper})."
        )

    sections = _extract_sections(working_text)
    expected_sections = _required_sections(include_health_rating)
    seen_headings = [(match.group(1) or "").strip() for match in _HEADING_PATTERN.finditer(working_text)]
    extra_sections = [
        heading for heading in seen_headings if heading not in expected_sections
    ]
    if extra_sections:
        report.global_failures.append(
            f"Extra sections detected: {', '.join(extra_sections)}."
        )

    for section_name in expected_sections:
        body = (sections.get(section_name) or "").strip()
        if not body:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name=section_name,
                    code="missing_section",
                    message=f"Missing required section '{section_name}'.",
                    severity=1.5,
                )
            )
            continue
        if section_name != "Key Metrics":
            if not _has_terminal_punctuation(body):
                report.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="missing_terminal_punctuation",
                        message=f"{section_name} must end with terminal punctuation.",
                        severity=1.0,
                    )
                )
            if _has_dangling_ending(body):
                report.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="dangling_ending",
                        message=f"{section_name} ends with a dangling clause.",
                        severity=1.1,
                    )
                )
            if _sentence_count(body) < 2:
                report.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="too_few_sentences",
                        message=f"{section_name} must contain at least 2 full sentences.",
                        severity=1.2,
                    )
                )
        budget = int(section_budgets.get(section_name, 0) or 0)
        if budget > 0:
            actual_words = count_words(body)
            tolerance = _validation_section_budget_tolerance_words(
                section_name,
                budget,
                target_words=target_words,
            )
            lower_budget = max(1, budget - tolerance)
            upper_budget = budget + tolerance
            if section_name == "Key Metrics":
                key_metrics_lower_budget = int(lower_budget)
                try:
                    from app.api.filings import _key_metrics_contract_word_window

                    key_metrics_window = _key_metrics_contract_word_window(
                        target_length=target_words,
                        include_health_rating=include_health_rating,
                    )
                    key_metrics_lower_budget = max(
                        int(key_metrics_lower_budget),
                        int(key_metrics_window.get("min_words") or 0),
                    )
                except Exception:
                    pass
                if actual_words < key_metrics_lower_budget:
                    report.section_failures.append(
                        SectionValidationFailure(
                            section_name=section_name,
                            code="key_metrics_contract_under",
                            message=(
                                "Key Metrics contract underflow: "
                                f"{actual_words} words; need ≥{key_metrics_lower_budget}."
                            ),
                            actual_words=actual_words,
                            budget_words=budget,
                            severity=(
                                max(0, key_metrics_lower_budget - actual_words)
                                / max(1, key_metrics_lower_budget)
                            ),
                        )
                    )
                elif actual_words > upper_budget:
                    report.section_failures.append(
                        SectionValidationFailure(
                            section_name=section_name,
                            code="section_budget_over",
                            message=(
                                f"Section balance issue: '{section_name}' is overweight "
                                f"({actual_words} words; target ~{budget}±{tolerance})."
                            ),
                            actual_words=actual_words,
                            budget_words=budget,
                            severity=abs(actual_words - budget) / max(1, budget),
                        )
                    )
                continue
            if actual_words < lower_budget:
                report.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="section_budget_under",
                        message=(
                            f"Section balance issue: '{section_name}' is underweight "
                            f"({actual_words} words; target ~{budget}±{tolerance})."
                        ),
                        actual_words=actual_words,
                        budget_words=budget,
                        severity=abs(actual_words - budget) / max(1, budget),
                    )
                )
            elif actual_words > upper_budget:
                report.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="section_budget_over",
                        message=(
                            f"Section balance issue: '{section_name}' is overweight "
                            f"({actual_words} words; target ~{budget}±{tolerance})."
                        ),
                        actual_words=actual_words,
                        budget_words=budget,
                        severity=abs(actual_words - budget) / max(1, budget),
                    )
                )

    repetition_report = check_repetition(working_text)
    report.repetition_report = repetition_report
    fatal_repetition = bool(
        repetition_report.duplicate_sentences
        or repetition_report.similar_paragraph_pairs
    )
    if fatal_repetition:
        if repetition_report.duplicate_sentences:
            report.global_failures.append("Duplicate sentences detected.")
        if repetition_report.similar_paragraph_pairs:
            report.global_failures.append("Near-duplicate paragraphs detected.")
        for section_name in repetition_report.affected_sections:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name=section_name,
                    code="repetition",
                    message=f"{section_name} contains repeated analytical content.",
                    severity=2.5,
                )
            )

    if repetition_report.placeholder_number_artifacts:
        report.global_failures.append(
            "Placeholder numeric artifacts detected. Remove filler replacements such as 'that figure' and broken scale-word remnants."
        )
        for section_name in repetition_report.affected_sections:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name=section_name,
                    code="placeholder_number_artifact",
                    message=(
                        f"{section_name} contains broken numeric placeholder text. Regenerate the section from source-backed prose."
                    ),
                    severity=3.4,
                )
            )

    # Cross-section dollar figure repetition — fatal
    if repetition_report.cross_section_dollar_figures:
        figures_desc = ", ".join(
            f"{d.figure} in {d.count} sections" for d in repetition_report.cross_section_dollar_figures
        )
        report.global_failures.append(
            f"Cross-section dollar figure repetition: {figures_desc}. "
            "Each specific dollar figure should appear in at most 2 sections."
        )
        # Add section-level failures for each affected section (pick later sections for regen)
        dollar_affected: set[str] = set()
        for d in repetition_report.cross_section_dollar_figures:
            # Flag sections beyond the first two (the later ones should be rewritten)
            for section_name in d.sections[2:]:
                dollar_affected.add(section_name)
        for section_name in dollar_affected:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name=section_name,
                    code="cross_section_dollars",
                    message=(
                        f"{section_name} repeats dollar figures already used in earlier sections. "
                        "Use different supporting evidence or reference the figure by implication only."
                    ),
                    severity=2.3,
                )
            )

    # Note: filler_phrases and incoherent_endings are tracked in
    # repetition_report but NOT added as section_failures here.
    # They are injected by _inject_soft_quality_failures() only when
    # running through post_process_summary with a regen function.

    risk_count, risk_failures = _validate_risk_factors(
        working_text,
        risk_budget_words=int(section_budgets.get("Risk Factors", 0) or 0),
        risk_factors_excerpt=risk_factors_excerpt,
        company_name=company_name,
    )
    report.risk_count = risk_count
    for failure_code, failure_msg in risk_failures:
        report.section_failures.append(
            SectionValidationFailure(
                section_name="Risk Factors",
                code=failure_code,
                message=failure_msg,
                severity=(
                    3.5
                    if failure_code == "risk_schema"
                    else 3.4 if failure_code == "risk_specificity" else 1.5
                ),
            )
        )

    # Detect self-referential meta-language
    self_refs = _detect_self_references(working_text)
    if self_refs:
        report.global_failures.append(
            f"Self-referential structure text detected: {'; '.join(self_refs[:3])}. "
            "Remove all references to the memo's own sections."
        )

    # Detect instruction leakage (prompt text echoed in output)
    instruction_leaks = _detect_instruction_leakage(working_text)
    if instruction_leaks:
        report.global_failures.append(
            f"Instruction leakage detected: {'; '.join(instruction_leaks[:3])}. "
            "Remove all meta-instructions from the output."
        )

    # Management voice validation for MD&A
    mda_body = (sections.get("Management Discussion & Analysis") or "").strip()
    if mda_body and count_words(mda_body) >= 100:
        mda_quotes = re.findall(r'[“"]([^"”]{8,})[”"]', mda_body)
        mda_attributions = len(re.findall(
            r"\b(?:management|CEO|CFO|company)\b.{0,30}\b(?:noted|said|stated|indicated|acknowledged|emphasized|highlighted|cautioned|described|characterized)\b",
            mda_body,
            re.IGNORECASE,
        ))
        if not mda_quotes and mda_attributions < 1:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name="Management Discussion & Analysis",
                    code="insufficient_management_voice",
                    message=(
                        "MD&A lacks management voice. Include at least one direct quote "
                        "or clear management attribution (e.g., 'Management noted that...')."
                    ),
                    severity=1.8,
                )
            )

    # Executive Summary management voice validation
    exec_body = (sections.get("Executive Summary") or "").strip()
    if exec_body and count_words(exec_body) >= 80:
        exec_quotes = re.findall(r'["\u201c]([^"\u201d]{8,})["\u201d]', exec_body)
        exec_attributions = len(re.findall(
            r"\b(?:management|CEO|CFO|company|leadership)\b.{0,30}\b(?:noted|said|stated|indicated|acknowledged|emphasized|highlighted|cautioned|described|characterized|warned)\b",
            exec_body,
            re.IGNORECASE,
        ))
        if not exec_quotes and exec_attributions < 1:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name="Executive Summary",
                    code="insufficient_management_voice",
                    message=(
                        "Executive Summary lacks management voice. Include at least one direct quote "
                        "or clear management attribution (e.g., 'Management noted that...')."
                    ),
                    severity=1.5,
                )
            )

    # Risk Factors filing-grounding validation
    risk_body = (sections.get("Risk Factors") or "").strip()
    if risk_body and count_words(risk_body) >= 80:
        risk_quotes = re.findall(r'["\u201c]([^"\u201d]{8,})["\u201d]', risk_body)
        risk_attributions = len(re.findall(
            r"\b(?:filing|company|management|disclosure)\b.{0,40}\b(?:warns?|identifies?|discloses?|notes?|highlights?|acknowledges?|cites?|reports?)\b",
            risk_body,
            re.IGNORECASE,
        ))
        if not risk_quotes and risk_attributions < 1:
            report.section_failures.append(
                SectionValidationFailure(
                    section_name="Risk Factors",
                    code="insufficient_filing_grounding",
                    message=(
                        "Risk Factors lack filing grounding. Include at least one direct quote from "
                        "the filing's risk disclosures or explicit filing attribution "
                        "(e.g., 'The filing warns that...')."
                    ),
                    severity=1.5,
                )
            )

    # Executive Summary opening quality check
    exec_body_for_opening = (sections.get("Executive Summary") or "").strip()
    if exec_body_for_opening:
        first_sentence = (exec_body_for_opening.split(".")[0] or "").strip()
        if re.match(r'^\s*["\u201c]', first_sentence):
            report.section_failures.append(
                SectionValidationFailure(
                    section_name="Executive Summary",
                    code="weak_exec_opening",
                    message=(
                        "Executive Summary opens with a quote. The first sentence must be "
                        "an analytical claim about the company, not a quote."
                    ),
                    severity=2.8,
                )
            )
        elif _INSTRUCTION_LEAK_RE.search(first_sentence):
            report.section_failures.append(
                SectionValidationFailure(
                    section_name="Executive Summary",
                    code="weak_exec_opening",
                    message=(
                        "Executive Summary opens with instruction leakage. The first sentence "
                        "must be a core insight about the company."
                    ),
                    severity=3.0,
                )
            )

    # Casual first-person in Closing Takeaway (banned without explicit persona)
    closing_body = (sections.get("Closing Takeaway") or "").strip()
    if closing_body and _CASUAL_FIRST_PERSON_RE.search(closing_body):
        report.section_failures.append(
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="casual_first_person",
                message=(
                    "Closing Takeaway uses casual first-person framing "
                    "('For my own portfolio' or similar). Use institutional "
                    "third-person analyst voice unless a persona was requested."
                ),
                severity=2.5,
            )
        )

    report.section_failures.extend(_validate_stance_location_and_consistency(sections))
    health_alignment_failure = _validate_health_closing_alignment(sections)
    if health_alignment_failure is not None:
        report.section_failures.append(health_alignment_failure)

    actionable_section_failures = [
        failure
        for failure in report.section_failures
        if not _is_advisory_risk_quality_failure(failure)
    ]
    report.passed = not report.global_failures and not actionable_section_failures
    return report


_GOOD_ENOUGH_RETRY_THRESHOLD = 8


def _is_soft_pass(
    validation: SummaryValidationReport,
    *,
    section_budgets: Dict[str, int],
    target_words: int,
) -> bool:
    """Return True if all remaining failures are 'soft' — within 2x tolerance.

    After enough retries, the post-processor should accept a summary that is
    very close to passing rather than burning more retries and risking timeout.
    """
    if validation.passed:
        return True
    if validation.global_failures:
        return False
    for failure in validation.section_failures:
        if _is_advisory_risk_quality_failure(failure):
            continue
        code = str(failure.code or "")
        # Hard failures that must never be accepted
        if code in {
            "missing_section", "repetition", "repeated_leadin", "repeated_clause_family", "too_few_sentences",
            "cross_section_dollars",
        }:
            return False
        if code in {
            "placeholder_number_artifact",
            "stance_outside_closing",
            "conflicting_stance",
            "health_closing_misalignment",
        }:
            return False
        # Risk schema: allow soft-pass when risk count is off by exactly 1
        if code == "risk_schema":
            risk_budget = int(section_budgets.get("Risk Factors", 0) or 0)
            expected = risk_budget_target_count(risk_budget)
            if abs(validation.risk_count - expected) > 1:
                return False
            continue
        if code == "risk_specificity":
            return False
        # Incoherent endings: always hard-fail — garbled closers must be regenerated
        if code == "incoherent_ending":
            return False
        # Filler phrases: accept 1-2 hits, hard-fail on 3+
        if code == "filler_phrases":
            filler_count = len(getattr(failure, "details", None) or [failure])
            if filler_count > 2:
                return False
            continue
        # Analyst fog: accept 1-2 fog hits after retries, hard-fail on 3+
        if code == "analyst_fog":
            fog_count = len(
                validation.repetition_report.analyst_fog_phrases
                if validation.repetition_report
                else []
            )
            if fog_count > 2:
                return False
            continue
        # Boilerplate quotes: never accept — always hard-fail
        if code == "boilerplate_quotes":
            return False
        # Weak exec opening: never accept — must fix
        if code == "weak_exec_opening":
            return False
        # Casual first-person: never accept — must fix
        if code == "casual_first_person":
            return False
        # Aha insight: advisory only — never blocks pass or triggers regen
        if code == "missing_aha_insight":
            continue
        # Section balance: accept if within multiplied tolerance.
        # Risk Factors naturally seesaws with Closing Takeaway and is most
        # affected by thin filings — use 4x tolerance.  Closing uses 3x.
        if code in {"section_budget_under", "section_budget_over"}:
            budget = int(failure.budget_words or 0)
            actual = int(failure.actual_words or 0)
            if budget <= 0:
                return False
            tolerance = section_budget_tolerance_words(failure.section_name, budget)
            multiplier = 4 if failure.section_name == "Risk Factors" else (3 if failure.section_name == "Closing Takeaway" else 2)
            if abs(actual - budget) > tolerance * multiplier:
                return False
            continue
        # Minor formatting issues: acceptable in soft pass
        if code in {
            "missing_terminal_punctuation",
            "dangling_ending",
            "insufficient_filing_grounding",
        }:
            continue
        if code == "insufficient_management_voice":
            return False
        return False
    return True


def _inject_soft_quality_failures(validation: SummaryValidationReport, text: str) -> None:
    """Inject filler and incoherent-ending failures into section_failures.

    These are soft quality signals — they trigger regen attempts in the
    post-processing loop but are excluded from ``validate_summary`` so
    they don't block ``passed`` for callers that just want structural
    validation.
    """
    rep = validation.repetition_report
    if rep.filler_phrases:
        sections_map = _extract_sections(text)
        filler_affected: set[str] = set()
        for phrase in rep.filler_phrases:
            lower_phrase = phrase.lower()
            for sec_name, sec_body in sections_map.items():
                if lower_phrase in (sec_body or "").lower():
                    filler_affected.add(sec_name)
        for section_name in filler_affected:
            if not any(
                f.section_name == section_name and f.code == "filler_phrases"
                for f in validation.section_failures
            ):
                validation.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="filler_phrases",
                        message=(
                            f"{section_name} contains filler or padding phrases. "
                            "Replace with specific, evidence-grounded conclusions."
                        ),
                        severity=2.0,
                    )
                )
    if rep.analyst_fog_phrases:
        fog_sections_map = _extract_sections(text)
        fog_affected: set = set()
        for phrase in rep.analyst_fog_phrases:
            lower_phrase = phrase.lower()
            for sec_name, sec_body in fog_sections_map.items():
                if lower_phrase in (sec_body or "").lower():
                    fog_affected.add(sec_name)
        for section_name in fog_affected:
            if not any(
                f.section_name == section_name and f.code == "analyst_fog"
                for f in validation.section_failures
            ):
                validation.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="analyst_fog",
                        message=(
                            f"{section_name} contains analyst fog jargon. "
                            "Replace with plain-English explanations of specific "
                            "business dynamics."
                        ),
                        severity=1.9,
                    )
                )
    if rep.boilerplate_quotes:
        bp_sections_map = _extract_sections(text)
        bp_affected: set = set()
        for quote in rep.boilerplate_quotes:
            lower_quote = quote.lower()[:60]
            for sec_name, sec_body in bp_sections_map.items():
                if lower_quote in (sec_body or "").lower():
                    bp_affected.add(sec_name)
        for section_name in bp_affected:
            if not any(
                f.section_name == section_name and f.code == "boilerplate_quotes"
                for f in validation.section_failures
            ):
                validation.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="boilerplate_quotes",
                        message=(
                            f"{section_name} contains boilerplate legal/accounting "
                            "quotes. Replace with high-signal management quotes "
                            "about strategy, risk acknowledgment, or competitive "
                            "positioning."
                        ),
                        severity=1.8,
                    )
                )
    if rep.incoherent_endings:
        for ending_desc in rep.incoherent_endings:
            section_name = ending_desc.split(": ", 1)[0] if ": " in ending_desc else ""
            if section_name:
                validation.section_failures.append(
                    SectionValidationFailure(
                        section_name=section_name,
                        code="incoherent_ending",
                        message=(
                            f"{section_name} ends with an incoherent fragment. "
                            "End with a complete, evidence-grounded sentence."
                        ),
                        severity=2.2,
                    )
                )
    # Aha insight — soft signal for regen only (does not block passed)
    sections_map = _extract_sections(text)
    exec_body = (sections_map.get("Executive Summary") or "").strip()
    if exec_body and count_words(exec_body) >= 60:
        if not _AHA_SIGNAL_RE.search(exec_body):
            if not any(
                f.section_name == "Executive Summary" and f.code == "missing_aha_insight"
                for f in validation.section_failures
            ):
                validation.section_failures.append(
                    SectionValidationFailure(
                        section_name="Executive Summary",
                        code="missing_aha_insight",
                        message=(
                            "Executive Summary lacks a non-obvious insight or reframe. "
                            "Include at least one observation structured as: "
                            "'Most investors would conclude X, but this filing shows Y because Z.'"
                        ),
                        severity=1.6,
                    )
                )


_ARROW_ROW_RE = re.compile(
    r"^\s*→\s*(?P<name>[^:]+?)\s*:\s*(?P<value>\S[\s\S]{0,80}?)\s*$",
    re.MULTILINE,
)
_GENERIC_PROSE_RE = re.compile(
    r"\b(was|were|increased|decreased|grew|declined|strong|weak|because|due to|demand|customers?|performed|good sign|well during|efficient)\b",
    re.IGNORECASE,
)
_GRID_MARKER_RE = re.compile(
    r"^(DATA_GRID_START|DATA_GRID_END|What Matters:|- )",
)


def _is_key_metrics_prose(body: str) -> bool:
    """Return True if Key Metrics body looks like prose instead of structured data."""
    if not body:
        return False
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return False

    arrow_lines = [l for l in lines if l.startswith("→")]
    prose_line_count = 0
    structural_line_count = 0

    for line in lines:
        if line.startswith("→"):
            continue
        if _GRID_MARKER_RE.match(line):
            structural_line_count += 1
        elif _GENERIC_PROSE_RE.search(line) and len(line.split()) > 5:
            prose_line_count += 1

    # Pure structural lines (markers + bullets) with arrow data = fine
    non_arrow_non_structural = len(lines) - len(arrow_lines) - structural_line_count

    if prose_line_count >= 1 and len(arrow_lines) < 3:
        return True
    if prose_line_count > max(0, len(lines) // 3):
        return True
    return False


def _repair_key_metrics(text: str, metrics_lines: str) -> str:
    """Repair Key Metrics section if it contains prose instead of structured data.

    Strategy:
    1. Extract any valid → rows from the existing body
    2. Parse numeric values from remaining lines
    3. Prepend any pre-formatted metrics_lines as fallback data rows
    4. Replace the section body with the repaired version
    """
    body = _extract_section_body(text, "Key Metrics")
    if not body or not _is_key_metrics_prose(body):
        return text

    # Keep valid arrow rows
    existing_arrow = _ARROW_ROW_RE.findall(body)
    kept_rows = [f"→ {name}: {value}" for name, value in existing_arrow if name and value]

    # Extract any "What Matters:" lines
    what_matters_lines: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) < 120:
            what_matters_lines.append(stripped)
            if len(what_matters_lines) >= 4:
                break

    # Pre-formatted metrics_lines as fallback rows
    fallback_rows: List[str] = []
    if metrics_lines:
        for line in metrics_lines.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and ":" in stripped:
                # Convert "- MetricName: value" to "→ MetricName: value"
                parts = stripped[2:].split(":", 1)
                if len(parts) == 2 and parts[1].strip():
                    fallback_rows.append(f"→ {parts[0].strip()}: {parts[1].strip()}")

    # Merge: kept rows first, then fallback rows (avoid duplicates by name)
    seen_names: set = set()
    all_rows: List[str] = []
    for row in kept_rows + fallback_rows:
        match = _ARROW_ROW_RE.match(row)
        if match:
            name = (match.group("name") or "").strip().lower()
            if name not in seen_names:
                seen_names.add(name)
                all_rows.append(row)
        elif row not in [r.lower() for r in kept_rows]:
            all_rows.append(row)

    if not all_rows:
        return text  # nothing to repair with

    header_section = ""
    if what_matters_lines:
        header_lines = [l for l in what_matters_lines if len(l.split()) < 20][:4]
        if header_lines:
            header_section = "What Matters:\n" + "\n".join(header_lines) + "\n"

    repaired = f"DATA_GRID_START\n{header_section}" + "\n".join(all_rows) + "\nDATA_GRID_END"
    return _replace_section_body(text, "Key Metrics", repaired)


def post_process_summary(
    text: str,
    *,
    target_words: int,
    section_budgets: Dict[str, int],
    include_health_rating: bool = True,
    risk_factors_excerpt: str = "",
    max_retries: int = 12,
    max_retries_per_section: int = 3,
    regenerate_section_fn: Optional[Callable[..., str]] = None,
    metrics_lines: str = "",
) -> PostProcessResult:
    """Run the validator-driven post-processing pipeline."""
    working_text = str(text or "").strip()
    retries = 0
    section_retry_counts: Dict[str, int] = {}

    # Pre-flight: repair Key Metrics if it contains prose instead of data
    if metrics_lines:
        working_text = _repair_key_metrics(working_text, metrics_lines)

    while retries < int(max_retries):
        validation = validate_summary(
            working_text,
            target_words=target_words,
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            risk_factors_excerpt=risk_factors_excerpt,
        )
        # Inject soft quality failures (filler, incoherent endings) for regen
        _inject_soft_quality_failures(validation, working_text)
        if validation.passed or regenerate_section_fn is None:
            break
        # After enough retries, accept "good enough" summaries to prevent timeout
        if retries >= _GOOD_ENOUGH_RETRY_THRESHOLD and _is_soft_pass(
            validation,
            section_budgets=section_budgets,
            target_words=target_words,
        ):
            break

        # Hard failures (risk_schema, risk_specificity) block soft-pass entirely,
        # so give those sections extra retry budget before exhaustion.
        _hard_failure_sections = {
            f.section_name
            for f in validation.section_failures
            if f.code in {"risk_schema", "risk_specificity"}
        }
        target_failure = _select_regeneration_target(
            validation,
            text=working_text,
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            exhausted_sections={
                section_name
                for section_name, count in section_retry_counts.items()
                if int(count or 0) >= (
                    int(max_retries_per_section) + 2
                    if section_name in _hard_failure_sections
                    else int(max_retries_per_section)
                )
            },
        )
        if target_failure is None:
            break
        section_name = target_failure.section_name
        budget = int(section_budgets.get(section_name, 0) or 0)
        if budget <= 0:
            break
        existing_body = _extract_section_body(working_text, section_name)
        prior_section_text = _previous_section_text(
            working_text, section_name, include_health_rating
        )
        section_memory = _collect_section_memory(working_text, include_health_rating)
        try:
            new_body = regenerate_section_fn(
                section_name=section_name,
                budget=budget,
                failure_reason=target_failure.message,
                prior_section_text=prior_section_text,
                existing_section_text=existing_body,
                used_claims=list(section_memory.get("used_claims") or []),
                section_memory=section_memory,
            )
        except TypeError:
            new_body = regenerate_section_fn(section_name, budget)
        if not new_body:
            break
        working_text = _replace_section_body(working_text, section_name, str(new_body))
        section_retry_counts[section_name] = int(section_retry_counts.get(section_name, 0) or 0) + 1
        retries += 1

    if regenerate_section_fn is None and retries == 0:
        final_validation = validate_summary(
            working_text,
            target_words=target_words,
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            risk_factors_excerpt=risk_factors_excerpt,
        )
        _inject_soft_quality_failures(final_validation, working_text)
        violations = list(final_validation.global_failures) + [
            failure.message
            for failure in final_validation.section_failures
            if not _is_advisory_risk_quality_failure(failure)
        ]
        return PostProcessResult(
            text=working_text,
            passed=final_validation.passed,
            violations=violations,
            retries=retries,
            validation_report=final_validation,
        )

    final_validation = validate_summary(
        working_text,
        target_words=target_words,
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        risk_factors_excerpt=risk_factors_excerpt,
    )

    if final_validation.total_words > final_validation.upper_bound:
        closing_section = _last_present_section(working_text, include_health_rating)
        if (
            closing_section
            and regenerate_section_fn is not None
            and retries < int(max_retries)
        ):
            closing_budget = int(section_budgets.get(closing_section, 0) or 0)
            if closing_budget > 0:
                try:
                    rewritten = regenerate_section_fn(
                        section_name=closing_section,
                        budget=closing_budget,
                        failure_reason="Trim this final section to fit the remaining memo budget without padding or dangling endings.",
                        prior_section_text=_previous_section_text(
                            working_text, closing_section, include_health_rating
                        ),
                        existing_section_text=_extract_section_body(working_text, closing_section),
                        used_claims=list(
                            _collect_section_memory(
                                working_text, include_health_rating
                            ).get("used_claims")
                            or []
                        ),
                        section_memory=_collect_section_memory(
                            working_text, include_health_rating
                        ),
                    )
                except TypeError:
                    rewritten = regenerate_section_fn(closing_section, closing_budget)
                if rewritten:
                    working_text = _replace_section_body(working_text, closing_section, str(rewritten))
                    section_retry_counts[closing_section] = int(
                        section_retry_counts.get(closing_section, 0) or 0
                    ) + 1
                    retries += 1

    # Strip duplicate sentences before final trim
    working_text = strip_repeated_sentences(working_text)

    # Punctuation cleanup — fix LLM punctuation artifacts
    working_text = _repair_punctuation(working_text)

    # Seal each section at a sentence boundary before global trim
    for _sec_name, _sec_body in _extract_sections(working_text).items():
        if _sec_name == "Key Metrics":
            continue
        if _sec_body and not _sec_body.rstrip().endswith((".", "!", "?")):
            # Trim back to last sentence boundary
            _last_boundary = max(
                _sec_body.rfind("."),
                _sec_body.rfind("!"),
                _sec_body.rfind("?"),
            )
            if _last_boundary > 0:
                _sealed = _sec_body[: _last_boundary + 1]
            else:
                _sealed = _sec_body.rstrip() + "."
            working_text = _replace_section_body(working_text, _sec_name, _sealed)

    working_text = clean_ending(
        working_text,
        target_words,
        tolerance=_validation_total_tolerance_words(target_words),
    )
    final_validation = validate_summary(
        working_text,
        target_words=target_words,
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        risk_factors_excerpt=risk_factors_excerpt,
    )

    violations = list(final_validation.global_failures) + [
        failure.message
        for failure in final_validation.section_failures
        if not _is_advisory_risk_quality_failure(failure)
    ]
    return PostProcessResult(
        text=working_text,
        passed=final_validation.passed,
        violations=violations,
        retries=retries,
        validation_report=final_validation,
    )


def _select_regeneration_target(
    validation: SummaryValidationReport,
    *,
    text: str,
    section_budgets: Dict[str, int],
    include_health_rating: bool,
    exhausted_sections: Optional[set[str]] = None,
) -> Optional[SectionValidationFailure]:
    exhausted = exhausted_sections or set()
    section_failures = [
        failure
        for failure in validation.section_failures
        if (
            failure.section_name not in exhausted
            and not _is_advisory_risk_quality_failure(failure)
            and getattr(failure, "code", "") != "missing_aha_insight"
        )
    ]
    priorities = {
        "placeholder_number_artifact": 0,
        "risk_schema": 0,
        "risk_specificity": 0,
        "conflicting_stance": 0,
        "stance_outside_closing": 0,
        "weak_exec_opening": 0,
        "casual_first_person": 0,
        "health_closing_misalignment": 1,
        "insufficient_management_voice": 1,
        "insufficient_filing_grounding": 1,
        "repetition": 1,
        "repeated_leadin": 1,
        "repeated_clause_family": 1,
        "cross_section_dollars": 1,
        "incoherent_ending": 1,
        "boilerplate_quotes": 1,
        "analyst_fog": 2,
        "filler_phrases": 2,
        "section_budget_under": 3,
        "section_budget_over": 4,
        "dangling_ending": 5,
        "missing_terminal_punctuation": 6,
        "too_few_sentences": 7,
        "missing_section": 8,
        "risk_quality": 9,
    }
    if section_failures:
        canonical_order = {
            section_name: index
            for index, section_name in enumerate(CANONICAL_SECTION_ORDER)
        }

        def _failure_severity(failure: SectionValidationFailure) -> float:
            if float(failure.severity or 0.0) > 0.0:
                return float(failure.severity)
            if failure.code in {"section_budget_under", "section_budget_over"}:
                return abs(int(failure.actual_words or 0) - int(failure.budget_words or 0)) / max(
                    1, int(failure.budget_words or 0)
                )
            if failure.code == "risk_schema":
                return 3.5 if failure.section_name == "Risk Factors" else 3.0
            if failure.code == "risk_specificity":
                return 3.4 if failure.section_name == "Risk Factors" else 2.9
            if failure.code == "placeholder_number_artifact":
                return 3.4
            if failure.code == "conflicting_stance":
                return 3.3
            if failure.code == "stance_outside_closing":
                return 3.1
            if failure.code == "health_closing_misalignment":
                return 2.9
            if failure.code == "repetition":
                return 2.5
            if failure.code == "repeated_leadin":
                return 2.7
            if failure.code == "repeated_clause_family":
                return 2.6
            if failure.code == "cross_section_dollars":
                return 2.3
            if failure.code == "incoherent_ending":
                return 2.2
            if failure.code == "filler_phrases":
                return 2.0
            if failure.code == "missing_section":
                return 1.5
            if failure.code == "too_few_sentences":
                return 1.2
            if failure.code == "dangling_ending":
                return 1.1
            if failure.code == "missing_terminal_punctuation":
                return 1.0
            return 0.5

        top_failure = sorted(
            section_failures,
            key=lambda failure: (
                priorities.get(failure.code, 99),
                -_failure_severity(failure),
                canonical_order.get(failure.section_name, 99),
            ),
        )[0]

        # Enrich risk regeneration with budget-awareness hint so the
        # regenerator stays within its budget and avoids cascade shifts.
        if top_failure.code in {"risk_schema", "risk_specificity", "risk_quality"}:
            budget_hints = []
            for other in section_failures:
                if other.code in {"section_budget_under", "section_budget_over"}:
                    direction = "under" if other.code == "section_budget_under" else "over"
                    budget_hints.append(
                        f"{other.section_name} is {direction}weight "
                        f"({other.actual_words} vs target {other.budget_words})"
                    )
            if budget_hints:
                enriched_message = (
                    top_failure.message
                    + "\n\nBALANCE CONTEXT: Other sections also need adjustment: "
                    + "; ".join(budget_hints[:3])
                    + ". Keep the risk section within its budget to avoid cascading imbalances."
                )
                return SectionValidationFailure(
                    section_name=top_failure.section_name,
                    code=top_failure.code,
                    message=enriched_message,
                    actual_words=top_failure.actual_words,
                    budget_words=top_failure.budget_words,
                    severity=top_failure.severity,
                )

        # When 3+ sections are underweight simultaneously, the LLM is
        # systematically undershooting.  Enrich the failure message so the
        # regenerator aims slightly above budget to compensate.
        underweight_count = sum(
            1 for f in section_failures if f.code == "section_budget_under"
        )
        if (
            underweight_count >= 3
            and top_failure.code == "section_budget_under"
        ):
            enriched_message = (
                top_failure.message
                + f"\n\nSYSTEMATIC UNDERSHOOT: {underweight_count} sections are below budget. "
                "Aim for the upper half of the budget band to compensate."
            )
            return SectionValidationFailure(
                section_name=top_failure.section_name,
                code=top_failure.code,
                message=enriched_message,
                actual_words=top_failure.actual_words,
                budget_words=top_failure.budget_words,
                severity=top_failure.severity,
            )

        return top_failure

    if validation.total_words < validation.lower_bound:
        fallback_section = _last_present_section(
            text,
            include_health_rating=include_health_rating,
            exhausted_sections=exhausted,
        )
        if fallback_section and int(section_budgets.get(fallback_section, 0) or 0) > 0:
            return SectionValidationFailure(
                section_name=fallback_section,
                code="global_under_target",
                message=(
                    "Memo is below the allowed total word band. Expand this section with new, "
                    "filing-grounded analysis while staying within its local budget range."
                ),
            )

    if validation.total_words > validation.upper_bound:
        fallback_section = _last_present_section(
            text,
            include_health_rating=include_health_rating,
            exhausted_sections=exhausted,
        )
        if fallback_section and int(section_budgets.get(fallback_section, 0) or 0) > 0:
            return SectionValidationFailure(
                section_name=fallback_section,
                code="global_over_target",
                message=(
                    "Memo is above the allowed total word band. Compress this section without "
                    "dropping required insight or ending mid-sentence."
                ),
            )

    return None


def _last_present_section(
    text: str,
    include_health_rating: bool,
    exhausted_sections: Optional[set[str]] = None,
) -> str:
    exhausted = exhausted_sections or set()
    sections = _extract_sections(text)
    for section_name in reversed(_required_sections(include_health_rating)):
        if section_name == "Key Metrics":
            continue
        if section_name in exhausted:
            continue
        if sections.get(section_name):
            return section_name
    return ""
