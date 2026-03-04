from app.api import filings as filings_api


def test_ensure_final_strict_word_band_clamps_and_orders() -> None:
    target_length = 195
    tolerance = filings_api._effective_word_band_tolerance(target_length)
    lower = target_length - tolerance
    upper = target_length + tolerance

    # Deliberately make the whitespace token count exceed the strict upper bound by 1,
    # and also put sections out of order so we verify the final ordering pass.
    text = (
        "## Key Metrics\n"
        + " ".join(["word"] * 97)
        + "\n\n## Executive Summary\n"
        + " ".join(["word"] * 103)
    )

    assert len(text.split()) == upper + 1
    assert filings_api._count_words(text) <= upper

    enforced = filings_api._ensure_final_strict_word_band(
        text,
        target_length,
        include_health_rating=False,
        tolerance=tolerance,
    )

    assert lower <= len(enforced.split()) <= upper
    assert lower <= filings_api._count_words(enforced) <= upper
    assert enforced.find("## Executive Summary") < enforced.find("## Key Metrics")
