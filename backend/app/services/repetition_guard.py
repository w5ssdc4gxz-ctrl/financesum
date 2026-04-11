"""Repetition detection utilities for filing summary quality control.

Detects:
- Duplicate or near-duplicate sentences
- Repeated 5-12 word n-grams (excluding stopword-only sequences)
- Repeated trailing phrases across paragraphs
- Near-duplicate paragraphs via cosine similarity
- Filler phrases, padding sentences, and self-referential meta-language
- Cross-section dollar figure repetition (same $figure in 3+ sections)
- Incoherent section endings (fragments, broken sentences)
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
    repeated_leadins: List[str] = field(default_factory=list)
    repeated_ngrams: List[str] = field(default_factory=list)
    repeated_trailing_phrases: List[str] = field(default_factory=list)
    placeholder_number_artifacts: List[str] = field(default_factory=list)
    similar_paragraph_pairs: List["ParagraphSimilarity"] = field(default_factory=list)
    filler_phrases: List[str] = field(default_factory=list)
    cross_section_dollar_figures: List["DollarFigureRepetition"] = field(default_factory=list)
    incoherent_endings: List[str] = field(default_factory=list)
    analyst_fog_phrases: List[str] = field(default_factory=list)
    boilerplate_quotes: List[str] = field(default_factory=list)
    affected_sections: List[str] = field(default_factory=list)
    violation_types: List[str] = field(default_factory=list)
    has_violations: bool = False


@dataclass(frozen=True)
class DollarFigureRepetition:
    """A dollar figure that appears in too many sections."""

    figure: str
    sections: List[str]
    count: int


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


_REPEATED_LEADIN_STEMS = (
    "that leaves",
    "this leaves",
    "what matters now",
    "the key issue",
    "the next question is",
)

_PLACEHOLDER_NUMBER_ARTIFACT_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\s*that figure\b", re.IGNORECASE),
    re.compile(r"\bthat figure\s*%", re.IGNORECASE),
    re.compile(
        r"\b(?:that figure|the cited amount|that percentage level)\b"
        r"(?:\s+(?:million|billion|thousand|m|b|k))?",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![\d$€£])\b(?:million|billion|thousand)\s+(?:of|in|to|and)\b", re.IGNORECASE),
)


def detect_repeated_leadins(text: str) -> List[str]:
    """Return repeated rhetorical lead-in stems used across multiple sentences."""
    stem_counts: dict[str, int] = {}
    for _section_name, body in _split_sections(text):
        raw_sentences = re.split(r"(?<=[.!?])\s+", str(body or "").strip())
        for sentence in raw_sentences:
            normalized = re.sub(r"[^a-z0-9 ]+", " ", sentence.lower()).strip()
            normalized = re.sub(r"\s+", " ", normalized)
            if not normalized:
                continue
            for stem in _REPEATED_LEADIN_STEMS:
                if normalized.startswith(stem):
                    stem_counts[stem] = stem_counts.get(stem, 0) + 1
                    break
    return [stem for stem, count in stem_counts.items() if count >= 2]


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


def detect_placeholder_number_artifacts(text: str) -> List[str]:
    """Return obvious placeholder-number hybrids or scale-word remnants."""
    findings: List[str] = []
    for _section_name, body in _split_sections(text):
        raw_sentences = re.split(r"(?<=[.!?])\s+", str(body or "").strip())
        for sentence in raw_sentences:
            clean = str(sentence or "").strip()
            if not clean:
                continue
            if any(pattern.search(clean) for pattern in _PLACEHOLDER_NUMBER_ARTIFACT_PATTERNS):
                findings.append(clean)
    return findings


def detect_similar_paragraphs(
    text: str,
    *,
    threshold: float = 0.80,
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


_FILLER_PHRASES = [
    "and that matters",
    "and that still matters",
    "and that remains decisive",
    "and that still decides the call",
    "which remains the real test",
    "which remains the hinge",
    "and that still needs proof",
    "which remains the execution test",
    "and that remains the trigger",
    "that remains the trigger",
    "that remains the real trigger",
    "and that remains the hinge",
    "that remains the hinge",
    "that remains the core hinge",
    "that remains the near-term hinge",
    "that remains decisive",
    "that remains the test",
    "that remains the execution test",
    "that remains important",
    "still anchors how much",
    "the cleanest thread tying the story together",
    "the company-specific proof point behind the thesis",
    "the rating still depends on whether",
    "which is the key issue for the",
]

_FILLER_SENTENCES = [
    "execution matters",
    "execution still matters",
    "execution needs proof",
    "durability matters",
    "durability still matters",
    "durability still matters here",
    "durability still anchors the rating",
    "still decisive",
    "still the hinge",
    "that still matters",
    "watch that path",
    "watch that path closely",
    "watch that transmission path closely",
    "watch that transmission path carefully",
    "that remains the trigger",
    "that remains the hinge",
    "management needs proof",
    "management still needs proof",
    "management still needs execution proof",
    "inventory confirms",
    "the better",
    "the rating still depends",
    "execution credibility now depends",
    "financial footing weakens first",
    "the financial footing weakens",
    "leadership sequences investments",
]

_VAGUE_ENDING_PHRASES = [
    "if that execution slips",
    "if that changes",
    "remains to be seen",
    "time will tell",
    "only time will tell",
    "the jury is still out",
    "that remains the question",
    "we will have to wait and see",
    "the market will decide",
    "if things change",
    "should that shift",
    "if the environment shifts",
    "if conditions deteriorate",
    "that is the real question",
    "how that plays out",
    "what happens next matters",
    "what happens if that slips",
]

# Patterns that detect garbled / incomplete sentences produced by LLM padding
_GARBLED_SENTENCE_RE = re.compile(
    r"(?:"
    # "a The increase" — lowercase article immediately followed by capitalized phrase
    r"\b[a-z]{1,3}\s+[A-Z][a-z]+\s+[A-Z][a-z]"
    # "{CompanyName}s matters." / "{CompanyName}s still anchors" — possessive-free padding
    r"|[A-Z][a-z]{2,}s\s+(?:matters?|still\s+(?:anchors?|decides?|needs?)|depends?|weakens?)\b"
    # "Prove Investments." / "Ensure Durability." — imperative + capitalized noun as sentence
    r"|\.\s*(?:Prove|Ensure|Support|Confirm|Reinforce)\s+[A-Z]\w+\."
    # "{X} remains the proof point" / "{X} remains the company-specific"
    r"|\bremains the (?:proof point|company-specific|real test|execution test)\b"
    # "keeps supporting financial resilience" / "stops reinforcing durability"
    r"|\b(?:keeps|stops)\s+(?:supporting|reinforcing|anchoring|shaping)\s+(?:financial\s+)?(?:resilience|durability|credibility)\b"
    # "leadership sequences Investments"
    r"|\b(?:leadership|management)\s+sequences?\s+[A-Z]\w+\b"
    # Abstract circular filler
    r"|\bfinancial footing weakens\b"
    r"|\b(?:execution|rating|financial)\s+(?:credibility|footing|resilience)\s+(?:now\s+)?depends on (?:how|whether)\b"
    r"|\brating still depends on whether\b"
    r")",
)

_SELF_REF_RE = re.compile(
    r"(?:"
    r"[Tt]his sets up the \w[\w\s]* section"
    r"|[Aa]s (?:discussed|noted|tracked|outlined|covered) in the [\w\s]+ (?:section|above|below)"
    r"|[Tt]he Key Metrics (?:below |above |section )?show"
    r"|[Tt]hese [\w\s]* (?:are|is) tracked in"
    r"|[Aa]s the [\w\s]+ section (?:shows|details|explores|covers)"
    r"|(?:the )?more useful read[- ]through belongs in the [\w\s]+"
    r"|belongs in the (?:Executive Summary|Financial Performance|Management Discussion|MD&A|Risk Factors|Closing Takeaway)"
    r"|which is the key issue for the [\w\s]+"
    r"|the key (?:issue|question|point) for the (?:Executive Summary|Financial Performance|MD&A|Risk Factors|Closing Takeaway|Financial Health Rating)"
    r")",
    re.IGNORECASE,
)


def detect_filler_phrases(text: str) -> List[str]:
    """Detect filler padding phrases, filler sentences, and self-referential text.

    Returns a list of matched filler strings found in *text*.
    """
    if not text:
        return []
    lower_text = text.lower()
    found: List[str] = []

    # Check for filler tail-clause phrases
    for phrase in _FILLER_PHRASES:
        if phrase in lower_text:
            found.append(phrase)

    # Check for filler standalone sentences
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for sent in sentences:
        norm = sent.strip().rstrip(".!?").strip().lower()
        if norm in _FILLER_SENTENCES:
            found.append(sent.strip())

    # Check for self-referential structure text
    for m in _SELF_REF_RE.finditer(text):
        found.append(m.group(0))

    # Check for garbled / incomplete sentences (LLM padding artifacts)
    found.extend(find_garbled_sentences(text))

    # Check for vague ending phrases in section-final sentences
    for _section_name, body in _split_sections(text):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(body or "").strip()) if s.strip()]
        if not sentences:
            continue
        last_sent_lower = sentences[-1].lower()
        for vague in _VAGUE_ENDING_PHRASES:
            if vague in last_sent_lower:
                found.append(f"Vague ending: {sentences[-1][:80]}")
                break

    return found


def find_garbled_sentences(text: str) -> List[str]:
    """Return full sentences that contain garbled padding artifacts."""
    if not text:
        return []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        if sentence.strip()
    ]
    findings: List[str] = []
    for sentence in sentences:
        if _GARBLED_SENTENCE_RE.search(sentence):
            findings.append(sentence)
    return findings


# ---------------------------------------------------------------------------
# Concept-level repetition detection — synonym-normalized phrase patterns
# ---------------------------------------------------------------------------

_FUND_WORDS = ("funding", "fueling", "supporting", "enabling", "powering")
_BUILD_WORDS = ("buildout", "build-out", "investment", "reinvestment", "expansion", "spending")
_CASHGEN_WORDS = ("cash generation", "cash flow", "free cash flow", "cash conversion")
_MARGIN_WORDS = ("margin strength", "operating leverage", "margin quality", "margin expansion")


def detect_concept_repetitions(text: str, threshold: int = 3) -> List[str]:
    """Detect repeated conceptual phrases (synonym-normalized patterns).

    Returns a list of concept pattern descriptions that appear *threshold*+ times.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    concept_counts: Dict[str, int] = {}
    concept_examples: Dict[str, str] = {}

    for sent in sentences:
        lower = sent.lower()
        # Check for concept pairs: [funding-type word] + [building-type word]
        for fund_word in _FUND_WORDS:
            for build_word in _BUILD_WORDS:
                if fund_word in lower and build_word in lower:
                    key = "fund+build"
                    concept_counts[key] = concept_counts.get(key, 0) + 1
                    concept_examples.setdefault(key, sent.strip()[:80])
        # Check for [cashgen-type word] + [building-type word]
        for cash_word in _CASHGEN_WORDS:
            for build_word in _BUILD_WORDS:
                if cash_word in lower and build_word in lower:
                    key = "cashgen+build"
                    concept_counts[key] = concept_counts.get(key, 0) + 1
                    concept_examples.setdefault(key, sent.strip()[:80])
        # Check for [margin-type word] + [funding-type word]
        for margin_word in _MARGIN_WORDS:
            for fund_word in _FUND_WORDS:
                if margin_word in lower and fund_word in lower:
                    key = "margin+fund"
                    concept_counts[key] = concept_counts.get(key, 0) + 1
                    concept_examples.setdefault(key, sent.strip()[:80])

    return [
        f"Repeated concept '{key}' ({count}x, e.g., '{concept_examples[key]}')"
        for key, count in concept_counts.items()
        if count >= threshold
    ]


