"""Validator-driven post-processing pipeline for filing summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.repetition_guard import RepetitionReport, check_repetition
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
    r"\*\*(?P<name>[^*:\n]{2,120}?):?\*\*\s*:?\s*(?P<body>.+?)(?=(?:\n\s*\*\*[^*]+?\*\*\s*:?)|\Z)",
    re.DOTALL,
)
_GENERIC_RISK_NAME_RE = re.compile(
    r"\b(macro(?:economic)?|competition|competitive pressure|regulatory risk|margin compression|liquidity risk|cash flow risk)\b",
    re.IGNORECASE,
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
SHORT_SECTIONED_WORD_BAND_TOLERANCE = 20


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


def _collect_used_claims(text: str, include_health_rating: bool) -> List[str]:
    claims: List[str] = []
    for section_name in _required_sections(include_health_rating):
        body = _extract_section_body(text, section_name)
        if not body:
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        if sentences:
            claims.append(sentences[0])
    return claims


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


def _validate_risk_factors(
    text: str,
    *,
    risk_budget_words: int,
    risk_factors_excerpt: str = "",
) -> tuple[int, List[str]]:
    body = _extract_section_body(text, "Risk Factors")
    if not body:
        return 0, ["Risk Factors section is missing."]

    shape = get_risk_factors_shape(risk_budget_words)
    expected_count = int(shape.risk_count or 0)
    items = list(_RISK_ITEM_RE.finditer(body))
    if len(items) != expected_count:
        return len(items), [
            f"Risk Factors has {len(items)} structured risk(s); expected exactly {expected_count} for this budget."
        ]

    excerpt_terms = {
        token
        for token in re.findall(r"[a-z]{5,}", (risk_factors_excerpt or "").lower())
        if token not in {"about", "their", "which", "these", "those", "other"}
    }
    seen_names: set[str] = set()
    failures: List[str] = []

    for item in items:
        name = (item.group("name") or "").strip()
        body_text = (item.group("body") or "").strip()
        canon_name = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if canon_name in seen_names:
            failures.append("Risk Factors contain duplicate risk names. Use distinct, non-overlapping drivers.")
            continue
        seen_names.add(canon_name)

        if _GENERIC_RISK_NAME_RE.search(name):
            failures.append(
                f"Risk name '{name}' is too generic. Name the specific exposure, counterparty, regulation, or product/segment at risk."
            )
        sentence_count = _sentence_count(body_text)
        if not (
            int(shape.per_risk_min_sentences or 2)
            <= sentence_count
            <= int(shape.per_risk_max_sentences or 3)
        ):
            failures.append(
                f"Risk Factors under '{name}' must contain "
                + describe_sentence_range(
                    int(shape.per_risk_min_sentences or 2),
                    int(shape.per_risk_max_sentences or 3),
                )
                + "."
            )
        if len(body_text.split()) < 18:
            failures.append(
                f"Risk Factors are too thin under '{name}'. Expand each risk with enough sentence depth to explain the mechanism, transmission path, and early-warning signal."
            )
        if not _MECHANISM_RE.search(body_text):
            failures.append(
                f"Risk Factors under '{name}' need a concrete mechanism (what causes the risk and how it hits the business)."
            )
        if not _TRANSMISSION_RE.search(body_text):
            failures.append(
                f"Risk Factors under '{name}' must explain the financial impact path into revenue, margins, cash flow, or balance-sheet flexibility."
            )
        if not _EARLY_WARNING_RE.search(body_text):
            failures.append(
                f"Risk Factors under '{name}' should include a concrete early-warning signal."
            )
        if excerpt_terms:
            body_tokens = set(re.findall(r"[a-z]{5,}", body_text.lower()))
            if not (body_tokens & excerpt_terms):
                failures.append(
                    f"Risk Factors under '{name}' are too generic relative to the filing excerpt. Tie the mechanism to company-specific terms."
                )

    return len(items), failures


def validate_summary(
    text: str,
    *,
    target_words: int,
    section_budgets: Dict[str, int],
    include_health_rating: bool = True,
    risk_factors_excerpt: str = "",
    total_tolerance_words: Optional[int] = None,
) -> SummaryValidationReport:
    working_text = str(text or "").strip()
    total_words = count_words(working_text)
    target = int(target_words or 0)
    if total_tolerance_words is not None:
        tolerance_words = max(0, int(total_tolerance_words))
    elif (
        int(SHORT_FORM_SECTIONED_TARGET_MIN_WORDS)
        <= target
        < int(SHORT_FORM_SECTIONED_TARGET_MAX_WORDS)
    ):
        tolerance_words = int(SHORT_SECTIONED_WORD_BAND_TOLERANCE)
    else:
        tolerance_words = total_word_tolerance_words(target_words)
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
            tolerance = section_budget_tolerance_words(section_name, budget)
            lower_budget = max(1, budget - tolerance)
            upper_budget = budget + tolerance
            if section_name != "Key Metrics" and actual_words < lower_budget:
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

    risk_count, risk_failures = _validate_risk_factors(
        working_text,
        risk_budget_words=int(section_budgets.get("Risk Factors", 0) or 0),
        risk_factors_excerpt=risk_factors_excerpt,
    )
    report.risk_count = risk_count
    for failure in risk_failures:
        report.section_failures.append(
            SectionValidationFailure(
                section_name="Risk Factors",
                code="risk_schema",
                message=failure,
                severity=3.5,
            )
        )

    report.passed = not report.global_failures and not report.section_failures
    return report


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
) -> PostProcessResult:
    """Run the validator-driven post-processing pipeline."""
    working_text = str(text or "").strip()
    retries = 0
    section_retry_counts: Dict[str, int] = {}

    while retries < int(max_retries):
        validation = validate_summary(
            working_text,
            target_words=target_words,
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            risk_factors_excerpt=risk_factors_excerpt,
        )
        if validation.passed or regenerate_section_fn is None:
            break

        target_failure = _select_regeneration_target(
            validation,
            text=working_text,
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            exhausted_sections={
                section_name
                for section_name, count in section_retry_counts.items()
                if int(count or 0) >= int(max_retries_per_section)
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
        used_claims = _collect_used_claims(working_text, include_health_rating)
        try:
            new_body = regenerate_section_fn(
                section_name=section_name,
                budget=budget,
                failure_reason=target_failure.message,
                prior_section_text=prior_section_text,
                existing_section_text=existing_body,
                used_claims=used_claims,
            )
        except TypeError:
            new_body = regenerate_section_fn(section_name, budget)
        if not new_body:
            break
        working_text = _replace_section_body(working_text, section_name, str(new_body))
        section_retry_counts[section_name] = int(section_retry_counts.get(section_name, 0) or 0) + 1
        retries += 1

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
                        used_claims=_collect_used_claims(working_text, include_health_rating),
                    )
                except TypeError:
                    rewritten = regenerate_section_fn(closing_section, closing_budget)
                if rewritten:
                    working_text = _replace_section_body(working_text, closing_section, str(rewritten))
                    section_retry_counts[closing_section] = int(
                        section_retry_counts.get(closing_section, 0) or 0
                    ) + 1
                    retries += 1

    working_text = clean_ending(
        working_text,
        target_words,
        tolerance=total_word_tolerance_words(target_words),
    )
    final_validation = validate_summary(
        working_text,
        target_words=target_words,
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        risk_factors_excerpt=risk_factors_excerpt,
    )

    violations = list(final_validation.global_failures) + [
        failure.message for failure in final_validation.section_failures
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
        if failure.section_name not in exhausted
    ]
    priorities = {
        "risk_schema": 0,
        "repetition": 1,
        "section_budget_under": 2,
        "section_budget_over": 3,
        "dangling_ending": 4,
        "missing_terminal_punctuation": 5,
        "too_few_sentences": 6,
        "missing_section": 7,
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
            if failure.code == "repetition":
                return 2.5
            if failure.code == "missing_section":
                return 1.5
            if failure.code == "too_few_sentences":
                return 1.2
            if failure.code == "dangling_ending":
                return 1.1
            if failure.code == "missing_terminal_punctuation":
                return 1.0
            return 0.5

        return sorted(
            section_failures,
            key=lambda failure: (
                priorities.get(failure.code, 99),
                -_failure_severity(failure),
                canonical_order.get(failure.section_name, 99),
            ),
        )[0]

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
