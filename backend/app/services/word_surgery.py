"""Deterministic word-count surgery utilities.

Pure functions that trim or expand a markdown summary to land within a
strict ±tolerance word band.  No LLM calls — these are the "mechanical
fallback" pass that runs after the model has had its chance to hit the
target.

All word counting uses the same punctuation-stripping logic as
``eval_harness.count_words`` and ``filings._count_words`` so that the
numbers agree across the pipeline.
"""

from __future__ import annotations

import re
import string
from typing import Dict, List, Optional, Tuple

from app.services.prompt_pack import SECTION_ORDER as _SECTION_ORDER


# ---------------------------------------------------------------------------
# Word counting (shared logic with eval_harness / filings.py)
# ---------------------------------------------------------------------------

_PUNCT = string.punctuation + "\u201c\u201d\u2018\u2019\u2014\u2013\u2026"


def count_words(text: str) -> int:
    """Approximate MS Word-style word count (matches filings.py _count_words)."""
    if not text:
        return 0
    count = 0
    for raw_token in text.split():
        token = raw_token.strip(_PUNCT)
        if token:
            count += 1
    return count


# Canonical alias — use this name in new code to make intent explicit.
count_words_canonical = count_words


def in_word_band(text: str, target: int, tolerance: int = 10) -> bool:
    """Check if *text* is within ±*tolerance* of *target* words.

    Uses the canonical ``count_words`` (punctuation-stripped) counter.
    """
    if not text or target <= 0:
        return False
    wc = count_words(text)
    return (target - tolerance) <= wc <= (target + tolerance)


def word_band_delta(text: str, target: int) -> int:
    """Return the signed distance from *target*.

    Positive means over, negative means under.
    """
    return count_words(text) - target


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

# Regex that splits on markdown ## headers, capturing the header text.
_SECTION_HEADER_RE = re.compile(r"^(##\s+.+)$", re.MULTILINE)


def _split_into_sections(text: str) -> List[Tuple[str, str]]:
    """Split markdown text into (header, body) pairs.

    Returns a list of tuples.  Content *before* the first ``##`` header
    (if any) is returned with an empty header string.
    """
    parts = _SECTION_HEADER_RE.split(text)
    # parts alternates: [pre-content, header1, body1, header2, body2, ...]
    sections: List[Tuple[str, str]] = []
    i = 0
    while i < len(parts):
        if _SECTION_HEADER_RE.match(parts[i]):
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            sections.append((header, body))
            i += 2
        else:
            # Pre-header content (rare).
            if parts[i].strip():
                sections.append(("", parts[i]))
            i += 1
    return sections


def _reassemble(sections: List[Tuple[str, str]]) -> str:
    """Re-join (header, body) pairs into markdown text."""
    parts: List[str] = []
    for header, body in sections:
        if header:
            parts.append(header)
        parts.append(body)
    return "".join(parts).strip()


def _section_title(header: str) -> str:
    """Extract the plain title from a ``## Foo Bar`` header line."""
    m = re.match(r"^##\s+(.+)$", header.strip())
    return m.group(1).strip() if m else header.strip()


def _split_sentences(body: str) -> List[str]:
    """Split section body into sentences."""
    if not body or not body.strip():
        return []
    raw = re.split(r"(?<=[.!?])\s+", body.strip())
    return [s.strip() for s in raw if s.strip()]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def count_words_by_section(text: str) -> Dict[str, int]:
    """Count words per section using ``## `` header splitting.

    Keys are section titles (e.g. ``"Executive Summary"``).
    Content before any header is keyed as ``"_preamble"`` (usually empty).
    Compatible with ``eval_harness.extract_section_body()``.
    """
    sections = _split_into_sections(text)
    result: Dict[str, int] = {}
    for header, body in sections:
        title = _section_title(header) if header else "_preamble"
        result[title] = count_words(body)
    return result


def identify_adjustment_sections(
    section_counts: Dict[str, int],
    section_budgets: Dict[str, int],
) -> Tuple[List[str], List[str]]:
    """Return (sections_to_trim, sections_to_expand) sorted by deviation.

    *sections_to_trim* — sections whose word count exceeds their budget,
    sorted largest overshoot first.

    *sections_to_expand* — sections whose word count is below their budget,
    sorted largest undershoot first.

    Sections not present in *section_budgets* are ignored.
    """
    to_trim: List[Tuple[str, int]] = []
    to_expand: List[Tuple[str, int]] = []

    for section, budget in section_budgets.items():
        actual = section_counts.get(section, 0)
        if actual > budget:
            to_trim.append((section, actual - budget))
        elif actual < budget:
            to_expand.append((section, budget - actual))

    # Sort by deviation descending (biggest overshoot / undershoot first).
    to_trim.sort(key=lambda t: t[1], reverse=True)
    to_expand.sort(key=lambda t: t[1], reverse=True)

    return (
        [name for name, _ in to_trim],
        [name for name, _ in to_expand],
    )


# ---------------------------------------------------------------------------
# Protected sections — never remove sentences from these
# ---------------------------------------------------------------------------

_PROTECTED_SECTIONS = frozenset({
    "Key Metrics",
    "Financial Health Rating",
})