# ---------------------------------------------------------------------------
# Analyst fog detection — jargon that sounds sophisticated but says nothing
# ---------------------------------------------------------------------------

_ANALYST_FOG_PHRASES = [
    "underwriting thread",
    "capital absorption",
    "forward visibility constraints",
    "forward visibility",
    "cash drag",
    "the real frame for",
    "underwriting call",
    "transmission path",
    "underwriting setup",
    "the setup remains",
    "the cleanest read",
    "visibility inflection",
    "monetization runway",
    "the next proof point is",
    "the question now is",
    "the underwriting call",
    "cash conversion optionality",
    "margin absorption",
    "operating leverage thesis",
    "that is the lens",
    "the thesis breaks if",
    "the underwriting still works",
    "the underwriting case",
    "the underwriting case holds if",
    "growth visibility",
    "earnings power translation",
    "capital deployment thesis",
    "balance sheet optionality",
    "the tension resolves",
    "the golden thread",       # meta — LLM leaking prompt instructions
    "the central tension",     # meta — LLM leaking prompt instructions
]


def detect_analyst_fog(text: str) -> List[str]:
    """Detect analyst fog jargon phrases that communicate nothing.

    Returns a list of matched fog phrases found in *text*.
    """
    if not text:
        return []
    lower_text = text.lower()
    return [phrase for phrase in _ANALYST_FOG_PHRASES if phrase in lower_text]


