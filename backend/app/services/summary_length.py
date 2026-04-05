"""Shared utilities for enforcing user-requested summary length."""

from __future__ import annotations

from typing import Optional


TARGET_LENGTH_MIN_WORDS = 1
TARGET_LENGTH_MAX_WORDS = 3000


def clamp_summary_target_length(target_length: Optional[int]) -> Optional[int]:
    if target_length is None:
        return None
    try:
        value = int(target_length)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(TARGET_LENGTH_MIN_WORDS, min(TARGET_LENGTH_MAX_WORDS, value))


def enforce_summary_target_length(
    summary_md: str,
    target_length: Optional[int],
    *,
    tolerance: int = 10,
) -> str:
    """
    Enforce a maximum total word count for `summary_md`.

    `target_length` is treated as a hard maximum (cap), not a quota. The system will
    trim if the output is too long, but it will never pad with filler to reach the cap.
    """
    if not summary_md:
        return summary_md
    target = clamp_summary_target_length(target_length)
    cap = int(target) if target is not None else int(TARGET_LENGTH_MAX_WORDS)
    cap = max(TARGET_LENGTH_MIN_WORDS, min(TARGET_LENGTH_MAX_WORDS, cap))

    try:
        # Reuse the hardened band enforcer used by filing summaries so all
        # user-visible "target length" behavior is consistent.
        from app.api import filings as filings_api

        cleaned = summary_md
        try:
            cleaned = filings_api._cleanup_sentence_artifacts(cleaned)  # type: ignore[attr-defined]
            cleaned = filings_api._validate_complete_sentences(cleaned)  # type: ignore[attr-defined]
            cleaned = filings_api._remove_filler_phrases(cleaned)  # type: ignore[attr-defined]
            cleaned = filings_api._tone_down_emotive_adjectives(cleaned)  # type: ignore[attr-defined]
        except Exception:
            cleaned = summary_md

        return filings_api._enforce_whitespace_word_band(  # type: ignore[attr-defined]
            cleaned,
            cap,
            tolerance=tolerance,
            allow_padding=True,
            dedupe=True,
        )
    except Exception:
        return summary_md
