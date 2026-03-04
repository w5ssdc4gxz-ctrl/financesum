"""Deterministic summary contract validation/repair utilities.

Used by filing-summary one-shot target-length flows to avoid extra model calls.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

_END_PUNCT_RE = re.compile(r'[.!?](?:["\')\]]+)?$')
_DANGLING_ENDINGS = {
    "and",
    "or",
    "but",
    "so",
    "because",
    "with",
    "to",
    "for",
    "of",
    "in",
    "on",
    "at",
    "by",
    "from",
    "the",
    "a",
    "an",
}
_REPETITION_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "to",
    "of",
    "for",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "that",
    "this",
    "these",
    "those",
}
_TRIM_CANDIDATES = [
    {"a", "an", "the"},
    {"of", "in", "on", "at", "by", "to", "for", "from", "with"},
    {
        "very",
        "quite",
        "rather",
        "somewhat",
        "increasingly",
        "relatively",
        "particularly",
        "notably",
        "significantly",
        "overall",
        "generally",
        "largely",
        "mostly",
        "still",
    },
    {"and", "but", "or", "yet", "so", "while"},
]


def normalize_summary_contract_text(text: str) -> str:
    """Normalize whitespace and obvious prefixes without changing semantics."""
    out = str(text or "")
    out = out.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    out = re.sub(r"\s+", " ", out).strip()
    for _ in range(3):
        prev = out
        out = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)+", "", out).strip()
        out = re.sub(r"^(?:summary|tldr|tl;?dr)\s*[:\-]\s*", "", out, flags=re.IGNORECASE).strip()
        if out == prev:
            break
    return re.sub(r"\s+", " ", out).strip()


def count_summary_contract_words(text: str) -> int:
    normalized = normalize_summary_contract_text(text)
    return len(normalized.split()) if normalized else 0


def _canonicalize_token(token: str) -> str:
    lowered = str(token or "").lower().strip()
    return re.sub(r"^[^\w']+|[^\w']+$", "", lowered)


def _min_unique_tokens(target_words: int) -> int:
    target = max(1, int(target_words or 0))
    return max(5, min(target, int(math.ceil(target * 0.65))))


def validate_summary_contract(
    text: str,
    *,
    target_words: int,
    require_single_line: bool = False,
    forbid_markdown_headings: bool = False,
) -> Dict[str, Any]:
    """Validate exact-word plain-text summary quality contract."""
    raw = str(text or "")
    normalized = normalize_summary_contract_text(raw)
    tokens = normalized.split() if normalized else []
    canonical_tokens = [_canonicalize_token(tok) for tok in tokens]
    canonical_nonempty = [tok for tok in canonical_tokens if tok]
    reasons: List[str] = []

    def _add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if not normalized:
        _add("empty")

    if require_single_line and re.search(r"[\r\n]", raw or ""):
        _add("multiple_lines")

    if forbid_markdown_headings and re.search(r"^\s*#+\s+", raw or "", re.MULTILINE):
        _add("contains_markdown_heading")

    word_count = len(tokens)
    if word_count != int(target_words):
        _add(f"word_count_mismatch:{word_count}")

    if normalized and not _END_PUNCT_RE.search(normalized):
        _add("missing_terminal_punctuation")

    for prev, curr in zip(canonical_tokens, canonical_tokens[1:]):
        if prev and curr and prev == curr:
            _add("consecutive_duplicate_token")
            break

    counts: Dict[str, int] = {}
    repeated_nonstopword: Optional[str] = None
    for tok in canonical_nonempty:
        counts[tok] = counts.get(tok, 0) + 1
        if tok not in _REPETITION_STOPWORDS and counts[tok] > 2:
            repeated_nonstopword = tok
            break
    if repeated_nonstopword:
        _add(f"repeated_nonstopword_token:{repeated_nonstopword}")

    if canonical_nonempty and len(set(canonical_nonempty)) < _min_unique_tokens(target_words):
        _add("low_unique_token_count")

    last_token = next((tok for tok in reversed(canonical_tokens) if tok), "")
    if last_token in _DANGLING_ENDINGS:
        _add(f"dangling_ending_token:{last_token}")

    return {
        "normalized": normalized,
        "word_count": word_count,
        "reasons": reasons,
    }


def _safe_trim_to_exact_words(text: str, *, target_words: int) -> Optional[str]:
    normalized = normalize_summary_contract_text(text)
    words = normalized.split() if normalized else []
    delta = len(words) - int(target_words)
    if delta <= 0 or delta > 3:
        return None
    trimmed = list(words)
    for _ in range(delta):
        removed = False
        for candidate_set in _TRIM_CANDIDATES:
            for idx in range(len(trimmed) - 2, 0, -1):
                token_lower = re.sub(r"^[^\w']+|[^\w']+$", "", trimmed[idx].lower())
                if token_lower in candidate_set:
                    trimmed.pop(idx)
                    removed = True
                    break
            if removed:
                break
        if not removed:
            return None
    out = " ".join(trimmed).strip()
    if out and not _END_PUNCT_RE.search(out):
        out += "."
    return out


def repair_summary_contract_deterministically(
    text: str,
    *,
    target_words: int,
    require_single_line: bool = False,
    forbid_markdown_headings: bool = False,
) -> str:
    """Best-effort deterministic repair. Returns candidate (may still fail validation)."""
    candidate = str(text or "")
    # Normalize whitespace/newlines for single-line formats.
    if require_single_line:
        candidate = normalize_summary_contract_text(candidate)
    report = validate_summary_contract(
        candidate,
        target_words=target_words,
        require_single_line=require_single_line,
        forbid_markdown_headings=forbid_markdown_headings,
    )
    if not report["reasons"]:
        return str(report["normalized"] or "").strip()

    normalized = str(report.get("normalized") or candidate or "").strip()
    if normalized and "missing_terminal_punctuation" in report["reasons"]:
        normalized = normalized.rstrip() + "."

    trim_attempt = _safe_trim_to_exact_words(normalized, target_words=target_words)
    if trim_attempt:
        normalized = trim_attempt

    # Final cleanup to collapse whitespace and keep single-line output.
    if require_single_line:
        normalized = normalize_summary_contract_text(normalized)
        if normalized and not _END_PUNCT_RE.search(normalized):
            normalized += "."
    return normalized.strip()

