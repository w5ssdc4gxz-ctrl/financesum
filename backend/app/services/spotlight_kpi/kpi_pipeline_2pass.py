"""Streamlined 2-pass KPI extraction pipeline for reliable company-specific KPI extraction.

This pipeline consolidates the previous 4-pass approach into 2 passes:
- Pass 1: Combined Discovery + Filter (with file upload)
- Pass 2: Combined Rank + Verify (text-only)

Key improvements:
- 45s total timeout (vs 20s in v3)
- 3000 output tokens for Pass 1 (vs 1200 in v3)
- Fuzzy quote matching (vs exact verbatim in v3)
- Deterministic regex fallback when AI fails
- Better handling of diverse industries
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .json_parse import parse_json_object
from .regex_fallback import extract_kpis_with_regex
from .types import SpotlightKpiCandidate


@dataclass
class Pipeline2PassConfig:
    """Configuration for the 2-pass KPI extraction pipeline."""

    total_timeout_seconds: float = 45.0
    max_upload_bytes: int = 50_000_000  # 50 MB

    # Pass 1: Discovery + Filter (file-native)
    pass1_thinking_level: str = "low"
    pass1_max_output_tokens: int = 3000
    pass1_timeout_seconds: float = 25.0

    # Pass 2: Rank + Verify (text-only)
    pass2_thinking_level: str = "medium"
    pass2_max_output_tokens: int = 1500
    pass2_timeout_seconds: float = 15.0

    # Fallback settings
    enable_regex_fallback: bool = True


def _thinking_config(level: str) -> Dict[str, Any]:
    """Build thinkingConfig for Gemini 3 Flash."""
    lvl = str(level or "").strip().lower()
    if lvl not in ("minimal", "low", "medium", "high"):
        lvl = "low"
    return {"thinkingConfig": {"thinkingLevel": lvl}}


def _normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy quote matching."""
    if not text:
        return ""
    # Lowercase, collapse whitespace, remove punctuation except digits/$/%
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    normalized = re.sub(r"[^\w\s$%.,0-9]", "", normalized)
    return normalized


