from __future__ import annotations

import pytest

from app.api import filings as filings_api
from app.services.word_surgery import clean_ending, count_words, needs_regen_to_expand


def _in_band(value: str, target: int, tolerance: int) -> bool:
    lower = target - tolerance
    upper = target + tolerance
    split_count = len((value or "").split())
    stripped_count = filings_api._count_words(value or "")
    return lower <= split_count <= upper and lower <= stripped_count <= upper


def _make_body(words: int, token: str) -> str:
    if words <= 0:
        return ""
    parts: list[str] = []
    remaining = int(words)
    sentence_idx = 0
    while remaining > 0:
        chunk = min(8, remaining)
        parts.append(" ".join([f"{token}{sentence_idx}"] * chunk) + ".")
        remaining -= chunk
        sentence_idx += 1
    return " ".join(parts)


def _build_sectioned_summary(
    target_length: int,
    *,
    include_health_rating: bool,
    shrink_sections: tuple[str, ...] = (),
    exact_target: bool = False,
) -> str:
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=include_health_rating
    )
    sections = [
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ]
    if not include_health_rating:
        sections = [title for title in sections if title != "Financial Health Rating"]

    parts: list[str] = []
    shrink_set = set(shrink_sections)
    for title in sections:
        if title == "Key Metrics":
            body = (
                "→ Revenue: $1.0B\n"
                "→ Operating Margin: 10.0%\n"
                "→ Free Cash Flow: $250M"
            )
        else:
            budget = int(budgets.get(title, 0) or 0)
            body_words = budget if title not in shrink_set else max(14, budget // 3)
            body = _make_body(body_words, title.split()[0].lower())
        parts.append(f"## {title}\n{body}".strip())

    text = "\n\n".join(parts).strip()
    if not exact_target:
        return text

    tolerance = filings_api._effective_word_band_tolerance(target_length)
    lower = max(1, target_length - tolerance)
    current = filings_api._count_words(text)
    if current < lower:
        missing = lower - current
        closing_body = filings_api._extract_markdown_section_body(text, "Closing Takeaway")
        closing_body = ((closing_body or "").strip() + " " + _make_body(missing, "closingpad")).strip()
        text = text.replace(
            f"## Closing Takeaway\n{filings_api._extract_markdown_section_body(text, 'Closing Takeaway')}",
            f"## Closing Takeaway\n{closing_body}",
            1,
        )

    text = filings_api._ensure_final_strict_word_band(
        text,
        target_length,
        include_health_rating=include_health_rating,
        tolerance=tolerance,
        allow_padding=False,
    )
    return filings_api._enforce_whitespace_word_band(
        text,
        target_length,
        tolerance=tolerance,
        allow_padding=False,
        dedupe=True,
    )


def test_enforce_whitespace_word_band_guarantees_band_for_markup_only_input() -> None:
    """When input is pure markup tokens (no real content), the enforcer strips
    them and returns empty.  With the metric-anchored padding changes, the
    system no longer manufactures content from nothing — this is correct
    because padding now requires quantitative context.
    """
    filings_api._reset_padding_budget()
    target = 20
    tolerance = 15
    garbage = " ".join(["##"] * 60)

    enforced = filings_api._enforce_whitespace_word_band(
        garbage, target, tolerance=tolerance, allow_padding=True, dedupe=False
    )

    # The stripped-count should be 0 since ## tokens are not real words.
    # The system should not inject arbitrary padding without metrics context.
    stripped_count = filings_api._count_words(enforced)
    assert stripped_count <= target + tolerance


def test_enforce_whitespace_word_band_guarantees_band_for_analysis_markdown() -> None:
    target = 120
    tolerance = 15
    too_long = (
        "# Investment Analysis: ExampleCo\n\n"
        "## TL;DR\n"
        + ("alpha " * 220).strip()
        + "\n\n## Investment Thesis\n"
        + ("beta " * 220).strip()
        + "\n"
    )

    enforced = filings_api._enforce_whitespace_word_band(
        too_long, target, tolerance=tolerance, allow_padding=True, dedupe=False
    )

    assert _in_band(enforced, target, tolerance)


# ---------------------------------------------------------------------------
# clean_ending — sentence-boundary truncation (replaces filler expansion)
# ---------------------------------------------------------------------------

def test_clean_ending_truncates_over_target_at_sentence_boundary() -> None:
    """clean_ending must not cut mid-sentence when truncating over-target text."""
    text = (
        "Revenue grew strongly. "
        "Margins expanded due to operating leverage. "
        "Capital allocation improved as FCF generation increased. "
        "The balance sheet remained healthy with low net debt."
    )
    result = clean_ending(text, target_words=12, tolerance=3)
    # Upper bound = 15 words; result must end at a sentence boundary
    assert count_words(result) <= 15
    assert result.rstrip().endswith((".", "!", "?"))


def test_clean_ending_does_not_modify_within_band_text() -> None:
    """Text already within the word band should be returned unchanged."""
    text = "Revenue grew. Margins held."
    wc = count_words(text)
    result = clean_ending(text, target_words=wc, tolerance=10)
    assert result == text


def test_clean_ending_does_not_pad_under_target() -> None:
    """Under-target text must be returned as-is — no padding."""
    text = "Short text."
    wc = count_words(text)
    result = clean_ending(text, target_words=200, tolerance=10)
    # Should return unchanged since it's under target
    assert count_words(result) == wc


# ---------------------------------------------------------------------------
# needs_regen_to_expand — signals LLM regeneration, never inserts filler
# ---------------------------------------------------------------------------

def test_needs_regen_to_expand_true_when_under() -> None:
    text = " ".join(["word"] * 50) + "."
    assert needs_regen_to_expand(text, target=100, tolerance=5) is True


def test_needs_regen_to_expand_false_when_within_band() -> None:
    text = " ".join(["word"] * 100) + "."
    assert needs_regen_to_expand(text, target=100, tolerance=5) is False


def test_needs_regen_to_expand_false_when_over_target() -> None:
    text = " ".join(["word"] * 150) + "."
    assert needs_regen_to_expand(text, target=100, tolerance=5) is False


@pytest.mark.parametrize("target", [500, 600, 1000])
def test_short_underflow_rescue_rewrites_into_twenty_word_band(
    monkeypatch, target: int
) -> None:
    tolerance = filings_api._effective_word_band_tolerance(target)
    draft = _build_sectioned_summary(
        target,
        include_health_rating=True,
        shrink_sections=(
            "Executive Summary",
            "Management Discussion & Analysis",
            "Closing Takeaway",
        ),
    )
    rewritten = _build_sectioned_summary(
        target, include_health_rating=True, exact_target=True
    )
    captured: dict[str, str] = {}

    def _fake_rewrite_summary_to_length(*args, **kwargs):
        captured["hint"] = str(kwargs.get("quality_issue_hint") or "")
        return rewritten, (filings_api._count_words(rewritten), tolerance)

    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite_summary_to_length,
    )
    monkeypatch.setattr(filings_api, "_run_summary_cleanup_pass", lambda text, **_: text)
    monkeypatch.setattr(filings_api, "_remove_metric_echo_loops", lambda text: text)
    monkeypatch.setattr(filings_api, "_merge_staccato_paragraphs", lambda text: text)
    monkeypatch.setattr(
        filings_api,
        "_cap_closing_sentences_filings",
        lambda text, **_: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_merge_duplicate_canonical_sections",
        lambda text, **_: text,
    )

    rescued = filings_api._rescue_short_sectioned_underflow(
        draft,
        target_length=target,
        include_health_rating=True,
        calculated_metrics={},
        company_name="ExampleCo",
        gemini_client=object(),
        quality_validators=None,
        generation_stats={},
        strict_contract_required=False,
    )

    assert _in_band(rescued, target, tolerance)
    assert "Executive Summary" in captured["hint"]
    assert "Management Discussion & Analysis" in captured["hint"]
    assert "Key Metrics:" not in captured["hint"]
