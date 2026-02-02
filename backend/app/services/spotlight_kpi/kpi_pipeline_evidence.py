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

from .regex_fallback import (
    extract_kpis_with_key_metrics_table_scan_by_page,
    extract_kpis_with_regex_by_page,
)
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


def _strip_page_header_prefix(text: str) -> str:
    """Remove common page-marker prefixes that models sometimes include in quotes."""
    s = (text or "").strip()
    if not s:
        return ""
    # Examples:
    # - "PAGE 12: ...", "Page 12 - ...", "[PAGE 12] ..."
    s = re.sub(r"^\[?\s*page\s+\d+\s*\]?\s*[:\\-–—]?\\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\\s*={2,}\\s*page\\s+\\d+\\s*={2,}\\s*", "", s, flags=re.IGNORECASE)
    return s.strip()


def _build_compact_kpi_description(
    *, what_it_measures: str = "", why_company_specific: str = "", max_chars: int = 180
) -> Optional[str]:
    """Create a 1-2 line blurb for the UI from model-provided fields.

    Prefer concise, company-relevant explanations and avoid leaking long quotes.
    """

    def _clean(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        cleaned = cleaned.strip().strip('"').strip("'").strip()
        return cleaned

    def _ensure_sentence(text: str) -> str:
        s = _clean(text)
        if not s:
            return ""
        if s[-1] not in ".!?":
            s = f"{s}."
        return s

    what = _ensure_sentence(what_it_measures)
    why_raw = _clean(why_company_specific)
    why = ""
    if why_raw:
        prefix = ""
        lowered = why_raw.lower()
        if not any(token in lowered for token in ("because", "important", "matters", "key", "core")):
            prefix = "Matters because "
        why = _ensure_sentence(f"{prefix}{why_raw}")

    parts = [p for p in (what, why) if p]
    if not parts:
        return None

    out = " ".join(parts)
    out = re.sub(r"\s+", " ", out).strip()
    if max_chars and len(out) > int(max_chars):
        clipped = out[: max(0, int(max_chars) - 1)].rstrip()
        out = f"{clipped}…"
    return out or None


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
        # Corporate-fact disclosures (not operating KPIs)
        "headquarters",
        "principal executive office",
        "principal executive offices",
        "corporate headquarters",
        "commission file",
        "file number",
        "cik",
        "telephone",
        "phone",
        "fax",
        "address",
        "zip",
        "postal",
        "incorporat",
        "office space",
        "square footage",
        "square feet",
        "sq ft",
    )

    return any(phrase in n for phrase in banned_phrases)


