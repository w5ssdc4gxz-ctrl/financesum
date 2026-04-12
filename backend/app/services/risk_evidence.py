"""Shared helpers for strict, source-backed risk extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RiskEvidenceCandidate:
    """A source-backed risk candidate.

    The candidate is only acceptable if it is grounded in filing language,
    names a specific exposure, and avoids metric-led or boilerplate framing.
    """

    risk_name: str
    source_section: str
    source_quote: str
    source_anchor_terms: Tuple[str, ...] = ()
    mechanism_seed: str = ""
    early_warning_seed: str = ""


@dataclass(frozen=True)
class RiskOverlapAssessment:
    """Semantic overlap result for two risk entries."""

    exact_name_match: bool
    names_overlap: bool
    bodies_overlap: bool
    shared_name_tokens: Tuple[str, ...] = ()
    name_jaccard: float = 0.0
    body_jaccard: float = 0.0


_GENERIC_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "asset",
        "assets",
        "be",
        "business",
        "cash",
        "capital",
        "company",
        "customer",
        "customers",
        "conversion",
        "cost",
        "costs",
        "demand",
        "delivery",
        "deployment",
        "earnings",
        "execution",
        "flow",
        "funding",
        "growth",
        "infrastructure",
        "liquidity",
        "margin",
        "margins",
        "mix",
        "model",
        "operating",
        "pressure",
        "pricing",
        "profit",
        "profitability",
        "rate",
        "risk",
        "service",
        "the",
        "to",
        "utilization",
        "risk factors",
    }
)

_GENERIC_ANCHOR_HEADER_TOKENS = frozenset(
    {
        "about",
        "disclosure",
        "disclosures",
        "factor",
        "factors",
        "forward",
        "harbor",
        "item",
        "market",
        "part",
        "qualitative",
        "quantitative",
        "safe",
        "statement",
        "statements",
    }
)

_METRIC_WORDS = frozenset(
    {
        "cash",
        "capital",
        "conversion",
        "cost",
        "debt",
        "deployment",
        "earnings",
        "equity",
        "fcf",
        "funding",
        "growth",
        "income",
        "liquidity",
        "margin",
        "margins",
        "metric",
        "metrics",
        "operating",
        "profit",
        "profitability",
        "revenue",
        "risk",
        "returns",
        "serve",
        "valuation",
    }
)

_RISK_OVERLAP_STOPWORDS = frozenset(
    set(_GENERIC_STOPWORDS)
    | set(_METRIC_WORDS)
    | {
        "about",
        "actual",
        "affect",
        "after",
        "against",
        "also",
        "any",
        "before",
        "being",
        "between",
        "break",
        "business",
        "cause",
        "causes",
        "change",
        "changes",
        "commentary",
        "compliance",
        "completely",
        "concrete",
        "different",
        "driver",
        "drivers",
        "early",
        "develops",
        "enforcement",
        "event",
        "exposure",
        "factors",
        "financial",
        "filing",
        "free",
        "generic",
        "grounding",
        "hits",
        "how",
        "impact",
        "impacts",
        "indicator",
        "indicators",
        "investigation",
        "issue",
        "issues",
        "legal",
        "makes",
        "management",
        "mechanism",
        "mechanisms",
        "more",
        "named",
        "only",
        "other",
        "over",
        "overlap",
        "path",
        "pathway",
        "pathways",
        "previous",
        "rather",
        "regulation",
        "regulations",
        "regulatory",
        "relative",
        "reset",
        "remedies",
        "remedy",
        "related",
        "same",
        "scenario",
        "scenarios",
        "scrutiny",
        "section",
        "shared",
        "should",
        "showing",
        "signal",
        "signals",
        "specific",
        "spend",
        "still",
        "starts",
        "symptom",
        "that",
        "terms",
        "than",
        "these",
        "those",
        "through",
        "timeline",
        "true",
        "under",
        "unique",
        "warning",
        "warnings",
        "watch",
        "what",
        "when",
        "which",
        "while",
        "would",
        "warns",
        "weaken",
        "whether",
        "bookings",
        "pacing",
        "shipments",
    }
)

_NUMERIC_LED_RE = re.compile(r"^\s*(?:[-–—]?\s*)?(?:\$?\d|\d+(?:[.,]\d+)?(?:%|x|x)?\b)", re.IGNORECASE)
_BOILERPLATE_RE = re.compile(
    r"("
    r"pricing,?\s*demand,?\s*or cost-to-serve pressure can flow into|"
    r"the transmission path runs through weaker unit economics|"
    r"the transmission path runs through reduced flexibility|"
    r"current cash conversion proves more cyclical than durable|"
    r"management should monitor those indicators and adjust execution before pressure|"
    r"the mechanism is that pricing,?\s*demand,?\s*or cost-to-serve"
    r")",
    re.IGNORECASE,
)
_GENERIC_NAME_RE = re.compile(
    r"\b("
    r"margin\s*/?\s*reinvestment risk|cash conversion\s*/?\s*(?:capex )?risk|"
    r"liquidity\s*/?\s*funding risk|reinvestment risk|capex risk|funding risk|"
    r"margin risk|liquidity risk|cash flow risk|execution risk|demand risk|growth risk|"
    r"unit[- ]economics reset risk|infrastructure utilization risk|"
    r"capital allocation constraint risk|operating model \w+ risk|"
    r"revenue (?:concentration|mix|diversification) risk|"
    r"margin durability risk|cash conversion sustainability risk|"
    r"balance sheet flexibility risk|operating leverage risk|"
    r"pricing pressure risk|cybersecurity risk|regulatory risk|compliance risk"
    r")\b",
    re.IGNORECASE,
)

# Filing structure / table-of-contents fragments that should never be risk names
_FILING_FRAGMENT_RE = re.compile(
    r"\b("
    r"to risks and|actual (?:results?|execution(?: risk)?)|"
    r"forward[- ]looking|safe harbor|"
    r"table of contents|controls and procedures|part\s+[ivx]+|"
    r"item\s+\d+[a-z]?|quantitative and qualitative|"
    r"management discussion|selected financial|"
    r"legal proceedings|unregistered sales|defaults upon senior|"
    r"exhibits? and financial(?: statement schedules?)?|"
    r"executive compensation|properties|mine safety|market risk disclosures?"
    r")\b",
    re.IGNORECASE,
)

_ANCHOR_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bhyperscale customers?\b", "Hyperscale Customer Spending Risk"),
    (r"\bcustomer concentration\b", "Customer Concentration Risk"),
    (r"\benterprise renewals?\b", "Enterprise Renewal Slippage Risk"),
    (r"\brenewal cohorts?\b", "Renewal Cohort Risk"),
    (r"\bbacklog conversion\b", "Backlog Shipment Conversion Risk"),
    (r"\bshipment timing\b", "Shipment Timing Risk"),
    (r"\bexport controls?\b", "Export Controls / Shipment Risk"),
    (r"\bpower availability\b", "Power Availability Capacity Ramp Risk"),
    (r"\bdata[- ]center(?:s)?\b", "Data-Center Capacity Ramp Risk"),
    (r"\bmerchant mix\b", "Merchant Mix Risk"),
    (r"\btake rate\b", "Take-Rate / Merchant Mix Risk"),
    (r"\bpayment volume\b", "Payment Volume Monetization Risk"),
    (r"\bcharge[- ]offs?\b", "Charge-Off / Reserve Risk"),
    (r"\bcredit quality\b", "Credit Quality Risk"),
    (r"\bdeposit mix\b", "Deposit Mix Risk"),
    (r"\bworking capital\b", "Working-Capital Timing Risk"),
    (r"\blaunch uptake\b", "Launch Uptake Risk"),
    (r"\btrial readouts?\b", "Pipeline Timing Risk"),
    (r"\breimbursement\b", "Reimbursement Risk"),
    (r"\bchurn\b", "Churn Risk"),
    (r"\binstalled base\b", "Installed-Base Service Risk"),
    (r"\butilization\b", "Utilization Risk"),
    (r"\bpricing realization\b", "Pricing Realization Risk"),
    (r"\bprice realization\b", "Pricing Realization Risk"),
    (r"\btraffic acquisition\b", "Traffic Acquisition Risk"),
    (r"\bsame-store sales\b", "Same-Store Sales Risk"),
    (r"\binventory\b", "Inventory Markdown Risk"),
    (r"\bproduction volumes?\b", "Production Reliability Risk"),
    (r"\bthroughput\b", "Throughput Risk"),
    (r"\bproject execution\b", "Project Execution Risk"),
    (r"\bcontent spend\b", "Content Payback Risk"),
)

_SECTION_PRIORITY = {
    "Risk Factors": 0,
    "Risk": 0,
    "Management Discussion & Analysis": 1,
    "MD&A": 1,
    "Management Expectations": 2,
    "Promise Scorecard": 3,
}

_GENERIC_ANCHOR_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bquantitative and qualitative(?: disclosures about)? market risk\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdisclosures about market risk\b", re.IGNORECASE),
    re.compile(r"\bmarket risk(?: disclosures?)?\b", re.IGNORECASE),
    re.compile(r"\brisk factors?\b", re.IGNORECASE),
    re.compile(r"\bforward[- ]looking statements?\b", re.IGNORECASE),
    re.compile(r"\bsafe harbor\b", re.IGNORECASE),
)

_RISK_SENTENCE_RE = re.compile(
    r"\b("
    r"risk|risks|warn|warning|could|may|might|uncertain|uncertainty|"
    r"pressure|constraint|delay|delays|delayed|slip|slips|slippage|"
    r"exposure|exposed|trigger|violate|breach|restrict|restriction|"
    r"shortage|default|delinquen|attrition|churn|refinancing|"
    r"investigation|enforcement|sanction|license|licensing|"
    r"outpace|outpaced|weaken|weakens|erode|erodes|reduce|reduces|"
    r"slow|slows|soften|softens|adverse|adversely|materially"
    r")\b",
    re.IGNORECASE,
)
_RISK_TRIGGER_RE = re.compile(
    r"\b("
    r"if|when|unless|over the next|within the next|next\s+(?:quarter|two quarters|year|12 months)|"
    r"watch|trigger|threshold|backlog|bookings|shipment|renewal|churn|utilization|capacity|"
    r"approval|launch|pricing|working capital|refinancing|license|licensing|remedy|"
    r"power availability|deployment pacing|lead times?"
    r")\b",
    re.IGNORECASE,
)
_RISK_TRANSMISSION_RE = re.compile(
    r"\b("
    r"revenue|pricing|volume|mix|margin|margins|gross margin|operating margin|"
    r"cash flow|free cash flow|cash conversion|liquidity|working capital|"
    r"balance sheet|debt|refinancing|capex|opex|bookings|backlog|payback|"
    r"utilization|returns?"
    r")\b",
    re.IGNORECASE,
)
_RISK_MATERIALITY_RE = re.compile(
    r"\b(material|materially|significant|substantial|major|key|critical|meaningful)\b",
    re.IGNORECASE,
)
_LOW_SIGNAL_RISK_RE = re.compile(
    r"\b("
    r"general economic|macroeconomic|geopolitical|competition(?: from)?|competitive pressure|"
    r"cybersecurity|cyber threats?|climate change|weather events?|foreign currency|"
    r"interest rates?|key personnel|regulatory environment|compliance with laws"
    r")\b",
    re.IGNORECASE,
)


def normalize_risk_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _risk_overlap_name_tokens(value: str) -> Tuple[str, ...]:
    normalized = normalize_risk_name(value)
    if not normalized:
        return ()
    tokens = [
        token
        for token in normalized.split()
        if len(token) > 2 and token not in _RISK_OVERLAP_STOPWORDS
    ]
    return tuple(dict.fromkeys(tokens))


def _risk_overlap_body_tokens(value: str) -> Tuple[str, ...]:
    tokens = [
        token
        for token in re.findall(r"[a-z]{4,}", str(value or "").lower())
        if token not in _RISK_OVERLAP_STOPWORDS
    ]
    return tuple(dict.fromkeys(tokens))


def assess_risk_overlap(
    *,
    risk_name: str,
    risk_body: str,
    other_risk_name: str,
    other_risk_body: str,
) -> RiskOverlapAssessment:
    """Return whether two risks are semantically too close to coexist."""

    normalized_name = normalize_risk_name(risk_name)
    normalized_other_name = normalize_risk_name(other_risk_name)
    exact_name_match = bool(normalized_name) and normalized_name == normalized_other_name

    name_tokens = set(_risk_overlap_name_tokens(risk_name))
    other_name_tokens = set(_risk_overlap_name_tokens(other_risk_name))
    shared_name_tokens = tuple(sorted(name_tokens & other_name_tokens))
    name_union = name_tokens | other_name_tokens
    name_jaccard = (
        float(len(shared_name_tokens)) / float(len(name_union))
        if name_union
        else 0.0
    )
    names_overlap = bool(
        exact_name_match
        or (
            len(shared_name_tokens) >= 2
            and name_union
            and name_jaccard >= 0.55
        )
    )

    body_tokens = set(_risk_overlap_body_tokens(risk_body))
    other_body_tokens = set(_risk_overlap_body_tokens(other_risk_body))
    body_union = body_tokens | other_body_tokens
    body_jaccard = (
        float(len(body_tokens & other_body_tokens)) / float(len(body_union))
        if body_union
        else 0.0
    )
    bodies_overlap = bool(body_union and body_jaccard >= 0.75)

    return RiskOverlapAssessment(
        exact_name_match=exact_name_match,
        names_overlap=names_overlap,
        bodies_overlap=bodies_overlap,
        shared_name_tokens=shared_name_tokens,
        name_jaccard=name_jaccard,
        body_jaccard=body_jaccard,
    )


def _split_sentences(text: str) -> List[str]:
    blob = " ".join(str(text or "").split()).strip()
    if not blob:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", blob) if s.strip()]


def _normalize_phrase_key(value: str) -> str:
    return normalize_risk_name(value)


def _is_generic_anchor(anchor: str) -> bool:
    normalized = _normalize_phrase_key(anchor)
    if not normalized:
        return True
    if any(pattern.search(normalized) for pattern in _GENERIC_ANCHOR_PATTERNS):
        return True
    if normalized in _GENERIC_STOPWORDS:
        return True
    tokens = normalized.split()
    header_tokens = [
        token for token in tokens if token in _GENERIC_ANCHOR_HEADER_TOKENS
    ]
    if len(tokens) >= 2 and len(header_tokens) >= max(2, len(tokens) - 1):
        return True
    return all(
        token in _GENERIC_STOPWORDS or token in _GENERIC_ANCHOR_HEADER_TOKENS
        for token in tokens
    )


def looks_like_risk_sentence(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    if _RISK_SENTENCE_RE.search(lowered):
        return True
    return any(
        fragment in lowered
        for fragment in (
            "materially affect",
            "adversely affect",
            "subject to",
            "depends on",
            "failure to",
        )
    )


def is_metric_only_risk_name(name: str) -> bool:
    words = re.findall(r"[a-z]+", str(name or "").lower())
    if len(words) < 3:
        return False
    return all(word in _METRIC_WORDS or word in _GENERIC_STOPWORDS for word in words)


def is_generic_risk_name(name: str) -> bool:
    return bool(_GENERIC_NAME_RE.search(str(name or "")))


def _matches_anchor_pattern(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    return any(
        re.search(pattern, lowered, re.IGNORECASE) for pattern, _name in _ANCHOR_PATTERNS
    )


def is_filing_structure_line(text: str) -> bool:
    """Return True when a line looks like filing structure, TOC, or header debris."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return False
    if _matches_anchor_pattern(cleaned):
        return False
    if re.match(r"^\d{1,4}$", cleaned):
        return True
    if re.match(r"^conversion\s+risk$", cleaned, re.IGNORECASE):
        return True
    if re.match(r"^item\s+\d+[a-z]?\.?\s*(?:[-:–—]\s*)?.*$", cleaned, re.IGNORECASE):
        return True
    if re.match(r"^[A-Z][A-Z0-9 &/().,-]{2,}\s+\d{1,4}\s*$", cleaned):
        return True

    alpha_tokens = re.findall(r"[a-z]+", cleaned.lower())
    if cleaned.isupper() and len(cleaned) <= 80 and len(alpha_tokens) <= 8:
        return True
    if len(alpha_tokens) <= 3 and (cleaned.isupper() or cleaned.endswith(":")):
        return True
    if _FILING_FRAGMENT_RE.search(cleaned) and len(alpha_tokens) <= 8 and not re.search(
        r"\b(could|may|might|would|can|will|if|because|delay|delays|pressure|affect|adversely|materially|increase|decrease|slow|slip|result)\b",
        cleaned,
        re.IGNORECASE,
    ):
        return True
    return False


