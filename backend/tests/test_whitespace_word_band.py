from app.api import filings as filings_api


def test_enforce_whitespace_word_band_guarantees_dual_counting() -> None:
    """Regression: ensure whitespace token counts can't drift outside the strict band.

    Some markdown tokens (e.g., leading '-' bullets) count toward `len(text.split())`
    but are ignored by `_count_words()` after punctuation stripping. The final band
    clamp must satisfy BOTH counting styles.
    """

    target_length = 650
    tolerance = 15
    lower = target_length - tolerance
    upper = target_length + tolerance

    # 650 "real" words + 25 standalone markdown list-marker lines that inflate
    # whitespace tokens but are ignored by `_count_words()`.
    real_words = " ".join(["word"] * target_length)
    bullets = "\n".join(["-"] * 25)
    text = f"{bullets}\n{real_words}".strip()

    assert filings_api._count_words(text) == target_length
    assert len(text.split()) == target_length + 25
    assert len(text.split()) > upper

    enforced = filings_api._enforce_whitespace_word_band(
        text,
        target_length,
        tolerance=tolerance,
        allow_padding=False,
        dedupe=False,
    )

    assert lower <= filings_api._count_words(enforced) <= upper
    assert lower <= len(enforced.split()) <= upper


def test_enforce_whitespace_word_band_compacts_key_metrics_pipes_when_split_count_dominates() -> None:
    target_length = 20
    tolerance = 2
    lower = target_length - tolerance
    upper = target_length + tolerance

    text = "\n".join(
        [
            "## Key Metrics",
            "DATA_GRID_START",
            "Revenue | $1.0B",
            "Operating Income | $0.2B",
            "Operating Margin | 20%",
            "Free Cash Flow | $0.1B",
            "Cash | $0.5B",
            "Debt | $0.3B",
            "DATA_GRID_END",
        ]
    )

    assert lower <= filings_api._count_words(text) <= upper
    assert len(text.split()) > upper

    enforced = filings_api._enforce_whitespace_word_band(
        text,
        target_length,
        tolerance=tolerance,
        allow_padding=False,
        dedupe=False,
    )

    assert lower <= filings_api._count_words(enforced) <= upper
    assert lower <= len(enforced.split()) <= upper
    assert "Revenue| $1.0B" in enforced
