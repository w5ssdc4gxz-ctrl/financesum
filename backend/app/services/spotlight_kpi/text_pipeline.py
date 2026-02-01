from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .json_parse import parse_json_object
from .pipeline_utils import make_text_slices, safe_json_retry_prompt, thinking_config
from .types import SpotlightKpiCandidate


def _normalize_ws(text: str) -> str:
    lowered = re.sub(r"\s+", " ", (text or "").strip()).lower()
    return re.sub(r"[^a-z0-9%$ ]+", "", lowered)


def _contains_verbatimish(quote: str, context: str) -> bool:
    """Loose "verbatim" check that tolerates whitespace/punctuation differences."""
    if not quote or not context:
        return False
    q = _normalize_ws(quote)
    if not q:
        return False
    c = _normalize_ws(context)
    return q in c


def _extract_number_from_excerpt(excerpt: str) -> Optional[float]:
    """Extract a plausible KPI number from a verbatim excerpt (best-effort)."""
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

    pattern = re.compile(
        r"(?P<neg>\()?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suf>%|[bBkKmM]|bn|mn|billion|million|thousand)?\s*(?P<neg2>\))?",
        re.IGNORECASE,
    )
    candidates: List[Tuple[float, int, bool, bool]] = []

    for m in pattern.finditer(text):
        raw = (m.group("num") or "").replace(",", "").strip()
        if not raw:
            continue
        has_comma = "," in (m.group("num") or "")
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

    has_big = any(abs(v) >= 100 or has_comma for v, _s, has_comma, _p in candidates)
    filtered = (
        [c for c in candidates if abs(c[0]) >= 50 or c[2] or c[3]]
        if has_big
        else candidates
    )
    if not filtered:
        filtered = candidates

    filtered.sort(key=lambda t: (t[1], abs(t[0])), reverse=True)
    return float(filtered[0][0])


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


def _infer_unit_from_excerpt(excerpt: str) -> Optional[str]:
    text = str(excerpt or "")
    if not text:
        return None
    if "$" in text:
        return "$"
    if "€" in text:
        return "€"
    if "£" in text:
        return "£"
    if "%" in text:
        return "%"
    if re.search(r"\bpercent(?:age)?\b", text, re.IGNORECASE):
        return "%"
    return None


def _infer_unit_from_name(name: str) -> Optional[str]:
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


def _sanitize_unit(unit: Optional[str], *, kpi_name: str, excerpt: str) -> Optional[str]:
    inferred = _infer_unit_from_excerpt(excerpt)
    if inferred:
        return inferred

    raw = str(unit or "").strip()
    lower = raw.lower().strip()
    if not lower:
        return _infer_unit_from_name(kpi_name)

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
        return _infer_unit_from_name(kpi_name)

    return raw[:48].strip() or _infer_unit_from_name(kpi_name)


