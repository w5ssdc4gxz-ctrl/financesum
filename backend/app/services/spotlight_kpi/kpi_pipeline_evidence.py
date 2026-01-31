"""Evidence-backed KPI extraction pipeline (2-call).

This replaces the older multi-pass Spotlight KPI pipelines by enforcing a hard rule:
return a KPI ONLY when it can be proven with page-numbered quotes from the document.

The model is required to output strict JSON and to return `selected_kpi: null` when
it cannot provide evidence. The backend then verifies evidence quotes against the
document text (page-scoped for PDFs) and fails closed when verification is not
possible.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .json_parse import parse_json_object
from .pipeline_utils import safe_json_retry_prompt, thinking_config
from .types import SpotlightKpiCandidate


@dataclass
class EvidencePipelineConfig:
    total_timeout_seconds: float = 22.0
    max_upload_bytes: int = 50_000_000  # 50 MB

    max_candidates: int = 5

    pass1_thinking_level: str = "minimal"
    pass1_max_output_tokens: int = 1600
    pass1_timeout_seconds: float = 9.0

    pass2_thinking_level: str = "minimal"
    pass2_max_output_tokens: int = 900
    pass2_timeout_seconds: float = 8.0

    # Optional repair pass to find missing evidence (definition/value) without
    # relaxing verification rules.
    pass3_thinking_level: str = "minimal"
    pass3_max_output_tokens: int = 520
    pass3_timeout_seconds: float = 5.5
    enable_definition_repair: bool = True

    min_confidence: float = 0.7

    # Evidence rules
    require_value_evidence: bool = True
    # IMPORTANT: Many filings disclose an operating metric value but do not explicitly
    # define it. For the UI, we still want an evidence-backed KPI rather than a
    # false "no KPI" state. Definition evidence remains best-effort.
    require_definition_evidence: bool = False


EvidenceType = str
_ALLOWED_EVIDENCE_TYPES: set[str] = {"definition", "value", "context"}


def _strip_unsupported_generation_fields(
    gen_cfg: Dict[str, Any], *, error_text: str
) -> Tuple[Dict[str, Any], List[str]]:
    """Retry shim for mixed Gemini API versions."""
    lowered = (error_text or "").lower()
    removed: List[str] = []
    out = dict(gen_cfg or {})

    if "thinkingconfig" in lowered or "thinkinglevel" in lowered or "thinking" in lowered:
        if "thinkingConfig" in out:
            out.pop("thinkingConfig", None)
            removed.append("thinkingConfig")

    if "responsemimetype" in lowered or "response mime type" in lowered:
        if "responseMimeType" in out:
            out.pop("responseMimeType", None)
            removed.append("responseMimeType")

    if "responseschema" in lowered or "response schema" in lowered:
        if "responseSchema" in out:
            out.pop("responseSchema", None)
            removed.append("responseSchema")

    return out, removed


def _normalize_for_matching(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    # Join common PDF hyphenation artifacts: "cus-\n tomers" -> "customers"
    lowered = re.sub(r"(\w)-\s+(\w)", r"\1\2", lowered)
    normalized = re.sub(r"\s+", " ", lowered.strip())
    normalized = normalized.replace("…", " ")
    normalized = re.sub(r"[^\w\s$%.,0-9]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value is not None:
        try:
            return float(value)
        except Exception:
            return None

    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    is_paren_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").strip()
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[$€£]", "", cleaned).strip()
    cleaned = cleaned.replace("%", "").strip()

    mult = 1.0
    lower = cleaned.lower()

    def _strip_word_suffix(word: str, scale: float) -> bool:
        nonlocal cleaned, mult
        m = re.match(rf"^-?\d+(?:\.\d+)?\s*{re.escape(word)}$", lower)
        if not m:
            return False
        mult = scale
        cleaned = re.sub(rf"\s*{re.escape(word)}$", "", cleaned, flags=re.IGNORECASE).strip()
        return True

    # Words
    if not (
        _strip_word_suffix("billion", 1_000_000_000.0)
        or _strip_word_suffix("million", 1_000_000.0)
        or _strip_word_suffix("thousand", 1_000.0)
        or _strip_word_suffix("trillion", 1_000_000_000_000.0)
    ):
        # Compact suffixes (e.g., 2.3B, 250M, 10K, 1.2T, 3.4bn, 5mn)
        if lower.endswith("bn") and re.match(r"^-?\d+(?:\.\d+)?bn$", lower):
            mult = 1_000_000_000.0
            cleaned = cleaned[:-2]
        elif lower.endswith("mn") and re.match(r"^-?\d+(?:\.\d+)?mn$", lower):
            mult = 1_000_000.0
            cleaned = cleaned[:-2]
        elif lower.endswith("b") and re.match(r"^-?\d+(?:\.\d+)?b$", lower):
            mult = 1_000_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("m") and re.match(r"^-?\d+(?:\.\d+)?m$", lower):
            mult = 1_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("k") and re.match(r"^-?\d+(?:\.\d+)?k$", lower):
            mult = 1_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("t") and re.match(r"^-?\d+(?:\.\d+)?t$", lower):
            mult = 1_000_000_000_000.0
            cleaned = cleaned[:-1]

    try:
        out = float(cleaned) * mult
        return -out if is_paren_negative else out
    except Exception:
        return None


def _extract_number_from_excerpt(excerpt: str) -> Optional[float]:
    text = (excerpt or "").strip()
    if not text:
        return None

    lower = text.lower()
    default_mult = 1.0
    if "in billions" in lower or "billion" in lower:
        default_mult = 1_000_000_000.0
    elif "in millions" in lower or "million" in lower:
        default_mult = 1_000_000.0
    elif "in thousands" in lower or "thousand" in lower:
        default_mult = 1_000.0
    elif "in trillions" in lower or "trillion" in lower:
        default_mult = 1_000_000_000_000.0

    pattern = re.compile(
        r"(?P<neg>\()?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
        r"(?P<suf>%|[bBkKmMtT]|bn|mn|billion|million|thousand|trillion)?\s*(?P<neg2>\))?",
        re.IGNORECASE,
    )

    candidates: List[Tuple[float, int]] = []
    for m in pattern.finditer(text):
        raw = (m.group("num") or "").replace(",", "").strip()
        if not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue

        # Skip likely years
        if 1900 <= val <= 2100 and raw.isdigit() and len(raw) == 4:
            continue

        suf = (m.group("suf") or "").strip().lower()
        mult = default_mult
        if suf in ("b", "bn", "billion"):
            mult = 1_000_000_000.0
        elif suf in ("m", "mn", "million"):
            mult = 1_000_000.0
        elif suf in ("k", "thousand"):
            mult = 1_000.0
        elif suf in ("t", "trillion"):
            mult = 1_000_000_000_000.0

        neg = bool(m.group("neg")) and bool(m.group("neg2"))
        out = val * mult
        out = -out if neg else out

        score = 0
        if "," in (m.group("num") or ""):
            score += 6
        if abs(out) >= 1_000_000:
            score += 6
        elif abs(out) >= 1000:
            score += 5
        elif abs(out) >= 100:
            score += 3
        elif abs(out) >= 10:
            score += 1
        if mult != 1.0:
            score += 2
        if suf == "%":
            score += 1

        candidates.append((out, score))

    if not candidates:
        return None

    candidates.sort(key=lambda t: (t[1], abs(t[0])), reverse=True)
    return float(candidates[0][0])


def _is_generic_financial_metric(name: str) -> bool:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not n:
        return True

    allow_revenue_contexts = (
        "annual recurring revenue",
        "arr",
        "monthly recurring revenue",
        "mrr",
        "net revenue retention",
        "dollar-based net retention",
        "revenue retention",
        "average revenue per",
        "arpu",
        "arpa",
        "arppu",
        "revpar",
    )
    if "revenue" in n and any(tok in n for tok in allow_revenue_contexts):
        return False

    banned_phrases = (
        "total revenue",
        "net revenue",
        "revenue",
        "net income",
        "earnings per share",
        "eps",
        "gross margin",
        "operating margin",
        "ebitda",
        "adjusted ebitda",
        "free cash flow",
        "fcf",
        "capex",
        "capital expenditures",
        "cash",
        "debt",
        # Generic accounting / financial statement line items (NOT operating KPIs)
        "stock-based compensation",
        "stock based compensation",
        "share-based compensation",
        "share based compensation",
        "share-based payment",
        "share based payment",
        "excess tax benefit",
        "excess tax benefits",
        "tax benefit on stock-based compensation",
        "tax benefits on stock-based compensation",
        "excess tax benefit on stock-based compensation",
        "excess tax benefits on stock-based compensation",
        "income tax",
        "income taxes",
        "effective tax rate",
        "deferred tax",
        "valuation allowance",
        "depreciation",
        "amortization",
        "interest expense",
        "interest income",
        "accounts receivable",
        "accounts payable",
        "inventory",
        "working capital",
        "deferred revenue",
        "contract liabilit",
        "goodwill",
        "intangible asset",
    )

    return any(phrase in n for phrase in banned_phrases)


def _build_pass1_prompt(company_name: str, *, max_candidates: int) -> str:
    return f"""