def _fuzzy_quote_in_context(quote: str, context: str, threshold: float = 0.7) -> bool:
    """Check if quote appears in context with fuzzy matching.
    
    More lenient than exact matching - tolerates whitespace/punctuation differences.
    """
    if not quote or not context:
        return False
    
    q = _normalize_for_matching(quote)
    c = _normalize_for_matching(context)
    
    if not q or len(q) < 10:
        return False
    
    # Direct substring match
    if q in c:
        return True
    
    # Check if most words from quote appear in order in context
    q_words = q.split()
    if len(q_words) < 3:
        return q in c
    
    # Sliding window match - check if 70%+ of quote words appear in context
    matched = sum(1 for w in q_words if w in c and len(w) > 2)
    if matched / len(q_words) >= threshold:
        return True
    
    # Check prefix and suffix for long quotes
    if len(q) >= 100:
        prefix = q[:50]
        suffix = q[-50:]
        if prefix in c and suffix in c:
            return True
    
    return False


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
        cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").strip()
        cleaned = cleaned.replace("%", "").strip()

        mult = 1.0
        lower = cleaned.lower()
        if lower.endswith("b") or lower.endswith("bn") or lower.endswith("billion"):
            mult = 1_000_000_000.0
            cleaned = re.sub(r"(b|bn|billion)$", "", cleaned, flags=re.IGNORECASE)
        elif lower.endswith("m") or lower.endswith("mn") or lower.endswith("million"):
            mult = 1_000_000.0
            cleaned = re.sub(r"(m|mn|million)$", "", cleaned, flags=re.IGNORECASE)
        elif lower.endswith("k") or lower.endswith("thousand"):
            mult = 1_000.0
            cleaned = re.sub(r"(k|thousand)$", "", cleaned, flags=re.IGNORECASE)
        elif lower.endswith("t") or lower.endswith("trillion"):
            mult = 1_000_000_000_000.0
            cleaned = re.sub(r"(t|trillion)$", "", cleaned, flags=re.IGNORECASE)

        try:
            num = float(cleaned.strip()) * mult
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

    # Pattern to find numbers like 1,234.56, (123), 12%, 3.4B, 5m, etc.
    pattern = re.compile(
        r"(?P<neg>\()?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suf>%|[bBkKmMtT]|bn|mn|billion|million|thousand|trillion)?\s*(?P<neg2>\))?",
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

        # Skip likely years (1900-2100)
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
        is_percent = suf == "%"

        neg = bool(m.group("neg")) and bool(m.group("neg2"))
        out = val * mult
        out = -out if neg else out

        # Score based on likelihood of being the main KPI value
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

    # Filter out small numbers that are likely qualifiers (e.g., "3 devices")
    has_big = any(abs(v) >= 100 or has_comma for v, _s, has_comma, _p in candidates)
    filtered = (
        [c for c in candidates if abs(c[0]) >= 50 or c[2] or c[3]]
        if has_big
        else candidates
    )
    if not filtered:
        filtered = candidates

    # Pick highest score; tie-break on magnitude
    filtered.sort(key=lambda t: (t[1], abs(t[0])), reverse=True)
    return float(filtered[0][0])


def _build_pass1_prompt(company_name: str) -> str:
    """Build the combined Discovery + Filter prompt for Pass 1."""
    return f"""You are analyzing a corporate filing for {company_name} to find the SINGLE BEST company-specific KPI.

TASK: Scan the ENTIRE document (beginning, middle, AND end) to find operational metrics that are UNIQUE to this company's business model.

GOOD KPIs (company-specific, operational):
- Tech/Social: Monthly Active Users (MAUs), Daily Active Users (DAUs), Paid Subscribers, Watch Hours, Engagement Minutes
- E-commerce/Retail: Gross Merchandise Volume (GMV), Orders, Active Customers, Same-Store Sales, Store Count
- SaaS: Annual Recurring Revenue (ARR), Net Revenue Retention (NRR), Dollar-Based Net Retention (DBNR), Subscription Revenue
- Fintech/Payments: Total Payment Volume (TPV), Active Accounts, Transaction Volume, Payment Transactions
- Manufacturing: Units Shipped, Vehicles Delivered, Systems Sold, Production Volume, Installed Base
- Travel/Hospitality: Bookings, Room Nights, RevPAR, Trips, Rides
- Gaming: Bookings, Active Players, Paying Users, Virtual Currency Sales
- Streaming: Paid Memberships, Hours Streamed, Content Engagement
- Telecom: Subscribers, ARPU, Postpaid Adds, Prepaid Adds

BAD KPIs (generic, do NOT select):
- Revenue, Net Revenue, Total Revenue (unless segment-specific like "Cloud Revenue")
- Net Income, Operating Income, Gross Profit, EBITDA
- EPS, Cash, Debt, Total Assets
- Backlog, RPO, Remaining Performance Obligations (accounting constructs)
- Generic "Customers" or "Users" without qualifier
- Contract liabilities, Deferred revenue, Obligations

INSTRUCTIONS:
1. Search the ENTIRE document, not just the beginning
2. Find 3-5 candidate operational KPIs with NUMERIC values
3. Filter out generic financial metrics
4. For each candidate, copy a VERBATIM excerpt containing the KPI name and value
5. Prefer KPIs with business-specific qualifiers (e.g., "Prime Members" not just "Members")

OUTPUT FORMAT (strict JSON):
{{
  "candidates": [
    {{
      "name": "Exact KPI name as written",
      "value": 123456789,
      "unit": "users|subscribers|$|%|units|trips|etc",
      "excerpt": "Verbatim quote from document containing name and value",
      "why_company_specific": "Brief explanation"
    }}
  ]
}}

Return 3-5 candidates in order of quality (best first). If no company-specific KPIs exist, return {{"candidates": []}}."""


def _build_pass2_prompt(company_name: str, candidates: List[Dict[str, Any]]) -> str:
    """Build the combined Rank + Verify prompt for Pass 2."""
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    
    return f"""You are selecting the SINGLE BEST company-specific KPI for {company_name} from these candidates.

CANDIDATES:
{candidates_json}

SELECTION CRITERIA (in order of importance):
1. COMPANY-SPECIFIC: Represents what makes THIS company unique, not generic metrics all companies have
2. OPERATIONAL: Measures actual business activity (users, transactions, units) not accounting constructs
3. NUMERIC VALUE: Has a clear, verifiable number in the excerpt
4. CORE BUSINESS: Reflects the company's primary way of generating value

SCORING (0-5 each):
- Uniqueness: How distinctive is this KPI to this company's business model?
- Representativeness: How well does this capture the company's core operations?
- Verifiability: How clear and unambiguous is the value in the excerpt?

VERIFICATION RULES:
- The excerpt MUST contain a numeric value
- The KPI name must match what's in the excerpt
- Reject if the KPI is a generic financial metric in disguise

OUTPUT FORMAT (strict JSON):
{{
  "selected_kpi": {{
    "name": "KPI name",
    "value": 123456789,
    "unit": "string or null",
    "excerpt": "Verbatim excerpt",
    "what_it_measures": "Brief description",
    "why_selected": "Why this is the best choice",
    "scores": {{
      "uniqueness": 0-5,
      "representativeness": 0-5,
      "verifiability": 0-5
    }}
  }},
  "verification": {{
    "status": "approved|rejected",
    "reason": "Brief explanation",
    "confidence": 0.0-1.0
  }}
}}

If no candidate passes verification, return:
{{
  "selected_kpi": null,
  "verification": {{
    "status": "rejected",
    "reason": "Explanation",
    "confidence": 0.0
  }}
}}"""


def extract_kpi_2pass(
    gemini_client: Any,
    *,
    file_bytes: bytes,
    company_name: str,
    mime_type: str,
    context_text: Optional[str] = None,
    config: Optional[Pipeline2PassConfig] = None,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """2-pass KPI extraction pipeline.
    
    Pass 1: Discovery + Filter (with file upload)
    Pass 2: Rank + Verify (text-only)
    Fallback: Regex extraction if AI fails
    
    Returns (candidate, debug_info) or (None, debug_info).
    """
    config = config or Pipeline2PassConfig()
    debug: Dict[str, Any] = {
        "mode": "kpi_pipeline_2pass",
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
    total_timeout = max(15.0, total_timeout)

    started = time.monotonic()

    def _remaining_time() -> float:
        return max(0, total_timeout - (time.monotonic() - started))

    def _check_timeout(stage: str) -> bool:
        if _remaining_time() <= 0:
            debug["reason"] = f"timeout_before_{stage}"
            return True
        return False

    # =========================================================================
    # STEP 1: Upload file to Gemini
    # =========================================================================
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
        # Try regex fallback
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    if not file_uri:
        debug["reason"] = "no_file_uri"
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    debug["file_uploaded"] = True
    debug["upload_time_ms"] = int((time.monotonic() - started) * 1000)

    # =========================================================================
    # PASS 1: Discovery + Filter (file-native)
    # =========================================================================
    if _check_timeout("pass1"):
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    pass1_prompt = _build_pass1_prompt(company_name)
    
    gen_cfg_pass1: Dict[str, Any] = {
        "temperature": 0.2,
        "maxOutputTokens": config.pass1_max_output_tokens,
        "responseMimeType": "application/json",
    }
    gen_cfg_pass1.update(_thinking_config(config.pass1_thinking_level))

    pass1_raw = ""
    try:
        timeout = min(config.pass1_timeout_seconds, _remaining_time())
        pass1_raw = gemini_client.stream_generate_content_with_file_uri(
            file_uri=file_uri,
            file_mime_type=file_mime,
            prompt=pass1_prompt,
            stage_name="KPI Pass 1 (Discovery+Filter)",
            expected_tokens=config.pass1_max_output_tokens,
            generation_config_override=gen_cfg_pass1,
            timeout_seconds=timeout,
        )
    except Exception as exc:
        debug["pass1_error"] = str(exc)[:500]
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    pass1 = parse_json_object(pass1_raw)
    if not pass1 or not isinstance(pass1.get("candidates"), list):
        debug["reason"] = "pass1_invalid_json"
        debug["pass1_raw_head"] = (pass1_raw or "")[:800]
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    candidates = [c for c in pass1.get("candidates", []) if isinstance(c, dict)]
    debug["pass1_candidates_count"] = len(candidates)

    if not candidates:
        debug["reason"] = "pass1_no_candidates"
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    # Sanitize candidates
    sanitized_candidates: List[Dict[str, Any]] = []
    for c in candidates[:10]:  # Max 10 candidates
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        excerpt = str(c.get("excerpt") or "").strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:250] + " ... " + excerpt[-200:]
        sanitized_candidates.append({
            "name": name,
            "value": c.get("value"),
            "unit": c.get("unit"),
            "excerpt": excerpt,
            "why_company_specific": str(c.get("why_company_specific") or "")[:200],
        })

    if not sanitized_candidates:
        debug["reason"] = "pass1_no_valid_candidates"
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    debug["pass1_time_ms"] = int((time.monotonic() - started) * 1000)

    # =========================================================================
    # PASS 2: Rank + Verify (text-only)
    # =========================================================================
    if _check_timeout("pass2"):
        # Use best candidate from Pass 1 without verification
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    pass2_prompt = _build_pass2_prompt(company_name, sanitized_candidates)
    
    gen_cfg_pass2: Dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": config.pass2_max_output_tokens,
        "responseMimeType": "application/json",
    }
    gen_cfg_pass2.update(_thinking_config(config.pass2_thinking_level))

    pass2_raw = ""
    try:
        timeout = min(config.pass2_timeout_seconds, _remaining_time())
        pass2_raw = gemini_client.stream_generate_content(
            pass2_prompt,
            stage_name="KPI Pass 2 (Rank+Verify)",
            expected_tokens=config.pass2_max_output_tokens,
            generation_config_override=gen_cfg_pass2,
            timeout_seconds=timeout,
            retry=False,
        )
    except TypeError:
        # Fallback for older client without retry param
        try:
            pass2_raw = gemini_client.stream_generate_content(
                pass2_prompt,
                stage_name="KPI Pass 2 (Rank+Verify)",
                expected_tokens=config.pass2_max_output_tokens,
                generation_config_override=gen_cfg_pass2,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            debug["pass2_error"] = str(exc)[:500]
            return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)
    except Exception as exc:
        debug["pass2_error"] = str(exc)[:500]
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    pass2 = parse_json_object(pass2_raw)
    if not pass2:
        debug["reason"] = "pass2_invalid_json"
        debug["pass2_raw_head"] = (pass2_raw or "")[:800]
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    verification = pass2.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}
    
    status = str(verification.get("status") or "").strip().lower()
    debug["verification_status"] = status
    debug["verification_reason"] = str(verification.get("reason") or "")[:200]
    debug["verification_confidence"] = verification.get("confidence")

    selected = pass2.get("selected_kpi")
    if status == "rejected" or not isinstance(selected, dict):
        debug["reason"] = "pass2_verification_rejected"
        # Fall back to best Pass 1 candidate
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    # =========================================================================
    # BUILD FINAL CANDIDATE
    # =========================================================================
    name = str(selected.get("name") or "").strip()
    excerpt = str(selected.get("excerpt") or "").strip()

    if not name:
        debug["reason"] = "missing_kpi_name"
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    # Verify excerpt against context if available
    if context_text and excerpt and not _fuzzy_quote_in_context(excerpt, context_text):
        debug["quote_verification"] = "fuzzy_match_failed"
        # Don't fail - just note it

    # Extract numeric value
    value_f = _coerce_number(selected.get("value"))
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)

    if value_f is None:
        debug["reason"] = "kpi_missing_numeric_value"
        # Try other candidates
        for alt in sanitized_candidates[1:3]:
            alt_excerpt = str(alt.get("excerpt") or "")
            alt_value = _coerce_number(alt.get("value"))
            if alt_value is None:
                alt_value = _extract_number_from_excerpt(alt_excerpt)
            if alt_value is not None:
                return _build_candidate_from_pass1(alt, debug, context_text)
        if config.enable_regex_fallback and context_text:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    unit = selected.get("unit")
    unit_s = str(unit).strip() if unit and str(unit).strip() else None

    # Build scores
    scores = selected.get("scores", {})
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
        ver = int(scores.get("verifiability", 0))
    except (ValueError, TypeError):
        ver = 0

    debug["total_time_ms"] = int((time.monotonic() - started) * 1000)

    candidate: SpotlightKpiCandidate = {
        "name": name,
        "value": float(value_f),
        "unit": unit_s,
        "prior_value": None,
        "chart_type": "metric",
        "description": str(selected.get("what_it_measures") or "").strip() or None,
        "source_quote": excerpt,
        "representativeness_score": max(0, min(100, rep * 20)),
        "company_specificity_score": max(0, min(100, uniq * 20)),
        "verifiability_score": max(0, min(100, ver * 20)),
        "ban_flags": [],
    }

    return candidate, debug


