from app.api import filings as filings_api


def test_ensure_final_strict_word_band_clamps_and_orders() -> None:
    target_length = 120
    upper = target_length + 10

    # Deliberately make the whitespace token count exceed the strict upper bound by 1,
    # and also put sections out of order so we verify the final ordering pass.
    text = (
        "## Key Metrics\n"
        + " ".join(["word"] * 60)
        + "\n\n## Executive Summary\n"
        + " ".join(["word"] * 65)
    )

    assert len(text.split()) == upper + 1
    assert filings_api._count_words(text) <= upper

    enforced = filings_api._ensure_final_strict_word_band(
        text, target_length, include_health_rating=False, tolerance=10
    )

    assert target_length - 10 <= len(enforced.split()) <= target_length + 10
    assert (
        target_length - 10
        <= filings_api._count_words(enforced)
        <= target_length + 10
    )
    assert enforced.find("## Executive Summary") < enforced.find("## Key Metrics")