def extract_company_specific_spotlight_kpi_from_text(
    gemini_client: Any,
    *,
    context_text: str,
    company_name: str,
    max_attempts_per_pass: int = 2,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """Text-only, hardened multi-pass Spotlight KPI pipeline (Gemini 3 Flash).

    This exists for contexts where we do not have PDF bytes available. It mirrors
    the PDF pipeline structure (discovery → filter → rank → verify) and enforces:
    - Verbatim evidence excerpts (no paraphrases)
    - Safe empty outcome (no KPI) when evidence is weak
    """
    debug: Dict[str, Any] = {"mode": "text_pipeline_v1", "company_name": str(company_name or "")}
    if not gemini_client or not (company_name or "").strip() or not (context_text or "").strip():
        debug["reason"] = "missing_inputs"
        return None, debug

    pipeline_timeout = os.getenv("SPOTLIGHT_TEXT_PIPELINE_TIMEOUT_SECONDS", "35")
    try:
        pipeline_timeout_s = max(8.0, float(pipeline_timeout))
    except ValueError:
        pipeline_timeout_s = 35.0
    started = time.monotonic()

    max_chars = int(os.getenv("SPOTLIGHT_TEXT_PASS1_MAX_CHARS", "180000") or "180000")
    max_slices = int(os.getenv("SPOTLIGHT_TEXT_PASS1_SLICES", "2") or "2")
    slices = make_text_slices(context_text, max_chars=max_chars, max_slices=max_slices)
    debug["pass1_slice_count"] = len(slices)

    def _call_pass(
        *,
        pass_name: str,
        thinking_level: str,
        prompt: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        remaining = pipeline_timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("spotlight_text_pipeline_timeout")
        timeout_seconds = min(float(timeout_seconds), max(3.0, float(remaining)))

        gen_cfg: Dict[str, Any] = {
            "temperature": 0.1,
            "maxOutputTokens": int(max_output_tokens),
            "responseMimeType": "application/json",
        }
        gen_cfg.update(thinking_config(thinking_level))

        # For Spotlight KPI extraction we prefer fast failure over minutes of retries.
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
            # Back-compat for older clients/tests.
            return gemini_client.stream_generate_content(
                prompt,
                stage_name=pass_name,
                expected_tokens=max(200, int(max_output_tokens)),
                generation_config_override=gen_cfg,
                timeout_seconds=timeout_seconds,
            )

    # Pass 1 — discovery (text slices, no judgment)
    all_metrics: List[Dict[str, Any]] = []
    pass1_raw_head: str = ""

    for idx, sl in enumerate(slices):
        page_hint = f"(slice {idx + 1}/{len(slices)})"
        pass1_prompt = f"""
You are reading a corporate filing text for {company_name} {page_hint}.

Task: Identify ALL management-defined metrics/KPIs and operational measures used in the text.

Rules:
- Do NOT judge importance.
- Do NOT invent values.
- For every metric you list, include a VERBATIM excerpt from the text that contains the metric name/definition/value.
- Output STRICT JSON only.

JSON schema:
{{
  "metrics": [
    {{
      "name_as_written": "string",
      "definition_or_context": "string|null",
      "excerpt": "string"
    }}
  ]
}}

FILING TEXT (authoritative):
{sl}
""".strip()

        pass1_raw = ""
        pass1 = None
        for attempt in range(int(max_attempts_per_pass)):
            pass1_raw = _call_pass(
                pass_name=f"KPI Pass 1 (Discovery) {page_hint}",
                thinking_level="minimal",
                prompt=pass1_prompt
                if attempt == 0
                else safe_json_retry_prompt("Pass 1", bad_output=pass1_raw),
                max_output_tokens=1600,
                timeout_seconds=14.0,
            )
            pass1 = parse_json_object(pass1_raw)
            if pass1 and isinstance(pass1.get("metrics"), list):
                break
            pass1 = None

        if not pass1:
            pass1_raw_head = (pass1_raw or "")[:800]
            continue

        for m in pass1.get("metrics") or []:
            if isinstance(m, dict):
                all_metrics.append(m)

    if not all_metrics:
        debug["reason"] = "pass1_no_metrics"
        if pass1_raw_head:
            debug["pass1_raw_head"] = pass1_raw_head
        return None, debug

    # Deduplicate & sanitize metrics (clamp evidence size).
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for m in all_metrics:
        name = str(m.get("name_as_written") or "").strip()
        if not name:
            continue
        key = re.sub(r"\s+", " ", name).strip().lower()
        if key in seen:
            continue
        seen.add(key)

        excerpt = str(m.get("excerpt") or "").strip()
        # Evidence must be a literal substring to prevent hallucinations later.
        if excerpt and not _contains_verbatimish(excerpt, context_text):
            continue
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
                "excerpt": excerpt,
            }
        )

    if len(deduped) > 80:
        deduped = deduped[:80]
        debug["pass1_metrics_clamped"] = True
    debug["pass1_metrics_count"] = len(deduped)
    if not deduped:
        debug["reason"] = "pass1_no_verbatim_evidence"
        return None, debug

    # Pass 2 — generic filter
    pass2_prompt = f"""
You are filtering extracted metrics from a corporate filing.

Disqualify metrics that are generic across most companies, including:
- Financial statement line-items: revenue/net sales, gross margin, operating income, EBITDA/Adjusted EBITDA, net income, EPS, cash, debt.
- Balance sheet / obligations schedules: contract liabilities, deferred revenue timing, content obligations, lease/debt maturities, commitments.
- Accounting constructs often used as "safe KPIs": backlog, RPO/remaining performance obligations.
- "Single-word totals" that are not distinctive without a qualifier: Customers, Users, Subscribers, Members, Accounts, Orders, Transactions, Shipments, Deliveries, Units.
  - Prefer qualified metrics (e.g., "Prime Members", "iPhone Units", "Active Merchants", "Monthly Active Users").
  - If no better qualified KPI exists, a bare total MAY be kept as a fallback (but it is lower quality).

Keep metrics that are management-defined and specific to {company_name}'s business model.

Output STRICT JSON only:
{{
  "kept": [{{"name_as_written":"string","reason_kept":"string","excerpt":"string"}}],
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
        if excerpt and not _contains_verbatimish(excerpt, context_text):
            continue
        if len(excerpt) > 420:
            excerpt = excerpt[:420].rstrip() + "…"
        kept_sanitized.append(
            {
                "name_as_written": name,
                "reason_kept": str(item.get("reason_kept") or "").strip() or None,
                "excerpt": excerpt,
            }
        )
    if not kept_sanitized:
        debug["reason"] = "pass2_no_verbatim_kept"
        return None, debug

    # Reduce to a small, high-signal set for pass 3 to keep latency low and
    # avoid incomplete/invalid JSON outputs.
    def _hint_score(n: str) -> int:
        low = (n or "").lower()
        hits = 0
        for tok in (
            "mau",
            "dau",
            "active users",
            "subscrib",
            "members",
            "orders",
            "transactions",
            "deliver",
            "ship",
            "bookings",
            "gmv",
            "tpv",
            "aum",
            "arr",
            "mrr",
            "retention",
            "churn",
            "arpu",
            "take rate",
        ):
            if tok in low:
                hits += 1
        if len((n or "").split()) >= 3:
            hits += 1
        if "(" in (n or "") and ")" in (n or ""):
            hits += 1
        return hits

    kept_sanitized.sort(
        key=lambda m: (_hint_score(str(m.get("name_as_written") or "")), len(str(m.get("excerpt") or ""))),
        reverse=True,
    )
    kept_sanitized = kept_sanitized[:12]

    # Pass 3 — rank
    pass3_prompt = f"""