def _build_candidate_from_pass1(
    raw: Dict[str, Any],
    debug: Dict[str, Any],
    context_text: Optional[str],
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """Build a candidate from Pass 1 output when Pass 2 fails."""
    debug["fallback_to_pass1"] = True
    
    name = str(raw.get("name") or "").strip()
    if not name:
        debug["reason"] = "pass1_fallback_no_name"
        return None, debug

    excerpt = str(raw.get("excerpt") or "").strip()
    
    # Extract value
    value_f = _coerce_number(raw.get("value"))
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)
    
    if value_f is None:
        debug["reason"] = "pass1_fallback_no_value"
        return None, debug

    unit = raw.get("unit")
    unit_s = str(unit).strip() if unit and str(unit).strip() else None

    candidate: SpotlightKpiCandidate = {
        "name": name,
        "value": float(value_f),
        "unit": unit_s,
        "prior_value": None,
        "chart_type": "metric",
        "description": str(raw.get("why_company_specific") or "").strip() or None,
        "source_quote": excerpt,
        "representativeness_score": 60,  # Default scores for Pass 1 fallback
        "company_specificity_score": 60,
        "verifiability_score": 60,
        "ban_flags": [],
    }

    return candidate, debug


def _try_regex_fallback(
    context_text: str,
    company_name: str,
    debug: Dict[str, Any],
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """Try deterministic regex extraction as last resort."""
    debug["regex_fallback_attempted"] = True
    
    try:
        candidates = extract_kpis_with_regex(context_text, company_name)
        if candidates:
            debug["regex_fallback_success"] = True
            debug["regex_candidates_count"] = len(candidates)
            return candidates[0], debug
    except Exception as exc:
        debug["regex_fallback_error"] = str(exc)[:200]
    
    debug["reason"] = "all_extraction_methods_failed"
    return None, debug


def extract_kpi_2pass_from_text(
    gemini_client: Any,
    *,
    context_text: str,
    company_name: str,
    config: Optional[Pipeline2PassConfig] = None,
) -> Tuple[Optional[SpotlightKpiCandidate], Dict[str, Any]]:
    """Text-only 2-pass KPI extraction (no file upload).
    
    Used when PDF bytes are not available.
    """
    config = config or Pipeline2PassConfig()
    debug: Dict[str, Any] = {
        "mode": "kpi_pipeline_2pass_text",
        "company_name": str(company_name or ""),
    }

    if not gemini_client:
        debug["reason"] = "no_gemini_client"
        return None, debug
    if not (context_text or "").strip():
        debug["reason"] = "no_context_text"
        return None, debug
    if not (company_name or "").strip():
        debug["reason"] = "no_company_name"
        return None, debug

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
    total_timeout = max(15.0, total_timeout)

    started = time.monotonic()

    def _remaining_time() -> float:
        return max(0, total_timeout - (time.monotonic() - started))

    # Prepare context - take head, middle, and tail for coverage
    text = context_text.strip()
    max_chars = 180_000
    if len(text) > max_chars:
        head = text[:70_000]
        mid_start = max(0, (len(text) // 2) - 35_000)
        mid = text[mid_start:mid_start + 70_000]
        tail = text[-40_000:]
        text = f"{head}\n\n--- MIDDLE OF DOCUMENT ---\n\n{mid}\n\n--- END OF DOCUMENT ---\n\n{tail}"

    # =========================================================================
    # PASS 1: Discovery + Filter (text-based)
    # =========================================================================
    pass1_prompt = _build_pass1_prompt(company_name) + f"\n\nDOCUMENT TEXT:\n{text}"
    
    gen_cfg_pass1: Dict[str, Any] = {
        "temperature": 0.2,
        "maxOutputTokens": config.pass1_max_output_tokens,
        "responseMimeType": "application/json",
    }
    gen_cfg_pass1.update(_thinking_config(config.pass1_thinking_level))

    pass1_raw = ""
    try:
        timeout = min(config.pass1_timeout_seconds, _remaining_time())
        pass1_raw = gemini_client.stream_generate_content(
            pass1_prompt,
            stage_name="KPI Pass 1 Text (Discovery+Filter)",
            expected_tokens=config.pass1_max_output_tokens,
            generation_config_override=gen_cfg_pass1,
            timeout_seconds=timeout,
            retry=False,
        )
    except TypeError:
        try:
            pass1_raw = gemini_client.stream_generate_content(
                pass1_prompt,
                stage_name="KPI Pass 1 Text (Discovery+Filter)",
                expected_tokens=config.pass1_max_output_tokens,
                generation_config_override=gen_cfg_pass1,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            debug["pass1_error"] = str(exc)[:500]
            if config.enable_regex_fallback:
                return _try_regex_fallback(context_text, company_name, debug)
            return None, debug
    except Exception as exc:
        debug["pass1_error"] = str(exc)[:500]
        if config.enable_regex_fallback:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    pass1 = parse_json_object(pass1_raw)
    if not pass1 or not isinstance(pass1.get("candidates"), list):
        debug["reason"] = "pass1_invalid_json"
        debug["pass1_raw_head"] = (pass1_raw or "")[:800]
        if config.enable_regex_fallback:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    candidates = [c for c in pass1.get("candidates", []) if isinstance(c, dict)]
    debug["pass1_candidates_count"] = len(candidates)

    if not candidates:
        debug["reason"] = "pass1_no_candidates"
        if config.enable_regex_fallback:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    # Sanitize and verify quotes against context
    sanitized_candidates: List[Dict[str, Any]] = []
    for c in candidates[:10]:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        excerpt = str(c.get("excerpt") or "").strip()
        
        # Verify excerpt is in context (fuzzy match)
        if excerpt and not _fuzzy_quote_in_context(excerpt, context_text):
            continue
            
        if len(excerpt) > 500:
            excerpt = excerpt[:250] + " ... " + excerpt[-200:]
        sanitized_candidates.append({
            "name": name,
            "value": c.get("value"),
            "unit": c.get("unit"),
            "excerpt": excerpt,
            "why_company_specific": str(c.get("why_company_specific") or "")[:200],
        })

    if not sanitized_candidates:
        debug["reason"] = "pass1_no_verified_candidates"
        if config.enable_regex_fallback:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    debug["pass1_verified_count"] = len(sanitized_candidates)
    debug["pass1_time_ms"] = int((time.monotonic() - started) * 1000)

    # =========================================================================
    # PASS 2: Rank + Verify
    # =========================================================================
    if _remaining_time() <= 2.0:
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    pass2_prompt = _build_pass2_prompt(company_name, sanitized_candidates)
    
    gen_cfg_pass2: Dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": config.pass2_max_output_tokens,
        "responseMimeType": "application/json",
    }
    gen_cfg_pass2.update(_thinking_config(config.pass2_thinking_level))

    pass2_raw = ""
    try:
        timeout = min(config.pass2_timeout_seconds, _remaining_time())
        pass2_raw = gemini_client.stream_generate_content(
            pass2_prompt,
            stage_name="KPI Pass 2 Text (Rank+Verify)",
            expected_tokens=config.pass2_max_output_tokens,
            generation_config_override=gen_cfg_pass2,
            timeout_seconds=timeout,
            retry=False,
        )
    except TypeError:
        try:
            pass2_raw = gemini_client.stream_generate_content(
                pass2_prompt,
                stage_name="KPI Pass 2 Text (Rank+Verify)",
                expected_tokens=config.pass2_max_output_tokens,
                generation_config_override=gen_cfg_pass2,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            debug["pass2_error"] = str(exc)[:500]
            return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)
    except Exception as exc:
        debug["pass2_error"] = str(exc)[:500]
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    pass2 = parse_json_object(pass2_raw)
    if not pass2:
        debug["reason"] = "pass2_invalid_json"
        debug["pass2_raw_head"] = (pass2_raw or "")[:800]
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    verification = pass2.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}
    
    status = str(verification.get("status") or "").strip().lower()
    debug["verification_status"] = status
    debug["verification_reason"] = str(verification.get("reason") or "")[:200]

    selected = pass2.get("selected_kpi")
    if status == "rejected" or not isinstance(selected, dict):
        debug["reason"] = "pass2_verification_rejected"
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    # Build final candidate
    name = str(selected.get("name") or "").strip()
    if not name:
        return _build_candidate_from_pass1(sanitized_candidates[0], debug, context_text)

    excerpt = str(selected.get("excerpt") or "").strip()
    
    value_f = _coerce_number(selected.get("value"))
    if value_f is None:
        value_f = _extract_number_from_excerpt(excerpt)
    
    if value_f is None:
        for alt in sanitized_candidates[1:3]:
            alt_excerpt = str(alt.get("excerpt") or "")
            alt_value = _coerce_number(alt.get("value"))
            if alt_value is None:
                alt_value = _extract_number_from_excerpt(alt_excerpt)
            if alt_value is not None:
                return _build_candidate_from_pass1(alt, debug, context_text)
        debug["reason"] = "kpi_missing_numeric_value"
        if config.enable_regex_fallback:
            return _try_regex_fallback(context_text, company_name, debug)
        return None, debug

    unit = selected.get("unit")
    unit_s = str(unit).strip() if unit and str(unit).strip() else None

    scores = selected.get("scores", {})
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
        ver = int(scores.get("verifiability", 0))
    except (ValueError, TypeError):
        ver = 0

    debug["total_time_ms"] = int((time.monotonic() - started) * 1000)

    candidate: SpotlightKpiCandidate = {
        "name": name,
        "value": float(value_f),
        "unit": unit_s,
        "prior_value": None,
        "chart_type": "metric",
        "description": str(selected.get("what_it_measures") or "").strip() or None,
        "source_quote": excerpt,
        "representativeness_score": max(0, min(100, rep * 20)),
        "company_specificity_score": max(0, min(100, uniq * 20)),
        "verifiability_score": max(0, min(100, ver * 20)),
        "ban_flags": [],
    }

    return candidate, debug
