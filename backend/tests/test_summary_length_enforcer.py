from app.api import filings as filings_api
from app.services.summary_length import (
    TARGET_LENGTH_MAX_WORDS,
    enforce_summary_target_length,
)


def _make_words(n: int, token: str) -> str:
    if n <= 0:
        return ""
    return " ".join([token] * n)


def test_enforce_summary_target_length_caps_markdown_to_target() -> None:
    # Deliberately over-long markdown with headings so trimming must preserve structure.
    base = (
        "# Investment Analysis: ExampleCo\n\n"
        "## TL;DR\n"
        f"{_make_words(120, 'tldr')}.\n\n"
        "## Investment Thesis\n"
        f"{_make_words(220, 'thesis')}.\n\n"
        "## Top 5 Risks\n"
        f"{_make_words(220, 'risk')}.\n\n"
        "## Catalysts\n"
        f"{_make_words(120, 'cat')}.\n\n"
        "## Key KPIs\n"
        f"{_make_words(120, 'kpi')}.\n"
    )

    target = 200
    enforced = enforce_summary_target_length(base, target)

    assert len(enforced.split()) <= target
    assert filings_api._count_words(enforced) <= target


def test_cleanup_sentence_artifacts_removes_stray_quotes_and_fragments() -> None:
    raw = (
        "## Executive Summary\n"
        "Strong liquidity supports near-term flexibility, but\"\n"
        "\n"
        "## Financial Performance\n"
        "Margins improved and cash flow held, but\n"
        "\"\n"
        "\n"
        "## Key KPIs to Monitor\n"
        "- Monitor leverage and liquidity\n"
    )
    cleaned = filings_api._cleanup_sentence_artifacts(raw)
    assert "\"\n" not in cleaned
    assert "## Key KPIs to Monitor" not in cleaned
    assert "## Key KPIs" in cleaned
    assert "- Monitor leverage and liquidity" not in cleaned
    assert "- leverage and liquidity" in cleaned
    assert "flexibility." in cleaned
    assert "held." in cleaned


def test_enforce_summary_target_length_caps_to_global_max_when_no_target() -> None:
    base = _make_words(TARGET_LENGTH_MAX_WORDS + 25, "word")
    enforced = enforce_summary_target_length(base, None)

    assert filings_api._count_words(enforced) <= TARGET_LENGTH_MAX_WORDS
