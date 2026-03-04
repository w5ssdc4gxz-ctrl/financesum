"""Automated evaluation harness for filing summary quality.

Provides checks for word count, repetition, numeric density, narrative flow,
quote validation, section completeness, boilerplate detection, and cost tracking.

Quality thresholds (numeric density caps, banned phrases, section order) are
sourced from ``app.services.prompt_pack`` so that the evaluation criteria and
the generation prompts stay in sync.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from app.services.prompt_pack import (
    ANTI_BOREDOM_RULES as _ANTI_BOREDOM_RULES,
    NUMERIC_DENSITY_CAPS as _PROMPT_PACK_DENSITY_CAPS,
    SECTION_ORDER as _PROMPT_PACK_SECTION_ORDER,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Result of a single quality check."""

    check_name: str
    passed: bool
    score: float = 0.0
    details: str = ""
    hard_fail: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "score": round(self.score, 4),
            "details": self.details,
            "hard_fail": self.hard_fail,
        }


@dataclass
class StageCost:
    """Cost breakdown for a single pipeline stage."""

    stage_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class EvalReport:
    """Aggregated evaluation report for a summary."""

    company: str = ""
    filing_type: str = ""
    target_length: int = 0
    results: List[EvalResult] = field(default_factory=list)
    cost_report: Optional[Dict[str, Any]] = None
    overall_pass: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company": self.company,
            "filing_type": self.filing_type,
            "target_length": self.target_length,
            "results": [r.to_dict() for r in self.results],
            "cost_report": self.cost_report,
            "overall_pass": self.overall_pass,
        }


# ---------------------------------------------------------------------------
# Constants — sourced from prompt_pack where possible
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN_ESTIMATE = 4

DEFAULT_BUDGET_CAP_USD = 0.10

# Standard (non-optional) sections in canonical order from prompt_pack.
# "Financial Health Rating" is always optional in the eval harness.
STANDARD_SECTIONS = [
    s for s in _PROMPT_PACK_SECTION_ORDER if s != "Financial Health Rating"
]

OPTIONAL_SECTIONS = [
    "Financial Health Rating",
]

NARRATIVE_CONNECTORS = [
    "however",
    "this suggests",
    "looking ahead",
    "importantly",
    "notably",
    "consequently",
    "meanwhile",
    "furthermore",
    "nevertheless",
    "that said",
    "in contrast",
    "as a result",
    "going forward",
    "more broadly",
    "the practical read-through",
]

# Boilerplate / filler phrases — combines the prompt_pack ANTI_BOREDOM_RULES
# banned phrases with additional generic filler patterns.
BOILERPLATE_PHRASES = [
    # From prompt_pack ANTI_BOREDOM_RULES (banned openers + corporate fluff):
    "it is also worth noting",
    "it is worth noting",
    "it should be noted",
    "it should be noted that",
    "showcases its dominance",
    "driving shareholder value",
    "incredibly encouraging",
    "clear indication",
    "fueling future growth",
    "welcome addition",
    "poised for growth",
    "testament to",
    "remains to be seen",
    "robust financial picture",
    "leveraging synergies",
    "well-positioned",
    # Additional generic filler patterns:
    "in conclusion",
    "all things considered",
    "at the end of the day",
    "moving forward",
    "only time will tell",
    "the company continues to",
    "it remains to be seen",
    "as previously mentioned",
    "needless to say",
    "last but not least",
]

ATTRIBUTION_PHRASES = [
    "management noted",
    "management stated",
    "management highlighted",
    "management emphasized",
    "management said",
    "management indicated",
    "management acknowledged",
    "management cautioned",
    "management described",
    "management characterized",
    "the filing states",
    "the filing notes",
    "the company noted",
    "the company stated",
    "the company described",
    "according to the filing",
    "according to management",
    "as noted in the filing",
    "as disclosed",
]