def _build_pass1_prompt(company_name: str, *, max_candidates: int) -> str:
    return f"""
You are an expert financial document analyst. Your job is to find company-specific KPIs / operating metrics from the provided filing for {company_name}.

Constraints:
- Do NOT choose generic metrics (revenue, net income, EPS, gross margin, EBITDA, free cash flow, capex, cash, debt).
- Do NOT choose generic accounting/GAAP line items or policy/tax disclosures (e.g., stock-based compensation, excess tax benefits,
  deferred taxes, effective tax rate, depreciation/amortization, interest expense, working capital, balance sheet line items).
- Do NOT choose corporate-fact disclosures (headquarters location/address, principal executive offices, phone numbers, building square footage,
  office space size, number of facilities/buildings). These are not operating KPIs.
- Do NOT treat phone numbers, addresses, CIK/commission file numbers, or zip/postal codes as KPIs.
- Do NOT choose footnote/one-off disclosure tables (related party transactions, stock/option activity, compensation/tax schedules).
- "Company-specific" means the metric is explicitly disclosed for this company; it does NOT need to be unique across companies.
- The KPI `name` MUST match the wording used in the filing (do not rename "Transactions" to "Orders", do not add/remove words).
- The KPI MUST be explicitly mentioned in the filing and MUST include evidence.
- You MUST provide page numbers and short quotes from the document that contain:
  - a reported value (most recent value), and
  - (if present) a definition (how the KPI is calculated / what it means).
- Prefer TRUE operating KPIs that describe the business (usage/volume/capacity), such as: customers/subscribers, orders/transactions,
  units shipped/delivered, bookings/backlog, AUM, GMV/TPV, churn/retention, occupancy/utilization, store count, etc.
- For EACH candidate, `evidence` MUST include at least ONE item with type = "value" that contains a numeric value.
  - If the KPI is in a TABLE and the value cell does not repeat the KPI name, you may:
    (a) quote the full table row (KPI label + value), OR
    (b) provide TWO evidence quotes on the SAME page: one with the KPI name (type "context") and one with the numeric value (type "value").
  - Quotes MUST be verbatim excerpts from the filing (include enough surrounding text to be uniquely matchable).
- If the filing content includes page markers like `PAGE 12:` (plain-text extraction), use those numbers for `evidence.page`.
  Do NOT include the `PAGE N:` marker text in the `quote` field.
- `unit` MUST be the actual unit (e.g., "users", "transactions", "orders", "$", "%"). Do NOT use magnitude/scales as units
  ("million", "billion", "M", "B"). If the filing reports values "in millions/billions", put that in `most_recent_value`
  (e.g., "250 million") and keep `unit` as the real unit (e.g., "users").
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
- Do NOT choose corporate-fact disclosures (headquarters location/address, principal executive offices, phone numbers, building square footage,
  office space size, number of facilities/buildings). These are not operating KPIs.
- Do NOT treat phone numbers, addresses, CIK/commission file numbers, or zip/postal codes as KPIs.
- Do NOT choose footnote/one-off disclosure tables (related party transactions, stock/option activity, compensation/tax schedules).
- "Company-specific" means the metric is disclosed for this company; it does NOT need to be unique across companies.
- The KPI `name` MUST match the wording used in the filing (do not rename "Transactions" to "Orders", do not add/remove words).
- If you cannot prove the KPI exists with evidence, you MUST return `"selected_kpi": null`.
- Evidence requirements for a non-null KPI:
  - at least ONE "value" evidence quote + page that contains a numeric value.
    If the value appears in a TABLE cell without the KPI name, include an additional "context" quote on the SAME page that contains the KPI name.
  - Include "definition" evidence if the filing explicitly defines the metric. If not defined, still return the KPI if value evidence exists.
- `unit` MUST be the actual unit (e.g., "users", "transactions", "orders", "$", "%"). Do NOT use magnitude/scales as units
  ("million", "billion", "M", "B"). If the filing reports values "in millions/billions", put that in `most_recent_value`
  (e.g., "250 million") and keep `unit` as the real unit (e.g., "users").
- If the filing content includes page markers like `PAGE 12:` (plain-text extraction), use those numbers for `evidence.page`.
  Do NOT include the `PAGE N:` marker text in the `quote` field.
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


_GARBLED_ALLOWED_PUNCT = set("_.;,:%$()[]'\"-+/&")


def _looks_like_garbled_text(text: str) -> bool:
    """Heuristic: detect PDF text layers that are present but unreadable."""
    s = (
        (text or "")
        .replace("\u00a0", " ")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .strip()
    )
    if not s:
        return True

    sample = s[:4000]
    non_space = re.sub(r"\s+", "", sample)
    if not non_space:
        return True

    tokens = re.findall(r"\S+", sample)
    if not tokens:
        return True

    good_words = 0
    token_cap = min(len(tokens), 350)
    for tok in tokens[:token_cap]:
        cleaned = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", tok)
        if re.fullmatch(r"[A-Za-z]{3,}", cleaned or ""):
            good_words += 1
    good_ratio = good_words / max(1, token_cap)

    weird = sum(
        1 for ch in non_space if not (ch.isalnum() or ch in _GARBLED_ALLOWED_PUNCT)
    )
    weird_ratio = weird / max(1, len(non_space))

    if weird_ratio >= 0.18:
        return True
    if weird_ratio >= 0.08 and good_ratio <= 0.18:
        return True
    if good_ratio <= 0.10 and weird_ratio >= 0.03 and len(tokens) >= 40:
        return True
    return False


def _looks_like_garbled_pdf_text_layer(page_texts: List[str]) -> bool:
    if not page_texts:
        return True

    n = len(page_texts)
    idxs: List[int] = []
    for i in (0, 1, 2, n // 2, max(0, n - 3), n - 2, n - 1):
        if 0 <= i < n and i not in idxs:
            idxs.append(i)

    parts: List[str] = []
    total = 0
    for i in idxs:
        t = str(page_texts[i] or "").strip()
        if not t:
            continue
        take = t[:1200]
        parts.append(take)
        total += len(take)
        if total >= 5000:
            break

    sample = "\n".join(parts).strip()
    if not sample:
        return True
    return _looks_like_garbled_text(sample)


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

            if total_chars < 800:
                # Defer OCR to later escalation: we want to avoid doing OCR on every
                # scanned PDF up-front. The main pipeline will attempt model+regex first
                # (for text-layer PDFs), then escalate to OCR only when needed.
                return [], "no_text_layer"
            if _looks_like_garbled_pdf_text_layer(pages):
                # Some SEC PDFs have a "text layer" that is technically present but
                # effectively unreadable due to font encoding. Treat this as
                # unverifiable so we can OCR the rendered page images.
                return [], "garbled_text_layer"
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
        if len(qn_compact) >= 40:
            page_compact = page_norm.replace(" ", "")
            if qn_compact and qn_compact in page_compact:
                hits.append(idx + 1)
                continue
        if len(qn) >= 60:
            prefix = qn[:40]
            suffix = qn[-40:]
            if prefix in page_norm or suffix in page_norm:
                hits.append(idx + 1)
                continue
        if len(qn) >= 30:
            words = qn.split()
            if len(words) >= 4:
                sig_words = [w for w in words if len(w) >= 4][:6]
                if sig_words:
                    matches = sum(1 for w in sig_words if w in page_norm)
                    if matches >= len(sig_words) * 0.7:
                        hits.append(idx + 1)
                        continue
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
    # Industrial / commodity / regulated volume indicators
    "production",
    "throughput",
    "capacity",
    "proved reserves",
    "proven reserves",
    "reserves",
    "barrels",
    "boe",
    "bpd",
    "mcf",
    "mmcf",
    "tons",
    "ounces",
    "megawatts",
    "mw",
    "gwh",
    "mwh",
    "passengers",
    "asm",
    "rpm",
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

    # Also include mid/last anchors to cover long filings where KPIs appear far
    # from the cover/ToC (common for older 10-Ks).
    if len(page_texts) >= 3:
        mid = max(0, (len(page_texts) // 2))
        for anchor in (mid, len(page_texts) - 1):
            if anchor in seen:
                continue
            selected.append(int(anchor))
            seen.add(int(anchor))
            if len(selected) >= int(max_pages):
                break

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

    # If we still have room, add a few high-digit-density pages even if they lack
    # explicit KPI keywords (tables sometimes omit the obvious headings).
    if len(selected) < int(max_pages):
        density: List[Tuple[int, int]] = []
        for idx, text in enumerate(page_texts):
            if idx in seen:
                continue
            digits = sum(1 for ch in (text or "") if ch.isdigit())
            if digits >= 25:
                density.append((int(idx), int(digits)))
        density.sort(key=lambda t: t[1], reverse=True)
        for idx, _d in density[: max(0, int(max_pages) - len(selected))]:
            if idx in seen:
                continue
            selected.append(int(idx))
            seen.add(int(idx))
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
- Do NOT choose corporate-fact disclosures (headquarters location/address, principal executive offices, phone numbers, building square footage,
  office space size, number of facilities/buildings). These are not operating KPIs.
- Do NOT treat phone numbers, addresses, CIK/commission file numbers, or zip/postal codes as KPIs.
- Do NOT choose footnote/one-off disclosure tables (related party transactions, stock/option activity, compensation/tax schedules).
- "Company-specific" means the metric is explicitly disclosed for this company; it does NOT need to be unique across companies.
- The KPI `name` MUST match the wording used in the page text (do not rename "Transactions" to "Orders", do not add/remove words).
- The KPI MUST be explicitly mentioned in the provided page text and MUST include evidence.
- You MUST provide page numbers and short quotes from the page text that contain:
  - a reported value (most recent value), and
  - (if present) a definition (how the KPI is calculated / what it means).
- For EACH candidate, `evidence` MUST include at least ONE item with type = "value" that contains a numeric value.
  - If the KPI is in a TABLE and the value cell does not repeat the KPI name, you may:
    (a) quote the full table row (KPI label + value), OR
    (b) provide TWO evidence quotes on the SAME page: one with the KPI name (type "context") and one with the numeric value (type "value").
- Quotes MUST be verbatim substrings of the corresponding PAGE text.
- `unit` MUST be the actual unit (e.g., "users", "transactions", "orders", "$", "%"). Do NOT use magnitude/scales as units
  ("million", "billion", "M", "B"). If the page reports values "in millions/billions", put that in `most_recent_value`
  (e.g., "250 million") and keep `unit` as the real unit (e.g., "users").
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


def _looks_like_value_quote(
    quote: str, *, kpi_name: str = "", unit_hint: Optional[str] = None
) -> bool:
    """Return True when the quote appears to contain a KPI value.

    When `kpi_name` is provided, we avoid treating digits embedded in the KPI name
    itself (e.g., product numbers like "365") as the KPI value.
    """
    if (kpi_name or "").strip():
        return (
            _extract_value_number_from_quote_near_name(
                quote or "", kpi_name=kpi_name, unit_hint=unit_hint
            )
            is not None
        )
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


_TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(total\s+)?customers?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?users?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?subscribers?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?members?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?accounts?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?orders?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?transactions?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?shipments?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?deliver(?:y|ies)\s*$", re.I),
    re.compile(r"^\s*(total\s+)?units?\s*$", re.I),
)

_STRONG_OPERATIONAL_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmonthly\s+active\s+users?\b|\bMAUs?\b", re.I),
    re.compile(r"\bdaily\s+active\s+users?\b|\bDAUs?\b", re.I),
    re.compile(r"\bpaid\s+subscribers?\b|\bpaid\s+members\b", re.I),
    re.compile(r"\b(ARR|MRR)\b|\b(recurring\s+revenue)\b", re.I),
    re.compile(r"\b(net|dollar[- ]based)\s+retention\b|\b(NRR|NDR)\b", re.I),
    re.compile(r"\bchurn\b", re.I),
    re.compile(r"\bARPU\b|\bARPA\b|\bARPPU\b|\bASP\b|\bAOV\b|\baverage\s+revenue\s+per\b", re.I),
    re.compile(r"\b(GMV|GMS|GTV)\b|\bgross\s+merchandise\s+volume\b", re.I),
    re.compile(r"\bTPV\b|\b(total\s+payment\s+volume|payment\s+volume|processed\s+volume)\b", re.I),
    re.compile(r"\bAUM\b|\bassets\s+under\s+management\b", re.I),
    re.compile(r"\bvehicles?\s+delivered\b", re.I),
    re.compile(r"\bunits?\s+(?:shipped|sold|delivered)\b", re.I),
    re.compile(r"\boccupancy\b|\butilization\b|\bload\s+factor\b", re.I),
    re.compile(r"\brevpar\b|\bsame[- ]store\b|\bcomparable\s+sales\b", re.I),
)

_NON_OPERATING_EVIDENCE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\brelated\s+party\b", re.I),
    re.compile(r"\btransactions?\s+related\s+to\s+our\b", re.I),
    re.compile(r"\bshare[- ]based\b|\bstock[- ]based\b", re.I),
    re.compile(r"\bcompensation\b", re.I),
    re.compile(r"\b(excess\s+tax|income\s+tax|deferred\s+tax|effective\s+tax\s+rate)\b", re.I),
    re.compile(r"\b(vesting|grant\s+date|exercise\s+price|restricted\s+stock|rsu)\b", re.I),
)


def _looks_like_non_operating_kpi(
    name: str, *, evidence: List[Dict[str, Any]]
) -> bool:
    """Reject 'metrics' that are clearly footnote/one-off disclosures, not operating KPIs."""
    nm = (name or "").strip()
    if not nm:
        return True

    text = " \n ".join(
        [nm]
        + [
            str(ev.get("quote") or "").strip()
            for ev in (evidence or [])[:8]
            if isinstance(ev, dict)
        ]
    ).strip()
    lower = text.lower()

    if any(p.search(lower) for p in _NON_OPERATING_EVIDENCE_PATTERNS):
        return True

    # Special-case: bare "Transactions"/"Orders" with tiny values are frequently from
    # schedules/footnotes rather than core operating metrics.
    if any(p.search(nm) for p in _TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS):
        if "related to our" in lower or "related to the" in lower:
            return True

    return False


def _candidate_importance_score(
    *,
    name: str,
    unit: Optional[str],
    value: Optional[float],
    evidence: List[Dict[str, Any]],
) -> int:
    """Heuristic representativeness score for picking the single Spotlight KPI."""
    score = 0
    nm = (name or "").strip()
    if not nm:
        return -10_000

    if any(p.search(nm) for p in _STRONG_OPERATIONAL_NAME_PATTERNS):
        score += 40
    # Still give credit for generic operational tokens (users/orders/etc.)
    if re.search(
        r"\b(users|subscribers|customers|accounts|orders|transactions|shipments|deliveries|units)\b",
        nm,
        re.I,
    ):
        score += 15
    if any(p.search(nm) for p in _TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS):
        score -= 22

    if evidence:
        if any(str(ev.get("type") or "") == "definition" for ev in evidence):
            score += 5
        joined = " ".join(str(ev.get("quote") or "") for ev in evidence[:6]).lower()
        if any(tok in joined for tok in ("active", "paid", "monthly", "daily")):
            score += 6
        if any(tok in joined for tok in ("total", "ended", "as of", "period")):
            score += 2

    unit_lower = (unit or "").strip().lower() if unit else ""
    if unit_lower in ("users", "subscribers", "customers", "accounts", "orders", "transactions", "units", "trips", "rides", "stores", "locations"):
        score += 6
    elif unit_lower in ("$", "€", "£", "usd", "eur", "gbp"):
        # Currency totals are often less representative than operational volumes unless they
        # are clearly business-model KPIs (GMV/TPV/AUM/Bookings/etc.).
        if any(p.search(nm) for p in _STRONG_OPERATIONAL_NAME_PATTERNS):
            score += 4
        else:
            score -= 10

    if value is not None:
        try:
            abs_v = abs(float(value))
        except Exception:  # noqa: BLE001
            abs_v = 0.0
        if abs_v >= 1_000_000:
            score += 4
        elif abs_v >= 10_000:
            score += 2
        # Penalize extremely tiny totals for generic names (often footnotes/schedules).
        if abs_v < 5 and any(p.search(nm) for p in _TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS):
            score -= 15

    return int(score)


_SCALE_UNIT_TOKENS: set[str] = {
    "k",
    "m",
    "b",
    "t",
    "thousand",
    "million",
    "billion",
    "trillion",
    "mn",
    "bn",
    "mm",
}


def _infer_unit_from_kpi_name(name: str) -> Optional[str]:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not n:
        return None

    if any(tok in n for tok in ("margin", "rate", "ratio", "churn", "retention", "occupancy", "utilization", "load factor")):
        return "%"

    if "transaction" in n:
        return "transactions"
    if "order" in n:
        return "orders"
    if "subscriber" in n or "membership" in n or "member" in n:
        return "subscribers"
    if "mau" in n or "dau" in n or "active user" in n or n.endswith(" users"):
        return "users"
    if "customer" in n or "account" in n or "merchant" in n:
        return "customers"
    if "trip" in n:
        return "trips"
    if "ride" in n:
        return "rides"
    if any(tok in n for tok in ("shipment", "shipped", "deliver", "delivered", "units")):
        return "units"
    if any(tok in n for tok in ("store", "location", "restaurant")):
        return "locations"

    return None


def _infer_unit_from_evidence_quotes(evidence: List[Dict[str, Any]]) -> Optional[str]:
    for ev in evidence or []:
        quote = str(ev.get("quote") or "")
        if not quote:
            continue
        if "$" in quote:
            return "$"
        if "€" in quote:
            return "€"
        if "£" in quote:
            return "£"
        if "%" in quote:
            return "%"
        if re.search(r"\bpercent(?:age)?\b", quote, re.IGNORECASE):
            return "%"
    return None


def _sanitize_unit(
    unit: Optional[str],
    *,
    kpi_name: str,
    evidence: List[Dict[str, Any]],
) -> Optional[str]:
    inferred = _infer_unit_from_evidence_quotes(evidence)
    if inferred:
        return inferred

    raw = str(unit or "").strip()
    lower = raw.lower().strip()
    if not lower:
        return _infer_unit_from_kpi_name(kpi_name)

    if lower in {"%", "percent", "percentage", "pct"}:
        return "%"
    if lower in {"$", "usd", "us$", "dollar", "dollars", "us dollars"}:
        return "$"
    if lower in {"€", "eur", "euro", "euros"}:
        return "€"
    if lower in {"£", "gbp", "pound", "pounds", "sterling"}:
        return "£"

    # Never treat magnitude/scales as units (prevents "2000B" display bugs).
    if lower in _SCALE_UNIT_TOKENS:
        return _infer_unit_from_kpi_name(kpi_name)

    # Clamp to keep payloads compact and avoid accidental blobs.
    raw = raw[:48].strip()
    return raw or _infer_unit_from_kpi_name(kpi_name)


def _extract_value_number_from_quote_near_name(
    quote: str,
    *,
    kpi_name: str,
    unit_hint: Optional[str] = None,
) -> Optional[float]:
    """Extract a KPI value from a quote, preferring numbers near the KPI name.

    Table rows often contain multiple numbers. We bias toward the number closest
    to the KPI name/token, which reduces errors like picking a date or a currency
    column instead of the KPI value.
    """
    if not quote:
        return None

    variants = _kpi_name_variants(kpi_name)
    if not variants:
        return _extract_number_from_excerpt(quote)

    normalized = _normalize_for_matching(quote)
    if not normalized:
        return _extract_number_from_excerpt(quote)

    positions: List[int] = []
    name_spans: List[Tuple[int, int]] = []
    for v in variants:
        if not v:
            continue
        start = 0
        while True:
            pos = normalized.find(v, start)
            if pos < 0:
                break
            positions.append(int(pos))
            name_spans.append((int(pos), int(pos) + len(v)))
            start = pos + max(1, len(v))
            if len(positions) >= 24:
                break
        if len(positions) >= 24:
            break

    # Only apply default multipliers when explicitly stated ("in millions/billions").
    lower = normalized.lower()
    default_mult = 1.0
    if "in billions" in lower:
        default_mult = 1_000_000_000.0
    elif "in millions" in lower:
        default_mult = 1_000_000.0
    elif "in thousands" in lower:
        default_mult = 1_000.0

    unit_lower = (str(unit_hint or "").strip().lower() if unit_hint else "").strip()
    prefer_currency = unit_lower in {"$", "€", "£", "usd", "eur", "gbp"}
    prefer_percent = unit_lower in {"%", "percent", "percentage", "pct"}

    month_re = re.compile(
        r"\b(?:"
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
        r")\b",
        re.IGNORECASE,
    )

    pattern = re.compile(
        r"(?<![A-Za-z0-9])(?P<cur>[$€£])?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suf>%|[kKmMbBtT]|bn|mn|billion|million|thousand|trillion)?",
        re.IGNORECASE,
    )

    scored: List[Tuple[int, int, float, bool, bool, bool]] = []  # (distance, -score, value, has_cur, is_pct, is_date)
    fallback: List[Tuple[int, float, bool, bool, bool]] = []  # (-score, value, has_cur, is_pct, is_date)

    for m in pattern.finditer(normalized):
        if name_spans:
            m_start = int(m.start())
            m_end = int(m.end())
            if any(span_start <= m_start and m_end <= span_end for span_start, span_end in name_spans):
                # Avoid treating product/model numbers embedded in the KPI name
                # (e.g., "365", "5G") as the KPI value.
                continue

        num_raw = (m.group("num") or "").strip()
        if not num_raw:
            continue
        try:
            base = float(num_raw.replace(",", ""))
        except ValueError:
            continue

        # Skip likely years
        if 1900 <= base <= 2100 and num_raw.isdigit() and len(num_raw) == 4:
            continue

        has_cur = bool((m.group("cur") or "").strip())

        # Skip date-day numbers (e.g., "January 30, 2022") to avoid picking the day
        # instead of the KPI value when the KPI name appears after the date.
        is_date_day = False
        if 1 <= base <= 31 and float(base).is_integer():
            before = normalized[max(0, int(m.start()) - 20) : int(m.start())]
            after = normalized[int(m.end()) : min(len(normalized), int(m.end()) + 20)]
            # "January 30" or "30 January" style date fragments.
            if re.search(r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s*$", before, re.IGNORECASE):
                is_date_day = True
            elif month_re.match(after.lstrip()[:12] or ""):
                is_date_day = True

        suf = (m.group("suf") or "").strip().lower()
        mult = default_mult
        is_percent = False
        if suf in {"b", "bn", "billion"}:
            mult = 1_000_000_000.0
        elif suf in {"m", "mn", "million"}:
            mult = 1_000_000.0
        elif suf in {"k", "thousand"}:
            mult = 1_000.0
        elif suf in {"t", "trillion"}:
            mult = 1_000_000_000_000.0
        elif suf == "%":
            mult = 1.0
            is_percent = True

        value = float(base * mult)

        score = 0
        if "," in num_raw:
            score += 6
        if abs(value) >= 1_000_000:
            score += 6
        elif abs(value) >= 1000:
            score += 5
        elif abs(value) >= 100:
            score += 3
        elif abs(value) >= 10:
            score += 1
        if mult != 1.0:
            score += 2
        if is_percent:
            score += 1
        if has_cur:
            score += 3

        if positions:
            distance = min(abs(int(m.start()) - int(p)) for p in positions)
            scored.append((int(distance), -int(score), float(value), has_cur, is_percent, is_date_day))
        else:
            fallback.append((-int(score), float(value), has_cur, is_percent, is_date_day))

    if scored:
        # Filter date-like numbers first.
        scored = [t for t in scored if not t[5]]
        if not scored:
            return _extract_number_from_excerpt(quote)

        def _match_pref_tuple(t: Tuple[int, int, float, bool, bool, bool]) -> Tuple[int, int, int, float]:
            distance, neg_score, value, has_cur, is_pct, _is_date = t
            if prefer_percent:
                pref_ok = bool(is_pct)
            elif prefer_currency:
                pref_ok = bool(has_cur) or abs(value) >= 1_000  # scaled totals often represent currency KPIs
            else:
                pref_ok = (not has_cur) and (not is_pct)
            # Prefer matches aligned with expected unit class, then distance/score.
            pref_rank = 0 if pref_ok else 1
            return (pref_rank, int(distance), int(neg_score), -abs(float(value)))

        scored.sort(key=_match_pref_tuple)
        return float(scored[0][2])

    if fallback:
        fallback = [t for t in fallback if not t[4]]
        if not fallback:
            return _extract_number_from_excerpt(quote)

        def _fallback_pref_tuple(t: Tuple[int, float, bool, bool, bool]) -> Tuple[int, int, float]:
            neg_score, value, has_cur, is_pct, _is_date = t
            if prefer_percent:
                pref_ok = bool(is_pct)
            elif prefer_currency:
                pref_ok = bool(has_cur) or abs(value) >= 1_000
            else:
                pref_ok = (not has_cur) and (not is_pct)
            pref_rank = 0 if pref_ok else 1
            return (pref_rank, int(neg_score), -abs(float(value)))

        fallback.sort(key=_fallback_pref_tuple)
        return float(fallback[0][1])

    # If the KPI name is present but we couldn't extract a number outside the name
    # itself, fail closed instead of falling back to a generic number extractor.
    if positions:
        return None
    return _extract_number_from_excerpt(quote)


def _extract_value_number_from_page_near_name(
    page_text: str,
    *,
    kpi_name: str,
    unit_hint: Optional[str] = None,
    window_chars: int = 1400,
) -> Optional[float]:
    """Extract a KPI value from the full page text when table cells omit the KPI name.

    We locate the KPI name on the page and then apply the same "nearest-number"
    heuristic within a bounded window. This avoids picking unrelated numbers from
    the value cell (dates/currencies) when the KPI name is only present in the
    row/column header.
    """
    if not page_text:
        return None

    variants = _kpi_name_variants(kpi_name)
    if not variants:
        return None

    page_norm = _normalize_for_matching(page_text)
    if not page_norm:
        return None

    positions: List[int] = []
    for v in variants:
        if not v:
            continue
        start = 0
        while True:
            pos = page_norm.find(v, start)
            if pos < 0:
                break
            positions.append(int(pos))
            start = pos + max(1, len(v))
            if len(positions) >= 8:
                break
        if len(positions) >= 8:
            break

    if not positions:
        return None

    for pos in positions[:8]:
        start = max(0, int(pos) - int(window_chars))
        end = min(len(page_norm), int(pos) + int(window_chars))
        snippet = page_norm[start:end]
        value = _extract_value_number_from_quote_near_name(
            snippet, kpi_name=kpi_name, unit_hint=unit_hint
        )
        if value is not None:
            return float(value)

    return None


def _has_name_backed_value_evidence(
    name: str, *, evidence: List[Dict[str, Any]], page_texts: List[str]
) -> bool:
    """Return True if the KPI name appears in/near the value evidence.

    Prevents mislabeling like returning KPI name "Orders" when the filing clearly
    says "Transactions" on the same page.
    """
    variants = _kpi_name_variants(name)
    if not variants:
        return False

    value_evidence = [ev for ev in (evidence or []) if str(ev.get("type") or "") == "value"]
    if not value_evidence:
        return False

    # If any evidence quote contains the name, we’re good (works for text filings too).
    for ev in evidence or []:
        quote = str(ev.get("quote") or "")
        qn = _normalize_for_matching(quote)
        if any(v and v in qn for v in variants):
            return True

    # For PDFs, allow table cases where the value cell lacks the name but the page contains it.
    if len(page_texts) < 2:
        return False

    for ev in value_evidence:
        try:
            page = int(ev.get("page") or 0)
        except Exception:  # noqa: BLE001
            continue
        if page < 1 or page > len(page_texts):
            continue
        page_norm = _normalize_for_matching(page_texts[page - 1] or "")
        if not page_norm:
            continue
        if not any(v and v in page_norm for v in variants):
            continue

        quote_norm = _normalize_for_matching(str(ev.get("quote") or ""))
        if not quote_norm or len(quote_norm) < 10:
            return True

        pos_quote = page_norm.find(quote_norm)
        if pos_quote < 0:
            return True

        positions: List[int] = []
        for v in variants:
            start = 0
            while True:
                pos = page_norm.find(v, start)
                if pos < 0:
                    break
                positions.append(int(pos))
                start = pos + max(1, len(v))
                if len(positions) >= 24:
                    break
            if len(positions) >= 24:
                break

        if not positions:
            return True

        nearest = min(abs(int(pos_quote) - int(p)) for p in positions)
        if nearest <= 1600:
            return True

    return False


def _sanitize_evidence_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    quote = str(item.get("quote") or "").replace("…", "...").strip()
    quote = _strip_page_header_prefix(quote)
    if quote.endswith("..."):
        quote = quote[:-3].rstrip()
    if quote.startswith("..."):
        quote = quote[3:].lstrip()
    if not quote:
        return None
    if _looks_like_garbled_text(quote):
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
        value_like = _looks_like_value_quote(quote, kpi_name=kpi_name)

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
    kpi_name: str = "",
    unit_hint: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """Infer/duplicate evidence types to satisfy (definition,value) requirements when possible."""
    has_value = any(str(ev.get("type") or "") == "value" for ev in verified_evidence)
    has_definition = any(str(ev.get("type") or "") == "definition" for ev in verified_evidence)

    value_like = [
        ev
        for ev in verified_evidence
        if _looks_like_value_quote(
            str(ev.get("quote") or ""), kpi_name=kpi_name, unit_hint=unit_hint
        )
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

    # Extract verification text up-front so we can verify model quotes.
    # For PDFs: per-page text layer (and optionally OCR later).
    # For plain text/HTML: treat as a single "page" so we can still verify excerpts.
    page_texts, unverifiable_reason = _extract_verification_pages(
        file_bytes=file_bytes, mime_type=mime_type
    )
    if unverifiable_reason:
        if (mime_type or "").startswith("text/"):
            # Never fail early for text inputs; we can always verify against the full text.
            unverifiable_reason = None
            try:
                text = file_bytes.decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                text = ""
            page_texts = [text]
        elif str(unverifiable_reason) in ("no_text_layer", "garbled_text_layer"):
            # Defer OCR to escalation. For scanned PDFs we try OCR + regex before giving up.
            try:
                from .ocr_fallback import extract_text_with_ocr_from_pdf

                ocr_pages, ocr_debug = extract_text_with_ocr_from_pdf(file_bytes)
                debug["ocr_debug"] = ocr_debug
                if ocr_pages and sum(len(t or "") for t in ocr_pages) >= 800:
                    page_texts = ocr_pages
                    unverifiable_reason = None
            except Exception as exc:  # noqa: BLE001
                debug["ocr_exception"] = str(exc)[:200]

    if unverifiable_reason:
        debug["reason"] = unverifiable_reason
        return None, debug
    debug["verification_pages"] = len(page_texts)

    # ---------------------------------------------------------------------
    # Model input selection
    # ---------------------------------------------------------------------
    # Gemini can be inconsistent at extracting metrics directly from binary PDFs,
    # especially for scanned filings and iXBRL-heavy artifacts. Since we already
    # extracted per-page text (and ran OCR when needed) for strict verification,
    # prefer uploading a plain-text, page-marked representation to the model.
    upload_bytes = file_bytes
    upload_mime = str(mime_type or "")
    if str(mime_type or "") == "application/pdf" and page_texts and sum(len(t or "") for t in page_texts) >= 800:
        try:
            paged_text = "\n\n".join(
                [f"PAGE {idx + 1}:\n{page_texts[idx] or ''}".rstrip() for idx in range(len(page_texts))]
            ).strip()
        except Exception:  # noqa: BLE001
            paged_text = ""
        if paged_text:
            paged_bytes = paged_text.encode("utf-8", errors="ignore")
            if 0 < len(paged_bytes) <= int(config.max_upload_bytes):
                upload_bytes = paged_bytes
                upload_mime = "text/plain"
                debug["model_upload_mode"] = "paged_text_from_pdf"

    def _try_regex_page_fallback(reason: str) -> Optional[SpotlightKpiCandidate]:
        """Deterministic last-resort KPI extraction using page-scoped regex patterns."""
        candidates: List[Dict[str, Any]] = []
        try:
            regex_candidates = extract_kpis_with_regex_by_page(
                page_texts, company_name, max_results=int(config.max_candidates)
            )
            candidates.extend(regex_candidates)
        except Exception as exc:  # noqa: BLE001
            debug[f"{reason}_regex_page_error"] = str(exc)[:200]
            return None

        debug[f"{reason}_regex_page_candidates"] = len(candidates)

        # If the curated regex list yields nothing, try a looser table-row scan on
        # pages that look like they contain "Key metrics" / "Operating metrics".
        if not candidates:
            try:
                table_candidates = extract_kpis_with_key_metrics_table_scan_by_page(
                    page_texts, company_name, max_results=int(config.max_candidates)
                )
                debug[f"{reason}_key_metrics_table_candidates"] = len(table_candidates)
                candidates.extend(table_candidates)
            except Exception as exc:  # noqa: BLE001
                debug[f"{reason}_key_metrics_table_error"] = str(exc)[:200]

        if not candidates:
            return None

        # Prefer more representative KPIs when multiple fallbacks exist.
        ranked: List[Tuple[int, Dict[str, Any]]] = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            name = str(cand.get("name") or "").strip()
            if not name:
                continue
            unit = str(cand.get("unit") or "").strip() or None
            value_f = _coerce_number(cand.get("value"))
            if value_f is None:
                value_f = _coerce_number(cand.get("most_recent_value"))
            if value_f is None:
                continue
            ev_raw = cand.get("evidence")
            ev_list = ev_raw if isinstance(ev_raw, list) else []
            score = _candidate_importance_score(
                name=name, unit=unit, value=float(value_f), evidence=ev_list
            )
            ranked.append((int(score), cand))
        ranked.sort(key=lambda t: t[0], reverse=True)

        for _score, cand in ranked:
            if not isinstance(cand, dict):
                continue
            name = str(cand.get("name") or "").strip()
            if not name or _is_generic_financial_metric(name):
                continue

            ev_raw = cand.get("evidence")
            ev_list = ev_raw if isinstance(ev_raw, list) else []
            if not ev_list:
                continue

            verified, _ = _verify_evidence_against_document(ev_list, page_texts=page_texts, kpi_name=name)
            if not verified:
                continue

            verified = _dedupe_evidence(verified)
            verified, has_value, has_definition = _enrich_evidence_types(
                verified,
                require_value=bool(config.require_value_evidence),
                require_definition=bool(config.require_definition_evidence),
                kpi_name=name,
            )

            if config.require_value_evidence and not has_value:
                continue
            if config.require_definition_evidence and not has_definition:
                continue

            if _looks_like_non_operating_kpi(name, evidence=verified):
                continue

            value_f = _coerce_number(cand.get("value"))
            if value_f is None:
                value_f = _coerce_number(cand.get("most_recent_value"))
            if value_f is None:
                continue

            out = dict(cand)
            out["name"] = name
            out["value"] = float(value_f)
            out["confidence"] = max(float(config.min_confidence), float(out.get("confidence") or 0.72))
            out["evidence"] = verified

            existing_flags = out.get("ban_flags")
            flags = list(existing_flags) if isinstance(existing_flags, list) else []
            if "regex_page_fallback" not in flags:
                flags.append("regex_page_fallback")
            out["ban_flags"] = flags

            if not str(out.get("why_company_specific") or "").strip():
                out["why_company_specific"] = "Disclosed as an operating metric in the company's filing."

            source_quote = str(out.get("source_quote") or "").strip()
            if not source_quote and verified:
                try:
                    page_i = int(verified[0].get("page") or 1)
                except Exception:  # noqa: BLE001
                    page_i = 1
                source_quote = f"[p. {page_i}] {str(verified[0].get('quote') or '').strip()}"
                out["source_quote"] = source_quote

            return out

        return None

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
            data=upload_bytes,
            mime_type=str(upload_mime),
            display_name=f"{company_name[:50]}-filing",
            timeout_seconds=upload_timeout,
        )
        file_uri = str(file_obj.get("uri") or "")
        file_mime = str(file_obj.get("mimeType") or file_obj.get("mime_type") or str(upload_mime))
    except Exception as exc:  # noqa: BLE001
        debug["reason"] = "upload_failed"
        debug["upload_error"] = str(exc)[:500]
        kpi = _try_regex_page_fallback("upload_failed")
        if kpi:
            debug["fallback_used"] = "regex_page"
            debug["fallback_reason"] = "upload_failed"
            return kpi, debug
        return None, debug

    if not file_uri:
        debug["reason"] = "no_file_uri"
        kpi = _try_regex_page_fallback("no_file_uri")
        if kpi:
            debug["fallback_used"] = "regex_page"
            debug["fallback_reason"] = "no_file_uri"
            return kpi, debug
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
            kpi = _try_regex_page_fallback("pass1_failed")
            if kpi:
                debug["fallback_used"] = "regex_page"
                debug["fallback_reason"] = "pass1_failed"
                return kpi, debug
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
        kpi = _try_regex_page_fallback("pass1_invalid_json")
        if kpi:
            debug["fallback_used"] = "regex_page"
            debug["fallback_reason"] = "pass1_invalid_json"
            return kpi, debug
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
            kpi = _try_regex_page_fallback("pass1_no_candidates")
            if kpi:
                debug["fallback_used"] = "regex_page"
                debug["fallback_reason"] = "pass1_no_candidates"
                return kpi, debug
            return None, debug

    def _canon_name_key(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    # Pre-verify Pass 1 candidates so we can:
    # - fall back safely when Pass 2 output is invalid, and
    # - repair missing evidence when Pass 2 drops/garbles definition/value quotes.
    pass1_verified_by_name: Dict[str, List[Dict[str, Any]]] = {}
    pass1_fallback_candidate: Optional[SpotlightKpiCandidate] = None
    pass1_scored_candidates: List[Tuple[int, SpotlightKpiCandidate]] = []

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
            kpi_name=name,
        )

        # Reject candidates where the KPI name is not actually present in/near the evidence.
        if not _has_name_backed_value_evidence(name, evidence=verified, page_texts=page_texts):
            continue

        # Reject footnote/one-off disclosure tables (e.g., related party transactions).
        if _looks_like_non_operating_kpi(name, evidence=verified):
            continue

        pass1_verified_by_name[_canon_name_key(name)] = verified

        unit = str(c.get("unit") or "").strip() or None
        unit = _sanitize_unit(unit, kpi_name=name, evidence=verified)
        period = str(c.get("period") or "").strip() or ""
        most_recent_value = str(c.get("most_recent_value") or "").strip()

        name_variants = _kpi_name_variants(name)

        value_f: Optional[float] = None
        value_page_hint: Optional[int] = None
        for ev in verified:
            if str(ev.get("type") or "") == "value":
                quote_text = str(ev.get("quote") or "")
                try:
                    value_page_hint = int(ev.get("page") or 0) or None
                except Exception:  # noqa: BLE001
                    value_page_hint = None

                value_f = _extract_value_number_from_quote_near_name(
                    quote_text, kpi_name=name, unit_hint=unit
                )
                quote_norm = _normalize_for_matching(quote_text)
                quote_has_name = bool(
                    name_variants and any(v and v in quote_norm for v in name_variants)
                )
                if (
                    value_f is not None
                    and not quote_has_name
                    and isinstance(value_page_hint, int)
                    and 1 <= value_page_hint <= len(page_texts)
                ):
                    page_value = _extract_value_number_from_page_near_name(
                        page_texts[value_page_hint - 1] or "", kpi_name=name, unit_hint=unit
                    )
                    if page_value is not None:
                        value_f = page_value
                if value_f is not None:
                    break
        if value_f is None:
            # If value evidence quotes omit the KPI name (common in tables), the numeric value
            # may still exist on the same page near the KPI name. Search evidence pages first.
            pages_to_try: List[int] = []
            seen_pages: set[int] = set()
            for typ in ("value", "definition", "context"):
                for ev in verified:
                    if str(ev.get("type") or "") != typ:
                        continue
                    try:
                        p = int(ev.get("page") or 0)
                    except Exception:  # noqa: BLE001
                        continue
                    if p < 1 or p > len(page_texts) or p in seen_pages:
                        continue
                    seen_pages.add(p)
                    pages_to_try.append(p)

            tried_pages: set[int] = set()
            for p in pages_to_try:
                for page in (p, p - 1, p + 1):
                    if page in tried_pages:
                        continue
                    tried_pages.add(page)
                    if page < 1 or page > len(page_texts):
                        continue
                    page_value = _extract_value_number_from_page_near_name(
                        page_texts[page - 1] or "", kpi_name=name, unit_hint=unit
                    )
                    if page_value is not None:
                        value_f = float(page_value)
                        if value_page_hint is None:
                            value_page_hint = int(page)
                        break
                if value_f is not None:
                    break
        if value_f is None:
            continue

        def _quote_has_name_and_number(q: str) -> bool:
            qn = _normalize_for_matching(q or "")
            if not qn:
                return False
            if name_variants and not any(v and v in qn for v in name_variants):
                return False
            return (
                _extract_value_number_from_quote_near_name(
                    q or "", kpi_name=name, unit_hint=unit
                )
                is not None
            )

        picked_ev: Optional[Dict[str, Any]] = None
        for ev in verified:
            if str(ev.get("type") or "") == "value" and _quote_has_name_and_number(
                str(ev.get("quote") or "")
            ):
                picked_ev = ev
                break
        if picked_ev is None:
            for ev in verified:
                if str(ev.get("type") or "") in ("value", "context") and _quote_has_name_and_number(
                    str(ev.get("quote") or "")
                ):
                    picked_ev = ev
                    break
        if picked_ev is None:
            for ev in verified:
                if str(ev.get("type") or "") == "value":
                    picked_ev = ev
                    break
        if picked_ev is None and verified:
            picked_ev = verified[0]

        source_quote = str((picked_ev or {}).get("quote") or "").strip()
        try:
            source_page = int((picked_ev or {}).get("page") or 0) or None
        except Exception:  # noqa: BLE001
            source_page = None
        if source_quote and source_page:
            source_quote = f"[p. {source_page}] {source_quote}"

        candidate_obj: SpotlightKpiCandidate = {
            "name": name,
            "value": float(value_f),
            "unit": unit,
            "prior_value": None,
            "chart_type": "metric",
            "description": _build_compact_kpi_description(
                what_it_measures=str(c.get("what_it_measures") or ""),
                why_company_specific=str(c.get("why_company_specific") or ""),
            ),
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

        score = _candidate_importance_score(
            name=name, unit=unit, value=float(value_f), evidence=verified
        )
        pass1_scored_candidates.append((int(score), candidate_obj))

    if pass1_scored_candidates:
        pass1_scored_candidates.sort(key=lambda t: t[0], reverse=True)
        pass1_fallback_candidate = pass1_scored_candidates[0][1]

    debug["pass1_candidate_ranked_count"] = len(pass1_scored_candidates)
    debug["pass1_verified_fallback_available"] = bool(pass1_fallback_candidate)

    # Sanitize candidates (keep prompts bounded for Pass 2)
    score_by_name: Dict[str, int] = {}
    for score, cand in pass1_scored_candidates:
        key = _canon_name_key(str(cand.get("name") or ""))
        if not key:
            continue
        prev = score_by_name.get(key)
        if prev is None or int(score) > int(prev):
            score_by_name[key] = int(score)

    sanitized_candidates: List[Dict[str, Any]] = []
    for c in raw_candidates[: int(config.max_candidates)]:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        if _is_generic_financial_metric(name):
            continue
        if _canon_name_key(name) not in pass1_verified_by_name:
            # Only keep candidates whose evidence was verified against the document.
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

    if sanitized_candidates and score_by_name:
        sanitized_candidates.sort(
            key=lambda c: score_by_name.get(_canon_name_key(str(c.get("name") or "")), -9999),
            reverse=True,
        )

    if not sanitized_candidates:
        debug["reason"] = "pass1_no_valid_candidates"
        kpi = _try_regex_page_fallback("pass1_no_valid_candidates")
        if kpi:
            debug["fallback_used"] = "regex_page"
            debug["fallback_reason"] = "pass1_no_valid_candidates"
            return kpi, debug
        return None, debug

    # ---------------------------------------------------------------------
    # PASS 2: choose best KPI and output final strict schema (text-only)
    # ---------------------------------------------------------------------
    if _remaining_time() <= 0:
        debug["reason"] = "timeout_before_pass2"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        kpi = _try_regex_page_fallback("timeout_before_pass2")
        if kpi:
            debug["fallback_used"] = "regex_page"
            debug["fallback_reason"] = "timeout_before_pass2"
            return kpi, debug
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
        kpi_name=name,
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
                kpi_name=name,
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

    # Hard guard: KPI name must be supported by the evidence (prevents mislabeled KPIs).
    if has_value and not _has_name_backed_value_evidence(
        name, evidence=verified_evidence, page_texts=page_texts
    ):
        debug["reason"] = "kpi_name_not_in_evidence_context"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    # Reject obvious footnote/schedule disclosures even if verifiable.
    if _looks_like_non_operating_kpi(name, evidence=verified_evidence):
        debug["reason"] = "non_operating_metric"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

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
    unit = _sanitize_unit(unit, kpi_name=name, evidence=verified_evidence)
    period = str(selected.get("period") or "").strip() or ""
    most_recent_value = str(selected.get("most_recent_value") or "").strip()

    name_variants = _kpi_name_variants(name)

    value_f: Optional[float] = None
    # Prefer the value quote(s) if present: they are verifiable evidence.
    value_page_hint: Optional[int] = None
    value_evidence = [
        ev for ev in verified_evidence if str(ev.get("type") or "") == "value"
    ]
    for ev in value_evidence:
        quote_text = str(ev.get("quote") or "")
        try:
            page_hint = int(ev.get("page") or 0) or None
        except Exception:  # noqa: BLE001
            page_hint = None

        if value_page_hint is None and isinstance(page_hint, int):
            value_page_hint = page_hint

        value_f = _extract_value_number_from_quote_near_name(
            quote_text, kpi_name=name, unit_hint=unit
        )
        quote_norm = _normalize_for_matching(quote_text)
        quote_has_name = bool(
            name_variants and any(v and v in quote_norm for v in name_variants)
        )
        if (
            value_f is not None
            and not quote_has_name
            and isinstance(page_hint, int)
            and 1 <= page_hint <= len(page_texts)
        ):
            page_value = _extract_value_number_from_page_near_name(
                page_texts[page_hint - 1] or "", kpi_name=name, unit_hint=unit
            )
            if page_value is not None:
                value_f = page_value

        if value_f is not None:
            break
    if value_f is None:
        pages_to_try: List[int] = []
        seen_pages: set[int] = set()
        for typ in ("value", "definition", "context"):
            for ev in verified_evidence:
                if str(ev.get("type") or "") != typ:
                    continue
                try:
                    p = int(ev.get("page") or 0)
                except Exception:  # noqa: BLE001
                    continue
                if p < 1 or p > len(page_texts) or p in seen_pages:
                    continue
                seen_pages.add(p)
                pages_to_try.append(p)

        tried_pages: set[int] = set()
        for p in pages_to_try:
            for page in (p, p - 1, p + 1):
                if page in tried_pages:
                    continue
                tried_pages.add(page)
                if page < 1 or page > len(page_texts):
                    continue
                page_value = _extract_value_number_from_page_near_name(
                    page_texts[page - 1] or "", kpi_name=name, unit_hint=unit
                )
                if page_value is not None:
                    value_f = float(page_value)
                    if value_page_hint is None:
                        value_page_hint = int(page)
                    break
            if value_f is not None:
                break
    if value_f is None:
        debug["reason"] = "unparseable_value"
        if pass1_fallback_candidate:
            debug["fallback_used"] = "pass1_candidate"
            return pass1_fallback_candidate, debug
        return None, debug

    # Prefer a "value" evidence quote for the UI source quote.
    def _quote_has_name_and_number(q: str) -> bool:
        qn = _normalize_for_matching(q or "")
        if not qn:
            return False
        if name_variants and not any(v and v in qn for v in name_variants):
            return False
        return (
            _extract_value_number_from_quote_near_name(q or "", kpi_name=name, unit_hint=unit)
            is not None
        )

    picked_ev: Optional[Dict[str, Any]] = None
    for ev in verified_evidence:
        if str(ev.get("type") or "") == "value" and _quote_has_name_and_number(
            str(ev.get("quote") or "")
        ):
            picked_ev = ev
            break
    if picked_ev is None:
        for ev in verified_evidence:
            if str(ev.get("type") or "") in ("value", "context") and _quote_has_name_and_number(
                str(ev.get("quote") or "")
            ):
                picked_ev = ev
                break
    if picked_ev is None:
        for ev in verified_evidence:
            if str(ev.get("type") or "") == "value":
                picked_ev = ev
                break
    if picked_ev is None and verified_evidence:
        picked_ev = verified_evidence[0]

    source_quote = str((picked_ev or {}).get("quote") or "").strip()
    try:
        source_page = int((picked_ev or {}).get("page") or 0) or None
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
        "description": _build_compact_kpi_description(
            what_it_measures=str(selected.get("what_it_measures") or ""),
            why_company_specific=str(selected.get("why_company_specific") or ""),
        )
        if has_definition
        else None,
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

    selected_score = _candidate_importance_score(
        name=name, unit=unit, value=float(value_f), evidence=verified_evidence
    )
    debug["selected_importance_score"] = int(selected_score)
    if pass1_scored_candidates and pass1_fallback_candidate:
        try:
            best_score = int(pass1_scored_candidates[0][0])
        except Exception:  # noqa: BLE001
            best_score = int(selected_score)
        debug["pass1_best_importance_score"] = int(best_score)
        if best_score >= int(selected_score) + 18:
            debug["fallback_used"] = "pass1_candidate"
            debug["fallback_reason"] = "prefer_higher_importance_candidate"
            return pass1_fallback_candidate, debug

    debug["total_time_ms"] = int((time.monotonic() - started) * 1000)
    return candidate, debug
