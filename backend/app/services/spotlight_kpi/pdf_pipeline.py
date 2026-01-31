from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None  # type: ignore[assignment]

from .json_parse import parse_json_object
from .pipeline_utils import safe_json_retry_prompt, thinking_config
from .types import SpotlightKpiCandidate


@dataclass(frozen=True)
class PdfKpiCoverage:
    original_page_count: int
    subset_page_count: int
    subset_to_original_pages: List[int]  # 1-based original page numbers


def _pdf_page_count(pdf_bytes: bytes) -> int:
    if not pdf_bytes or fitz is None:
        return 0
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return 0


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value is not None:
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        is_paren_negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()").strip()
        cleaned = cleaned.replace(",", "")
        cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").strip()
        cleaned = cleaned.replace("%", "").strip()

        mult = 1.0
        lower = cleaned.lower()
        if lower.endswith("b") and re.match(r"^-?\d+(?:\.\d+)?b$", lower):
            mult = 1_000_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("m") and re.match(r"^-?\d+(?:\.\d+)?m$", lower):
            mult = 1_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("k") and re.match(r"^-?\d+(?:\.\d+)?k$", lower):
            mult = 1_000.0
            cleaned = cleaned[:-1]

        try:
            num = float(cleaned) * mult
            return -num if is_paren_negative else num
        except Exception:  # noqa: BLE001
            return None
    return None


def _extract_number_from_excerpt(excerpt: str) -> Optional[float]:
    """Extract a plausible KPI number from a verbatim excerpt (best-effort)."""
    text = (excerpt or "").strip()
    if not text:
        return None

    # Common multiplier words (applies to the first matched number).
    lower = text.lower()
    default_mult = 1.0
    if "in billions" in lower or "billion" in lower:
        default_mult = 1_000_000_000.0
    elif "in millions" in lower or "million" in lower:
        default_mult = 1_000_000.0
    elif "in thousands" in lower or "thousand" in lower:
        default_mult = 1_000.0

    # Find candidates like 1,234.56, (123), 12%, 3.4B, 5m, etc.
    pattern = re.compile(
        r"(?P<neg>\()?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suf>%|[bBkKmM]|bn|mn|billion|million|thousand)?\s*(?P<neg2>\))?",
        re.IGNORECASE,
    )
    candidates: List[Tuple[float, int, bool, bool]] = []
    # tuple: (value, score_seed, has_comma, is_percent)
    for m in pattern.finditer(text):
        raw = (m.group("num") or "").replace(",", "").strip()
        if not raw:
            continue
        has_comma = "," in (m.group("num") or "")
        try:
            val = float(raw)
        except ValueError:
            continue

        # Skip likely years/page refs (e.g., 2024, 2025).
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
        is_percent = suf == "%"

        neg = bool(m.group("neg")) and bool(m.group("neg2"))
        out = val * mult
        out = -out if neg else out

        score = 0
        if has_comma:
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
        if is_percent:
            score += 1

        candidates.append((out, score, has_comma, is_percent))

    if not candidates:
        return None

    # If we saw any "big" candidate, ignore small integers that commonly appear in KPI names
    # (e.g. "≥3 devices") to avoid picking the wrong number.
    has_big = any(abs(v) >= 100 or has_comma for v, _s, has_comma, _p in candidates)
    filtered = (
        [c for c in candidates if abs(c[0]) >= 50 or c[2] or c[3]]
        if has_big
        else candidates
    )
    if not filtered:
        filtered = candidates

    # Pick highest score; tie-break on magnitude.
    filtered.sort(key=lambda t: (t[1], abs(t[0])), reverse=True)
    return float(filtered[0][0])