def is_filing_fragment_risk_name(name: str) -> bool:
    """Reject risk names that are filing structure / TOC fragments."""
    cleaned = " ".join(str(name or "").split()).strip()
    if not cleaned:
        return True
    if is_filing_structure_line(cleaned):
        return True

    lowered = cleaned.lower()
    if re.match(r"^(?:conversion|actual\s+\w+)\s+risk$", lowered, re.IGNORECASE):
        return True
    if re.match(r"^(?:actual\s+results?|actual\s+execution)$", lowered, re.IGNORECASE):
        return True

    if not _matches_anchor_pattern(cleaned):
        significant_words = [
            token
            for token in re.findall(r"[a-z]+", lowered)
            if token not in _GENERIC_STOPWORDS and token != "risk"
        ]
        if len(significant_words) < 2 and len(re.findall(r"[a-z]+", lowered)) <= 3:
            return True
    return False


def is_fragment_quote(text: str) -> bool:
    """Reject source quotes that are too short or are filing structure fragments."""
    cleaned = " ".join(str(text or "").split()).strip()
    if len(cleaned.split()) < 4:
        return True
    return bool(_FILING_FRAGMENT_RE.search(cleaned)) or is_filing_structure_line(cleaned)


def looks_numeric_led(text: str) -> bool:
    return bool(_NUMERIC_LED_RE.search(str(text or "")))


