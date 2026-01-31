from __future__ import annotations


from app.api import filings as filings_api


def _in_band(value: str, target: int, tolerance: int) -> bool:
    lower = target - tolerance
    upper = target + tolerance
    split_count = len((value or "").split())
    stripped_count = filings_api._count_words(value or "")
    return lower <= split_count <= upper and lower <= stripped_count <= upper


def test_enforce_whitespace_word_band_guarantees_band_for_markup_only_input() -> None:
    target = 20
    tolerance = 10
    garbage = " ".join(["##"] * 60)

    enforced = filings_api._enforce_whitespace_word_band(
        garbage, target, tolerance=tolerance, allow_padding=True, dedupe=False
    )

    assert _in_band(enforced, target, tolerance)


def test_enforce_whitespace_word_band_guarantees_band_for_analysis_markdown() -> None:
    target = 120
    tolerance = 10
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