You are an expert financial document analyst. Your job is to find company-specific KPIs / operating metrics from the provided filing for {company_name}.

Constraints:
- Do NOT choose generic metrics (revenue, net income, EPS, gross margin, EBITDA, free cash flow, capex, cash, debt).
- Do NOT choose generic accounting/GAAP line items or policy/tax disclosures (e.g., stock-based compensation, excess tax benefits,
  deferred taxes, effective tax rate, depreciation/amortization, interest expense, working capital, balance sheet line items).
- "Company-specific" means the metric is explicitly disclosed for this company; it does NOT need to be unique across companies.
- The KPI MUST be explicitly mentioned in the filing and MUST include evidence.
- You MUST provide page numbers and short quotes from the document that contain:
  - a reported value (most recent value), and
  - (if present) a definition (how the KPI is calculated / what it means).
- Prefer TRUE operating KPIs that describe the business (usage/volume/capacity), such as: customers/subscribers, orders/transactions,
  units shipped/delivered, bookings/backlog, AUM, GMV/TPV, churn/retention, occupancy/utilization, store count, etc.
- For EACH candidate, `evidence` MUST include at least ONE item with type = "value"
  (must contain the KPI name and a numeric value). Include definition evidence when available.
- If you cannot find a company-specific KPI with evidence, return `candidates: []` and set `failure_reason`.
- Do not guess.

Return up to {int(max_candidates)} candidates, best first.

Output MUST be valid JSON only. No extra text.

Return JSON with this shape:
{{
  "candidates": [
    {{
      "name": "string",
      "why_company_specific": "string",
      "what_it_measures": "string",
      "how_calculated_or_defined": "string",
      "most_recent_value": "string",
      "period": "string",
      "unit": "string",
      "evidence": [
        {{ "page": 1, "quote": "string", "type": "definition|value|context" }}
      ]
    }}
  ],
  "failure_reason": "string|null"
}}
""".strip()


def _build_pass2_prompt(company_name: str, *, candidates: List[Dict[str, Any]]) -> str:
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    return f"""
You are an expert financial document analyst. Your job is to select ONE company-specific KPI / operating metric for {company_name} from the provided candidates.

Hard rules:
- Do NOT choose generic metrics (revenue, net income, EPS, gross margin, EBITDA, free cash flow, capex, cash, debt).
- Do NOT choose generic accounting/GAAP line items (e.g., stock-based compensation, excess tax benefits, taxes, depreciation/amortization,
  interest expense, working capital, balance sheet line items). These are not operating KPIs.
- "Company-specific" means the metric is disclosed for this company; it does NOT need to be unique across companies.
- If you cannot prove the KPI exists with evidence, you MUST return `"selected_kpi": null`.
- Evidence requirements for a non-null KPI:
  - at least ONE "value" evidence quote + page (must contain the KPI name and a numeric value).
  - Include "definition" evidence if the filing explicitly defines the metric. If not defined, still return the KPI if value evidence exists.
- Do not guess.
- Output MUST be valid JSON matching the schema below. No extra text.

CANDIDATES (do not invent new ones):
{candidates_json}