# Sections where trimming is least harmful (trimmed first).
# Order: least narratively critical → most.
_TRIM_PRIORITY = [
    "Key Metrics",           # data block — trimming is unusual but harmless
    "Risk Factors",
    "Management Discussion & Analysis",
    "Financial Performance",
    "Executive Summary",
    "Closing Takeaway",      # verdict — trim last
]


# ---------------------------------------------------------------------------
# Expansion signal — pipeline signals, no filler phrases inserted
# ---------------------------------------------------------------------------

def needs_regen_to_expand(text: str, target: int, tolerance: int = 10) -> bool:
    """Return True when *text* is under-target and needs LLM regeneration.

    The pipeline should use this signal to trigger a re-generation pass
    rather than injecting generic filler phrases.
    """
    if not text or target <= 0:
        return False
    lower = max(0, target - tolerance)
    return count_words(text) < lower


# ---------------------------------------------------------------------------
# Clean ending — sentence-boundary truncation without mid-sentence cuts
# ---------------------------------------------------------------------------

# Patterns that indicate a dangling/incomplete clause at line end.
_DANGLING_PATTERNS: Tuple[str, ...] = (
    r",\s*$",        # trailing comma
    r"\bbut\s*$",    # trailing "but"
    r"\band\s*$",    # trailing "and"
    r"\bor\s*$",     # trailing "or"
    r"\bthat\s*$",   # trailing "that"
    r"\bwith\s*$",   # trailing "with"
    r"\bwhich\s*$",  # trailing "which"
    r"\bwhere\s*$",  # trailing "where"
    r"\bif\s*$",     # trailing "if"
)


def _has_dangling_ending(text: str) -> bool:
    """Return True if *text* ends with an incomplete clause."""
    stripped = text.rstrip()
    for pattern in _DANGLING_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    return False


def clean_ending(text: str, target_words: int, tolerance: int = 10) -> str:
    """Truncate *text* to a sentence boundary at or before *target_words + tolerance*.

    Rules:
    - If word count is within [target - tolerance, target + tolerance]: return as-is.
    - If over-target + tolerance: find the last sentence that ends at or before
      the upper bound (target + tolerance words), truncate there.
    - Never cut mid-sentence; never leave a dangling clause.
    - Preserve markdown section structure when the text is sectioned.
    - If under-target (but within tolerance): return as-is — do NOT pad.
    """
    if not text or target_words <= 0:
        return text

    wc = count_words(text)
    upper = target_words + tolerance

    if wc <= upper:
        return text  # within band or under target — no truncation

    sections = _split_into_sections(text)
    if any(header for header, _body in sections):
        return trim_to_target(text, target=target_words, tolerance=tolerance)

    # Split into sentences and rebuild up to the upper limit.
    sentences_raw = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: List[str] = []
    running_words = 0

    for sentence in sentences_raw:
        sentence_wc = count_words(sentence)
        if running_words + sentence_wc > upper:
            break
        # Skip dangling sentences at the cut point
        if not sentence.rstrip().endswith((".", "!", "?")):
            break
        if _has_dangling_ending(sentence):
            break
        kept.append(sentence)
        running_words += sentence_wc

    if not kept:
        # Fallback: return first sentence to avoid empty output
        if sentences_raw:
            first = sentences_raw[0]
            if first.rstrip().endswith((".", "!", "?")):
                return first
        return text[:200]  # last resort hard truncate

    result = " ".join(kept)
    if _has_dangling_ending(result):
        # Remove the last sentence if it leaves a dangling end
        kept = kept[:-1]
        result = " ".join(kept) if kept else text
    return result


# ---------------------------------------------------------------------------
# Trim: remove sentences to reduce word count
# ---------------------------------------------------------------------------

def trim_to_target(
    text: str,
    target: int,
    tolerance: int = 10,
) -> str:
    """Deterministically trim *text* to within ±*tolerance* of *target* words.

    Strategy:
    1. Compute per-section word counts.
    2. Find the section with the most words relative to its share (or the
       least important section).  Protected sections (Key Metrics, Health
       Rating) are skipped.
    3. Remove sentences from the *end* of that section first.
    4. Repeat until total word count is within the band.
    5. Never remove section headers.

    Returns the trimmed text.  If the text is already within band, it is
    returned unchanged.
    """
    upper = target + tolerance

    current_wc = count_words(text)
    if current_wc <= upper:
        return text  # already within band (or under)

    sections = _split_into_sections(text)
    if not sections:
        return text

    # Build a mutable list of (header, sentences) per section.
    parsed: List[Tuple[str, List[str]]] = []
    for header, body in sections:
        sentences = _split_sentences(body)
        parsed.append((header, sentences))

    # Iteratively remove one sentence at a time from the least-critical
    # section with the most relative overshoot.
    max_iterations = current_wc  # safety bound
    for _ in range(max_iterations):
        # Rebuild text and recount.
        rebuilt = _reassemble_from_parsed(parsed)
        wc = count_words(rebuilt)
        if wc <= upper:
            return rebuilt.strip()

        # Find the best section to trim.
        trimmed = False
        for section_title in _trim_priority_order(parsed):
            idx = _find_section_index(parsed, section_title)
            if idx is None:
                continue
            header, sentences = parsed[idx]
            title = _section_title(header)
            if title in _PROTECTED_SECTIONS:
                continue
            if len(sentences) <= 1:
                continue  # keep at least one sentence
            # Remove the last sentence.
            parsed[idx] = (header, sentences[:-1])
            trimmed = True
            break

        if not trimmed:
            break  # nothing left to trim

    return _reassemble_from_parsed(parsed).strip()