# Numeric density caps per section — sourced from prompt_pack.
# Only sections with prose caps (< 99) are enforced; Key Metrics is excluded.
NUMERIC_DENSITY_CAPS: Dict[str, float] = {
    section: float(cap)
    for section, cap in _PROMPT_PACK_DENSITY_CAPS.items()
    if cap < 99  # Exclude Key Metrics (cap=99, data block)
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def count_words(text: str) -> int:
    """Approximate MS Word-style word count (matches filings.py _count_words)."""
    if not text:
        return 0
    punct = string.punctuation + "\u201c\u201d\u2018\u2019\u2014\u2013\u2026"
    count = 0
    for raw_token in text.split():
        token = raw_token.strip(punct)
        if token:
            count += 1
    return count


def extract_section_body(text: str, title: str) -> Optional[str]:
    """Extract the body of a markdown section by heading title.

    Mirrors the ``_extract_markdown_section_body`` logic in filings.py.
    """
    if not text or not title:
        return None
    pattern = re.compile(
        rf"^\s*##\s*{re.escape(title)}\s*\n+(.*?)(?=^\s*##\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences on sentence-ending punctuation."""
    if not text:
        return []
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if s.strip()]


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length (matches gemini_usage.py)."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN_ESTIMATE))


def _extract_quoted_strings(text: str) -> List[str]:
    """Return all strings enclosed in double quotes or smart quotes."""
    # Match both straight and smart quotes.
    patterns = [
        re.compile(r'"([^"]{4,})"'),
        re.compile(r'\u201c([^\u201d]{4,})\u201d'),
    ]
    quotes: List[str] = []
    for pat in patterns:
        quotes.extend(pat.findall(text))
    return quotes


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

def check_word_count(
    summary: str,
    target: int,
    tolerance: int = 10,
) -> EvalResult:
    """HARD FAIL check: |actual - target| must be ≤ tolerance."""
    actual = count_words(summary)
    diff = abs(actual - target)
    passed = diff <= tolerance
    return EvalResult(
        check_name="word_count",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=f"target={target}, actual={actual}, diff={diff}, tolerance=±{tolerance}",
        hard_fail=not passed,
    )


def check_repetition(summary: str, threshold: float = 0.85) -> EvalResult:
    """Detect duplicate/near-duplicate sentences via fuzzy matching.

    Returns a score 0-1 where 0 = no repetition, 1 = all duplicates.
    """
    sentences = _split_sentences(summary)
    if len(sentences) < 2:
        return EvalResult(
            check_name="repetition",
            passed=True,
            score=0.0,
            details="fewer than 2 sentences",
        )

    duplicate_pairs: List[Tuple[int, int, float]] = []
    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            ratio = SequenceMatcher(None, sentences[i], sentences[j]).ratio()
            if ratio >= threshold:
                duplicate_pairs.append((i, j, ratio))

    dup_count = len(duplicate_pairs)
    total_pairs = len(sentences) * (len(sentences) - 1) // 2
    score = dup_count / total_pairs if total_pairs > 0 else 0.0

    passed = dup_count == 0
    details_parts: List[str] = [
        f"sentences={len(sentences)}, duplicate_pairs={dup_count}",
    ]
    for i, j, ratio in duplicate_pairs[:5]:
        details_parts.append(
            f"  pair ({i},{j}) ratio={ratio:.2f}: "
            f"{sentences[i][:60]}... ~ {sentences[j][:60]}..."
        )

    return EvalResult(
        check_name="repetition",
        passed=passed,
        score=round(score, 4),
        details="; ".join(details_parts),
    )


def check_numeric_density(summary: str) -> EvalResult:
    """Check numeric density per section against caps from prompt_pack.

    Caps are sourced from ``prompt_pack.NUMERIC_DENSITY_CAPS`` (e.g.
    Executive Summary ≤ 2, MD&A ≤ 3, Risk Factors ≤ 2, etc.).
    """
    violations: List[str] = []
    section_densities: Dict[str, float] = {}

    for section_title, cap in NUMERIC_DENSITY_CAPS.items():
        body = extract_section_body(summary, section_title)
        if not body:
            continue
        wc = count_words(body)
        if wc == 0:
            continue
        numbers = re.findall(r"\d+", body)
        density = len(numbers) / (wc / 100.0)
        section_densities[section_title] = round(density, 2)
        if density > cap:
            violations.append(
                f"{section_title}: {density:.1f} numbers/100w (cap={cap})"
            )

    passed = len(violations) == 0
    details = "; ".join(violations) if violations else "all sections within density caps"
    if section_densities:
        details += f" | densities={section_densities}"

    return EvalResult(
        check_name="numeric_density",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=details,
    )


def check_flow_score(summary: str) -> EvalResult:
    """Score narrative flow based on connector variety and frequency.

    Score = unique_connectors_found / total_connector_vocabulary.
    """
    lowered = summary.lower()
    found_connectors: List[str] = []
    unique_found: set[str] = set()

    for connector in NARRATIVE_CONNECTORS:
        occurrences = lowered.count(connector)
        if occurrences > 0:
            unique_found.add(connector)
            for _ in range(occurrences):
                found_connectors.append(connector)

    total_connectors = len(found_connectors)
    unique_count = len(unique_found)
    variety_score = unique_count / len(NARRATIVE_CONNECTORS) if NARRATIVE_CONNECTORS else 0.0

    sentences = _split_sentences(summary)
    frequency = total_connectors / len(sentences) if sentences else 0.0

    # Soft check: variety_score > 0 is desirable but not a hard requirement.
    passed = True  # flow is advisory, not a hard fail
    details = (
        f"unique_connectors={unique_count}/{len(NARRATIVE_CONNECTORS)}, "
        f"total_uses={total_connectors}, "
        f"variety_score={variety_score:.2f}, "
        f"frequency={frequency:.2f}/sentence"
    )

    return EvalResult(
        check_name="flow_score",
        passed=passed,
        score=round(variety_score, 4),
        details=details,
    )


def check_quotes(
    summary: str,
    source_text: str,
    min_quotes: int = 3,
    max_quotes: int = 8,
    grounding_threshold: float = 0.80,
) -> EvalResult:
    """Validate direct quotes in summary against source text.

    If source has quoted text → summary should have min_quotes to max_quotes grounded quotes.
    If source has no quotes → check for attribution phrases instead.
    """
    source_quotes = _extract_quoted_strings(source_text)
    summary_quotes = _extract_quoted_strings(summary)
    quote_count = len(summary_quotes)

    # Source has no quoted text — check for attribution phrases.
    if not source_quotes:
        lowered = summary.lower()
        attribution_count = sum(
            1 for phrase in ATTRIBUTION_PHRASES if phrase in lowered
        )
        passed = attribution_count > 0
        return EvalResult(
            check_name="quote_validation",
            passed=passed,
            score=1.0 if passed else 0.0,
            details=(
                f"no source quotes; attribution_phrases_found={attribution_count}"
            ),
        )

    # Source has quotes — verify count range and grounding.
    issues: List[str] = []

    if quote_count < min_quotes:
        issues.append(f"too few quotes: {quote_count} < {min_quotes}")
    if quote_count > max_quotes:
        issues.append(f"too many quotes: {quote_count} > {max_quotes}")

    # Check grounding: each summary quote should match a source quote.
    ungrounded: List[str] = []
    for sq in summary_quotes:
        best_ratio = 0.0
        for src_q in source_quotes:
            ratio = SequenceMatcher(None, sq.lower(), src_q.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        if best_ratio < grounding_threshold:
            ungrounded.append(sq[:80])

    if ungrounded:
        issues.append(
            f"{len(ungrounded)} quote(s) not grounded in filing text"
        )

    passed = len(issues) == 0
    details = "; ".join(issues) if issues else (
        f"quotes={quote_count} (range {min_quotes}-{max_quotes}), all grounded"
    )

    return EvalResult(
        check_name="quote_validation",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=details,
    )


def check_section_completeness(
    summary: str,
    include_health_rating: bool = False,
) -> EvalResult:
    """Verify all expected sections are present in the summary."""
    expected = list(STANDARD_SECTIONS)
    if include_health_rating:
        expected = ["Financial Health Rating"] + expected

    missing: List[str] = []
    for section in expected:
        pattern = re.compile(
            rf"^\s*##\s*{re.escape(section)}\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        if not pattern.search(summary):
            # Also check the "and" variant for MD&A.
            if section == "Management Discussion & Analysis":
                alt = re.compile(
                    r"^\s*##\s*Management Discussion and Analysis\s*$",
                    re.IGNORECASE | re.MULTILINE,
                )
                if alt.search(summary):
                    continue
            missing.append(section)

    passed = len(missing) == 0
    return EvalResult(
        check_name="section_completeness",
        passed=passed,
        score=1.0 if passed else (len(expected) - len(missing)) / len(expected),
        details=f"missing={missing}" if missing else "all sections present",
        hard_fail=not passed,
    )


def check_boilerplate(summary: str) -> EvalResult:
    """Flag generic filler / boilerplate phrases."""
    lowered = summary.lower()
    found: List[str] = []
    for phrase in BOILERPLATE_PHRASES:
        if phrase in lowered:
            found.append(phrase)

    passed = len(found) == 0
    return EvalResult(
        check_name="boilerplate",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=f"boilerplate_found={found}" if found else "no boilerplate detected",
    )


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

class PipelineCostTracker:
    """Track token usage and costs across pipeline stages."""

    def __init__(self, budget_cap_usd: float = DEFAULT_BUDGET_CAP_USD) -> None:
        self.budget_cap_usd = budget_cap_usd
        self.stages: List[StageCost] = []

    def add_stage(
        self,
        name: str,
        input_tokens: int,
        output_tokens: int,
        rate_per_m_input: float = 0.04,
        rate_per_m_output: float = 0.15,
    ) -> StageCost:
        """Record a pipeline stage and return its cost breakdown."""
        cost = (input_tokens / 1_000_000) * rate_per_m_input + (
            output_tokens / 1_000_000
        ) * rate_per_m_output
        stage = StageCost(
            stage_name=name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.stages.append(stage)
        return stage

    def add_stage_from_text(
        self,
        name: str,
        input_text: str,
        output_text: str,
        rate_per_m_input: float = 0.04,
        rate_per_m_output: float = 0.15,
    ) -> StageCost:
        """Convenience: estimate tokens from text and record stage."""
        return self.add_stage(
            name=name,
            input_tokens=estimate_tokens(input_text),
            output_tokens=estimate_tokens(output_text),
            rate_per_m_input=rate_per_m_input,
            rate_per_m_output=rate_per_m_output,
        )

    def total_cost(self) -> float:
        """Total cost across all stages."""
        return sum(s.cost_usd for s in self.stages)

    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages)

    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    def check_budget(self) -> bool:
        """Return True if total cost is within budget cap."""
        return self.total_cost() <= self.budget_cap_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget_cap_usd": self.budget_cap_usd,
            "total_cost_usd": round(self.total_cost(), 6),
            "total_input_tokens": self.total_input_tokens(),
            "total_output_tokens": self.total_output_tokens(),
            "within_budget": self.check_budget(),
            "stages": [s.to_dict() for s in self.stages],
        }

    def check_cost(self) -> EvalResult:
        """Return an EvalResult for the cost budget check."""
        within = self.check_budget()
        return EvalResult(
            check_name="cost_budget",
            passed=within,
            score=1.0 if within else 0.0,
            details=(
                f"total=${self.total_cost():.4f}, cap=${self.budget_cap_usd:.2f}, "
                f"input_tokens={self.total_input_tokens()}, "
                f"output_tokens={self.total_output_tokens()}"
            ),
            hard_fail=not within,
        )


# ---------------------------------------------------------------------------
# Supabase logging
# ---------------------------------------------------------------------------

def log_eval_to_supabase(
    eval_report: Dict[str, Any],
    *,
    supabase_url: str = "",
    supabase_key: str = "",
) -> None:
    """Best-effort logging of evaluation results to Supabase.

    Never raises — all errors are silently swallowed.
    """
    try:
        if not supabase_url or not supabase_key:
            import os
            supabase_url = supabase_url or os.getenv("SUPABASE_URL", "")
            supabase_key = supabase_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

        if not supabase_url or not supabase_key:
            return

        from supabase import create_client  # type: ignore[import-untyped]

        client = create_client(supabase_url, supabase_key)
        client.table("eval_results").insert(eval_report).execute()
    except Exception:
        # Best-effort only; never raise from eval logging.
        return


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def evaluate_summary(
    summary: str,
    target_length: int,
    *,
    source_text: str = "",
    company: str = "",
    filing_type: str = "",
    include_health_rating: bool = False,
    cost_tracker: Optional[PipelineCostTracker] = None,
    min_quotes: int = 3,
    max_quotes: int = 8,
    word_count_tolerance: int = 10,
) -> EvalReport:
    """Run all quality checks and return an aggregated report."""
    results: List[EvalResult] = []

    # 1. Word count (HARD FAIL)
    results.append(check_word_count(summary, target_length, tolerance=word_count_tolerance))

    # 2. Repetition
    results.append(check_repetition(summary))

    # 3. Numeric density
    results.append(check_numeric_density(summary))

    # 4. Flow score
    results.append(check_flow_score(summary))

    # 5. Quote validation
    if source_text:
        results.append(
            check_quotes(summary, source_text, min_quotes=min_quotes, max_quotes=max_quotes)
        )

    # 6. Section completeness (HARD FAIL)
    results.append(check_section_completeness(summary, include_health_rating=include_health_rating))

    # 7. Boilerplate
    results.append(check_boilerplate(summary))

    # 8. Cost budget
    cost_report_dict: Optional[Dict[str, Any]] = None
    if cost_tracker is not None:
        results.append(cost_tracker.check_cost())
        cost_report_dict = cost_tracker.to_dict()

    # overall_pass = True if no hard-fail check has failed
    overall_pass = not any(r.hard_fail and not r.passed for r in results)

    return EvalReport(
        company=company,
        filing_type=filing_type,
        target_length=target_length,
        results=results,
        cost_report=cost_report_dict,
        overall_pass=overall_pass,
    )