Output JSON EXACTLY with this schema:
{{
  "selected_kpi": {{
    "name": "string",
    "why_company_specific": "string",
    "what_it_measures": "string",
    "how_calculated_or_defined": "string",
    "most_recent_value": "string",
    "period": "string",
    "unit": "string",
    "evidence": [
      {{ "page": 1, "quote": "string", "type": "definition|value|context" }}
    ],
    "confidence": 0.0
  }},
  "failure_reason": "string|null"
}}
""".strip()


def _extract_verification_pages(
    *, file_bytes: bytes, mime_type: str
) -> Tuple[List[str], Optional[str]]:
    """Return (page_texts, reason_if_unverifiable). Page numbers are 1-indexed."""
    mime = str(mime_type or "").lower().strip()
    if mime == "application/pdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=file_bytes, filetype="pdf")
            pages: List[str] = []
            total_chars = 0
            for page in doc:
                try:
                    txt = page.get_text("text") or ""
                except Exception:  # noqa: BLE001
                    txt = ""
                pages.append(txt)
                total_chars += len(txt or "")

            # Heuristic: scanned/no-text PDFs yield almost no extractable text.
            if total_chars < 800:
                return [], "no_text_layer"
            return pages, None
        except Exception:  # noqa: BLE001
            return [], "pdf_text_extract_failed"

    # Non-PDF: treat as a single "page" (page=1)
    try:
        text = file_bytes.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        text = ""
    text = (text or "").strip()
    if not text:
        return [], "no_text_available"
    return [text], None


def _find_pages_for_quote(quote: str, *, page_texts_norm: List[str]) -> List[int]:
    qn = _normalize_for_matching(quote)
    if not qn or len(qn) < 10:
        return []
    qn_compact = qn.replace(" ", "")
    hits: List[int] = []
    for idx, page_norm in enumerate(page_texts_norm):
        if not page_norm:
            continue
        if qn in page_norm:
            hits.append(idx + 1)
            continue
        # Contiguous fallback that ignores whitespace differences.
        # This stays strict (no token-overlap fuzzy matching) to avoid false positives.
        if len(qn_compact) >= 80:
            page_compact = page_norm.replace(" ", "")
            if qn_compact and qn_compact in page_compact:
                hits.append(idx + 1)
    return hits


_DEFINITION_HINTS = (
    "we define",
    "is defined as",
    "are defined as",
    "defined as",
    "definition of",
    "as used in this",
    "we refer to",
    "refers to",
    "we use the term",
    "we calculate",
    "is calculated as",
    "are calculated as",
    "calculated as",
    "calculated by",
    "calculated by dividing",
    "is measured as",
    "are measured as",
    "measured as",
    "is computed as",
    "are computed as",
    "computed as",
    "is derived from",
    "are derived from",
    "is the measure of",
    "is a measure of",
    "are a measure of",
    "we measure",
    "measures",
    "represents",
    "reflects",
    "indicator of",
    "proxy for",
    "means",
    "is the number of",
    "are the number of",
    "is the amount of",
    "are the amount of",
    "are users",
    "are customers",
    "are accounts",
    "are members",
    "are subscribers",
    "are those",
    "consists of",
    "comprised of",
    "is based on",
    "is determined by",
)


_KPI_PAGE_HINTS = (
    # Common headings
    "key metrics",
    "operating metrics",
    "operating metric",
    "key performance indicators",
    "kpis",
    # Very common operational KPI tokens
    "mau",
    "dau",
    "active users",
    "active customers",
    "subscribers",
    "paid subscribers",
    "members",
    "accounts",
    "merchants",
    "arr",
    "mrr",
    "arpu",
    "arpa",
    "arppu",
    "aov",
    "asp",
    "bookings",
    "gross bookings",
    "orders",
    "transactions",
    "gmv",
    "tpv",
    "aum",
    "churn",
    "retention",
    "occupancy",
    "utilization",
    "load factor",
    "revpar",
    "same-store",
    "comparable sales",
    "deliveries",
    "shipments",
    "units",
    "store count",
    "locations",
    "employees",
)


def _score_page_for_kpi_hints(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    score = 0

    # KPI/definition hints
    for tok in _KPI_PAGE_HINTS:
        if tok and tok in lower:
            score += 10
    for tok in _DEFINITION_HINTS:
        if tok and tok in lower:
            score += 6

    # Numeric density hints
    digits = sum(1 for ch in lower if ch.isdigit())
    if digits >= 60:
        score += 10
    elif digits >= 30:
        score += 6
    elif digits >= 15:
        score += 3

    if "%" in text:
        score += 3
    if any(sym in text for sym in ("$", "€", "£")):
        score += 2

    # Prefer pages with "table-ish" structure (common for operating metrics)
    if "\t" in text:
        score += 2
    if text.count("\n") >= 30:
        score += 2

    return int(score)


def _extract_page_snippet(text: str, *, max_chars: int) -> str:
    """Return a compact excerpt of a page, biased toward KPI keyword regions."""
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    if len(normalized) <= int(max_chars):
        return normalized

    lower = normalized.lower()
    hit_positions: List[int] = []
    for tok in _KPI_PAGE_HINTS:
        if not tok:
            continue
        pos = lower.find(tok)
        if pos >= 0:
            hit_positions.append(int(pos))
        if len(hit_positions) >= 4:
            break

    if hit_positions:
        windows: List[str] = []
        for pos in hit_positions[:4]:
            start = max(0, pos - 700)
            end = min(len(normalized), pos + 1100)
            windows.append(normalized[start:end])
        combined = re.sub(r"\s+", " ", " … ".join(windows)).strip()
        if len(combined) <= int(max_chars):
            return combined
        return combined[: int(max_chars)].rstrip() + "..."

    return normalized[: int(max_chars)].rstrip() + "..."


def _select_pages_for_text_pass(
    page_texts: List[str], *, max_pages: int = 12, max_chars_per_page: int = 2600
) -> List[Tuple[int, str]]:
    """Pick a small set of pages that are likely to contain KPI disclosures."""
    if not page_texts:
        return []

    scored: List[Tuple[int, int]] = []
    for idx, text in enumerate(page_texts):
        scored.append((int(idx), _score_page_for_kpi_hints(text or "")))

    scored.sort(key=lambda t: t[1], reverse=True)
    core = [idx for idx, score in scored if score > 0][: max(1, int(max_pages // 2))]
    if not core:
        core = [0]

    selected: List[int] = []
    seen: set[int] = set()

    # Always include the first page for context if available.
    if 0 not in seen:
        selected.append(0)
        seen.add(0)

    # Add top-scoring pages and their neighbors to capture table headers.
    for idx in core:
        for j in (idx - 1, idx, idx + 1):
            if j < 0 or j >= len(page_texts) or j in seen:
                continue
            selected.append(int(j))
            seen.add(int(j))
            if len(selected) >= int(max_pages):
                break
        if len(selected) >= int(max_pages):
            break

    selected.sort()

    out: List[Tuple[int, str]] = []
    for idx in selected[: int(max_pages)]:
        snippet = _extract_page_snippet(page_texts[idx] or "", max_chars=int(max_chars_per_page))
        if not snippet:
            continue
        out.append((int(idx) + 1, snippet))
    return out


def _build_pass1_prompt_from_page_excerpts(
    company_name: str,
    *,
    max_candidates: int,
    pages: List[Tuple[int, str]],
) -> str:
    pages_blob = "\n\n".join([f"PAGE {p}:\n{txt}" for p, txt in pages if txt])
    return f"""
You are an expert financial document analyst. Your job is to find company-specific KPIs / operating metrics from the provided filing for {company_name}.

You are given excerpts from specific pages of the filing. You MUST use ONLY the text provided below.

Constraints:
- Do NOT choose generic metrics (revenue, net income, EPS, gross margin, EBITDA, free cash flow, capex, cash, debt).
- Do NOT choose generic accounting/GAAP line items or policy/tax disclosures (e.g., stock-based compensation, excess tax benefits,
  deferred taxes, effective tax rate, depreciation/amortization, interest expense, working capital, balance sheet line items).
- "Company-specific" means the metric is explicitly disclosed for this company; it does NOT need to be unique across companies.
- The KPI MUST be explicitly mentioned in the provided page text and MUST include evidence.
- You MUST provide page numbers and short quotes from the page text that contain:
  - a reported value (most recent value), and
  - (if present) a definition (how the KPI is calculated / what it means).
- For EACH candidate, `evidence` MUST include at least ONE item with type = "value"
  (must contain the KPI name and a numeric value).
- Quotes MUST be verbatim substrings of the corresponding PAGE text.
- Prefer TRUE operating KPIs that describe the business (usage/volume/capacity), such as: customers/subscribers, orders/transactions,
  units shipped/delivered, bookings/backlog, AUM, GMV/TPV, churn/retention, occupancy/utilization, store count, etc.
- If you cannot find a company-specific KPI with evidence in the provided pages, return `candidates: []` and set `failure_reason`.
- Do not guess.

Return up to {int(max_candidates)} candidates, best first.
Output MUST be valid JSON only. No extra text.

PAGES:
{pages_blob}