# ---------------------------------------------------------------------------
# Boilerplate quote detection — legal/accounting quotes with no insight
# ---------------------------------------------------------------------------

_BOILERPLATE_QUOTE_PATTERNS = [
    re.compile(r"investments?\s+with\s+maturities?\s+beyond", re.IGNORECASE),
    re.compile(r"may\s+be\s+classified\s+as\s+short[- ]term\s+based\s+on", re.IGNORECASE),
    re.compile(
        r"forward[- ]looking\s+statements?\s+(?:involve|are\s+subject\s+to|may\s+differ)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:actual\s+results?\s+may\s+differ|no\s+assurance\s+can\s+be\s+given)",
        re.IGNORECASE,
    ),
    re.compile(
        r"in\s+accordance\s+with\s+(?:gaap|generally\s+accepted)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:we|the\s+company)\s+(?:adopted?|implemented?)\s+(?:asu|asc|accounting\s+standard)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:fair\s+value|carrying\s+(?:value|amount))\s+(?:of|is\s+determined)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:federal\s+foreign\s+tax\s+credits?|foreign\s+tax\s+credits?|"
        r"excess\s+tax\s+benefits?|effective\s+tax\s+rate|deferred\s+tax|"
        r"valuation\s+allowance)",
        re.IGNORECASE,
    ),
]


