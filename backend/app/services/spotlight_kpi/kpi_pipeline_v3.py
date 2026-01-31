"""Streamlined KPI extraction pipeline v3 - Gemini 3 Flash with PDF-native understanding.

This is a complete rewrite of the KPI extraction system with:
- Full PDF upload to Gemini (no text extraction)
- 4-pass hardened pipeline: Discovery -> Filter -> Rank -> Verify
- At most one JSON-repair retry per pass (no backoff loops)
- Hard 30s total pipeline timeout
- Fail-fast to "no differentiated KPI" rather than fallback chains
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .json_parse import parse_json_object
from .pipeline_utils import safe_json_retry_prompt
from .types import SpotlightKpiCandidate


@dataclass
class PipelineConfig:
    """Configuration for the KPI extraction pipeline."""

    total_timeout_seconds: float = 20.0
    max_upload_bytes: int = 50_000_000  # 50 MB

    # Per-pass configuration: (thinking_level, max_output_tokens, timeout_seconds)
    # Use minimal thinking to keep latency close to the web UI experience.
    pass1_config: Tuple[str, int, float] = ("minimal", 1200, 7.0)
    pass2_config: Tuple[str, int, float] = ("minimal", 520, 4.5)
    pass3_config: Tuple[str, int, float] = ("minimal", 520, 4.5)
    pass4_config: Tuple[str, int, float] = ("minimal", 260, 3.5)


def _thinking_config(level: str) -> Dict[str, Any]:
    """Build thinkingConfig for Gemini 3 Flash."""
    lvl = str(level or "").strip().lower()
    if lvl not in ("minimal", "low", "medium", "high"):
        lvl = "low"
    return {"thinkingConfig": {"thinkingLevel": lvl}}


def _kpi_response_schema_pass1() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "metrics": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name_as_written": {"type": "STRING"},
                        "definition_or_context": {"type": "STRING"},
                        "excerpt": {"type": "STRING"},
                    },
                    "required": ["name_as_written", "excerpt"],
                },
            }
        },
        "required": ["metrics"],
    }


def _kpi_response_schema_pass2() -> Dict[str, Any]:
    item = {
        "type": "OBJECT",
        "properties": {
            "name_as_written": {"type": "STRING"},
            "reason_kept": {"type": "STRING"},
            "excerpt": {"type": "STRING"},
        },
        "required": ["name_as_written"],
    }
    removed_item = {
        "type": "OBJECT",
        "properties": {"name_as_written": {"type": "STRING"}, "reason_removed": {"type": "STRING"}},
        "required": ["name_as_written"],
    }
    return {
        "type": "OBJECT",
        "properties": {"kept": {"type": "ARRAY", "items": item}, "removed": {"type": "ARRAY", "items": removed_item}},
        "required": ["kept", "removed"],
    }


def _kpi_response_schema_pass3() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "company_specific_kpi": {
                "type": "OBJECT",
                "properties": {
                    "kpi_name": {"type": "STRING"},
                    "value": {},
                    "unit": {"type": "STRING"},
                    "what_it_measures": {"type": "STRING"},
                    "why_it_represents_this_company": {"type": "STRING"},
                    "why_not_generic": {"type": "STRING"},
                    "scores": {"type": "OBJECT"},
                    "supporting_excerpt": {"type": "STRING"},
                },
            },
            "fallback_if_none": {"type": "OBJECT"},
        },
        "required": ["company_specific_kpi", "fallback_if_none"],
    }


def _kpi_response_schema_pass4() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "status": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "confidence": {},
        },
        "required": ["status", "reason", "confidence"],
    }


def _strip_unsupported_generation_fields(
    gen_cfg: Dict[str, Any], *, error_text: str
) -> Tuple[Dict[str, Any], List[str]]:
    """Retry shim for mixed Gemini API versions.

    Some Gemini endpoints (or older deployments) reject newer `generationConfig`
    keys like `thinkingConfig` or `responseMimeType`. When we detect that, we
    retry the pass once with those fields removed.
    """
    lowered = (error_text or "").lower()
    removed: List[str] = []
    out = dict(gen_cfg or {})

    if "thinkingconfig" in lowered or "thinkinglevel" in lowered:
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


def _coerce_number(value: Any) -> Optional[float]:
    """Coerce a value to a float, handling common formats."""
    if isinstance(value, (int, float)) and value is not None:
        try:
            return float(value)
        except Exception:
            return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        is_paren_negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()").strip()
        cleaned = cleaned.replace(",", "")
        cleaned = cleaned.replace("$", "").replace("EUR", "").replace("£", "").strip()
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
        except Exception:
            return None
    return None


def _extract_number_from_excerpt(excerpt: str) -> Optional[float]:
    """Extract a plausible KPI number from a verbatim excerpt."""
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


def extract_kpi_from_file(
    gemini_client: Any,
    *,
    file_bytes: bytes,
    company_name: str,
    mime_type: str,
    config: Optional[PipelineConfig] = None,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """File-native KPI extraction using Gemini 3 Flash.

    This is the main entry point for KPI extraction. It:
    1. Uploads the full filing document to Gemini Files API (PDF preferred, HTML works too)
    2. Runs a 4-pass pipeline: Discovery -> Filter -> Rank -> Verify
    3. Returns (candidate, debug_info) or (None, debug_info)

    No retries, no fallbacks - fail fast to "no differentiated KPI".
    """
    config = config or PipelineConfig()
    debug: Dict[str, Any] = {
        "mode": "kpi_pipeline_v3",
        "company_name": str(company_name or ""),
        "mime_type": str(mime_type or ""),
    }

    # Validate inputs
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

    # Check file size
    if len(file_bytes) > config.max_upload_bytes:
        debug["reason"] = "file_too_large"
        debug["file_size_bytes"] = len(file_bytes)
        debug["max_bytes"] = config.max_upload_bytes
        return None, debug

    debug["file_size_bytes"] = len(file_bytes)

    # Load timeout from env or use config default
    try:
        total_timeout = float(
            os.getenv(
                "SPOTLIGHT_KPI_PIPELINE_TIMEOUT_SECONDS",
                str(config.total_timeout_seconds),
            )
        )
    except ValueError:
        total_timeout = config.total_timeout_seconds
    total_timeout = max(10.0, total_timeout)

    started = time.monotonic()

    def _remaining_time() -> float:
        return max(0, total_timeout - (time.monotonic() - started))

    def _check_timeout(stage: str) -> bool:
        if _remaining_time() <= 0:
            debug["reason"] = f"timeout_before_{stage}"
            return True
        return False

    # Step 1: Upload filing document to Gemini Files API
    if _check_timeout("upload"):
        return None, debug

    try:
        upload_timeout = min(15.0, _remaining_time())
        file_obj = gemini_client.upload_file_bytes(
            data=file_bytes,
            mime_type=str(mime_type),
            display_name=f"{company_name[:50]}-filing",
            timeout_seconds=upload_timeout,
        )
        file_uri = str(file_obj.get("uri") or "")
        file_mime = str(
            file_obj.get("mimeType") or file_obj.get("mime_type") or str(mime_type)
        )
    except Exception as exc:
        debug["reason"] = "upload_failed"
        debug["upload_error"] = str(exc)[:500]
        return None, debug

    if not file_uri:
        debug["reason"] = "no_file_uri"
        return None, debug

    debug["file_uploaded"] = True
    debug["upload_time_ms"] = int((time.monotonic() - started) * 1000)

    # Helper to call a pass with no retries
    def _call_pass(
        *,
        pass_name: str,
        prompt: str,
        thinking_level: str,
        max_output_tokens: int,
        pass_timeout: float,
        with_file: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Call a single pass with no retries. Returns (parsed_json, raw_text)."""
        remaining = _remaining_time()
        if remaining <= 0:
            return None, ""

        timeout = min(pass_timeout, remaining)

        gen_cfg: Dict[str, Any] = {
            "temperature": 0.1,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        }
        gen_cfg.update(_thinking_config(thinking_level))
        # Best-effort structured response enforcement (older endpoints will reject this,
        # and we auto-retry once without it).
        if pass_name.startswith("KPI Pass 1"):
            gen_cfg["responseSchema"] = _kpi_response_schema_pass1()
        elif pass_name.startswith("KPI Pass 2"):
            gen_cfg["responseSchema"] = _kpi_response_schema_pass2()
        elif pass_name.startswith("KPI Pass 3"):
            gen_cfg["responseSchema"] = _kpi_response_schema_pass3()
        elif pass_name.startswith("KPI Pass 4"):
            gen_cfg["responseSchema"] = _kpi_response_schema_pass4()

        def _do_call(cfg: Dict[str, Any]) -> str:
            if with_file:
                return gemini_client.stream_generate_content_with_file_uri(
                    file_uri=file_uri,
                    file_mime_type=file_mime,
                    prompt=prompt,
                    stage_name=pass_name,
                    expected_tokens=max_output_tokens,
                    generation_config_override=cfg,
                    timeout_seconds=timeout,
                )

            # Try with retry=False first, fall back if not supported
            try:
                return gemini_client.stream_generate_content(
                    prompt,
                    stage_name=pass_name,
                    expected_tokens=max_output_tokens,
                    generation_config_override=cfg,
                    timeout_seconds=timeout,
                    retry=False,
                )
            except TypeError:
                return gemini_client.stream_generate_content(
                    prompt,
                    stage_name=pass_name,
                    expected_tokens=max_output_tokens,
                    generation_config_override=cfg,
                    timeout_seconds=timeout,
                )

        raw = ""
        try:
            raw = _do_call(gen_cfg)
        except Exception as exc:
            # Compatibility retry: some endpoints reject thinkingConfig/responseMimeType.
            cfg2, removed = _strip_unsupported_generation_fields(
                gen_cfg, error_text=str(exc)
            )
            if removed and cfg2 != gen_cfg:
                debug.setdefault("compat_removed_generation_fields", [])
                try:
                    removed_list = debug.get("compat_removed_generation_fields")
                    if isinstance(removed_list, list):
                        removed_list.extend([f"{pass_name}:{f}" for f in removed])
                except Exception:  # noqa: BLE001
                    pass
                try:
                    raw = _do_call(cfg2)
                except Exception as exc2:
                    debug[f"{pass_name.lower().replace(' ', '_')}_error"] = str(exc2)[:300]
                    return None, raw
            else:
                debug[f"{pass_name.lower().replace(' ', '_')}_error"] = str(exc)[:300]
                return None, raw

        parsed = parse_json_object(raw)
        return parsed, raw

    def _call_pass_with_retry(
        *,
        pass_name: str,
        prompt: str,
        thinking_level: str,
        max_output_tokens: int,
        pass_timeout: float,
        with_file: bool,
        schema_name: str,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        parsed, raw = _call_pass(
            pass_name=pass_name,
            prompt=prompt,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
            pass_timeout=pass_timeout,
            with_file=with_file,
        )
        if parsed is not None:
            return parsed, raw
        if _remaining_time() <= 1.5:
            return parsed, raw
        retry_prompt = safe_json_retry_prompt(schema_name, bad_output=raw)
        return _call_pass(
            pass_name=pass_name,
            prompt=retry_prompt,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
            pass_timeout=pass_timeout,
            with_file=with_file,
        )

    # PASS 1: Discovery (file-native, no judgment)
    if _check_timeout("pass1"):
        return None, debug

    thinking, tokens, timeout = config.pass1_config
    pass1_prompt = f"""You are reading a corporate filing document for {company_name}.

Task: Identify ALL management-defined metrics/KPIs and operational measures used in the document.

Rules:
- Do NOT judge importance.
- Do NOT invent values.
- Only list metrics that have a clear numeric value disclosed somewhere in the document.
- For every metric you list, include a VERBATIM excerpt from the document that contains BOTH the metric name and a numeric value.
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
}}"""

    pass1, pass1_raw = _call_pass_with_retry(
        pass_name="KPI Pass 1 Discovery",
        prompt=pass1_prompt,
        thinking_level=thinking,
        max_output_tokens=tokens,
        pass_timeout=timeout,
        with_file=True,
        schema_name="Pass 1",
    )

    if not pass1 or not isinstance(pass1.get("metrics"), list):
        debug["reason"] = "pass1_invalid_json"
        debug["pass1_raw_head"] = (pass1_raw or "")[:500]
        return None, debug

    metrics = [m for m in pass1.get("metrics", []) if isinstance(m, dict)]
    debug["pass1_metrics_count"] = len(metrics)

    if not metrics:
        debug["reason"] = "pass1_no_metrics"
        return None, debug

    def _shrink_excerpt(excerpt: str, *, limit: int = 900) -> str:
        """Keep excerpts bounded but preserve end-of-line numbers/tokens.

        Many filings place the numeric value at the end of a long sentence/table row.
        A naive head-only truncation can remove the number and cause false
        `kpi_missing_numeric_value` failures later.
        """
        text = (excerpt or "").strip()
        if not text or len(text) <= limit:
            return text
        head = max(0, (limit // 2) - 3)
        tail = max(0, limit - head - 5)
        if tail <= 0:
            return text[:limit].rstrip()
        return f"{text[:head].rstrip()} ... {text[-tail:].lstrip()}".strip()

    # Deduplicate and sanitize metrics
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

        excerpt = _shrink_excerpt(str(m.get("excerpt") or ""))

        deduped.append(
            {
                "name_as_written": name,
                "definition_or_context": str(
                    m.get("definition_or_context") or ""
                ).strip()
                or None,
                "excerpt": excerpt,
            }
        )

    # Clamp to 60 metrics max
    if len(deduped) > 60:
        deduped = deduped[:60]
        debug["pass1_clamped"] = True

    # PASS 2: Filter (remove generic metrics)
    if _check_timeout("pass2"):
        return None, debug

    thinking, tokens, timeout = config.pass2_config
    pass2_prompt = f"""You are filtering extracted metrics from a corporate filing.

Disqualify metrics that are generic across most companies, including:
- Financial statement line-items: revenue/net sales, gross margin, operating income, EBITDA/Adjusted EBITDA, net income, EPS, cash, debt.
- Balance sheet / obligations schedules: contract liabilities, deferred revenue timing, content obligations, lease/debt maturities, commitments.
- Accounting constructs often used as "safe KPIs": backlog, RPO/remaining performance obligations.
- "Single-word totals" that are not distinctive without a qualifier: Customers, Users, Subscribers, Members, Accounts, Orders, Transactions, Shipments, Deliveries, Units.
  - Prefer qualified metrics (e.g., "Prime Members", "iPhone Units", "Active Merchants", "Monthly Active Users").
  - If no better qualified KPI exists, a bare total MAY be kept as a fallback (but it is lower quality).

Keep metrics that are management-defined and specific to {company_name}'s business model.
Only keep metrics that have a clear numeric value in their excerpt (do NOT invent numbers).

Output STRICT JSON only:
{{
  "kept": [{{"name_as_written":"string","reason_kept":"string","excerpt":"string"}}],
  "removed": [{{"name_as_written":"string","reason_removed":"string"}}]
}}

INPUT METRICS:
{json.dumps(deduped, ensure_ascii=False)}"""

    pass2, pass2_raw = _call_pass_with_retry(
        pass_name="KPI Pass 2 Filter",
        prompt=pass2_prompt,
        thinking_level=thinking,
        max_output_tokens=tokens,
        pass_timeout=timeout,
        with_file=False,
        schema_name="Pass 2",
    )

    if not pass2 or not isinstance(pass2.get("kept"), list):
        debug["reason"] = "pass2_invalid_json"
        debug["pass2_raw_head"] = (pass2_raw or "")[:500]
        return None, debug

    kept = [k for k in pass2.get("kept", []) if isinstance(k, dict)]
    debug["pass2_kept_count"] = len(kept)

    if not kept:
        debug["reason"] = "no_company_specific_metrics"
        return None, debug

    # Sanitize kept metrics
    kept_sanitized: List[Dict[str, Any]] = []
    for item in kept[:30]:  # Max 30
        name = str(item.get("name_as_written") or "").strip()
        if not name:
            continue
        excerpt = _shrink_excerpt(str(item.get("excerpt") or ""))

        # Clamp excerpt more aggressively for ranking to avoid long prompts that
        # increase latency and cause incomplete JSON outputs.
        excerpt = _shrink_excerpt(excerpt, limit=320)

        kept_sanitized.append(
            {"name_as_written": name, "reason_kept": str(item.get("reason_kept") or "").strip() or None, "excerpt": excerpt}
        )

    # Reduce to a small, high-signal set for pass 3.
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
        # Prefer qualified names (multiple words / parentheses / hyphens).
        if len((n or "").split()) >= 3:
            hits += 1
        if "(" in (n or "") and ")" in (n or ""):
            hits += 1
        return hits

    kept_sanitized.sort(key=lambda m: (_hint_score(str(m.get("name_as_written") or "")), len(str(m.get("excerpt") or ""))), reverse=True)
    kept_sanitized = kept_sanitized[:12]

    # PASS 3: Rank (pick the single best KPI)
    if _check_timeout("pass3"):
        return None, debug

    thinking, tokens, timeout = config.pass3_config
    pass3_prompt = f"""Select the single BEST company-specific KPI from the kept metrics for {company_name}.

Criteria:
- Represents the company's core engine (how it makes money / wins)
- Not generic
- Supported by a strong VERBATIM evidence excerpt
- MUST include a numeric value in the excerpt (do NOT invent numbers). If you cannot extract a numeric value, return company_specific_kpi=null.
- The KPI name must be reasonably distinctive for the business model. Avoid returning a bare total like "Customers" unless it is clearly qualified.

Scoring (0-5 each):
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
{json.dumps([{"name_as_written": m.get("name_as_written"), "excerpt": m.get("excerpt")} for m in kept_sanitized], ensure_ascii=False)}"""

    pass3, pass3_raw = _call_pass_with_retry(
        pass_name="KPI Pass 3 Rank",
        prompt=pass3_prompt,
        thinking_level=thinking,
        max_output_tokens=tokens,
        pass_timeout=timeout,
        with_file=False,
        schema_name="Pass 3",
    )

    if not pass3 or "company_specific_kpi" not in pass3:
        debug["reason"] = "pass3_invalid_json"
        debug["pass3_raw_head"] = (pass3_raw or "")[:500]
        return None, debug

    kpi_obj = pass3.get("company_specific_kpi")
    if not isinstance(kpi_obj, dict):
        debug["reason"] = "pass3_no_kpi_found"
        fallback = pass3.get("fallback_if_none", {})
        if isinstance(fallback, dict):
            debug["fallback_reason"] = str(fallback.get("reason") or "")
        return None, debug

    # PASS 4: Verify (skeptical check)
    if _check_timeout("pass4"):
        return None, debug

    thinking, tokens, timeout = config.pass4_config
    verify_input = {
        "kpi_name": str(kpi_obj.get("kpi_name") or "").strip(),
        "supporting_excerpt": str(kpi_obj.get("supporting_excerpt") or "").strip(),
    }

    pass4_prompt = f"""You are a skeptical verifier. Your job is to reject the KPI unless the excerpt clearly supports it as a meaningful, company-specific KPI.

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
{json.dumps(verify_input, ensure_ascii=False)}"""

    pass4, pass4_raw = _call_pass_with_retry(
        pass_name="KPI Pass 4 Verify",
        prompt=pass4_prompt,
        thinking_level=thinking,
        max_output_tokens=tokens,
        pass_timeout=timeout,
        with_file=False,
        schema_name="Pass 4",
    )

    if not pass4:
        debug["reason"] = "pass4_invalid_json"
        debug["pass4_raw_head"] = (pass4_raw or "")[:500]
        return None, debug

    status = str(pass4.get("status") or "").strip().lower()
    debug["verifier_status"] = status
    debug["verifier_reason"] = str(pass4.get("reason") or "").strip()
    debug["verifier_confidence"] = pass4.get("confidence")

    if status != "approved":
        debug["reason"] = "verifier_rejected"
        return None, debug

    # Build the final candidate
    name = str(kpi_obj.get("kpi_name") or "").strip()
    excerpt = str(kpi_obj.get("supporting_excerpt") or "").strip()

    if not name or not excerpt:
        debug["reason"] = "missing_kpi_fields"
        return None, debug

    # Extract numeric value
    value_f = _coerce_number(kpi_obj.get("value"))
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)

    if value_f is None:
        debug["reason"] = "kpi_missing_numeric_value"
        return None, debug

    unit = kpi_obj.get("unit")
    unit_s = str(unit).strip() if unit and str(unit).strip() else None

    # Build scores
    scores = kpi_obj.get("scores", {})
    if not isinstance(scores, dict):
        scores = {}

    try:
        rep = int(scores.get("representativeness", 0))
    except (ValueError, TypeError):
        rep = 0
    try:
        uniq = int(scores.get("uniqueness", 0))
    except (ValueError, TypeError):
        uniq = 0
    try:
        sig = int(scores.get("signal_quality", 0))
    except (ValueError, TypeError):
        sig = 0

    debug["total_time_ms"] = int((time.monotonic() - started) * 1000)

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


def extract_kpi_from_pdf(
    gemini_client: Any,
    *,
    pdf_bytes: bytes,
    company_name: str,
    config: Optional[PipelineConfig] = None,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """Back-compat wrapper for callers that still pass PDF bytes only."""
    return extract_kpi_from_file(
        gemini_client,
        file_bytes=pdf_bytes,
        company_name=company_name,
        mime_type="application/pdf",
        config=config,
    )