Return JSON with this shape:
{{
  "candidates": [
    {{
      "name": "string",
      "why_company_specific": "string",
      "what_it_measures": "string",
      "how_calculated_or_defined": "string",
      "most_recent_value": "string",
      "period": "string",
      "unit": "string",
      "evidence": [
        {{ "page": 1, "quote": "string", "type": "definition|value|context" }}
      ]
    }}
  ],
  "failure_reason": "string|null"
}}
""".strip()


def _looks_like_definition_quote(quote: str) -> bool:
    q = (quote or "").strip().lower()
    if not q:
        return False
    return any(tok in q for tok in _DEFINITION_HINTS)


def _looks_like_value_quote(quote: str) -> bool:
    return _extract_number_from_excerpt(quote or "") is not None


def _kpi_name_variants(name: str) -> List[str]:
    raw = str(name or "").strip()
    if not raw:
        return []

    variants: set[str] = set()
    variants.add(_normalize_for_matching(raw))

    no_parens = re.sub(r"\([^)]*\)", " ", raw).strip()
    if no_parens:
        variants.add(_normalize_for_matching(no_parens))

    for grp in re.findall(r"\(([^)]+)\)", raw):
        for part in re.split(r"[\s,;/]+", grp):
            p = part.strip()
            if not p:
                continue
            variants.add(_normalize_for_matching(p))
            if p.endswith("s") and len(p) > 3:
                variants.add(_normalize_for_matching(p[:-1]))

    # Best-effort acronym (e.g., "Net Dollar Retention" -> "NDR")
    stop = {"of", "and", "the", "per", "to", "in", "for", "on", "a", "an"}
    words = [w for w in re.findall(r"[A-Za-z0-9]+", no_parens or raw) if w]
    sig_words = [w for w in words if w.lower() not in stop]
    if 2 <= len(sig_words) <= 6:
        acronym = "".join(w[0] for w in sig_words if w)
        if len(acronym) >= 2:
            variants.add(_normalize_for_matching(acronym))
            variants.add(_normalize_for_matching(acronym + "s"))

    out = [v for v in variants if v and len(v) >= 3]
    out.sort(key=len, reverse=True)
    return out


def _sanitize_evidence_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    quote = str(item.get("quote") or "").replace("…", "...").strip()
    if quote.endswith("..."):
        quote = quote[:-3].rstrip()
    if quote.startswith("..."):
        quote = quote[3:].lstrip()
    if not quote:
        return None
    ev_type = str(item.get("type") or "").strip().lower() or None
    if ev_type is not None and ev_type not in _ALLOWED_EVIDENCE_TYPES:
        ev_type = None
    page_raw = item.get("page")
    page: Optional[int] = None
    if isinstance(page_raw, int):
        page = page_raw
    elif isinstance(page_raw, float) and float(page_raw).is_integer():
        page = int(page_raw)
    elif isinstance(page_raw, str) and page_raw.strip().isdigit():
        page = int(page_raw.strip())
    if page is not None and page < 1:
        page = None
    return {"page": page, "quote": quote, "type": ev_type}


def _verify_evidence_against_document(
    evidence: List[Any], *, page_texts: List[str], kpi_name: str = ""
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    debug: Dict[str, Any] = {}
    if not page_texts:
        return [], {"reason": "no_pages_text"}

    page_texts_norm = [_normalize_for_matching(t) for t in page_texts]
    name_variants = _kpi_name_variants(kpi_name)
    verified: List[Dict[str, Any]] = []
    for raw in evidence or []:
        ev = _sanitize_evidence_item(raw)
        if not ev:
            continue
        hits = _find_pages_for_quote(ev["quote"], page_texts_norm=page_texts_norm)
        if not hits:
            continue
        page = ev.get("page")
        if isinstance(page, int) and page in hits:
            picked = page
        else:
            picked = hits[0]
        quote = str(ev.get("quote") or "").strip()
        def_like = _looks_like_definition_quote(quote)
        value_like = _looks_like_value_quote(quote)

        inferred_type: str
        if def_like:
            inferred_type = "definition"
        elif value_like:
            inferred_type = "value"
        else:
            inferred_type = "context"

        if inferred_type in ("definition", "value") and name_variants:
            quote_norm = _normalize_for_matching(quote)
            if not any(v and v in quote_norm for v in name_variants):
                # Table rows frequently contain only the numeric value; the KPI name
                # can appear in the row/column header nearby. For multi-page PDFs,
                # accept value-like evidence when the KPI name appears close by on
                # the SAME page. This keeps verification strict (still page-scoped)
                # while avoiding false negatives.
                if inferred_type == "value" and len(page_texts_norm) >= 2:
                    page_norm = page_texts_norm[picked - 1] if (picked - 1) < len(page_texts_norm) else ""
                    if page_norm and any(v and v in page_norm for v in name_variants):
                        pos_quote = page_norm.find(quote_norm)
                        if pos_quote < 0:
                            inferred_type = "value"
                        else:
                            positions: List[int] = []
                            for v in name_variants:
                                if not v:
                                    continue
                                start = 0
                                while True:
                                    pos = page_norm.find(v, start)
                                    if pos < 0:
                                        break
                                    positions.append(int(pos))
                                    start = pos + max(1, len(v))
                                    if len(positions) >= 12:
                                        break
                                if len(positions) >= 12:
                                    break
                            if positions:
                                nearest = min(abs(int(pos_quote) - int(p)) for p in positions)
                                if nearest <= 1200:
                                    inferred_type = "value"
                                else:
                                    inferred_type = "context"
                            else:
                                inferred_type = "context"
                    else:
                        inferred_type = "context"
                else:
                    inferred_type = "context"

        verified.append({"page": picked, "quote": quote, "type": inferred_type})

    debug["verified_evidence_count"] = len(verified)
    return verified, debug


def _dedupe_evidence(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[Tuple[int, str, str]] = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        try:
            page_i = int(it.get("page"))
        except Exception:  # noqa: BLE001
            continue
        typ = str(it.get("type") or "").strip().lower()
        quote = str(it.get("quote") or "").strip()
        if not quote or typ not in _ALLOWED_EVIDENCE_TYPES or page_i < 1:
            continue
        key = (page_i, typ, quote)
        if key in seen:
            continue
        seen.add(key)
        out.append({"page": page_i, "type": typ, "quote": quote})
    return out


def _enrich_evidence_types(
    verified_evidence: List[Dict[str, Any]],
    *,
    require_value: bool,
    require_definition: bool,
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """Infer/duplicate evidence types to satisfy (definition,value) requirements when possible."""
    has_value = any(str(ev.get("type") or "") == "value" for ev in verified_evidence)
    has_definition = any(str(ev.get("type") or "") == "definition" for ev in verified_evidence)

    value_like = [
        ev for ev in verified_evidence if _looks_like_value_quote(str(ev.get("quote") or ""))
    ]
    definition_like = [
        ev
        for ev in verified_evidence
        if _looks_like_definition_quote(str(ev.get("quote") or ""))
    ]

    enriched = list(verified_evidence)
    if require_value and not has_value and value_like:
        best = value_like[0]
        enriched.append(
            {
                "page": int(best.get("page") or 1),
                "quote": str(best.get("quote") or ""),
                "type": "value",
            }
        )

    if require_definition and not has_definition and definition_like:
        best = definition_like[0]
        enriched.append(
            {
                "page": int(best.get("page") or 1),
                "quote": str(best.get("quote") or ""),
                "type": "definition",
            }
        )

    deduped = _dedupe_evidence(enriched)
    has_value = any(str(ev.get("type") or "") == "value" for ev in deduped)
    has_definition = any(str(ev.get("type") or "") == "definition" for ev in deduped)
    return deduped, has_value, has_definition


def extract_kpi_with_evidence_from_file(
    gemini_client: Any,
    *,
    file_bytes: bytes,
    company_name: str,
    mime_type: str,
    config: Optional[EvidencePipelineConfig] = None,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    config = config or EvidencePipelineConfig()
    debug: Dict[str, Any] = {
        "mode": "kpi_pipeline_evidence",
        "company_name": str(company_name or ""),
        "mime_type": str(mime_type or ""),
    }

    if not gemini_client:
        debug["reason"] = "no_gemini_client"
        return None, debug
    if not file_bytes:
        debug["reason"] = "no_file_bytes"
        return None, debug
    if not (company_name or "").strip():
        debug["reason"] = "no_company_name"
        return None, debug
    if not (mime_type or "").strip():
        debug["reason"] = "no_mime_type"
        return None, debug

    if len(file_bytes) > int(config.max_upload_bytes):
        debug["reason"] = "file_too_large"
        debug["file_size_bytes"] = len(file_bytes)
        debug["max_bytes"] = int(config.max_upload_bytes)
        return None, debug

    # Extract text layer up-front so we can fail fast on scanned PDFs / unverifiable docs.
    page_texts, unverifiable_reason = _extract_verification_pages(
        file_bytes=file_bytes, mime_type=mime_type
    )
    if unverifiable_reason:
        debug["reason"] = unverifiable_reason
        return None, debug
    debug["verification_pages"] = len(page_texts)

    # Total timeout (env override)
    try:
        total_timeout = float(
            (os.getenv("SPOTLIGHT_KPI_EVIDENCE_PIPELINE_TIMEOUT_SECONDS") or "").strip()
            or str(config.total_timeout_seconds)
        )
    except ValueError:
        total_timeout = float(config.total_timeout_seconds)
    total_timeout = max(8.0, float(total_timeout))

    started = time.monotonic()

    def _remaining_time() -> float:
        return max(0.0, float(total_timeout) - (time.monotonic() - started))

    # ---------------------------------------------------------------------
    # Upload file once
    # ---------------------------------------------------------------------
    try:
        upload_timeout = min(12.0, _remaining_time())
        file_obj = gemini_client.upload_file_bytes(
            data=file_bytes,
            mime_type=str(mime_type),
            display_name=f"{company_name[:50]}-filing",
            timeout_seconds=upload_timeout,
        )
        file_uri = str(file_obj.get("uri") or "")
        file_mime = str(file_obj.get("mimeType") or file_obj.get("mime_type") or str(mime_type))
    except Exception as exc:  # noqa: BLE001
        debug["reason"] = "upload_failed"
        debug["upload_error"] = str(exc)[:500]
        return None, debug

    if not file_uri:
        debug["reason"] = "no_file_uri"
        return None, debug

    debug["file_uploaded"] = True

    def _call_with_compat_retry(
        *,
        fn,
        gen_cfg: Dict[str, Any],
        pass_name: str,
    ) -> Tuple[Optional[str], Optional[List[str]]]:
        raw: Optional[str] = None
        removed: Optional[List[str]] = None
        try:
            raw = fn(gen_cfg)
            return raw, removed
        except Exception as exc:  # noqa: BLE001
            cfg2, removed_fields = _strip_unsupported_generation_fields(
                gen_cfg, error_text=str(exc)
            )
            if removed_fields and cfg2 != gen_cfg:
                removed = removed_fields
                try:
                    raw = fn(cfg2)
                    debug.setdefault("compat_removed_generation_fields", [])
                    lst = debug.get("compat_removed_generation_fields")
                    if isinstance(lst, list):
                        lst.extend([f"{pass_name}:{f}" for f in removed_fields])
                    return raw, removed
                except Exception as exc2:  # noqa: BLE001
                    debug[f"{pass_name}_error"] = str(exc2)[:300]
                    return None, removed
            debug[f"{pass_name}_error"] = str(exc)[:300]
            return None, removed

    # ---------------------------------------------------------------------
    # PASS 1: find candidate KPIs with evidence (file-native)
    # ---------------------------------------------------------------------
    if _remaining_time() <= 0:
        debug["reason"] = "timeout_before_pass1"
        return None, debug

    pass1_prompt = _build_pass1_prompt(company_name, max_candidates=int(config.max_candidates))
    gen_cfg_pass1: Dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": int(config.pass1_max_output_tokens),
        "responseMimeType": "application/json",
    }
    gen_cfg_pass1.update(thinking_config(config.pass1_thinking_level))

    def _do_pass1(cfg: Dict[str, Any]) -> str:
        timeout = min(float(config.pass1_timeout_seconds), _remaining_time())
        return gemini_client.stream_generate_content_with_file_uri(
            file_uri=file_uri,
            file_mime_type=file_mime,
            prompt=pass1_prompt,
            stage_name="KPI Evidence Pass 1 (Candidates)",
            expected_tokens=int(config.pass1_max_output_tokens),
            generation_config_override=cfg,
            timeout_seconds=timeout,
        )

    pass1_raw, _ = _call_with_compat_retry(fn=_do_pass1, gen_cfg=gen_cfg_pass1, pass_name="pass1")
    if not pass1_raw:
        # Fallback: avoid fileUri calls (which can be flaky/rate-limited) by running
        # Pass 1 against a small set of high-signal page excerpts.
        pages = _select_pages_for_text_pass(page_texts)
        if pages and _remaining_time() >= 2.5:
            debug["pass1_fallback_attempted"] = "page_excerpts"
            pass1_prompt_text = _build_pass1_prompt_from_page_excerpts(
                company_name,
                max_candidates=int(config.max_candidates),
                pages=pages,
            )

            def _do_pass1_text(cfg: Dict[str, Any]) -> str:
                timeout = min(float(config.pass1_timeout_seconds), _remaining_time())
                try:
                    return gemini_client.stream_generate_content(
                        pass1_prompt_text,
                        stage_name="KPI Evidence Pass 1 (Candidates Text Fallback)",
                        expected_tokens=int(config.pass1_max_output_tokens),
                        generation_config_override=cfg,
                        timeout_seconds=timeout,
                        retry=False,
                    )
                except TypeError:
                    return gemini_client.stream_generate_content(
                        pass1_prompt_text,
                        stage_name="KPI Evidence Pass 1 (Candidates Text Fallback)",
                        expected_tokens=int(config.pass1_max_output_tokens),
                        generation_config_override=cfg,
                        timeout_seconds=timeout,
                    )

            pass1_raw2, _ = _call_with_compat_retry(
                fn=_do_pass1_text, gen_cfg=gen_cfg_pass1, pass_name="pass1_text_fallback"
            )
            if pass1_raw2:
                pass1_raw = pass1_raw2
                debug["pass1_fallback_used"] = True

        if not pass1_raw:
            debug["reason"] = "pass1_failed"
            return None, debug

    pass1 = parse_json_object(pass1_raw)
    if not pass1:
        if _remaining_time() > 1.5:
            retry_prompt = safe_json_retry_prompt("Pass 1", bad_output=pass1_raw)
            pass1_prompt_retry = retry_prompt
            pass1_used_text = bool(debug.get("pass1_fallback_used"))

            def _do_pass1_retry(cfg: Dict[str, Any]) -> str:
                timeout = min(float(config.pass1_timeout_seconds), _remaining_time())
                if pass1_used_text:
                    try:
                        return gemini_client.stream_generate_content(
                            pass1_prompt_retry,
                            stage_name="KPI Evidence Pass 1 (Retry)",
                            expected_tokens=int(config.pass1_max_output_tokens),
                            generation_config_override=cfg,
                            timeout_seconds=timeout,
                            retry=False,
                        )
                    except TypeError:
                        return gemini_client.stream_generate_content(
                            pass1_prompt_retry,
                            stage_name="KPI Evidence Pass 1 (Retry)",
                            expected_tokens=int(config.pass1_max_output_tokens),
                            generation_config_override=cfg,
                            timeout_seconds=timeout,
                        )
                return gemini_client.stream_generate_content_with_file_uri(
                    file_uri=file_uri,
                    file_mime_type=file_mime,
                    prompt=pass1_prompt_retry,
                    stage_name="KPI Evidence Pass 1 (Candidates Retry)",
                    expected_tokens=int(config.pass1_max_output_tokens),
                    generation_config_override=cfg,
                    timeout_seconds=timeout,
                )

            pass1_raw2, _ = _call_with_compat_retry(
                fn=_do_pass1_retry, gen_cfg=gen_cfg_pass1, pass_name="pass1_retry"
            )
            pass1 = parse_json_object(pass1_raw2 or "")
            if pass1:
                pass1_raw = pass1_raw2 or pass1_raw

    if not pass1 or not isinstance(pass1.get("candidates"), list):
        debug["reason"] = "pass1_invalid_json"
        debug["pass1_raw_head"] = (pass1_raw or "")[:800]
        return None, debug

    raw_candidates = [c for c in (pass1.get("candidates") or []) if isinstance(c, dict)]
    debug["pass1_candidates_count"] = len(raw_candidates)
    if not raw_candidates:
        # Recovery: some PDFs are hard for the model to parse as a file, but the
        # extracted text layer contains the metric table. If Pass 1 returns no
        # candidates, try a text-only Pass 1 on high-signal page excerpts (once).
        if not bool(debug.get("pass1_fallback_used")) and _remaining_time() >= 2.8:
            pages = _select_pages_for_text_pass(page_texts, max_pages=16, max_chars_per_page=2800)
            if pages:
                debug["pass1_fallback_attempted"] = "page_excerpts_no_candidates"
                pass1_prompt_text = _build_pass1_prompt_from_page_excerpts(
                    company_name,
                    max_candidates=int(config.max_candidates),
                    pages=pages,
                )

                def _do_pass1_text_retry(cfg: Dict[str, Any]) -> str:
                    timeout = min(float(config.pass1_timeout_seconds), _remaining_time())
                    try:
                        return gemini_client.stream_generate_content(
                            pass1_prompt_text,
                            stage_name="KPI Evidence Pass 1 (Candidates Text Fallback)",
                            expected_tokens=int(config.pass1_max_output_tokens),
                            generation_config_override=cfg,
                            timeout_seconds=timeout,
                            retry=False,
                        )
                    except TypeError:
                        return gemini_client.stream_generate_content(
                            pass1_prompt_text,
                            stage_name="KPI Evidence Pass 1 (Candidates Text Fallback)",
                            expected_tokens=int(config.pass1_max_output_tokens),
                            generation_config_override=cfg,
                            timeout_seconds=timeout,
                        )

                pass1_raw_alt, _ = _call_with_compat_retry(
                    fn=_do_pass1_text_retry,
                    gen_cfg=gen_cfg_pass1,
                    pass_name="pass1_text_no_candidates",
                )
                pass1_alt = parse_json_object(pass1_raw_alt or "") if pass1_raw_alt else None
                alt_candidates = (
                    [c for c in (pass1_alt.get("candidates") or []) if isinstance(c, dict)]
                    if isinstance(pass1_alt, dict)
                    else []
                )
                if alt_candidates:
                    debug["pass1_fallback_used"] = True
                    pass1 = pass1_alt
                    raw_candidates = alt_candidates
                    debug["pass1_candidates_count"] = len(raw_candidates)

        if not raw_candidates:
            debug["reason"] = "pass1_no_candidates"
            debug["failure_reason"] = str(pass1.get("failure_reason") or "").strip() or None
            return None, debug

    def _canon_name_key(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    # Pre-verify Pass 1 candidates so we can:
    # - fall back safely when Pass 2 output is invalid, and
    # - repair missing evidence when Pass 2 drops/garbles definition/value quotes.
    pass1_verified_by_name: Dict[str, List[Dict[str, Any]]] = {}
    pass1_fallback_candidate: Optional[SpotlightKpiCandidate] = None

    for c in raw_candidates[: int(config.max_candidates)]:
        name = str(c.get("name") or "").strip()
        if not name or _is_generic_financial_metric(name):
            continue
        evidence_raw = c.get("evidence")
        evidence_list = evidence_raw if isinstance(evidence_raw, list) else []
        if not evidence_list:
            continue

        verified, _ = _verify_evidence_against_document(
            evidence_list, page_texts=page_texts, kpi_name=name
        )
        if not verified:
            continue

        verified = _dedupe_evidence(verified)
        verified, has_value, has_definition = _enrich_evidence_types(
            verified,
            require_value=bool(config.require_value_evidence),
            require_definition=bool(config.require_definition_evidence),
        )

        pass1_verified_by_name[_canon_name_key(name)] = verified

        if pass1_fallback_candidate is not None:
            continue
        if config.require_value_evidence and not has_value:
            continue
        if config.require_definition_evidence and not has_definition:
            continue

        unit = str(c.get("unit") or "").strip() or None
        period = str(c.get("period") or "").strip() or ""
        most_recent_value = str(c.get("most_recent_value") or "").strip()

        value_f = _coerce_number(most_recent_value)
        if value_f is None:
            for ev in verified:
                if str(ev.get("type") or "") == "value":
                    value_f = _extract_number_from_excerpt(str(ev.get("quote") or ""))
                    if value_f is not None:
                        break
        if value_f is None:
            continue

        source_quote = ""
        source_page: Optional[int] = None
        for ev in verified:
            if str(ev.get("type") or "") == "value":
                source_quote = str(ev.get("quote") or "").strip()
                try:
                    source_page = int(ev.get("page"))
                except Exception:  # noqa: BLE001
                    source_page = None
                break
        if not source_quote and verified:
            source_quote = str(verified[0].get("quote") or "").strip()
            try:
                source_page = int(verified[0].get("page"))
            except Exception:  # noqa: BLE001
                source_page = None
        if source_quote and source_page:
            source_quote = f"[p. {source_page}] {source_quote}"

        pass1_fallback_candidate = {
            "name": name,
            "value": float(value_f),
            "unit": unit,
            "prior_value": None,
            "chart_type": "metric",
            "description": str(c.get("what_it_measures") or "").strip() or None,
            "source_quote": source_quote,
            "why_company_specific": str(c.get("why_company_specific") or "").strip()[:400] or None,
            "how_calculated_or_defined": str(c.get("how_calculated_or_defined") or "").strip()[:700]
            or None,
            "most_recent_value": most_recent_value or None,
            "period": period or None,
            "confidence": max(float(config.min_confidence), 0.75),
            "evidence": verified,
            "ban_flags": [],
        }

    debug["pass1_verified_fallback_available"] = bool(pass1_fallback_candidate)

    # Sanitize candidates (keep prompts bounded for Pass 2)
    sanitized_candidates: List[Dict[str, Any]] = []
    for c in raw_candidates[: int(config.max_candidates)]:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        if _is_generic_financial_metric(name):
            continue

        evidence_raw = c.get("evidence")
        evidence_list = evidence_raw if isinstance(evidence_raw, list) else []
        # Keep evidence short and bounded
        trimmed_evidence: List[Dict[str, Any]] = []
        for ev in evidence_list[:6]:
            item = _sanitize_evidence_item(ev)
            if not item:
                continue
            quote = str(item.get("quote") or "")
            if len(quote) > 380:
                quote = quote[:380].rstrip()
            item["quote"] = quote
            trimmed_evidence.append(item)

        sanitized_candidates.append(
            {
                "name": name,
                "why_company_specific": str(c.get("why_company_specific") or "").strip()[:400],
                "what_it_measures": str(c.get("what_it_measures") or "").strip()[:400],
                "how_calculated_or_defined": str(c.get("how_calculated_or_defined") or "").strip()[:700],
                "most_recent_value": str(c.get("most_recent_value") or "").strip()[:120],
                "period": str(c.get("period") or "").strip()[:80],
                "unit": str(c.get("unit") or "").strip()[:32],
                "evidence": trimmed_evidence,
            }
        )

    if not sanitized_candidates:
        debug["reason"] = "pass1_no_valid_candidates"
        return None, debug

    # ---------------------------------------------------------------------
    # PASS 2: choose best KPI and output final strict schema (text-only)
    # ---------------------------------------------------------------------
    if _remaining_time() <= 0:
        debug["reason"] = "timeout_before_pass2"
        return None, debug

    pass2_prompt = _build_pass2_prompt(company_name, candidates=sanitized_candidates)
    gen_cfg_pass2: Dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": int(config.pass2_max_output_tokens),
        "responseMimeType": "application/json",
    }
    gen_cfg_pass2.update(thinking_config(config.pass2_thinking_level))

    def _do_pass2(cfg: Dict[str, Any]) -> str:
        timeout = min(float(config.pass2_timeout_seconds), _remaining_time())
        # Prefer retry=False when supported (faster failure)
        try:
            return gemini_client.stream_generate_content(
                pass2_prompt,
                stage_name="KPI Evidence Pass 2 (Select+Verify)",
                expected_tokens=int(config.pass2_max_output_tokens),
                generation_config_override=cfg,
                timeout_seconds=timeout,
                retry=False,
            )
        except TypeError:
            return gemini_client.stream_generate_content(
                pass2_prompt,
                stage_name="KPI Evidence Pass 2 (Select+Verify)",
                expected_tokens=int(config.pass2_max_output_tokens),
                generation_config_override=cfg,
                timeout_seconds=timeout,
            )

    pass2_raw, _ = _call_with_compat_retry(fn=_do_pass2, gen_cfg=gen_cfg_pass2, pass_name="pass2")
    if not pass2_raw:
        debug["reason"] = "pass2_failed"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    pass2 = parse_json_object(pass2_raw)
    if not pass2:
        if _remaining_time() > 1.5:
            retry_prompt = safe_json_retry_prompt("Pass 2", bad_output=pass2_raw)

            def _do_pass2_retry(cfg: Dict[str, Any]) -> str:
                timeout = min(float(config.pass2_timeout_seconds), _remaining_time())
                try:
                    return gemini_client.stream_generate_content(
                        retry_prompt,
                        stage_name="KPI Evidence Pass 2 (Retry)",
                        expected_tokens=int(config.pass2_max_output_tokens),
                        generation_config_override=cfg,
                        timeout_seconds=timeout,
                        retry=False,
                    )
                except TypeError:
                    return gemini_client.stream_generate_content(
                        retry_prompt,
                        stage_name="KPI Evidence Pass 2 (Retry)",
                        expected_tokens=int(config.pass2_max_output_tokens),
                        generation_config_override=cfg,
                        timeout_seconds=timeout,
                    )

            pass2_raw2, _ = _call_with_compat_retry(
                fn=_do_pass2_retry, gen_cfg=gen_cfg_pass2, pass_name="pass2_retry"
            )
            pass2 = parse_json_object(pass2_raw2 or "")
            if pass2:
                pass2_raw = pass2_raw2 or pass2_raw

    if not pass2:
        debug["reason"] = "pass2_invalid_json"
        debug["pass2_raw_head"] = (pass2_raw or "")[:800]
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    selected = pass2.get("selected_kpi")
    failure_reason = pass2.get("failure_reason")

    if selected is None:
        debug["reason"] = "no_selected_kpi"
        debug["failure_reason"] = str(failure_reason or "").strip() or None
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    if not isinstance(selected, dict):
        debug["reason"] = "selected_kpi_not_object"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    name = str(selected.get("name") or "").strip()
    if not name or _is_generic_financial_metric(name):
        debug["reason"] = "selected_kpi_banned_or_missing_name"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    # Confidence gate (model self-report + backend verification)
    conf_raw = selected.get("confidence")
    conf = 0.0
    try:
        if isinstance(conf_raw, (int, float)):
            conf = float(conf_raw)
        elif isinstance(conf_raw, str) and conf_raw.strip():
            conf = float(conf_raw.strip())
    except Exception:  # noqa: BLE001
        conf = 0.0

    debug["model_confidence"] = conf
    if conf < float(config.min_confidence):
        debug["reason"] = "low_confidence"
        debug["failure_reason"] = str(failure_reason or "").strip() or "confidence_below_threshold"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    evidence_raw = selected.get("evidence")
    evidence_list = evidence_raw if isinstance(evidence_raw, list) else []
    if not evidence_list:
        debug["reason"] = "no_evidence"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    verified_evidence, evidence_dbg = _verify_evidence_against_document(
        evidence_list, page_texts=page_texts, kpi_name=name
    )
    debug.update({f"evidence_{k}": v for k, v in evidence_dbg.items()})

    if not verified_evidence:
        debug["reason"] = "evidence_unverified"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    verified_evidence = _dedupe_evidence(verified_evidence)
    verified_evidence, has_value, has_definition = _enrich_evidence_types(
        verified_evidence,
        require_value=bool(config.require_value_evidence),
        require_definition=bool(config.require_definition_evidence),
    )

    # Repair missing evidence using Pass 1 verified quotes for the same KPI name.
    # This prevents false negatives when Pass 2 drops definition/value quotes.
    if (
        (bool(config.require_value_evidence) and not has_value)
        or (bool(config.require_definition_evidence) and not has_definition)
    ) and pass1_verified_by_name:
        selected_key = _canon_name_key(name)
        recovered = pass1_verified_by_name.get(selected_key)
        if recovered is None:
            for k, v in pass1_verified_by_name.items():
                if not k:
                    continue
                if selected_key in k or k in selected_key:
                    recovered = v
                    break
        if recovered:
            debug["pass1_evidence_repair_used"] = True
            verified_evidence = _dedupe_evidence(list(verified_evidence) + list(recovered))
            verified_evidence, has_value, has_definition = _enrich_evidence_types(
                verified_evidence,
                require_value=bool(config.require_value_evidence),
                require_definition=bool(config.require_definition_evidence),
            )

    # If we're missing definition evidence, do a small targeted repair pass that ONLY
    # tries to locate a definitional quote + page. This keeps the UX "one-shot" while
    # preventing false negatives caused by the model picking only value quotes.
    if (
        config.enable_definition_repair
        and config.require_definition_evidence
        and not has_definition
        and _remaining_time() >= 3.0
    ):
        debug["definition_repair_attempted"] = True

        # Provide the best verified value quote as a pointer (optional).
        value_anchor = ""
        for ev in verified_evidence:
            if str(ev.get("type") or "") == "value":
                value_anchor = str(ev.get("quote") or "").strip()
                break

        pass3_prompt = f"""