def detect_boilerplate_quotes(text: str) -> List[str]:
    """Detect boilerplate legal/accounting quotes that add no analytical value.

    Scans for direct quotes (text in double quotes) that match known
    boilerplate patterns.  Returns the matched quote text.
    """
    if not text:
        return []
    findings: List[str] = []
    for match in re.finditer(r'["\u201c]([^"\u201d\n]{15,300})["\u201d]', text):
        quote_text = match.group(1).strip()
        for pattern in _BOILERPLATE_QUOTE_PATTERNS:
            if pattern.search(quote_text):
                findings.append(quote_text)
                break
    return findings


_DOLLAR_FIGURE_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?\s*(?:billion|million|thousand|[BMKbmk])\b"
    r"|\$[\d,]+(?:\.\d+)?(?:\s*(?:billion|million|thousand|[BMKbmk]))?"
)


def _normalize_dollar_figure(raw: str) -> str:
    """Normalize a dollar figure for comparison (strip commas, lowercase)."""
    return re.sub(r"[,\s]+", "", raw.strip()).lower()


def detect_cross_section_dollar_figures(
    text: str,
    *,
    threshold: int = 3,
) -> List[DollarFigureRepetition]:
    """Return dollar figures that appear in *threshold* or more distinct sections.

    Extracts $-prefixed figures from each section, normalizes them, and flags
    any figure found in 3+ sections.
    """
    sections = _split_sections(text)
    if not sections:
        return []

    # Map normalized figure -> set of section names
    figure_sections: Dict[str, List[str]] = {}
    # Keep first raw form for display
    figure_raw: Dict[str, str] = {}

    for section_name, body in sections:
        if section_name == "Key Metrics":
            continue  # Key Metrics is a data block — figures are expected
        seen_in_section: set[str] = set()
        for match in _DOLLAR_FIGURE_RE.finditer(body or ""):
            raw = match.group(0).strip()
            norm = _normalize_dollar_figure(raw)
            if not norm or norm == "$" or len(norm) < 3:
                continue
            if norm in seen_in_section:
                continue
            seen_in_section.add(norm)
            if norm not in figure_sections:
                figure_sections[norm] = []
                figure_raw[norm] = raw
            figure_sections[norm].append(section_name)

    return [
        DollarFigureRepetition(
            figure=figure_raw[norm],
            sections=section_list,
            count=len(section_list),
        )
        for norm, section_list in figure_sections.items()
        if len(section_list) >= threshold
    ]


_INCOHERENT_ENDING_RE = re.compile(
    r"(?:"
    r"[A-Z][a-z]+\s+confirms\.\s*$"
    r"|(?:The|That)\s+better\.\s*$"
    r"|\.\s+The\s+better\.\s*$"
    r")",
    re.MULTILINE,
)

# Filler sentences that are too vague to end a section on.
_FILLER_ENDING_NORMS = frozenset({
    "the better",
    "inventory confirms",
    "that matters",
    "still matters",
    "execution matters",
    "durability matters",
})


def detect_incoherent_endings(text: str) -> List[str]:
    """Detect section endings that are incoherent fragments or broken sentences.

    Checks the last sentence of each section for:
    - Sentences under 3 words (clear fragments)
    - Known incoherent fragment patterns (e.g., "inventory confirms. The better.")
    - Known filler endings that add no analytical value
    """
    sections = _split_sections(text)
    findings: List[str] = []

    for section_name, body in sections:
        if not body or section_name == "Key Metrics":
            continue
        # Get the last sentence
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s.strip()]
        if not sentences:
            continue
        last_sent = sentences[-1]
        words = last_sent.split()

        # Fragment: under 3 content words — clearly broken
        if len(words) <= 2:
            findings.append(f"{section_name}: '{last_sent}'")
            continue

        # Check normalized form against known filler endings
        norm = last_sent.strip().rstrip(".!?").strip().lower()
        if norm in _FILLER_ENDING_NORMS:
            findings.append(f"{section_name}: '{last_sent}'")
            continue

        # Check for known incoherent patterns
        if _INCOHERENT_ENDING_RE.search(last_sent):
            findings.append(f"{section_name}: '{last_sent}'")
            continue

        # Check second-to-last + last sentence for broken pairs
        if len(sentences) >= 2:
            penult = sentences[-2]
            combined = f"{penult} {last_sent}"
            if _INCOHERENT_ENDING_RE.search(combined):
                findings.append(f"{section_name}: '{penult} {last_sent}'")

    return findings


