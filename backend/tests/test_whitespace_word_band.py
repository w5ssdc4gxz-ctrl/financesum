from app.api import filings as filings_api


def test_enforce_whitespace_word_band_guarantees_dual_counting() -> None:
    """Regression: ensure whitespace token counts can't drift outside the strict band.

    Some markdown tokens (e.g., leading '-' bullets) count toward `len(text.split())`
    but are ignored by `_count_words()` after punctuation stripping. The final band
    clamp must satisfy BOTH counting styles.
    """

    target_length = 650
    tolerance = 10
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