def looks_boilerplate_risk_body(text: str) -> bool:
    return bool(_BOILERPLATE_RE.search(str(text or "")))


def score_risk_evidence_candidate(
    candidate: RiskEvidenceCandidate,
    *,
    company_terms: Sequence[str] = (),
) -> int:
    """Rank risk candidates by current-period materiality and credible trigger density."""

    evidence_blob = " ".join(
        part
        for part in (
            str(candidate.source_quote or "").strip(),
            str(candidate.mechanism_seed or "").strip(),
            str(candidate.early_warning_seed or "").strip(),
        )
        if part
    )
    source_section = str(candidate.source_section or "").strip()
    non_generic_anchors = [
        term
        for term in (candidate.source_anchor_terms or ())
        if str(term or "").strip() and not _is_generic_anchor(str(term))
    ]
    if not non_generic_anchors:
        non_generic_anchors = [
            term
            for term in extract_anchor_terms(
                " ".join(
                    part
                    for part in (
                        str(candidate.risk_name or "").strip(),
                        evidence_blob,
                    )
                    if part
                ),
                company_terms=company_terms,
                limit=6,
            )
            if str(term or "").strip() and not _is_generic_anchor(str(term))
        ]

    score = 0
    score += max(0, 10 - int(_SECTION_PRIORITY.get(source_section, 5)))
    score += min(8, len(non_generic_anchors) * 2)
    score += min(6, len(evidence_blob.split()) // 10)
    if source_section in {"Risk Factors", "Risk"}:
        score += 4
    elif source_section in {"Management Discussion & Analysis", "MD&A"}:
        score += 2
    if _RISK_TRIGGER_RE.search(evidence_blob):
        score += 4
    if _RISK_TRANSMISSION_RE.search(evidence_blob):
        score += 3
    if _RISK_MATERIALITY_RE.search(evidence_blob):
        score += 2
    if _LOW_SIGNAL_RISK_RE.search(
        " ".join(
            part
            for part in (
                str(candidate.risk_name or "").strip(),
                evidence_blob,
            )
            if part
        )
    ) and len(non_generic_anchors) <= 1:
        score -= 7
    if looks_numeric_led(candidate.source_quote):
        score -= 3
    if looks_boilerplate_risk_body(evidence_blob):
        score -= 6
    return int(score)


def extract_anchor_terms(
    text: str,
    *,
    company_terms: Sequence[str] = (),
    limit: int = 6,
) -> Tuple[str, ...]:
    lowered = " ".join(str(text or "").lower().split())
    anchors: List[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        cleaned = " ".join(str(raw or "").split()).strip(" ,.;:()[]{}\"'“”")
        normalized = _normalize_phrase_key(cleaned)
        if not normalized or normalized in seen or _is_generic_anchor(cleaned):
            return
        seen.add(normalized)
        anchors.append(cleaned)

    for term in company_terms or []:
        cleaned = str(term or "").strip()
        if cleaned and _normalize_phrase_key(cleaned) in lowered:
            _add(cleaned)

    for pattern, _name in _ANCHOR_PATTERNS:
        for match in re.finditer(pattern, lowered, re.IGNORECASE):
            _add(match.group(0))

    if len(anchors) < limit:
        for match in re.finditer(
            r"\b(?:[A-Z][A-Za-z0-9&/-]+(?:\s+[A-Z][A-Za-z0-9&/-]+){0,3}|[A-Z]{2,6}(?:/[A-Z]{2,6})?)\b",
            str(text or ""),
        ):
            _add(match.group(0))
            if len(anchors) >= limit:
                break

    return tuple(anchors[:limit])


def derive_risk_name(
    *,
    source_quote: str,
    source_anchor_terms: Sequence[str] = (),
    company_terms: Sequence[str] = (),
) -> str:
    lowered = " ".join(str(source_quote or "").lower().split())
    anchor = ""
    for term in source_anchor_terms or ():
        cleaned = str(term or "").strip()
        if cleaned and not _is_generic_anchor(cleaned):
            anchor = cleaned
            break
    if not anchor:
        for term in company_terms or ():
            cleaned = str(term or "").strip()
            if cleaned and not _is_generic_anchor(cleaned) and _normalize_phrase_key(cleaned) in lowered:
                anchor = cleaned
                break

    explicit_patterns: Tuple[Tuple[str, str], ...] = (
        (r"\bhyperscale customers?\b", "Hyperscale Customer Spending Risk"),
        (r"\bcustomer concentration\b", "Customer Concentration Risk"),
        (r"\benterprise renewals?\b", "Enterprise Renewal Slippage Risk"),
        (r"\brenewal cohorts?\b", "Renewal Cohort Risk"),
        (r"\bbacklog conversion\b", "Backlog Shipment Conversion Risk"),
        (r"\bshipment timing\b", "Shipment Timing Risk"),
        (r"\bexport controls?\b", "Export Controls / Shipment Risk"),
        (r"\bpower availability\b", "Power Availability Capacity Ramp Risk"),
        (r"\bdata[- ]center(?:s)?\b", "Data-Center Capacity Ramp Risk"),
        (r"\bmerchant mix\b", "Merchant Mix Risk"),
        (r"\btake rate\b", "Take-Rate / Merchant Mix Risk"),
        (r"\bcharge[- ]offs?\b", "Charge-Off / Reserve Risk"),
        (r"\bcredit quality\b", "Credit Quality Risk"),
        (r"\bdeposit mix\b", "Deposit Mix Risk"),
        (r"\bworking capital\b", "Working-Capital Timing Risk"),
        (r"\blaunch uptake\b", "Launch Uptake Risk"),
        (r"\btrial readouts?\b", "Pipeline Timing Risk"),
        (r"\breimbursement\b", "Reimbursement Risk"),
        (r"\bchurn\b", "Churn Risk"),
        (r"\binstalled base\b", "Installed-Base Service Risk"),
        (r"\bpricing realization\b", "Pricing Realization Risk"),
        (r"\btraffic acquisition\b", "Traffic Acquisition Risk"),
        (r"\bsame-store sales\b", "Same-Store Sales Risk"),
        (r"\binventory\b", "Inventory Markdown Risk"),
        (r"\bproduction volumes?\b", "Production Reliability Risk"),
        (r"\bthroughput\b", "Throughput Risk"),
        (r"\bproject execution\b", "Project Execution Risk"),
        (r"\bcontent spend\b", "Content Payback Risk"),
    )
    for pattern, name in explicit_patterns:
        if re.search(pattern, lowered, re.IGNORECASE):
            return name

    if not anchor:
        return ""

    if any(token in lowered for token in ("pricing", "margin", "cost-to-serve", "cost to serve", "monetization")):
        return f"{anchor} Pricing Pressure Risk"
    if any(token in lowered for token in ("funding", "liquidity", "capital", "cash")):
        return f"{anchor} Funding Risk"
    if any(token in lowered for token in ("renewal", "churn", "retention")):
        return f"{anchor} Renewal Risk"
    if any(token in lowered for token in ("backlog", "shipment", "deliver", "delivery", "conversion")):
        return f"{anchor} Conversion Risk"
    if any(token in lowered for token in ("launch", "trial", "approval", "pipeline", "reimbursement")):
        return f"{anchor} Pipeline Risk"
    if any(token in lowered for token in ("production", "throughput", "utilization", "capacity", "power")):
        return f"{anchor} Capacity Risk"
    if any(token in lowered for token in ("merchant", "payment", "take rate", "volume", "chargeback")):
        return f"{anchor} Monetization Risk"
    if len(anchor.split()) >= 2:
        return f"{anchor} Risk"
    return ""


def build_risk_evidence_candidates(
    source_texts: Mapping[str, str],
    *,
    company_terms: Sequence[str] = (),
    limit: int = 3,
) -> List[RiskEvidenceCandidate]:
    candidates: List[Tuple[int, RiskEvidenceCandidate]] = []
    seen_names: set[str] = set()

    for section_name, text in source_texts.items():
        section = str(section_name or "").strip() or "Risk Factors"
        priority = _SECTION_PRIORITY.get(section, 4)
        for sentence in _split_sentences(text):
            cleaned = " ".join(sentence.split()).strip()
            if len(cleaned.split()) < 5:
                continue
            if not looks_like_risk_sentence(cleaned):
                continue
            anchors = extract_anchor_terms(cleaned, company_terms=company_terms, limit=6)
            if not anchors:
                continue
            risk_name = derive_risk_name(
                source_quote=cleaned,
                source_anchor_terms=anchors,
                company_terms=company_terms,
            )
            if not risk_name:
                continue
            candidate = RiskEvidenceCandidate(
                risk_name=risk_name,
                source_section=section,
                source_quote=cleaned,
                source_anchor_terms=anchors,
                mechanism_seed=cleaned,
                early_warning_seed=cleaned,
            )
            ok, _reason = candidate_is_strictly_acceptable(candidate, company_terms=company_terms)
            if not ok:
                continue
            canon = normalize_risk_name(candidate.risk_name)
            if canon in seen_names:
                continue
            seen_names.add(canon)
            score = score_risk_evidence_candidate(candidate, company_terms=company_terms)
            score += max(0, 4 - min(priority, 4))
            candidates.append((score, candidate))

    candidates.sort(key=lambda item: (-item[0], normalize_risk_name(item[1].risk_name), item[1].source_section.lower()))
    return [candidate for _score, candidate in candidates[: max(0, int(limit))]]


def candidate_is_strictly_acceptable(
    candidate: RiskEvidenceCandidate,
    *,
    company_terms: Sequence[str] = (),
) -> Tuple[bool, str]:
    risk_name = str(candidate.risk_name or "").strip()
    source_quote = str(candidate.source_quote or "").strip()
    source_section = str(candidate.source_section or "").strip()
    source_anchor_terms = tuple(
        term for term in (candidate.source_anchor_terms or ()) if str(term or "").strip()
    )

    if not risk_name:
        return False, "missing risk name"
    if is_generic_risk_name(risk_name):
        return False, f"generic risk name: {risk_name}"
    if is_filing_fragment_risk_name(risk_name):
        return False, f"filing fragment risk name: {risk_name}"
    if is_metric_only_risk_name(risk_name):
        return False, f"metric-only risk name: {risk_name}"
    if not source_quote:
        return False, "missing source quote"
    if is_fragment_quote(source_quote):
        return False, f"fragment source quote: {source_quote}"
    if looks_numeric_led(source_quote) or looks_numeric_led(candidate.mechanism_seed) or looks_numeric_led(candidate.early_warning_seed):
        return False, "numeric-led risk body"
    if looks_boilerplate_risk_body(source_quote) or looks_boilerplate_risk_body(candidate.mechanism_seed) or looks_boilerplate_risk_body(candidate.early_warning_seed):
        return False, "boilerplate risk body"

    combined = " ".join(
        part
        for part in (
            risk_name,
            source_quote,
            candidate.mechanism_seed,
            candidate.early_warning_seed,
            " ".join(source_anchor_terms),
            " ".join(company_terms or []),
        )
        if part
    ).lower()
    specific_anchor = next(
        (term for term in source_anchor_terms if not _is_generic_anchor(term)),
        "",
    )
    if not specific_anchor and company_terms:
        specific_anchor = next(
            (
                term
                for term in company_terms
                if str(term or "").strip()
                and _normalize_phrase_key(term) in combined
                and not _is_generic_anchor(term)
            ),
            "",
        )
    if not specific_anchor:
        # Secondary check: if the risk name itself contains 2+ non-generic tokens,
        # treat the name as self-anchoring (e.g. "TSMC Allocation Constraint Risk")
        name_tokens = set(re.findall(r"[a-z]{3,}", risk_name.lower()))
        non_generic_tokens = name_tokens - _GENERIC_STOPWORDS - _METRIC_WORDS
        if len(non_generic_tokens) < 2:
            return False, "missing filing-specific anchor"
    if specific_anchor.lower() not in combined:
        return False, "anchor not grounded in quote"
    if not source_section:
        return False, "missing source section"
    return True, ""


def candidate_to_evidence_line(candidate: RiskEvidenceCandidate) -> str:
    section = str(candidate.source_section or "Risk Factors").strip()
    quote = " ".join(str(candidate.source_quote or "").split()).strip()
    if section and quote:
        return f"{section}: {quote}"
    return quote or candidate.risk_name