You are verifying the existence of a KPI definition in the attached filing for {company_name}.

Task: Find a SHORT verbatim quote that DEFINES the KPI "{name}" (what it measures / how it is calculated).

Rules:
- The quote MUST explicitly define what the KPI measures or how it is calculated.
  Acceptable forms include phrases like: "we define", "defined as", "refers to", "means", "represents", "calculated as", "computed as",
  OR a plain-language definition like: "MAUs are users who …".
- Return `definition: null` if no explicit definition exists in the filing text (do not guess).
- Return JSON only.

KPI name: {name}
Value anchor (may help you locate the right section; do not paraphrase): {value_anchor or "N/A"}

Return JSON:
{{
  "definition": {{ "page": 1, "quote": "string" }} | null,
  "failure_reason": "string|null"
}}
""".strip()

        gen_cfg_pass3: Dict[str, Any] = {
            "temperature": 0.1,
            "maxOutputTokens": int(config.pass3_max_output_tokens),
            "responseMimeType": "application/json",
        }
        gen_cfg_pass3.update(thinking_config(config.pass3_thinking_level))

        def _do_pass3(cfg: Dict[str, Any]) -> str:
            timeout = min(float(config.pass3_timeout_seconds), _remaining_time())
            return gemini_client.stream_generate_content_with_file_uri(
                file_uri=file_uri,
                file_mime_type=file_mime,
                prompt=pass3_prompt,
                stage_name="KPI Evidence Pass 3 (Find Definition)",
                expected_tokens=int(config.pass3_max_output_tokens),
                generation_config_override=cfg,
                timeout_seconds=timeout,
            )

        pass3_raw, _ = _call_with_compat_retry(fn=_do_pass3, gen_cfg=gen_cfg_pass3, pass_name="pass3_def")
        pass3 = parse_json_object(pass3_raw or "")
        if not pass3 and (pass3_raw or "").strip() and _remaining_time() >= 1.5:
            retry_prompt = safe_json_retry_prompt("Pass 3", bad_output=pass3_raw or "")
            pass3_prompt_retry = retry_prompt

            def _do_pass3_retry(cfg: Dict[str, Any]) -> str:
                timeout = min(float(config.pass3_timeout_seconds), _remaining_time())
                return gemini_client.stream_generate_content_with_file_uri(
                    file_uri=file_uri,
                    file_mime_type=file_mime,
                    prompt=pass3_prompt_retry,
                    stage_name="KPI Evidence Pass 3 (Retry)",
                    expected_tokens=int(config.pass3_max_output_tokens),
                    generation_config_override=cfg,
                    timeout_seconds=timeout,
                )

            pass3_raw2, _ = _call_with_compat_retry(
                fn=_do_pass3_retry, gen_cfg=gen_cfg_pass3, pass_name="pass3_def_retry"
            )
            pass3 = parse_json_object(pass3_raw2 or "") or pass3

        definition_obj = pass3.get("definition") if isinstance(pass3, dict) else None
        definition_quote = ""
        definition_page_hint: Optional[int] = None
        if isinstance(definition_obj, dict):
            definition_quote = str(definition_obj.get("quote") or "").strip()
            page_raw = definition_obj.get("page")
            try:
                if isinstance(page_raw, (int, float)):
                    definition_page_hint = int(page_raw)
                elif isinstance(page_raw, str) and page_raw.strip().isdigit():
                    definition_page_hint = int(page_raw.strip())
            except Exception:  # noqa: BLE001
                definition_page_hint = None

        if definition_quote and _looks_like_definition_quote(definition_quote):
            page_texts_norm = [_normalize_for_matching(t) for t in page_texts]
            hits = _find_pages_for_quote(definition_quote, page_texts_norm=page_texts_norm)
            if hits:
                if isinstance(definition_page_hint, int) and definition_page_hint in hits:
                    picked = definition_page_hint
                else:
                    picked = hits[0]
                verified_evidence = _dedupe_evidence(
                    verified_evidence
                    + [{"page": int(picked), "quote": definition_quote, "type": "definition"}]
                )
                has_definition = any(
                    str(ev.get("type") or "") == "definition" for ev in verified_evidence
                )
                debug["definition_repair_success"] = bool(has_definition)
            else:
                debug["definition_repair_success"] = False
        else:
            debug["definition_repair_success"] = False

    if config.require_value_evidence and not has_value:
        debug["reason"] = "missing_value_evidence"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    if config.require_definition_evidence and not has_definition:
        debug["reason"] = "missing_definition_evidence"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    unit = str(selected.get("unit") or "").strip() or None
    period = str(selected.get("period") or "").strip() or ""
    most_recent_value = str(selected.get("most_recent_value") or "").strip()

    value_f = _coerce_number(most_recent_value)
    if value_f is None:
        # Prefer the value quote if present
        value_quotes = [ev.get("quote") for ev in verified_evidence if ev.get("type") == "value"]
        for q in value_quotes:
            value_f = _extract_number_from_excerpt(str(q or ""))
            if value_f is not None:
                break
    if value_f is None:
        debug["reason"] = "unparseable_value"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    # Prefer a "value" evidence quote for the UI source quote.
    source_quote = ""
    source_page: Optional[int] = None
    for ev in verified_evidence:
        if str(ev.get("type") or "") == "value":
            source_quote = str(ev.get("quote") or "").strip()
            try:
                source_page = int(ev.get("page"))
            except Exception:  # noqa: BLE001
                source_page = None
            break
    if not source_quote:
        source_quote = str(verified_evidence[0].get("quote") or "").strip()
        try:
            source_page = int(verified_evidence[0].get("page"))
        except Exception:  # noqa: BLE001
            source_page = None

    if source_quote and source_page:
        source_quote = f"[p. {source_page}] {source_quote}"

    candidate: SpotlightKpiCandidate = {
        "name": name,
        "value": float(value_f),
        "unit": unit,
        "prior_value": None,
        "chart_type": "metric",
        "description": str(selected.get("what_it_measures") or "").strip() if has_definition else None,
        "source_quote": source_quote,
        # Extra fields (frontend ignores if unused)
        "why_company_specific": str(selected.get("why_company_specific") or "").strip() or None,
        "how_calculated_or_defined": str(selected.get("how_calculated_or_defined") or "").strip()
        if has_definition
        else None,
        "most_recent_value": most_recent_value or None,
        "period": period or None,
        "confidence": conf,
        "evidence": verified_evidence,
        "ban_flags": [],
    }

    debug["total_time_ms"] = int((time.monotonic() - started) * 1000)
    return candidate, debug