def _trim_priority_order(
    parsed: List[Tuple[str, List[str]]],
) -> List[str]:
    """Return section titles sorted by trim priority.

    Sections with the most sentences are trimmed first (among non-protected
    sections), breaking ties by the canonical _TRIM_PRIORITY order.
    """
    priority_rank = {name: i for i, name in enumerate(_TRIM_PRIORITY)}

    candidates: List[Tuple[str, int, int]] = []
    for header, sentences in parsed:
        title = _section_title(header)
        if not header or title in _PROTECTED_SECTIONS:
            continue
        rank = priority_rank.get(title, len(_TRIM_PRIORITY))
        candidates.append((title, -len(sentences), rank))

    # Sort: most sentences first (negative so descending), then trim priority.
    candidates.sort(key=lambda t: (t[1], t[2]))
    return [name for name, _, _ in candidates]


# ---------------------------------------------------------------------------
# Expand: add sentences to increase word count
# ---------------------------------------------------------------------------

def expand_to_target(
    text: str,
    target: int,
    tolerance: int = 10,
    expansion_phrases: Optional[List[str]] = None,
) -> str:
    """Deterministically pad *text* to reach within ±*tolerance* of *target*.

    Strategy:
    1. Identify the section furthest below its proportional budget.
    2. Insert bridge phrases from *expansion_phrases* after the last sentence
       in that section.
    3. Repeat with the next section until within band.
    4. Never modify section headers or the Key Metrics data block.

    Returns the expanded text.  If already within band, or no phrases are
    provided, returned unchanged.

    Note: The preferred pipeline approach for under-target output is to use
    ``needs_regen_to_expand()`` to signal LLM regeneration rather than
    inserting filler phrases.
    """
    lower = target - tolerance
    phrases = list(expansion_phrases or [])

    current_wc = count_words(text)
    if current_wc >= lower:
        return text  # already within band (or over)

    sections = _split_into_sections(text)
    if not sections:
        return text

    parsed: List[Tuple[str, List[str]]] = []
    for header, body in sections:
        sentences = _split_sentences(body)
        parsed.append((header, sentences))

    phrase_idx = 0
    max_iterations = target  # safety bound

    for _ in range(max_iterations):
        rebuilt = _reassemble_from_parsed(parsed)
        wc = count_words(rebuilt)
        if wc >= lower:
            return rebuilt.strip()

        if phrase_idx >= len(phrases):
            break  # exhausted expansion phrases

        # Find the section with the most room to grow.
        best_idx: Optional[int] = None
        best_room = 0
        for i, (header, sentences) in enumerate(parsed):
            title = _section_title(header)
            if not header or title in _PROTECTED_SECTIONS:
                continue
            # Use sentence count as a proxy for "room" — sections with
            # fewer sentences have more room for an added bridge phrase.
            # Among sections with equal sentences, prefer earlier sections
            # in the canonical order.
            section_wc = sum(count_words(s) for s in sentences)
            # Prefer sections with the lowest word count (most under-filled).
            room = -section_wc  # negative so lower wc → higher room
            if best_idx is None or room > best_room:
                best_room = room
                best_idx = i

        if best_idx is None:
            break

        header, sentences = parsed[best_idx]
        title = _section_title(header)

        # Don't add expansion phrases to Closing Takeaway if it already
        # has 3+ sentences (to respect the validator's sentence cap).
        if title == "Closing Takeaway" and len(sentences) >= 3:
            # Try the next best section instead.
            # Mark this one as protected temporarily and recurse.
            # Simpler: just skip and try next phrase.
            phrase_idx += 1
            continue

        sentences.append(phrases[phrase_idx])
        parsed[best_idx] = (header, sentences)
        phrase_idx += 1

    return _reassemble_from_parsed(parsed).strip()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _reassemble_from_parsed(
    parsed: List[Tuple[str, List[str]]],
) -> str:
    """Rebuild markdown from (header, sentences) pairs."""
    parts: List[str] = []
    for header, sentences in parsed:
        if header:
            parts.append(header)
        body = " ".join(sentences) if sentences else ""
        # Ensure blank line between header and body, and between sections.
        if body:
            parts.append(f"\n{body}\n")
        else:
            parts.append("\n")
    return "\n".join(parts).strip()


def _find_section_index(
    parsed: List[Tuple[str, List[str]]],
    title: str,
) -> Optional[int]:
    """Find the index of a section by title (case-insensitive)."""
    lower = title.lower()
    for i, (header, _) in enumerate(parsed):
        if _section_title(header).lower() == lower:
            return i
    return None
