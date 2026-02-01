from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import anyio

from app.models.database import get_supabase_client
from app.services.gemini_client import get_gemini_client
from app.services.edgar_fetcher import download_filing
from app.services.local_cache import (
    fallback_filings,
    fallback_spotlight_kpis_by_id,
    save_spotlight_kpis_cache,
)

from .kpi_pipeline_evidence import extract_kpi_with_evidence_from_file
from .ranker import pick_best_spotlight_kpi
from .regex_fallback import extract_kpis_with_regex
from .text_pipeline import extract_company_specific_spotlight_kpi_from_text
from .types import SpotlightKpiCandidate


@dataclass(frozen=True)
class SpotlightPayload:
    filing_id: str
    company_kpi: Optional[Dict[str, Any]]
    company_charts: List[Dict[str, Any]]
    status: str
    reason: Optional[str]
    debug: Optional[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filing_id": self.filing_id,
            "company_kpi": self.company_kpi,
            "company_charts": self.company_charts,
            "status": self.status,
            "reason": self.reason,
            "debug": self.debug,
        }


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _spotlight_document_excerpt_limit() -> int:
    return _int_env("SPOTLIGHT_DOCUMENT_EXCERPT_CHARS", 650_000)


def _infer_local_document_mime_type(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            head = handle.read(5)
        if head == b"%PDF-":
            return "application/pdf"
    except Exception:  # noqa: BLE001
        pass

    suffix = str(path.suffix or "").lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in (".htm", ".html"):
        return "text/html"
    if suffix in (".txt", ".text"):
        return "text/plain"
    return "application/octet-stream"


def _is_sec_document_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if (parsed.scheme or "").lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().strip()
    return bool(host.endswith("sec.gov"))


def _strip_html_to_text(raw_html: str) -> str:
    """Convert SEC HTML (incl. iXBRL) into plain-ish text for extraction.

    Some filings (especially older iXBRL) contain huge amounts of taxonomy metadata
    as text nodes. That noise can dominate the extracted text and make KPI
    extraction fail. We aggressively strip common iXBRL patterns while preserving
    enough structure (newlines/tabs) for table-like KPI rows.
    """
    if not raw_html:
        return ""

    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)

    # iXBRL/taxonomy noise before tag stripping.
    cleaned = re.sub(r"https?://\S{10,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"www\.\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:us-gaap|dei|srt|ifrs-full|xbrli|xbrldi|xbrldt|iso4217|xlink|link|ref|xsd|ix|ixt):[A-Za-z0-9_.-]+\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b[a-z]{2,10}:[A-Za-z][A-Za-z0-9_.-]{2,}\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Preserve basic structure before stripping tags.
    cleaned = re.sub(r"(?is)</tr\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</td\s*>", "\t", cleaned)
    cleaned = re.sub(r"(?is)</th\s*>", "\t", cleaned)
    cleaned = re.sub(r"(?is)</p\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</div\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</li\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<br\s*/?\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)

    # Unescape entities.
    cleaned = unescape(cleaned)

    # Repeat iXBRL stripping after unescape (some filings encode `us-gaap:` etc).
    cleaned = re.sub(r"https?://\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"www\.\S{8,}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:us-gaap|dei|srt|ifrs-full|xbrli|xbrldi|xbrldt|iso4217|xlink|link|ref|xsd|ix|ixt):[A-Za-z0-9_.-]+\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'\bcontextRef="[^"]{1,80}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bunitRef="[^"]{1,40}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bname="[^"]{1,140}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bid="[^"]{1,120}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bdecimals="[^"]{1,20}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bscale="[^"]{1,20}"', " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bformat="[^"]{1,40}"', " ", cleaned, flags=re.IGNORECASE)

    # Normalize whitespace but preserve newlines for section/table readability.
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Drop ultra-noisy lines that look like XBRL context dumps.
    lines_out: List[str] = []
    for line in (cleaned or "").splitlines():
        s = line.strip()
        if not s:
            lines_out.append("")
            continue

        upper = s.upper()
        if (
            "ITEM " in upper
            or "MANAGEMENT DISCUSSION" in upper
            or "RISK FACTORS" in upper
            or "FINANCIAL STATEMENTS" in upper
            or "TABLE OF CONTENTS" in upper
        ):
            lines_out.append(s)
            continue

        tokens = s.split()
        if len(tokens) >= 10:
            noise_tokens = sum(1 for t in tokens if (":" in t or "/" in t))
            noise_ratio = noise_tokens / max(1, len(tokens))
            alpha = sum(1 for ch in s if ch.isalpha())
            digit = sum(1 for ch in s if ch.isdigit())
            if noise_ratio >= 0.35 and alpha < 40 and digit >= 10:
                continue
            if noise_ratio >= 0.55 and alpha < 80:
                continue

        lines_out.append(s)

    cleaned = "\n".join(lines_out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _load_spotlight_context_text(path: Path, *, limit: int) -> str:
    mime = _infer_local_document_mime_type(path)
    if mime == "application/pdf":
        try:
            import fitz  # PyMuPDF

            pdf_bytes = path.read_bytes()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = int(getattr(doc, "page_count", 0) or 0)

            # PDFs can be long; KPIs often appear in highlights/MD&A near the front,
            # but sometimes only appear in later sections. Sample across the document
            # so our text/regex fallbacks still have a chance when the evidence pipeline
            # fails (rate-limit/outage).
            if page_count <= 0:
                return ""

            max_pages = 18
            if page_count <= max_pages:
                indices = list(range(page_count))
            else:
                take = 6
                head = list(range(min(take, page_count)))
                tail = list(range(max(0, page_count - take), page_count))
                mid_start = max(0, (page_count // 2) - (take // 2))
                mid_end = min(page_count, mid_start + take)
                mid = list(range(mid_start, mid_end))

                seen: set[int] = set()
                indices = []
                for idx in head + mid + tail:
                    if idx in seen:
                        continue
                    seen.add(idx)
                    indices.append(idx)

            parts: List[str] = []
            total = 0
            for idx in indices:
                if total >= limit:
                    break
                try:
                    page = doc.load_page(int(idx))
                    text = page.get_text("text") or ""
                except Exception:  # noqa: BLE001
                    text = ""
                if not text:
                    continue
                parts.append(text)
                total += len(text)
            joined = "\n".join(parts).strip()
            return joined[:limit].rstrip() if joined else ""
        except Exception:  # noqa: BLE001
            return ""

    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            raw = path.read_text(errors="ignore")
        except Exception:  # noqa: BLE001
            raw = ""

    text = raw
    if mime == "text/html":
        text = _strip_html_to_text(raw)
    text = (text or "").strip()
    if not text:
        return ""

    if len(text) > limit:
        third = max(1, limit // 3)
        head = text[:third]
        mid_start = max(0, (len(text) // 2) - (third // 2))
        mid = text[mid_start : mid_start + third]
        tail = text[-third:]
        text = f"{head}\n\n--- MIDDLE ---\n\n{mid}\n\n--- END ---\n\n{tail}".strip()
    return text[:limit].rstrip()


def _format_period_label_from_dates(*, period_end: Any, filing_type: Any) -> str:
    filing_type_upper = str(filing_type or "").upper()
    is_quarterly = "10-Q" in filing_type_upper
    raw = str(period_end or "")[:10].strip()
    if not raw:
        return "Current" if is_quarterly else "FY"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if is_quarterly:
            quarter = (dt.month - 1) // 3 + 1
            return f"Q{quarter} {dt.year}"
        return f"FY {dt.year}"
    except Exception:  # noqa: BLE001
        return raw


def _normalize_spotlight_kpi_percent(kpi: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(kpi, dict):
        return kpi

    unit = str(kpi.get("unit") or "").strip().lower()
    if unit not in ("%", "percent", "percentage"):
        return kpi

    def _scale_if_decimal(v: Any) -> Any:
        if not isinstance(v, (int, float)):
            return v
        val = float(v)
        if -1.5 < val < 1.5 and abs(val) > 0.001:
            return val * 100.0
        return val

    kpi["value"] = _scale_if_decimal(kpi.get("value"))
    if kpi.get("prior_value") is not None:
        kpi["prior_value"] = _scale_if_decimal(kpi.get("prior_value"))

    history = kpi.get("history")
    if isinstance(history, list):
        for entry in history:
            if isinstance(entry, dict) and "value" in entry:
                entry["value"] = _scale_if_decimal(entry["value"])

    return kpi


def _apply_chart_type_hints(kpi: Dict[str, Any]) -> None:
    if not isinstance(kpi, dict):
        return
    if isinstance(kpi.get("segments"), list) and len(kpi.get("segments") or []) >= 2:
        kpi["chart_type"] = "breakdown"
        return
    if isinstance(kpi.get("history"), list) and len(kpi.get("history") or []) >= 3:
        kpi["chart_type"] = "trend"
        return

    unit = str(kpi.get("unit") or "").strip().lower()
    value = kpi.get("value")
    prior_value = kpi.get("prior_value")
    name_lower = str(kpi.get("name") or "").lower()

    has_prior = isinstance(prior_value, (int, float)) and float(prior_value) != 0.0

    gauge_patterns = (
        "margin",
        "rate",
        "ratio",
        "efficiency",
        "utilization",
        "occupancy",
        "penetration",
        "share",
        "conversion",
        "retention",
        "churn",
        "yield",
        "coverage",
        "load factor",
    )
    is_gauge_candidate = (
        unit in ("%", "percent", "percentage")
        and isinstance(value, (int, float))
        and 0 <= float(value) <= 100
        and any(pattern in name_lower for pattern in gauge_patterns)
    )

    if is_gauge_candidate:
        kpi["chart_type"] = "gauge"
    elif has_prior:
        kpi["chart_type"] = "comparison"
    else:
        kpi["chart_type"] = "metric"


def _sanitize_segments(
    segments: Any, *, company_name: str
) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(segments, list) or len(segments) < 2:
        return None

    generic = {"other", "total", "consolidated", "eliminations", "all other"}
    cleaned: List[Dict[str, Any]] = []
    seen: set[str] = set()

    company_tokens = {
        t for t in re.split(r"\W+", (company_name or "").lower()) if t
    }

    for seg in segments[:12]:
        if not isinstance(seg, dict):
            continue
        label = str(seg.get("label") or "").strip()
        if not label or len(label) > 64:
            continue
        label_norm = _normalize_ws(label)
        if not label_norm or label_norm in generic:
            continue
        if label_norm in seen:
            continue

        label_tokens = {t for t in re.split(r"\W+", label_norm) if t}
        if company_tokens and label_tokens and label_tokens.issubset(company_tokens):
            continue

        try:
            value = float(seg.get("value"))
        except Exception:  # noqa: BLE001
            continue
        if value <= 0:
            continue

        seen.add(label_norm)
        cleaned.append({"label": label, "value": value})

    if len(cleaned) < 2:
        return None

    non_other = [
        s for s in cleaned if _normalize_ws(str(s.get("label") or "")) != "other"
    ]
    if len(non_other) < 2:
        return None

    total = float(sum(float(s.get("value") or 0.0) for s in cleaned))
    if total <= 0:
        return None

    material_non_other = [
        s for s in non_other if (float(s.get("value") or 0.0) / total) >= 0.01
    ]
    if len(material_non_other) < 2:
        return None

    cleaned.sort(key=lambda s: float(s.get("value") or 0.0), reverse=True)
    return cleaned[:8]


def _normalize_ws(text: str) -> str:
    lowered = re.sub(r"\\s+", " ", (text or "").strip()).lower()
    lowered = re.sub(r"[^a-z0-9%$€£]+", " ", lowered)
    return re.sub(r"\\s+", " ", lowered).strip()


def _quote_in_context(source_quote: str, context_text: str) -> bool:
    if not source_quote or not context_text:
        return False
    q = source_quote.strip()
    if q.endswith("..."):
        q = q[:-3].rstrip()
    if not q:
        return False
    qn = _normalize_ws(q)
    ctx = _normalize_ws(context_text)
    if not qn or not ctx:
        return False
    return qn in ctx


def _kpi_to_frontend_shape(candidate: SpotlightKpiCandidate) -> Dict[str, Any]:
    allowed = {
        "name",
        "value",
        "unit",
        "prior_value",
        "chart_type",
        "company_specific",
        "period_label",
        "prior_period_label",
        "source_filing_id",
        "history",
        "segments",
        "source_quote",
        "description",
        # Evidence-backed KPI fields (frontend may ignore)
        "why_company_specific",
        "how_calculated_or_defined",
        "most_recent_value",
        "period",
        "confidence",
        "evidence",
    }
    return {k: v for k, v in dict(candidate).items() if k in allowed and v is not None}


def _load_company_filings_for_spotlight(
    *,
    company_id: Optional[str],
    current_filing_id: Optional[str],
    context_source: str,
    max_results: int = 25,
) -> List[Dict[str, Any]]:
    if not company_id:
        return []
    current = str(current_filing_id or "").strip()

    filings: List[Dict[str, Any]] = []
    if context_source == "supabase":
        try:
            supabase = get_supabase_client()
            resp = (
                supabase.table("filings")
                .select(
                    "id,company_id,filing_type,filing_date,period_end,report_date,local_document_path"
                )
                .eq("company_id", company_id)
                .order("filing_date", desc=True)
                .limit(int(max_results))
                .execute()
            )
            filings = list(resp.data or [])
        except Exception:  # noqa: BLE001
            filings = []
    else:
        raw = fallback_filings.get(company_id, []) if company_id else []
        filings = list(raw or [])[: int(max_results)]

    out: List[Dict[str, Any]] = []
    for f in filings:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        if not fid or (current and fid == current):
            continue
        ftype = str(f.get("filing_type") or "").upper()
        if not (
            ("10-Q" in ftype)
            or ("10-K" in ftype)
            or ("20-F" in ftype)
            or ("40-F" in ftype)
            or ("8-K" in ftype)
            or ("6-K" in ftype)
        ):
            continue
        out.append(f)
    return out


def _canonical_kpi_key(name: str) -> str:
    lowered = re.sub(r"\\s+", " ", (name or "").strip().lower())
    lowered = re.sub(r"[^a-z0-9%$€£ ]+", "", lowered)
    if "mau" in lowered or "monthly active users" in lowered:
        return "mau"
    if "dau" in lowered or "daily active users" in lowered:
        return "dau"
    if "subscriber" in lowered or "membership" in lowered:
        return "subscribers"
    if "arr" in lowered or "annual recurring revenue" in lowered:
        return "arr"
    if "mrr" in lowered or "monthly recurring revenue" in lowered:
        return "mrr"
    if "gmv" in lowered or "gross merchandise volume" in lowered:
        return "gmv"
    if "tpv" in lowered or "payment volume" in lowered or "processed volume" in lowered:
        return "tpv"
    if "nrr" in lowered or "ndr" in lowered or "net revenue retention" in lowered:
        return "nrr"
    if "aum" in lowered or "assets under management" in lowered:
        return "aum"
    if "orders" in lowered:
        return "orders"
    if "transactions" in lowered:
        return "transactions"
    if "deliver" in lowered and ("vehicle" in lowered or "units" in lowered):
        return "deliveries"
    return lowered


async def _build_history(
    *,
    kpi: Dict[str, Any],
    company: Dict[str, Any],
    company_name: str,
    current_filing: Dict[str, Any],
    current_filing_id: str,
    context_source: str,
    gemini_client: Any,
    max_points: int,
) -> List[Dict[str, Any]]:
    key = _canonical_kpi_key(str(kpi.get("name") or ""))
    if not key:
        return []

    company_id = str(company.get("id") or "") if company else ""
    candidates = _load_company_filings_for_spotlight(
        company_id=company_id,
        current_filing_id=current_filing_id,
        context_source=context_source,
        max_results=25,
    )
    if not candidates:
        return []

    points: List[Tuple[datetime, Dict[str, Any]]] = []
    excerpt_limit = _spotlight_document_excerpt_limit()

    def _parse_iso_date(raw: Any) -> Optional[datetime]:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text[:10].replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    for filing in candidates[:6]:
        path_str = filing.get("local_document_path")
        if not isinstance(path_str, str) or not path_str:
            continue
        path = Path(path_str)
        if not path.exists():
            continue
        doc_text = _load_spotlight_context_text(path, limit=excerpt_limit)
        if not doc_text:
            continue

        match: Optional[SpotlightKpiCandidate] = None
        if gemini_client:
            try:
                with anyio.fail_after(12.0):
                    kpi_candidate, _dbg = await anyio.to_thread.run_sync(
                        lambda: extract_company_specific_spotlight_kpi_from_text(
                            gemini_client,
                            context_text=doc_text,
                            company_name=company_name,
                        ),
                        cancellable=True,
                    )
                if kpi_candidate and _canonical_kpi_key(str(kpi_candidate.get("name") or "")) == key:
                    match = dict(kpi_candidate)
            except Exception:  # noqa: BLE001
                match = None
        else:
            for cand in extract_kpis_with_regex(doc_text, company_name, max_results=5):
                if _canonical_kpi_key(str(cand.get("name") or "")) != key:
                    continue
                if not _quote_in_context(str(cand.get("source_quote") or ""), doc_text):
                    continue
                match = dict(cand)
                break

        if not match:
            continue
        try:
            value = float(match.get("value"))
        except Exception:  # noqa: BLE001
            continue

        period_end = filing.get("period_end") or filing.get("report_date") or filing.get("filing_date")
        period_dt = _parse_iso_date(period_end) or _parse_iso_date(filing.get("filing_date"))
        if not period_dt:
            continue
        label = _format_period_label_from_dates(
            period_end=period_end,
            filing_type=filing.get("filing_type"),
        )
        points.append((period_dt, {"period_label": label, "value": value}))

    if not points:
        return []

    points.sort(key=lambda p: p[0])
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for _dt, payload in points:
        lbl = str(payload.get("period_label") or "").strip()
        if not lbl or lbl in seen:
            continue
        seen.add(lbl)
        deduped.append(payload)

    if len(deduped) > int(max_points):
        deduped = deduped[-int(max_points) :]

    current_label = str(kpi.get("period_label") or "").strip()
    try:
        current_value = float(kpi.get("value"))
    except Exception:  # noqa: BLE001
        current_value = None
    if current_label and current_value is not None:
        replaced = False
        for idx, existing in enumerate(deduped):
            if str(existing.get("period_label") or "") == current_label:
                deduped[idx] = {"period_label": current_label, "value": current_value}
                replaced = True
                break
        if not replaced:
            deduped.append({"period_label": current_label, "value": current_value})
            if len(deduped) > int(max_points):
                deduped = deduped[-int(max_points) :]

    return deduped


def _cache_config() -> Tuple[str, int, int]:
    version = (os.getenv("SPOTLIGHT_CACHE_VERSION") or "").strip() or "spotlight-cache-evidence-20260131-6"
    ttl_s = _int_env("SPOTLIGHT_CACHE_TTL_SECONDS", 604800)
    ttl_s = max(0, int(ttl_s))
    max_items = _int_env("SPOTLIGHT_CACHE_MAX_ITEMS", 4000)
    max_items = max(0, int(max_items))
    return version, ttl_s, max_items


def _cache_get(filing_id: str, *, debug: bool) -> Optional[Dict[str, Any]]:
    if debug or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    cache_key = str(filing_id)
    version, ttl_s, _max_items = _cache_config()
    if ttl_s <= 0:
        return None
    cached = fallback_spotlight_kpis_by_id.get(cache_key)
    if not isinstance(cached, dict) or cached.get("payload") is None:
        return None
    if str(cached.get("version") or "") != version:
        return None
    created_at = cached.get("created_at")
    created_dt = (
        datetime.fromisoformat(created_at)
        if isinstance(created_at, str) and created_at
        else None
    )
    if created_dt is not None:
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if (now - created_dt).total_seconds() > float(ttl_s):
            return None
    payload = cached.get("payload")
    return payload if isinstance(payload, dict) else None


def _cache_set(filing_id: str, payload: Dict[str, Any], *, reason: Optional[str], debug: bool) -> None:
    if debug or os.getenv("PYTEST_CURRENT_TEST"):
        return
    transient_reasons = {
        # Missing local document is not stable; the filing can be downloaded later.
        "no_local_document",
        # Evidence pipeline transient failures (network/model instability).
        "no_file_uri",
        "timeout_before_pass1",
        "timeout_before_pass2",
        "pass1_failed",
        "pass1_invalid_json",
        "pass2_failed",
        "pass2_invalid_json",
        "pass3_invalid_json",
        "pass4_invalid_json",
        "spotlight_v3_exception",
        "spotlight_v3_timeout",
        "spotlight_evidence_exception",
        "spotlight_evidence_timeout",
        "upload_failed",
    }
    if (reason or "") in transient_reasons:
        return

    cache_key = str(filing_id)
    version, _ttl_s, max_items = _cache_config()
    try:
        fallback_spotlight_kpis_by_id[cache_key] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": version,
            "payload": payload,
        }
        if max_items and len(fallback_spotlight_kpis_by_id) > max_items:
            try:
                items: List[Tuple[datetime, str]] = []
                for k, v in list(fallback_spotlight_kpis_by_id.items()):
                    if not isinstance(v, dict):
                        continue
                    ts = v.get("created_at")
                    if isinstance(ts, str) and ts:
                        try:
                            dt = datetime.fromisoformat(ts)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            dt = datetime.fromtimestamp(0, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(0, tz=timezone.utc)
                    items.append((dt, k))
                items.sort(key=lambda t: t[0])
                to_drop = max(0, len(items) - max_items)
                for _dt, k in items[:to_drop]:
                    fallback_spotlight_kpis_by_id.pop(k, None)
            except Exception:  # noqa: BLE001
                pass
        save_spotlight_kpis_cache()
    except Exception:  # noqa: BLE001
        pass


async def build_spotlight_payload_for_filing(
    filing_id: str,
    *,
    filing: Dict[str, Any],
    company: Dict[str, Any],
    local_document_path: Optional[Path],
    settings: Any,
    context_source: str = "",
    debug: bool = False,
) -> Dict[str, Any]:
    """Authoritative Spotlight KPI extraction pipeline.

    Owns:
    - cache get/set (local_cache fallback cache)
    - selecting pipeline (file-native v3 -> text pipeline -> regex fallback)
    - quote verification/ranking, percent normalization, chart-type hints
    - best-effort history construction
    """
    start_ts = time.monotonic()

    cached = _cache_get(filing_id, debug=debug)
    if cached is not None:
        return cached

    company_name = str(company.get("name") or company.get("ticker") or "Company").strip()
    company_name = re.sub(r"\\s+", " ", company_name).strip() or "Company"

    gemini_client = None
    try:
        if getattr(settings, "gemini_api_key", None) and str(settings.gemini_api_key).strip():
            gemini_client = get_gemini_client()
    except Exception:  # noqa: BLE001
        gemini_client = None

    debug_info: Dict[str, Any] = {}
    document_text = ""
    pipeline_file_bytes: Optional[bytes] = None
    pipeline_file_mime: Optional[str] = None
    pipeline_unavailable_reason: Optional[str] = None

    has_local = bool(local_document_path and local_document_path.exists())
    if has_local and local_document_path is not None:
        excerpt_limit = _spotlight_document_excerpt_limit()
        document_text = _load_spotlight_context_text(local_document_path, limit=excerpt_limit)

        if gemini_client:
            try:
                max_bytes_raw = (
                    os.getenv("SPOTLIGHT_FILE_PIPELINE_MAX_UPLOAD_BYTES") or ""
                ).strip() or "50000000"
                try:
                    max_bytes = int(max_bytes_raw)
                except ValueError:
                    max_bytes = 50_000_000
                max_bytes = max(0, int(max_bytes))
                if max_bytes <= 0:
                    pipeline_unavailable_reason = "file_pipeline_upload_disabled"
                else:
                    raw = local_document_path.read_bytes()
                    if raw and len(raw) <= max_bytes:
                        pipeline_file_mime = _infer_local_document_mime_type(local_document_path)
                        pipeline_file_bytes = raw
                    else:
                        pipeline_unavailable_reason = (
                            "file_pipeline_file_too_large"
                            if raw and len(raw) > max_bytes
                            else "file_pipeline_empty_file"
                        )
                        if debug:
                            debug_info["pipeline_file_size_bytes"] = int(len(raw or b""))
                            debug_info["pipeline_max_upload_bytes"] = int(max_bytes)
            except Exception:  # noqa: BLE001
                pipeline_file_bytes = None
                pipeline_file_mime = None
                if pipeline_unavailable_reason is None:
                    pipeline_unavailable_reason = "file_pipeline_read_failed"

        upload_mode = (os.getenv("SPOTLIGHT_HTML_UPLOAD_MODE") or "").strip().lower() or "clean_text"
        if (
            pipeline_file_bytes
            and pipeline_file_mime == "text/html"
            and upload_mode in ("clean_text", "text", "plain", "txt")
            and (document_text or "").strip()
        ):
            pipeline_file_bytes = (document_text or "").encode("utf-8", errors="ignore")
            pipeline_file_mime = "text/plain"



    if debug:
        debug_info.update(
            {
                "filing_id": str(filing_id),
                "company_name": company_name,
                "has_local_document": has_local,
                "local_document_suffix": str(local_document_path.suffix).lower()
                if local_document_path
                else None,
                "pipeline_file_bytes": bool(pipeline_file_bytes),
                "pipeline_file_mime": pipeline_file_mime,
                "pipeline_unavailable_reason": pipeline_unavailable_reason,
                "document_text_chars": int(len(document_text or "")),
                "has_gemini_client": bool(gemini_client),
            }
        )

    spotlight_status = "ok"
    spotlight_reason: Optional[str] = None
    spotlight_used_fallbacks = False

    best_kpi: Optional[SpotlightKpiCandidate] = None

    # 1) Preferred: evidence-backed pipeline (PDF/bytes upload) when Gemini configured.
    pipeline_dbg: Optional[Dict[str, Any]] = None
    evidence_pipeline_reason: Optional[str] = None
    if pipeline_file_bytes and pipeline_file_mime and gemini_client:
        try:
            timeout_s = 20.0
            raw_timeout = (os.getenv("SPOTLIGHT_KPI_ENDPOINT_TIMEOUT_SECONDS") or "").strip()
            if raw_timeout:
                timeout_s = min(float(raw_timeout), 30.0)
        except ValueError:
            timeout_s = 20.0

        try:
            with anyio.fail_after(timeout_s):
                kpi_candidate, pipeline_dbg = await anyio.to_thread.run_sync(
                    lambda: extract_kpi_with_evidence_from_file(
                        gemini_client,
                        file_bytes=pipeline_file_bytes or b"",
                        mime_type=pipeline_file_mime or "application/octet-stream",
                        company_name=company_name,
                    ),
                    cancellable=True,
                )
        except TimeoutError:
            kpi_candidate, pipeline_dbg = None, {"reason": "spotlight_evidence_timeout"}
        except Exception as exc:  # noqa: BLE001
            kpi_candidate, pipeline_dbg = None, {"reason": "spotlight_evidence_exception", "error": str(exc)[:500]}

        evidence_pipeline_reason = str((pipeline_dbg or {}).get("reason") or "").strip() or None

        if debug:
            debug_info["evidence_pipeline"] = pipeline_dbg

        if kpi_candidate:
            item = dict(kpi_candidate)
            item["company_specific"] = True

            # The evidence pipeline already verifies quotes against the full document.
            # Avoid rejecting on a truncated `document_text` excerpt (common for long PDFs).
            best_kpi = item

    # 2) Text-only pipeline when Gemini configured and local text exists, but we cannot
    # upload bytes (disabled/too-large). This pipeline still requires verbatim excerpts
    # and should return None when evidence is weak.
    evidence_pipeline_fallback_reasons = {
        "upload_failed",
        "no_file_uri",
        "timeout_before_pass1",
        "pass1_failed",
        "pass1_invalid_json",
        "pass1_no_candidates",
        "pass1_no_valid_candidates",
        "timeout_before_pass2",
        "pass2_failed",
        "pass2_invalid_json",
        "spotlight_evidence_exception",
        "spotlight_evidence_timeout",
    }
    if (
        not best_kpi
        and gemini_client
        and (document_text or "").strip()
        and (
            not (pipeline_file_bytes and pipeline_file_mime)
            or (evidence_pipeline_reason in evidence_pipeline_fallback_reasons)
        )
    ):
        try:
            with anyio.fail_after(18.0):
                kpi_candidate, text_dbg = await anyio.to_thread.run_sync(
                    lambda: extract_company_specific_spotlight_kpi_from_text(
                        gemini_client,
                        context_text=document_text,
                        company_name=company_name,
                    ),
                    cancellable=True,
                )
            if debug:
                debug_info["text_pipeline"] = text_dbg
            if kpi_candidate:
                spotlight_used_fallbacks = True
                item = dict(kpi_candidate)
                item["company_specific"] = True
                validated = pick_best_spotlight_kpi([item], context_text=document_text)
                if validated:
                    best_kpi = dict(validated)
                    best_kpi["company_specific"] = True
        except TimeoutError:
            if debug:
                debug_info["text_pipeline_error"] = "timeout"
        except Exception as exc:  # noqa: BLE001
            if debug:
                debug_info["text_pipeline_error"] = str(exc)[:300]

    # 3) Deterministic regex fallback (safe + verifiable quote).
    # Use even when Gemini is configured: this is our last-resort path when the
    # model is rate-limited/unavailable but the filing text contains a clear KPI.
    if not best_kpi and (document_text or "").strip():
        candidates = extract_kpis_with_regex(document_text, company_name, max_results=5)
        picked: Optional[SpotlightKpiCandidate] = None
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if not _quote_in_context(str(cand.get("source_quote") or ""), document_text):
                continue
            picked = dict(cand)
            break
        if picked:
            spotlight_used_fallbacks = True
            picked["company_specific"] = True
            best_kpi = picked

    # 4) Table-based KPI extraction fallback.
    # Handles structured "Key Metrics" tables that regex patterns may miss.
    if not best_kpi and (document_text or "").strip():
        try:
            from .table_kpi_extractor import extract_kpis_from_text_tables
            table_candidates = extract_kpis_from_text_tables(document_text, company_name, max_results=5)
            for cand in table_candidates:
                if not isinstance(cand, dict):
                    continue
                quote = str(cand.get("source_quote") or "").strip()
                if not quote or len(quote) <= 10:
                    continue
                if not _quote_in_context(quote, document_text):
                    continue

                spotlight_used_fallbacks = True
                cand_item = dict(cand)
                cand_item["company_specific"] = True

                validated = pick_best_spotlight_kpi([cand_item], context_text=document_text)
                if validated:
                    best_kpi = dict(validated)
                    best_kpi["company_specific"] = True
                    if debug:
                        debug_info["table_extraction_used"] = True
                    break
        except Exception as exc:  # noqa: BLE001
            if debug:
                debug_info["table_extraction_error"] = str(exc)[:200]

    # 5) EDGAR artifact fallback: older/obscure filings sometimes cache a low-signal
    # SEC primary document (or an iXBRL-heavy HTML) that contains few/no extractable
    # operating KPIs, while an exhibit or the full submission TXT does. When the
    # evidence pipeline returns no candidates, try re-downloading the best exhibit
    # from the accession directory into a sidecar file and re-run extraction.
    if (
        not best_kpi
        and gemini_client
        and has_local
        and evidence_pipeline_reason in {"pass1_no_candidates", "pass1_no_valid_candidates"}
        and str(os.getenv("SPOTLIGHT_ALLOW_NETWORK", "1") or "").strip().lower() in {"1", "true", "yes"}
        and str(os.getenv("SPOTLIGHT_EDGAR_ARTIFACT_FALLBACK", "1") or "").strip().lower() in {"1", "true", "yes"}
        and not os.getenv("PYTEST_CURRENT_TEST")
    ):
        source_url = str(filing.get("source_doc_url") or filing.get("url") or "").strip()
        if source_url and _is_sec_document_url(source_url) and local_document_path:
            try:
                raw_max = (os.getenv("SPOTLIGHT_EDGAR_FALLBACK_MAX_BYTES") or "").strip() or "25000000"
                try:
                    max_exhibit_size = int(raw_max)
                except ValueError:
                    max_exhibit_size = 25_000_000
                max_exhibit_size = max(0, int(max_exhibit_size))
            except Exception:  # noqa: BLE001
                max_exhibit_size = 25_000_000

            alt_path = local_document_path.with_name(
                f"{local_document_path.stem}.spotlight-edgar-alt{local_document_path.suffix or '.html'}"
            )

            ok = False
            try:
                with anyio.fail_after(18.0):
                    ok = await anyio.to_thread.run_sync(
                        lambda: download_filing(
                            source_url,
                            str(alt_path),
                            force_best_exhibit=True,
                            max_exhibit_size_bytes=max_exhibit_size,
                        ),
                        cancellable=True,
                    )
            except TimeoutError:
                ok = False
            except Exception as exc:  # noqa: BLE001
                ok = False
                if debug:
                    debug_info["edgar_fallback_download_error"] = str(exc)[:200]

            if debug:
                debug_info["edgar_fallback_attempted"] = True
                debug_info["edgar_fallback_source_url"] = source_url
                debug_info["edgar_fallback_ok"] = bool(ok)
                debug_info["edgar_fallback_max_bytes"] = int(max_exhibit_size)

            if ok and alt_path.exists():
                # Recompute excerpt and upload bytes for the alternate artifact.
                excerpt_limit = _spotlight_document_excerpt_limit()
                alt_text = _load_spotlight_context_text(alt_path, limit=excerpt_limit)

                alt_bytes: Optional[bytes] = None
                alt_mime: Optional[str] = None
                alt_unavailable_reason: Optional[str] = None

                try:
                    max_bytes_raw = (
                        os.getenv("SPOTLIGHT_FILE_PIPELINE_MAX_UPLOAD_BYTES") or ""
                    ).strip() or "50000000"
                    try:
                        max_bytes = int(max_bytes_raw)
                    except ValueError:
                        max_bytes = 50_000_000
                    max_bytes = max(0, int(max_bytes))
                    if max_bytes <= 0:
                        alt_unavailable_reason = "file_pipeline_upload_disabled"
                    else:
                        raw = alt_path.read_bytes()
                        if raw and len(raw) <= max_bytes:
                            alt_mime = _infer_local_document_mime_type(alt_path)
                            alt_bytes = raw
                        else:
                            alt_unavailable_reason = (
                                "file_pipeline_file_too_large"
                                if raw and len(raw) > max_bytes
                                else "file_pipeline_empty_file"
                            )
                except Exception:  # noqa: BLE001
                    alt_bytes = None
                    alt_mime = None
                    if alt_unavailable_reason is None:
                        alt_unavailable_reason = "file_pipeline_read_failed"

                upload_mode = (os.getenv("SPOTLIGHT_HTML_UPLOAD_MODE") or "").strip().lower() or "clean_text"
                if (
                    alt_bytes
                    and alt_mime == "text/html"
                    and upload_mode in ("clean_text", "text", "plain", "txt")
                    and (alt_text or "").strip()
                ):
                    alt_bytes = (alt_text or "").encode("utf-8", errors="ignore")
                    alt_mime = "text/plain"

                if debug:
                    debug_info["edgar_fallback_document_text_chars"] = int(len(alt_text or ""))
                    debug_info["edgar_fallback_pipeline_file_bytes"] = bool(alt_bytes)
                    debug_info["edgar_fallback_pipeline_file_mime"] = alt_mime
                    debug_info["edgar_fallback_pipeline_unavailable_reason"] = alt_unavailable_reason

                # Try the evidence pipeline again on the alternate artifact first.
                if alt_bytes and alt_mime:
                    try:
                        timeout_s = 20.0
                        raw_timeout = (
                            os.getenv("SPOTLIGHT_KPI_ENDPOINT_TIMEOUT_SECONDS") or ""
                        ).strip()
                        if raw_timeout:
                            timeout_s = min(float(raw_timeout), 30.0)
                    except ValueError:
                        timeout_s = 20.0

                    try:
                        with anyio.fail_after(timeout_s):
                            alt_kpi, alt_dbg = await anyio.to_thread.run_sync(
                                lambda: extract_kpi_with_evidence_from_file(
                                    gemini_client,
                                    file_bytes=alt_bytes or b"",
                                    mime_type=alt_mime or "application/octet-stream",
                                    company_name=company_name,
                                ),
                                cancellable=True,
                            )
                    except TimeoutError:
                        alt_kpi, alt_dbg = None, {"reason": "spotlight_evidence_timeout"}
                    except Exception as exc:  # noqa: BLE001
                        alt_kpi, alt_dbg = None, {
                            "reason": "spotlight_evidence_exception",
                            "error": str(exc)[:500],
                        }

                    if debug:
                        debug_info["edgar_fallback_evidence_pipeline"] = alt_dbg

                    if alt_kpi:
                        spotlight_used_fallbacks = True
                        item = dict(alt_kpi)
                        item["company_specific"] = True
                        best_kpi = item

                # If file upload is unavailable for the alternate artifact, try text-only extraction.
                if not best_kpi and (alt_text or "").strip():
                    try:
                        with anyio.fail_after(18.0):
                            alt_kpi, alt_text_dbg = await anyio.to_thread.run_sync(
                                lambda: extract_company_specific_spotlight_kpi_from_text(
                                    gemini_client,
                                    context_text=alt_text,
                                    company_name=company_name,
                                ),
                                cancellable=True,
                            )
                        if debug:
                            debug_info["edgar_fallback_text_pipeline"] = alt_text_dbg
                        if alt_kpi:
                            spotlight_used_fallbacks = True
                            item = dict(alt_kpi)
                            item["company_specific"] = True
                            validated = pick_best_spotlight_kpi([item], context_text=alt_text)
                            if validated:
                                best_kpi = dict(validated)
                                best_kpi["company_specific"] = True
                    except TimeoutError:
                        if debug:
                            debug_info["edgar_fallback_text_pipeline_error"] = "timeout"
                    except Exception as exc:  # noqa: BLE001
                        if debug:
                            debug_info["edgar_fallback_text_pipeline_error"] = str(exc)[:200]

                # Deterministic fallbacks on the alternate text.
                if not best_kpi and (alt_text or "").strip():
                    candidates = extract_kpis_with_regex(alt_text, company_name, max_results=5)
                    picked: Optional[SpotlightKpiCandidate] = None
                    for cand in candidates:
                        if not isinstance(cand, dict):
                            continue
                        if not _quote_in_context(str(cand.get("source_quote") or ""), alt_text):
                            continue
                        picked = dict(cand)
                        break
                    if picked:
                        spotlight_used_fallbacks = True
                        picked["company_specific"] = True
                        best_kpi = picked

    if debug:
        debug_info["spotlight_used_fallbacks"] = spotlight_used_fallbacks
        debug_info["spotlight_result"] = (
            [{"name": str(best_kpi.get("name") or ""), "company_specific": True}] if best_kpi else []
        )

    if not best_kpi:
        spotlight_status = "no_kpi"
        if not has_local:
            spotlight_reason = "no_local_document"
        elif not gemini_client:
            spotlight_reason = "gemini_not_configured"
        elif pipeline_file_bytes:
            spotlight_reason = str((pipeline_dbg or {}).get("reason") or "").strip() or "file_pipeline_no_kpi"
        elif (document_text or "").strip():
            spotlight_reason = pipeline_unavailable_reason or "file_pipeline_unavailable"
        else:
            spotlight_reason = "no_text_available"

    company_charts: List[Dict[str, Any]] = []
    company_kpi: Optional[Dict[str, Any]] = None

    if best_kpi:
        kpi_obj = _kpi_to_frontend_shape(best_kpi)

        if not kpi_obj.get("period_label"):
            kpi_obj["period_label"] = _format_period_label_from_dates(
                period_end=filing.get("period_end") or filing.get("report_date") or filing.get("filing_date"),
                filing_type=filing.get("filing_type"),
            )
        kpi_obj.setdefault("source_filing_id", str(filing_id))
        kpi_obj["company_specific"] = True

        segments = _sanitize_segments(kpi_obj.get("segments"), company_name=company_name)
        if segments:
            kpi_obj["segments"] = segments
        else:
            kpi_obj.pop("segments", None)

        # History is best-effort and bounded.
        history: List[Dict[str, Any]] = []
        history_timeout_s = 12.0
        raw_history_timeout = (os.getenv("SPOTLIGHT_HISTORY_TIMEOUT_SECONDS") or "").strip()
        if raw_history_timeout:
            try:
                history_timeout_s = float(raw_history_timeout)
            except ValueError:
                history_timeout_s = 12.0
        history_timeout_s = max(0.0, min(history_timeout_s, 25.0))

        if history_timeout_s > 0:
            try:
                with anyio.fail_after(history_timeout_s):
                    history = await _build_history(
                        kpi=kpi_obj,
                        company=company,
                        company_name=company_name,
                        current_filing=filing,
                        current_filing_id=str(filing_id),
                        context_source=str(context_source or ""),
                        gemini_client=gemini_client,
                        max_points=8,
                    )
            except TimeoutError:
                history = []
                if debug:
                    debug_info["history_error"] = "timeout"
            except Exception as exc:  # noqa: BLE001
                history = []
                if debug:
                    debug_info["history_error"] = str(exc)[:200]
        if history:
            kpi_obj["history"] = history

        _normalize_spotlight_kpi_percent(kpi_obj)
        _apply_chart_type_hints(kpi_obj)

        company_kpi = kpi_obj
        company_charts = [kpi_obj]

    if debug:
        debug_info["timing_ms"] = int((time.monotonic() - start_ts) * 1000)

    payload = SpotlightPayload(
        filing_id=str(filing_id),
        company_kpi=company_kpi,
        company_charts=company_charts,
        status=spotlight_status,
        reason=spotlight_reason,
        debug=debug_info if debug else None,
    ).as_dict()

    _cache_set(filing_id, payload, reason=spotlight_reason, debug=debug)
    return payload