def _select_pdf_pages_for_kpis(
    pdf_bytes: bytes,
    *,
    full_pdf_max_pages: int,
    full_pdf_max_bytes: int,
    max_subset_pages: int,
) -> Tuple[bytes, PdfKpiCoverage]:
    """Prefer full PDF when safe; otherwise build a smaller PDF slice.

    We want PDF-native understanding, but we still need a hard cap so a very large
    filing doesn't blow through the per-summary budget.

    Important: avoid extracting text from every page for keyword scoring. That can
    take minutes for very large PDFs. Instead, select a representative subset by
    sampling pages across the document (plus head/tail bias).
    """
    # Deprecated: callers now prefer the full PDF to match UI behavior.
    if not pdf_bytes:
        coverage = PdfKpiCoverage(0, 0, [])
        return b"", coverage

    if fitz is None:
        # Can't count pages or slice. Enforce a byte cap anyway to keep cost bounded.
        if len(pdf_bytes) > int(full_pdf_max_bytes):
            coverage = PdfKpiCoverage(0, 0, [])
            return b"", coverage
        coverage = PdfKpiCoverage(0, 0, [])
        return pdf_bytes, coverage

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    original_pages = int(doc.page_count)

    # Full-document path (preferred).
    if (
        original_pages > 0
        and original_pages <= int(full_pdf_max_pages)
        and len(pdf_bytes) <= int(full_pdf_max_bytes)
    ):
        coverage = PdfKpiCoverage(
            original_page_count=original_pages,
            subset_page_count=original_pages,
            subset_to_original_pages=[i + 1 for i in range(original_pages)],
        )
        return pdf_bytes, coverage

    subset_cap = max(1, int(max_subset_pages))

    head_n = min(6, original_pages)
    tail_n = min(6, max(0, original_pages - head_n))

    selected_set: set[int] = set(range(head_n))
    selected_set.update(range(max(0, original_pages - tail_n), original_pages))

    remaining = subset_cap - len(selected_set)
    if remaining > 0 and original_pages > 0:
        # Evenly spaced anchors across the full document.
        step = max(1, original_pages // max(1, remaining))
        for p in range(0, original_pages, step):
            selected_set.add(p)
            if len(selected_set) >= subset_cap:
                break

    # Add neighbors for context but keep within cap.
    expanded: set[int] = set()
    for p in sorted(selected_set):
        for n in (p - 1, p, p + 1):
            if 0 <= n < original_pages:
                expanded.add(n)
            if len(expanded) >= subset_cap:
                break
        if len(expanded) >= subset_cap:
            break
    selected_set = set(sorted(expanded)[:subset_cap])

    out = fitz.open()
    for p in sorted(selected_set):
        out.insert_pdf(doc, from_page=p, to_page=p)
    subset_bytes = out.write()

    subset_to_original_pages = [int(p) + 1 for p in sorted(selected_set)]
    coverage = PdfKpiCoverage(
        original_page_count=original_pages,
        subset_page_count=len(subset_to_original_pages),
        subset_to_original_pages=subset_to_original_pages,
    )
    return subset_bytes, coverage


def extract_company_specific_spotlight_kpi_from_pdf(
    gemini_client: Any,
    *,
    pdf_bytes: bytes,
    company_name: str,
    max_subset_pages: int = 35,
    full_pdf_max_pages: int = 35,
    full_pdf_max_bytes: int = 12_000_000,
    max_attempts_per_pass: int = 2,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """PDF-native, hardened multi-pass pipeline (Gemini 3 Flash preview).

    Pass 1 uses the PDF. Passes 2–4 do NOT resend the PDF (cost control).

    Pass 1: discovery (wide net, no judgment)
    Pass 2: filter (kill boilerplate/generic)
    Pass 3: rank (pick 1 KPI, scoring + evidence)
    Pass 4: verifier (veto weak picks)

    Returns (candidate_or_none, debug_info).
    """
    debug: Dict[str, Any] = {
        "mode": "pdf_pipeline_v2",
        "company_name": str(company_name or ""),
    }
    if not gemini_client or not pdf_bytes or not (company_name or "").strip():
        debug["reason"] = "missing_inputs"
        return None, debug

    # NOTE: User expectation is "use the entire PDF". We upload the full bytes and
    # rely on strict timeouts + single-file pass-1 to keep latency bounded.
    _ = max_subset_pages, full_pdf_max_pages, full_pdf_max_bytes

    max_upload_bytes = int(os.getenv("SPOTLIGHT_PDF_MAX_UPLOAD_BYTES", "50000000") or "50000000")
    if max_upload_bytes > 0 and len(pdf_bytes) > max_upload_bytes:
        debug["reason"] = "pdf_too_large_for_upload"
        debug["pdf_size_bytes"] = int(len(pdf_bytes))
        debug["max_upload_bytes"] = int(max_upload_bytes)
        return None, debug

    pipeline_timeout = os.getenv("SPOTLIGHT_PDF_PIPELINE_TIMEOUT_SECONDS", "40")
    try:
        pipeline_timeout_s = max(10.0, float(pipeline_timeout))
    except ValueError:
        pipeline_timeout_s = 40.0
    started = time.monotonic()

    pages = _pdf_page_count(pdf_bytes)
    debug["pdf_strategy"] = "full_pdf"
    debug["pdf_coverage"] = {
        "original_page_count": pages,
        "subset_page_count": pages,
        "subset_to_original_pages": [i + 1 for i in range(pages)] if pages > 0 else [],
    }

    if (time.monotonic() - started) > pipeline_timeout_s:
        debug["reason"] = "pipeline_timeout_before_upload"
        return None, debug

    file_obj = gemini_client.upload_file_bytes(
        data=pdf_bytes,
        mime_type="application/pdf",
        display_name=f"{company_name}-filing.pdf",
        timeout_seconds=min(25.0, max(10.0, pipeline_timeout_s / 2)),
    )
    file_uri = str(file_obj.get("uri") or "")
    file_mime = str(
        file_obj.get("mimeType") or file_obj.get("mime_type") or "application/pdf"
    )
    debug["file_uri_present"] = bool(file_uri)
    if not file_uri:
        debug["reason"] = "file_upload_failed"
        return None, debug

    page_map_block = ""

    def _call_pass(
        *,
        pass_name: str,
        thinking_level: str,
        prompt: str,
        max_output_tokens: int,
        with_file: bool,
        timeout_seconds: float,
    ) -> str:
        remaining = pipeline_timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("spotlight_pdf_pipeline_timeout")
        timeout_seconds = min(float(timeout_seconds), max(3.0, float(remaining)))

        gen_cfg: Dict[str, Any] = {
            "temperature": 0.1,
            "maxOutputTokens": int(max_output_tokens),
            "responseMimeType": "application/json",
        }
        gen_cfg.update(thinking_config(thinking_level))

        if with_file:
            try:
                return gemini_client.stream_generate_content_with_file_uri(
                    file_uri=file_uri,
                    file_mime_type=file_mime,
                    prompt=prompt,
                    stage_name=pass_name,
                    expected_tokens=max(400, int(max_output_tokens)),
                    generation_config_override=gen_cfg,
                    timeout_seconds=timeout_seconds,
                )
            except TypeError:
                return gemini_client.stream_generate_content_with_file_uri(
                    file_uri=file_uri,
                    file_mime_type=file_mime,
                    prompt=prompt,
                    stage_name=pass_name,
                    expected_tokens=max(400, int(max_output_tokens)),
                    generation_config_override=gen_cfg,
                )

        try:
            return gemini_client.stream_generate_content(
                prompt,
                stage_name=pass_name,
                expected_tokens=max(200, int(max_output_tokens)),
                generation_config_override=gen_cfg,
                timeout_seconds=timeout_seconds,
                retry=False,
            )
        except TypeError:
            return gemini_client.stream_generate_content(
                prompt,
                stage_name=pass_name,
                expected_tokens=max(200, int(max_output_tokens)),
                generation_config_override=gen_cfg,
            )

    # Pass 1 — discovery (PDF-native)
    pass1_prompt = f"""
You are reading a corporate filing PDF for {company_name}.{page_map_block}

Task: Identify ALL management-defined metrics/KPIs and operational measures used in the PDF.

Rules:
- Do NOT judge importance.
- Do NOT invent values.
- For every metric you list, include a VERBATIM excerpt from the PDF that contains the metric name/definition/value.
- If you can cite a page, set page_ref to the ORIGINAL page number (e.g., "original_page_57"). If a subset mapping is provided above, use it.
- Output STRICT JSON only.

JSON schema:
{{
  "metrics": [
    {{
      "name_as_written": "string",
      "definition_or_context": "string|null",
      "page_ref": "string|null",
      "excerpt": "string"
    }}
  ]
}}
""".strip()

    pass1_raw = ""
    pass1 = None
    for attempt in range(int(max_attempts_per_pass)):
        pass1_raw = _call_pass(
            pass_name="KPI Pass 1 (Discovery)",
            thinking_level="minimal",
            prompt=pass1_prompt
            if attempt == 0
            else safe_json_retry_prompt("Pass 1", bad_output=pass1_raw),
            max_output_tokens=1800,
            with_file=True,
            timeout_seconds=25.0,
        )
        pass1 = parse_json_object(pass1_raw)
        if pass1 and isinstance(pass1.get("metrics"), list):
            break
        pass1 = None

    if not pass1:
        debug["reason"] = "pass1_invalid_json"
        debug["pass1_raw_head"] = (pass1_raw or "")[:800]
        return None, debug

    metrics = [m for m in (pass1.get("metrics") or []) if isinstance(m, dict)]
    debug["pass1_metrics_count"] = len(metrics)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for m in metrics:
        name = str(m.get("name_as_written") or "").strip()
        if not name:
            continue
        key = re.sub(r"\s+", " ", name).strip().lower()
        if key in seen:
            continue
        seen.add(key)

        excerpt = str(m.get("excerpt") or "").strip()
        if len(excerpt) > 420:
            excerpt = excerpt[:420].rstrip() + "…"

        deduped.append(
            {
                "name_as_written": name,
                "definition_or_context": (
                    str(m.get("definition_or_context")).strip()
                    if m.get("definition_or_context") is not None
                    else None
                ),
                "page_ref": (
                    str(m.get("page_ref")).strip()
                    if m.get("page_ref") is not None
                    else None
                ),
                "excerpt": excerpt,
            }
        )

    if len(deduped) > 80:
        deduped = deduped[:80]
        debug["pass1_metrics_clamped"] = True

    subset_original_pages = set(
        int(p)
        for p in (debug.get("pdf_coverage") or {}).get("subset_to_original_pages", [])
        if isinstance(p, int)
    )
    cited_pages: set[int] = set()
    for m in deduped:
        ref = str(m.get("page_ref") or "").strip()
        m1 = re.search(r"original_page_(\d+)", ref)
        if not m1:
            continue
        try:
            cited_pages.add(int(m1.group(1)))
        except Exception:  # noqa: BLE001
            continue
    cited_in_subset = sorted(p for p in cited_pages if p in subset_original_pages)
    debug["coverage_pages_cited_in_subset"] = cited_in_subset
    debug["coverage_pages_cited_count"] = len(cited_in_subset)
    debug["coverage_pages_missing_count"] = max(
        0, len(subset_original_pages) - len(cited_in_subset)
    )
    if len(subset_original_pages) >= 20 and len(cited_in_subset) == 0:
        debug["coverage_warning"] = (
            "No page_ref citations returned for a large PDF subset; PDF reading may be degraded."
        )

    # Pass 2 — filter (text-only)
    pass2_prompt = f"""
You are filtering extracted metrics from a corporate filing.

Disqualify metrics that are generic across most companies, including:
Revenue, net sales, gross margin, operating income, EBITDA/Adjusted EBITDA, net income, EPS, cash, debt, headcount, backlog (unless uniquely defined).

Keep metrics that are management-defined and specific to {company_name}'s business model.

Output STRICT JSON only:
{{
  "kept": [{{"name_as_written":"string","reason_kept":"string","page_ref":"string|null","excerpt":"string"}}],
  "removed": [{{"name_as_written":"string","reason_removed":"string"}}]
}}

INPUT METRICS:
{{
  "metrics": {json.dumps(deduped, ensure_ascii=False)}
}}
""".strip()

    pass2_raw = ""
    pass2 = None
    for attempt in range(int(max_attempts_per_pass)):
        pass2_raw = _call_pass(
            pass_name="KPI Pass 2 (Filter)",
            thinking_level="low",
            prompt=pass2_prompt
            if attempt == 0
            else safe_json_retry_prompt("Pass 2", bad_output=pass2_raw),
            max_output_tokens=900,
            with_file=False,
            timeout_seconds=10.0,
        )
        pass2 = parse_json_object(pass2_raw)
        if pass2 and isinstance(pass2.get("kept"), list) and isinstance(
            pass2.get("removed"), list
        ):
            break
        pass2 = None

    if not pass2:
        debug["reason"] = "pass2_invalid_json"
        debug["pass2_raw_head"] = (pass2_raw or "")[:800]
        return None, debug

    kept = [k for k in (pass2.get("kept") or []) if isinstance(k, dict)]
    debug["pass2_kept_count"] = len(kept)
    if not kept:
        debug["reason"] = "no_company_specific_metrics"
        return None, debug

    kept_sanitized: List[Dict[str, Any]] = []
    for item in kept:
        name = str(item.get("name_as_written") or "").strip()
        if not name:
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        if len(excerpt) > 420:
            excerpt = excerpt[:420].rstrip() + "…"
        kept_sanitized.append(
            {
                "name_as_written": name,
                "reason_kept": str(item.get("reason_kept") or "").strip() or None,
                "page_ref": (
                    str(item.get("page_ref")).strip()
                    if item.get("page_ref") is not None
                    else None
                ),
                "excerpt": excerpt,
            }
        )
    if len(kept_sanitized) > 40:
        kept_sanitized = kept_sanitized[:40]
        debug["pass2_kept_clamped"] = True

    # Pass 3 — pick the single best KPI (text-only)
    pass3_prompt = f"""
Select the single BEST company-specific KPI from the kept metrics for {company_name}.

Criteria:
- Represents the company’s core engine (how it makes money / wins)
- Not generic
- Supported by a strong VERBATIM evidence excerpt
- Prefer a KPI that includes a numeric value in the excerpt (do NOT invent numbers)

Scoring (0–5 each):
- Uniqueness
- Representativeness
- Signal quality

Choose the top KPI with total >= 11.

Output STRICT JSON only:
{{
  "company_specific_kpi": {{
    "kpi_name": "string",
    "value": "number|null",
    "unit": "string|null",
    "page_ref": "string|null",
    "what_it_measures": "string",
    "why_it_represents_this_company": "string",
    "why_not_generic": "string",
    "scores": {{"uniqueness":0,"representativeness":0,"signal_quality":0}},
    "supporting_excerpt": "string"
  }} | null,
  "fallback_if_none": {{"company_specific_kpi": null, "reason": "string|null"}}
}}

INPUT METRICS (kept):
{json.dumps(kept_sanitized, ensure_ascii=False)}
""".strip()

    pass3_raw = ""
    pass3 = None
    for attempt in range(int(max_attempts_per_pass)):
        pass3_raw = _call_pass(
            pass_name="KPI Pass 3 (Rank)",
            thinking_level="medium",
            prompt=pass3_prompt
            if attempt == 0
            else safe_json_retry_prompt("Pass 3", bad_output=pass3_raw),
            max_output_tokens=900,
            with_file=False,
            timeout_seconds=12.0,
        )
        pass3 = parse_json_object(pass3_raw)
        if pass3 and ("company_specific_kpi" in pass3 or "fallback_if_none" in pass3):
            # Be tolerant: some model outputs omit fallback_if_none.
            pass3.setdefault(
                "fallback_if_none",
                {"company_specific_kpi": None, "reason": None},
            )
            if "company_specific_kpi" not in pass3:
                pass3["company_specific_kpi"] = None
            break
        pass3 = None

    if not pass3:
        debug["reason"] = "pass3_invalid_json"
        debug["pass3_raw_head"] = (pass3_raw or "")[:800]
        return None, debug

    kpi_obj = pass3.get("company_specific_kpi")
    if not isinstance(kpi_obj, dict):
        debug["reason"] = "pass3_no_kpi"
        debug["fallback_reason"] = (
            str((pass3.get("fallback_if_none") or {}).get("reason") or "").strip()
            if isinstance(pass3.get("fallback_if_none"), dict)
            else ""
        )
        return None, debug

    # Pass 4 — verifier (text-only)
    verify_input = {
        "kpi_name": str(kpi_obj.get("kpi_name") or "").strip(),
        "page_ref": (
            str(kpi_obj.get("page_ref")).strip()
            if kpi_obj.get("page_ref") is not None
            else None
        ),
        "supporting_excerpt": str(kpi_obj.get("supporting_excerpt") or "").strip(),
    }
    pass4_prompt = f"""
You are a skeptical verifier. Your job is to reject the KPI unless the excerpt clearly supports it as a meaningful, company-specific KPI.

Rules:
- Use ONLY the supporting excerpt provided.
- If the KPI is generic, weakly evidenced, or misinterpreted, REJECT it.

Output STRICT JSON only:
{{
  "status": "approved|rejected",
  "reason": "string",
  "confidence": 0.0
}}

KPI + EXCERPT:
{json.dumps(verify_input, ensure_ascii=False)}
""".strip()

    pass4_raw = ""
    pass4 = None
    for attempt in range(int(max_attempts_per_pass)):
        pass4_raw = _call_pass(
            pass_name="KPI Pass 4 (Verify)",
            thinking_level="high",
            prompt=pass4_prompt
            if attempt == 0
            else safe_json_retry_prompt("Pass 4", bad_output=pass4_raw),
            max_output_tokens=360,
            with_file=False,
            timeout_seconds=9.0,
        )
        pass4 = parse_json_object(pass4_raw)
        if pass4 and str(pass4.get("status") or "").strip().lower() in (
            "approved",
            "rejected",
        ):
            break
        pass4 = None

    if not pass4:
        debug["reason"] = "pass4_invalid_json"
        debug["pass4_raw_head"] = (pass4_raw or "")[:800]
        return None, debug

    status = str(pass4.get("status") or "").strip().lower()
    debug["verifier_status"] = status
    debug["verifier_reason"] = str(pass4.get("reason") or "").strip()
    if status != "approved":
        debug["reason"] = "verifier_rejected"
        return None, debug

    name = str(kpi_obj.get("kpi_name") or "").strip()
    excerpt = str(kpi_obj.get("supporting_excerpt") or "").strip()
    page_ref = str(kpi_obj.get("page_ref") or "").strip()

    if not name or not excerpt:
        debug["reason"] = "missing_kpi_fields"
        return None, debug

    value_f = _coerce_number(kpi_obj.get("value"))
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)
    if value_f is None:
        debug["reason"] = "kpi_missing_numeric_value"
        return None, debug

    unit = kpi_obj.get("unit")
    unit_s = str(unit).strip() if unit is not None and str(unit).strip() else None

    source_quote = excerpt
    if page_ref:
        source_quote = f"[{page_ref}] {source_quote}"

    scores = kpi_obj.get("scores") if isinstance(kpi_obj.get("scores"), dict) else {}
    try:
        rep = int(scores.get("representativeness", 0))
    except Exception:  # noqa: BLE001
        rep = 0
    try:
        uniq = int(scores.get("uniqueness", 0))
    except Exception:  # noqa: BLE001
        uniq = 0
    try:
        sig = int(scores.get("signal_quality", 0))
    except Exception:  # noqa: BLE001
        sig = 0

    candidate: SpotlightKpiCandidate = {
        "name": name,
        "value": float(value_f),
        "unit": unit_s,
        "prior_value": None,
        "chart_type": "metric",
        "description": str(kpi_obj.get("what_it_measures") or "").strip() or None,
        "source_quote": source_quote,
        "representativeness_score": max(0, min(100, rep * 20)),
        "company_specificity_score": max(0, min(100, uniq * 20)),
        "verifiability_score": max(0, min(100, sig * 20)),
        "ban_flags": [],
    }

    return candidate, debug