Select the single BEST company-specific KPI from the kept metrics for {company_name}.

Criteria:
- Represents the company’s core engine (how it makes money / wins)
- Not generic
- Supported by a strong VERBATIM evidence excerpt
- Prefer a KPI that includes a numeric value in the excerpt (do NOT invent numbers)
- The KPI name must be reasonably distinctive for the business model. Avoid returning a bare total like "Customers" unless it is clearly qualified.

Scoring (0–5 each):
- Uniqueness
- Representativeness
- Signal quality

Choose the top KPI with total >= 10.

Output STRICT JSON only:
{{
  "company_specific_kpi": {{
    "kpi_name": "string",
    "value": "number|null",
    "unit": "string|null",
    "what_it_measures": "string",
    "why_it_represents_this_company": "string",
    "why_not_generic": "string",
    "scores": {{"uniqueness":0,"representativeness":0,"signal_quality":0}},
    "supporting_excerpt": "string"
  }} | null,
  "fallback_if_none": {{"company_specific_kpi": null, "reason": "string|null"}}
}}

INPUT METRICS (kept):
{json.dumps([{"name_as_written": m.get("name_as_written"), "excerpt": m.get("excerpt")} for m in kept_sanitized], ensure_ascii=False)}
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
            max_output_tokens=850,
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

    excerpt = str(kpi_obj.get("supporting_excerpt") or "").strip()
    if not excerpt or not _contains_verbatimish(excerpt, context_text):
        debug["reason"] = "kpi_excerpt_not_verbatim"
        return None, debug

    # Pass 4 — verifier
    verify_input = {
        "kpi_name": str(kpi_obj.get("kpi_name") or "").strip(),
        "supporting_excerpt": excerpt,
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
            max_output_tokens=320,
            timeout_seconds=9.0,
        )
        pass4 = parse_json_object(pass4_raw)
        if pass4 and str(pass4.get("status") or "").strip().lower() in ("approved", "rejected"):
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
    if not name:
        debug["reason"] = "missing_kpi_fields"
        return None, debug

    value = kpi_obj.get("value")
    try:
        value_f = float(value) if value is not None else None
    except Exception:  # noqa: BLE001
        value_f = None
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)
    if value_f is None:
        # Require a numeric value to keep Spotlight cards grounded.
        debug["reason"] = "kpi_missing_numeric_value"
        return None, debug

    unit = kpi_obj.get("unit")
    unit_s = _sanitize_unit(
        str(unit).strip() if unit is not None and str(unit).strip() else None,
        kpi_name=name,
        excerpt=excerpt,
    )

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
        "source_quote": excerpt,
        "representativeness_score": max(0, min(100, rep * 20)),
        "company_specificity_score": max(0, min(100, uniq * 20)),
        "verifiability_score": max(0, min(100, sig * 20)),
        "ban_flags": [],
    }

    return candidate, debug