def check_repetition(text: str) -> RepetitionReport:
    """Run all repetition checks and return a consolidated report."""
    dup_sents = detect_duplicate_sentences(text)
    repeated_leadins = detect_repeated_leadins(text)
    repeated_ngrams: List[str] = []
    # Scan n-grams from 5-word (cross-section phrases) up to 12-word
    for n in range(5, 13):
        repeated_ngrams.extend(detect_repeated_ngrams(text, n=n))
    repeated_ngrams.extend(detect_concept_repetitions(text, threshold=3))
    repeated_ngrams = list(dict.fromkeys(repeated_ngrams))
    repeated_trailing = detect_repeated_trailing_phrases(text)
    placeholder_number_artifacts = detect_placeholder_number_artifacts(text)
    similar_paragraph_pairs = detect_similar_paragraphs(text)
    filler = detect_filler_phrases(text)
    cross_section_dollars = detect_cross_section_dollar_figures(text)
    incoherent = detect_incoherent_endings(text)
    analyst_fog = detect_analyst_fog(text)
    boilerplate_quotes = detect_boilerplate_quotes(text)

    affected_sections: List[str] = []
    for sentence in dup_sents:
        affected_sections.extend(_sections_containing_phrase(text, sentence))
    for leadin in repeated_leadins:
        affected_sections.extend(_sections_containing_phrase(text, leadin))
    for ngram in repeated_ngrams:
        affected_sections.extend(_sections_containing_phrase(text, ngram))
    for trailing in repeated_trailing:
        affected_sections.extend(_sections_containing_phrase(text, trailing))
    for artifact in placeholder_number_artifacts:
        affected_sections.extend(_sections_containing_phrase(text, artifact[:60]))
    for pair in similar_paragraph_pairs:
        affected_sections.extend([pair.section_a, pair.section_b])
    for phrase in filler:
        affected_sections.extend(_sections_containing_phrase(text, phrase))
    for dollar_rep in cross_section_dollars:
        affected_sections.extend(dollar_rep.sections)
    for ending in incoherent:
        # Format is "SectionName: 'text'" — extract the section name
        if ": " in ending:
            affected_sections.append(ending.split(": ", 1)[0])
    for fog_phrase in analyst_fog:
        affected_sections.extend(_sections_containing_phrase(text, fog_phrase))
    for bq in boilerplate_quotes:
        # Use first 40 chars of quote for section matching
        affected_sections.extend(_sections_containing_phrase(text, bq[:40]))

    violation_types: List[str] = []
    if dup_sents:
        violation_types.append("duplicate_sentences")
    if repeated_leadins:
        violation_types.append("repeated_leadins")
    if repeated_ngrams:
        violation_types.append("repeated_ngrams")
    if repeated_trailing:
        violation_types.append("repeated_trailing_phrases")
    if placeholder_number_artifacts:
        violation_types.append("placeholder_number_artifacts")
    if similar_paragraph_pairs:
        violation_types.append("similar_paragraphs")
    if filler:
        violation_types.append("filler_phrases")
    if cross_section_dollars:
        violation_types.append("cross_section_dollar_figures")
    if incoherent:
        violation_types.append("incoherent_endings")
    if analyst_fog:
        violation_types.append("analyst_fog")
    if boilerplate_quotes:
        violation_types.append("boilerplate_quotes")

    has_violations = bool(violation_types)
    return RepetitionReport(
        duplicate_sentences=dup_sents,
        repeated_leadins=repeated_leadins,
        repeated_ngrams=repeated_ngrams,
        repeated_trailing_phrases=repeated_trailing,
        placeholder_number_artifacts=placeholder_number_artifacts,
        similar_paragraph_pairs=similar_paragraph_pairs,
        filler_phrases=filler,
        cross_section_dollar_figures=cross_section_dollars,
        incoherent_endings=incoherent,
        analyst_fog_phrases=analyst_fog,
        boilerplate_quotes=boilerplate_quotes,
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
