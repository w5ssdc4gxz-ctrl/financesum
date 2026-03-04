"""Repetition detection utilities for filing summary quality control.

Detects:
- Duplicate or near-duplicate sentences
- Repeated 8-12 word n-grams (excluding stopword-only sequences)
- Repeated trailing phrases across paragraphs
- Near-duplicate paragraphs via cosine similarity
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import sqrt
from typing import Dict, List, Tuple

# Common English stopwords for n-gram filtering
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "that", "this",
    "these", "those", "it", "its", "as", "not", "no", "nor", "so", "yet",
    "both", "either", "each", "all", "any", "such", "than", "then", "when",
    "where", "which", "who", "whom", "how", "what", "there", "their",
    "they", "we", "our", "us", "he", "she", "him", "her", "his", "its",
    "also", "just", "more", "most", "other", "some", "into", "over",
    "after", "while", "about", "through", "during", "before", "above",
    "between", "out", "off", "up", "down", "if", "because", "until",
    "against", "among", "still", "only", "very", "too", "even",
})


@dataclass
class RepetitionReport:
    """Result of a repetition check."""

    duplicate_sentences: List[str] = field(default_factory=list)
    repeated_ngrams: List[str] = field(default_factory=list)
    repeated_trailing_phrases: List[str] = field(default_factory=list)
    similar_paragraph_pairs: List["ParagraphSimilarity"] = field(default_factory=list)
    affected_sections: List[str] = field(default_factory=list)
    violation_types: List[str] = field(default_factory=list)
    has_violations: bool = False


@dataclass(frozen=True)
class ParagraphSimilarity:
    """A near-duplicate paragraph pair."""

    section_a: str
    section_b: str
    score: float
    paragraph_a: str
    paragraph_b: str


def _tokenize(text: str) -> List[str]:
    """Lowercase word-token extraction."""
    return re.findall(r"\b[a-z0-9]+\b", text.lower())


def _is_stopword_only(tokens: List[str]) -> bool:
    """Return True when every token in *tokens* is a stopword."""
    return bool(tokens) and all(t in _STOPWORDS for t in tokens)


def _split_sections(text: str) -> List[Tuple[str, str]]:
    pattern = re.compile(r"##\s+(.+?)\s*\n(.*?)(?=\n##\s+|\Z)", re.DOTALL)
    sections = [
        ((match.group(1) or "").strip(), (match.group(2) or "").strip())
        for match in pattern.finditer(text or "")
    ]
    if sections:
        return sections
    stripped = str(text or "").strip()
    return [("_full_text", stripped)] if stripped else []


def _sections_containing_phrase(text: str, phrase: str) -> List[str]:
    lower_phrase = str(phrase or "").strip().lower()
    if not lower_phrase:
        return []
    affected: List[str] = []
    for section_name, body in _split_sections(text):
        if lower_phrase in (body or "").lower():
            affected.append(section_name)
    return affected


def _term_frequency(tokens: List[str]) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    for token in tokens:
        if token in _STOPWORDS:
            continue
        freq[token] = freq.get(token, 0) + 1
    return freq


def _cosine_similarity(lhs: Dict[str, int], rhs: Dict[str, int]) -> float:
    if not lhs or not rhs:
        return 0.0
    shared = set(lhs.keys()) & set(rhs.keys())
    dot = sum(lhs[key] * rhs[key] for key in shared)
    lhs_mag = sqrt(sum(value * value for value in lhs.values()))
    rhs_mag = sqrt(sum(value * value for value in rhs.values()))
    if lhs_mag <= 0.0 or rhs_mag <= 0.0:
        return 0.0
    return dot / (lhs_mag * rhs_mag)


def detect_repeated_ngrams(text: str, n: int = 10) -> List[str]:
    """Return n-grams (default n=10) that appear 2+ times in *text*.

    Excludes n-grams composed entirely of stopwords.
    """
    tokens = _tokenize(text)
    if len(tokens) < n:
        return []

    counts: dict[str, int] = {}
    for i in range(len(tokens) - n + 1):
        gram = tuple(tokens[i : i + n])
        if _is_stopword_only(list(gram)):
            continue
        key = " ".join(gram)
        counts[key] = counts.get(key, 0) + 1

    return [k for k, v in counts.items() if v >= 2]


def detect_duplicate_sentences(text: str) -> List[str]:
    """Return sentences that appear 2+ times (normalized comparison)."""
    raw_sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    seen: dict[str, str] = {}
    duplicates: List[str] = []

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        # Normalize: lowercase, strip punctuation/whitespace
        norm = re.sub(r"[^a-z0-9 ]+", "", s.lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm:
            continue
        if norm in seen:
            if s not in duplicates:
                duplicates.append(s)
        else:
            seen[norm] = s

    return duplicates


def detect_repeated_trailing_phrases(text: str) -> List[str]:
    """Return 4–6-word trailing phrases from sentences that appear in 2+ sentences."""
    # Split into sentences across all paragraphs
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    phrase_counts: dict[str, int] = {}
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = sent.split()
        # Collect trailing 4-to-6 word slices
        for n in range(4, 7):
            if len(words) >= n:
                phrase = re.sub(r"[^\w\s]", "", " ".join(words[-n:]).lower()).strip()
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

    return [phrase for phrase, count in phrase_counts.items() if phrase and count >= 2]


def detect_similar_paragraphs(
    text: str,
    *,
    threshold: float = 0.88,
) -> List[ParagraphSimilarity]:
    """Return paragraph pairs whose cosine similarity crosses *threshold*."""
    paragraphs: List[Tuple[str, str]] = []
    for section_name, body in _split_sections(text):
        for paragraph in re.split(r"\n\s*\n+", body or ""):
            clean = paragraph.strip()
            if clean:
                paragraphs.append((section_name, clean))

    findings: List[ParagraphSimilarity] = []
    for idx, (section_a, para_a) in enumerate(paragraphs):
        tokens_a = _tokenize(para_a)
        if len(tokens_a) < 8:
            continue
        freq_a = _term_frequency(tokens_a)
        if not freq_a:
            continue
        for section_b, para_b in paragraphs[idx + 1 :]:
            tokens_b = _tokenize(para_b)
            if len(tokens_b) < 8:
                continue
            freq_b = _term_frequency(tokens_b)
            if not freq_b:
                continue
            score = _cosine_similarity(freq_a, freq_b)
            if score >= float(threshold):
                findings.append(
                    ParagraphSimilarity(
                        section_a=section_a,
                        section_b=section_b,
                        score=score,
                        paragraph_a=para_a,
                        paragraph_b=para_b,
                    )
                )
    return findings


def check_repetition(text: str) -> RepetitionReport:
    """Run all repetition checks and return a consolidated report."""
    dup_sents = detect_duplicate_sentences(text)
    repeated_ngrams: List[str] = []
    for n in range(8, 13):
        repeated_ngrams.extend(detect_repeated_ngrams(text, n=n))
    repeated_ngrams = list(dict.fromkeys(repeated_ngrams))
    repeated_trailing = detect_repeated_trailing_phrases(text)
    similar_paragraph_pairs = detect_similar_paragraphs(text)

    affected_sections: List[str] = []
    for sentence in dup_sents:
        affected_sections.extend(_sections_containing_phrase(text, sentence))
    for ngram in repeated_ngrams:
        affected_sections.extend(_sections_containing_phrase(text, ngram))
    for trailing in repeated_trailing:
        affected_sections.extend(_sections_containing_phrase(text, trailing))
    for pair in similar_paragraph_pairs:
        affected_sections.extend([pair.section_a, pair.section_b])

    violation_types: List[str] = []
    if dup_sents:
        violation_types.append("duplicate_sentences")
    if repeated_ngrams:
        violation_types.append("repeated_ngrams")
    if repeated_trailing:
        violation_types.append("repeated_trailing_phrases")
    if similar_paragraph_pairs:
        violation_types.append("similar_paragraphs")

    has_violations = bool(violation_types)
    return RepetitionReport(
        duplicate_sentences=dup_sents,
        repeated_ngrams=repeated_ngrams,
        repeated_trailing_phrases=repeated_trailing,
        similar_paragraph_pairs=similar_paragraph_pairs,
        affected_sections=sorted(dict.fromkeys(section for section in affected_sections if section)),
        violation_types=violation_types,
        has_violations=has_violations,
    )


def strip_repeated_sentences(text: str) -> str:
    """Remove duplicate sentences from *text*, keeping the first occurrence.

    Sentence order and section structure (markdown headers) are preserved.
    """
    raw_sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    seen: set[str] = set()
    kept: List[str] = []

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        norm = re.sub(r"[^a-z0-9 ]+", "", s.lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm:
            kept.append(s)
            continue
        if norm not in seen:
            seen.add(norm)
            kept.append(s)

    return " ".join(kept)
