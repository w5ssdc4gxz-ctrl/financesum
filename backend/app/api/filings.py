"""Filings API endpoints."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from uuid import uuid4
from app.models.database import get_supabase_client
from app.models.schemas import (
    Filing,
    FilingsFetchRequest,
    FilingsFetchResponse,
    FilingSummaryPreferences,
)
from app.tasks.fetch import fetch_filings_task
from app.config import get_settings
from app.api.companies import _supabase_configured
from app.services.eodhd_client import (
    get_eodhd_client,
    EODHDAccessError,
    EODHDClientError,
)
from app.services.edgar_fetcher import (
    download_filing,
    get_company_filings,
    search_company_by_ticker_or_cik,
)
from app.services.local_cache import (
    fallback_companies,
    fallback_filings,
    fallback_filings_by_id,
    fallback_financial_statements,
    fallback_filing_summaries,
)
from app.services.gemini_client import get_gemini_client
from app.services.sample_data import sample_filings_by_ticker

router = APIRouter()
logger = logging.getLogger(__name__)

# Gemini 2.0 Flash Lite supports up to ~1M tokens. We limit to keep requests manageable.
MAX_GEMINI_CONTEXT_CHARS = 600_000
MAX_SUMMARY_ATTEMPTS = 3

DETAIL_LEVEL_PROMPTS: Dict[str, str] = {
    "snapshot": "Keep analysis concise (1–2 short paragraphs) and only cite headline metrics that prove the main point.",
    "balanced": "Provide balanced coverage with equal weight on growth, profitability, balance sheet, and guidance.",
    "deep dive": "Offer exhaustive commentary with supporting data points for every section, including subtle nuances from management commentary.",
}

OUTPUT_STYLE_PROMPTS: Dict[str, str] = {
    "narrative": "Write in cohesive paragraphs with strong topic sentences and transitions. Avoid bullet lists except where explicitly required by the base template.",
    "bullets": "Favor bullet lists and short sentences. Each bullet should start with a bolded label followed by insights.",
    "mixed": "Open each section with a short paragraph, then follow with a bulleted list of the most actionable takeaways.",
}


def _clamp_target_length(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(10, min(5000, value))


def _count_words(text: str) -> int:
    tokens = re.findall(r"\b\w+\b", text)
    return len(tokens)


def _needs_length_retry(text: str, target_length: int) -> Tuple[bool, int, int]:
    """Return tuple indicating if retry needed, actual count, tolerance band size."""
    words = _count_words(text)
    tolerance = max(5, int(target_length * 0.05))
    lower = target_length - tolerance
    upper = target_length + tolerance
    if lower <= words <= upper:
        return False, words, tolerance
    return True, words, tolerance


def _generate_summary_with_length_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
) -> str:
    return _generate_summary_with_quality_control(gemini_client, base_prompt, target_length, None)


def _generate_summary_with_quality_control(
    gemini_client,
    base_prompt: str,
    target_length: Optional[int],
    quality_validators: Optional[List[Callable[[str], Optional[str]]]],
) -> str:
    """
    Call Gemini up to MAX_SUMMARY_ATTEMPTS times, tightening instructions if word count or quality drifts.
    """
    corrections: List[str] = []
    prompt = base_prompt

    def _rebuild_prompt() -> str:
        return base_prompt + ("\n\n" + "\n\n".join(corrections) if corrections else "")

    for attempt in range(1, MAX_SUMMARY_ATTEMPTS + 1):
        response = gemini_client.model.generate_content(prompt)
        summary_text = response.text

        needs_length_retry = False
        if target_length:
            needs_length_retry, actual_words, tolerance = _needs_length_retry(summary_text, target_length)

        if not needs_length_retry:
            issue_message = None
            if quality_validators:
                for validator in quality_validators:
                    issue_message = validator(summary_text)
                    if issue_message:
                        break
            if not issue_message:
                return summary_text

            corrections.append(
                f"QUALITY CORRECTION #{attempt}: {issue_message} Rewrite the entire memo while keeping all previous requirements intact."
            )
            prompt = _rebuild_prompt()
            continue

        corrections.append(
            f"LENGTH CORRECTION #{attempt}: The previous draft contained {actual_words} words but the target is "
            f"{target_length} words (tolerance ±{tolerance}). Rewrite the entire memo so the total word count falls "
            f"within this band while preserving all original sections and requirements. Do not mention the counting process."
        )
        prompt = _rebuild_prompt()

    return summary_text


def _trim_text_to_word_limit(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    words = 0
    pieces: List[str] = []
    for match in re.finditer(r"\S+|\s+", text):
        part = match.group(0)
        if part.isspace():
            pieces.append(part)
            continue
        if words >= limit:
            break
        pieces.append(part)
        words += 1
    trimmed = "".join(pieces).rstrip()
    return trimmed if trimmed else text


def _finalize_summary_length(text: str, target_length: Optional[int]) -> str:
    if not target_length:
        return text
    needs_retry, actual_words, tolerance = _needs_length_retry(text, target_length)
    if not needs_retry:
        return text
    upper_limit = target_length + tolerance
    lower_limit = max(1, target_length - tolerance)
    if actual_words > upper_limit:
        return _trim_text_to_word_limit(text, upper_limit)
    # Too short: add a short clarifying line without exceeding tolerance too much
    padding_sentence = (
        "\n\nAdditional disclosure beyond this scope is not provided in the filing, so this memo covers every available detail."
    )
    candidate = text.rstrip() + padding_sentence
    # If padding overshoots, trim back down to upper limit
    _, new_words, _ = _needs_length_retry(candidate, target_length)
    if new_words > upper_limit:
        return _trim_text_to_word_limit(candidate, upper_limit)
    if new_words < lower_limit:
        # As a final measure, target the midpoint
        midpoint = (lower_limit + upper_limit) // 2
        return _trim_text_to_word_limit(candidate, midpoint)
    return candidate


MDNA_BANNED_PHRASES = [
    "not available",
    "not provided",
    "no insights",
    "no information",
    "cannot be gleaned",
    "cannot be inferred",
    "not included",
]


def _validate_mdna_section(text: str) -> Optional[str]:
    """Ensure Management Discussion section exists and has substance."""
    mdna_pattern = re.compile(
        r"(?:##+\s*)?Management Discussion(?:\s*&\s*Analysis)?[:\s]*(.*?)(?:\n(?:#|\w)|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = mdna_pattern.search(text)
    if not match:
        return (
            "You omitted a meaningful 'Management Discussion & Analysis' section. "
            "Add a dedicated subsection that discusses management's priorities, strategy, and outlook."
        )
    section_text = match.group(0).strip()
    lower_section = section_text.lower()
    if any(phrase in lower_section for phrase in MDNA_BANNED_PHRASES):
        return (
            "The 'Management Discussion & Analysis' section currently claims information is unavailable. "
            "Instead, synthesize management's likely commentary using the filing data and historical initiatives."
        )
    if len(section_text.split()) < 60:
        return (
            "The 'Management Discussion & Analysis' section is too brief. Expand it with concrete takeaways on strategy, "
            "competitive dynamics, capital deployment, and guidance signals."
        )
    return None

def _build_preference_instructions(
    preferences: Optional[FilingSummaryPreferences],
    company_name: Optional[str] = None,
) -> str:
    """Convert user-provided preferences into prompt guidance."""
    if not preferences or preferences.mode == "default":
        return "- Use the standard structure below with a balanced, neutral tone suitable for institutional investors."

    instructions: List[str] = ["- Absolute priority: satisfy the investor's custom brief before any boilerplate."]

    investor_focus = preferences.investor_focus.strip() if preferences.investor_focus else None
    if investor_focus:
        focus_clause = (
            f"{investor_focus} as it relates to {company_name}" if company_name else investor_focus
        )
        instructions.append(
            f"- Investor brief (absolute priority): {focus_clause}. Mirror this persona's diction, risk tolerance, and valuation discipline throughout."
        )
        instructions.append(
            "- Begin the memo with a labeled 'Investor Lens' paragraph that restates this brief and explains how the filing will be evaluated through it."
        )
        instructions.append(
            "- In both the Investor Lens and Executive Summary sections, explicitly reference this persona by name (or title) and show how each takeaway maps to their checklist."
        )
        instructions.append(
            "- In every major section, include at least one sentence explaining why the content matters to this investor profile before citing generic takeaways."
        )

    if preferences.focus_areas:
        joined = ", ".join(preferences.focus_areas)
        instructions.append(
            f"- Primary focus areas (cover strictly in this order, dedicating at least one labeled paragraph or subsection to each): {joined}."
        )
        instructions.append(
            "- Do not introduce unrelated themes unless they reinforce the requested focus areas; if information is missing, explicitly state the gap and why."
        )
        ordered_lines = "\n".join(f"   {idx + 1}. {area}" for idx, area in enumerate(preferences.focus_areas))
        instructions.append("  Focus area execution order:\n" + ordered_lines)

    if preferences.tone:
        instructions.append(f"- Tone must remain {preferences.tone.lower()} throughout.")

    detail_prompt = DETAIL_LEVEL_PROMPTS.get((preferences.detail_level or "").lower())
    if detail_prompt:
        instructions.append(f"- Detail expectation: {detail_prompt}")

    output_prompt = OUTPUT_STYLE_PROMPTS.get((preferences.output_style or "").lower())
    if output_prompt:
        instructions.append(f"- Output style: {output_prompt}")

    target_length = _clamp_target_length(preferences.target_length)
    if target_length:
        tolerance = max(5, int(target_length * 0.05))
        instructions.extend(
            [
                f"- Final deliverable must contain {target_length} words (acceptable band ±{tolerance}). Count the words before responding and revise until it fits.",
                "- Do NOT mention the counting process or the word count in the output; silently edit to meet the requirement.",
            ]
        )

    return "\n".join(instructions)


def _sample_entries_for_ticker(ticker: str) -> List[Dict[str, Any]]:
    """Return sample filing entries for tickers when live data is unavailable."""
    samples = sample_filings_by_ticker.get((ticker or "").upper(), [])
    formatted_entries: List[Dict[str, Any]] = []
    for sample in samples:
        formatted_entries.append(
            {
                "filing_type": sample.get("filing_type", "10-Q"),
                "date_str": sample.get("filing_date"),
                "income_statement": sample.get("income_statement", {}),
                "balance_sheet": sample.get("balance_sheet", {}),
                "cash_flow": sample.get("cash_flow", {}),
                "url": sample.get("url", "https://www.sec.gov"),
            }
        )
    return formatted_entries


def _build_document_path(filing_id: str, settings) -> str:
    return f"/api/{settings.api_version}/filings/{filing_id}/document"


def _strip_html_to_text(raw_html: str) -> str:
    """Convert HTML document into plain text for AI consumption."""
    # Remove script and style blocks
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    # Remove HTML tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Unescape HTML entities
    cleaned = unescape(cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\\s+", " ", cleaned)
    return cleaned.strip()


def _extract_section(text: str, start_marker: str, end_markers: List[str]) -> str:
    """Extract a section from text bounded by start and optional end markers."""
    upper_text = text.upper()
    start_upper = start_marker.upper()
    start_idx = upper_text.find(start_upper)
    if start_idx == -1:
        return ""

    end_idx = len(text)
    for marker in end_markers:
        marker_upper = marker.upper()
        candidate = upper_text.find(marker_upper, start_idx + len(start_upper))
        if candidate != -1 and candidate < end_idx:
            end_idx = candidate

    section = text[start_idx:end_idx].strip()
    return section


def _load_document_excerpt(path: Path, limit: Optional[int] = None) -> str:
    """Load filing document and extract the most relevant textual sections."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_text(errors="ignore")

    if path.suffix.lower() in {".htm", ".html"}:
        text = _strip_html_to_text(raw)
    else:
        text = raw

    # Extract key sections commonly used by investors
    sections: List[str] = []
    for start, ends, header in [
        ("ITEM 1. BUSINESS", ["ITEM 1A.", "ITEM 1B."], "BUSINESS OVERVIEW"),
        ("ITEM 1A. RISK FACTORS", ["ITEM 1B.", "ITEM 2."], "RISK FACTORS"),
        ("ITEM 7. MANAGEMENT'S DISCUSSION", ["ITEM 7A.", "ITEM 8."], "MANAGEMENT DISCUSSION & ANALYSIS"),
        ("MANAGEMENT'S DISCUSSION AND ANALYSIS", ["ITEM 7A.", "ITEM 8."], "MANAGEMENT DISCUSSION & ANALYSIS"),
        ("ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK", ["ITEM 8."], "MARKET RISK"),
        ("ITEM 8. FINANCIAL STATEMENTS", ["ITEM 9.", "ITEM 9A."], "FINANCIAL STATEMENTS"),
    ]:
        section = _extract_section(text, start, ends)
        if section:
            sections.append(f"{header}\n{section}")

    combined = "\n\n".join(sections) if sections else text
    if limit is None:
        return combined
    return combined[:limit]


def _extract_latest_numeric(line_item: Dict[str, Any]) -> Optional[float]:
    """Return the most recent numeric value from a line item dictionary."""
    if not isinstance(line_item, dict):
        return None
    try:
        sorted_entries = sorted(line_item.items(), key=lambda itm: str(itm[0]), reverse=True)
    except Exception:
        sorted_entries = line_item.items()
    for _, value in sorted_entries:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            nested = _extract_latest_numeric(value)
            if nested is not None:
                return nested
    return None


def _format_dollar(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def _build_financial_snapshot(statements: Optional[Dict[str, Any]]) -> str:
    """Create a concise financial snapshot from cached statements."""
    if not statements or not isinstance(statements, dict):
        return ""

    data = statements.get("statements") or {}

    income_statement = data.get("income_statement", {})
    balance_sheet = data.get("balance_sheet", {})
    cash_flow = data.get("cash_flow", {})

    revenue = _extract_latest_numeric(income_statement.get("totalRevenue") or income_statement.get("Revenue"))
    operating_income = _extract_latest_numeric(income_statement.get("OperatingIncomeLoss") or income_statement.get("OperatingIncome"))
    net_income = _extract_latest_numeric(income_statement.get("NetIncomeLoss") or income_statement.get("NetIncome"))
    eps = _extract_latest_numeric(income_statement.get("DilutedEPS"))

    total_assets = _extract_latest_numeric(balance_sheet.get("TotalAssets"))
    total_liabilities = _extract_latest_numeric(balance_sheet.get("TotalLiabilities"))
    cash = _extract_latest_numeric(balance_sheet.get("CashAndCashEquivalentsAtCarryingValue") or balance_sheet.get("CashAndCashEquivalents"))

    operating_cash_flow = _extract_latest_numeric(cash_flow.get("NetCashProvidedByUsedInOperatingActivities"))
    capex = _extract_latest_numeric(cash_flow.get("PaymentsToAcquirePropertyPlantAndEquipment"))
    free_cash_flow = (
        operating_cash_flow - capex if operating_cash_flow is not None and capex is not None else None
    )

    snapshot_lines: List[str] = []
    for label, value in [
        ("Revenue", _format_dollar(revenue)),
        ("Operating Income", _format_dollar(operating_income)),
        ("Net Income", _format_dollar(net_income)),
        ("Diluted EPS", f"${eps:.2f}" if eps is not None else None),
        ("Operating Cash Flow", _format_dollar(operating_cash_flow)),
        ("Capital Expenditures", _format_dollar(capex)),
        ("Free Cash Flow", _format_dollar(free_cash_flow)),
        ("Total Assets", _format_dollar(total_assets)),
        ("Total Liabilities", _format_dollar(total_liabilities)),
        ("Cash & Equivalents", _format_dollar(cash)),
    ]:
        if value:
            snapshot_lines.append(f"- {label}: {value}")

    return "\n".join(snapshot_lines)


def _build_calculated_metrics(statements: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Derive key metrics from financial statements for AI guidance."""
    if not statements or not isinstance(statements, dict):
        return {}

    data = statements.get("statements") or {}

    income_statement = data.get("income_statement", {})
    balance_sheet = data.get("balance_sheet", {})
    cash_flow = data.get("cash_flow", {})

    revenue = _extract_latest_numeric(income_statement.get("totalRevenue") or income_statement.get("Revenue"))
    net_income = _extract_latest_numeric(income_statement.get("NetIncomeLoss") or income_statement.get("NetIncome"))
    operating_income = _extract_latest_numeric(income_statement.get("OperatingIncomeLoss") or income_statement.get("OperatingIncome"))
    eps = _extract_latest_numeric(income_statement.get("DilutedEPS"))

    operating_cash_flow = _extract_latest_numeric(cash_flow.get("NetCashProvidedByUsedInOperatingActivities"))
    capex_raw = _extract_latest_numeric(cash_flow.get("PaymentsToAcquirePropertyPlantAndEquipment"))
    capex = abs(capex_raw) if capex_raw is not None else None
    free_cash_flow = (
        operating_cash_flow - capex if operating_cash_flow is not None and capex is not None else None
    )

    cash = _extract_latest_numeric(balance_sheet.get("CashAndCashEquivalentsAtCarryingValue") or balance_sheet.get("CashAndCashEquivalents"))
    marketable_securities = _extract_latest_numeric(balance_sheet.get("MarketableSecurities"))
    total_assets = _extract_latest_numeric(balance_sheet.get("TotalAssets"))
    total_liabilities = _extract_latest_numeric(balance_sheet.get("TotalLiabilities"))

    operating_margin = (
        (operating_income / revenue) * 100 if operating_income is not None and revenue else None
    )
    net_margin = (
        (net_income / revenue) * 100 if net_income is not None and revenue else None
    )

    metrics = {
        "revenue": revenue,
        "operating_income": operating_income,
        "net_income": net_income,
        "diluted_eps": eps,
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditures": capex,
        "free_cash_flow": free_cash_flow,
        "cash": cash,
        "marketable_securities": marketable_securities,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
    }

    return {key: value for key, value in metrics.items() if value is not None}


def _format_metric_value(key: str, value: float) -> str:
    if key == "diluted_eps":
        return f"${value:.2f}"
    if key in {"operating_margin", "net_margin"}:
        return f"{value:.1f}%"
    return _format_dollar(value) or f"{value:,.2f}"


def _prepare_filing_response(raw_filing: Dict[str, Any], settings) -> Filing:
    filing_data = {
        key: value
        for key, value in raw_filing.items()
        if key not in {"local_document_path", "source_doc_url"}
    }
    filing_id = str(filing_data.get("id"))
    if filing_id:
        filing_data["url"] = _build_document_path(filing_id, settings)
    return Filing(**filing_data)


def _resolve_filing_context(filing_id: str, settings) -> Dict[str, Any]:
    filing_key = str(filing_id)

    if not _supabase_configured(settings):
        filing = fallback_filings_by_id.get(filing_key)
        if not filing:
            raise HTTPException(status_code=404, detail="Filing not found")

        company_id = str(filing.get("company_id"))
        company = fallback_companies.get(company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found for filing")

        return {
            "filing": filing,
            "company": company,
            "source": "fallback",
        }

    supabase = get_supabase_client()

    filing_response = supabase.table("filings").select("*").eq("id", filing_key).execute()
    if not filing_response.data:
        raise HTTPException(status_code=404, detail="Filing not found")

    filing = filing_response.data[0]
    company_id = filing.get("company_id")

    company_response = (
        supabase.table("companies")
        .select("id, ticker, exchange, cik")
        .eq("id", company_id)
        .execute()
    )
    if not company_response.data:
        raise HTTPException(status_code=404, detail="Company not found for filing")

    company = company_response.data[0]

    return {
        "filing": filing,
        "company": company,
        "source": "supabase",
    }


def _fetch_eodhd_document(ticker: str, exchange: Optional[str] = None, filter_param: Optional[str] = None) -> Dict[str, Any]:
    client = get_eodhd_client()
    exchange_code = (exchange or "US") or "US"
    return client.get_fundamentals(ticker, exchange=exchange_code, filter_param=filter_param)


def _ensure_storage_dir(settings) -> Path:
    storage_dir = Path(settings.data_dir).expanduser().resolve() / "filings"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _build_local_document_path(storage_dir: Path, filing_id: str) -> Path:
    return storage_dir / f"{filing_id}.html"


def _ensure_local_document(context: Dict[str, Any], settings) -> Optional[Path]:
    filing = context["filing"]
    company = context["company"]
    storage_dir = _ensure_storage_dir(settings)

    existing_path = filing.get("local_document_path")
    if existing_path:
        path_obj = Path(existing_path)
        if path_obj.exists():
            return path_obj

    filing_id = filing.get("id")
    filing_id_str = str(filing_id)
    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")

    source_doc_url = filing.get("source_doc_url")

    if not source_doc_url:
        cik_value = company.get("cik") if company else None
        if cik_value and filing_type and filing_date:
            try:
                sec_filings = get_company_filings(
                    cik=cik_value,
                    filing_types=[filing_type],
                    max_results=200,
                )
                for candidate in sec_filings:
                    if candidate.get("filing_type") != filing_type:
                        continue

                    if candidate.get("filing_date") == filing_date or candidate.get("period_end") == filing_date:
                        source_doc_url = candidate.get("url")
                        filing["source_doc_url"] = source_doc_url
                        break
            except Exception as sec_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to resolve SEC document for filing %s: %s",
                    filing_id_str,
                    sec_exc,
                )

    if not source_doc_url:
        return None

    target_path = _build_local_document_path(storage_dir, filing_id_str)

    try:
        if download_filing(source_doc_url, str(target_path)):
            filing["local_document_path"] = str(target_path)
            return target_path
    except Exception as download_exc:  # noqa: BLE001
        logger.warning(
            "Failed to download SEC filing %s: %s",
            source_doc_url,
            download_exc,
        )

    return None


@router.post("/fetch", response_model=FilingsFetchResponse)
async def fetch_filings(request: FilingsFetchRequest):
    """
    Initiate background task to fetch filings for a company from SEC EDGAR.
    Returns a task ID for tracking progress.
    """
    settings = get_settings()

    if not _supabase_configured(settings):
        company_key = str(request.company_id)
        company = fallback_companies.get(company_key)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        ticker = company.get("ticker")
        if not ticker:
            raise HTTPException(status_code=400, detail="Company is missing a ticker symbol")

        entries_to_ingest: List[Dict[str, Any]] = []

        try:
            financial_data = get_eodhd_client().get_financial_statements(ticker, exchange="US")
            eodhd_url = f"https://eodhd.com/api/fundamentals/{ticker}.US"

            quarterly_income = financial_data.get("income_statement", {}).get("quarterly", {})
            for date_str, statement in quarterly_income.items():
                entries_to_ingest.append(
                    {
                        "filing_type": "10-Q",
                        "date_str": date_str,
                        "income_statement": statement,
                        "balance_sheet": financial_data.get("balance_sheet", {}).get("quarterly", {}).get(date_str, {}),
                        "cash_flow": financial_data.get("cash_flow", {}).get("quarterly", {}).get(date_str, {}),
                        "url": eodhd_url,
                    }
                )

            yearly_income = financial_data.get("income_statement", {}).get("yearly", {})
            for date_str, statement in yearly_income.items():
                entries_to_ingest.append(
                    {
                        "filing_type": "10-K",
                        "date_str": date_str,
                        "income_statement": statement,
                        "balance_sheet": financial_data.get("balance_sheet", {}).get("yearly", {}).get(date_str, {}),
                        "cash_flow": financial_data.get("cash_flow", {}).get("yearly", {}).get(date_str, {}),
                        "url": eodhd_url,
                    }
                )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (EODHDAccessError, EODHDClientError) as exc:
            logger.warning(
                "EODHD data unavailable for %s: %s. Set EODHD_API_KEY to a paid token to enable live fundamentals.",
                ticker,
                exc,
            )
            entries_to_ingest = _sample_entries_for_ticker(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected failure while fetching EODHD data for %s", ticker)
            entries_to_ingest = _sample_entries_for_ticker(ticker)

        if not entries_to_ingest:
            logger.warning("No sample filings available for %s; continuing with empty dataset.", ticker)

        cutoff_date = None
        if request.max_history_years:
            cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=365 * request.max_history_years)

        company_filings = fallback_filings.setdefault(company_key, [])
        existing_pairs = {(filing["filing_type"], filing["filing_date"]) for filing in company_filings}
        saved_count = 0

        for existing in company_filings:
            fallback_filings_by_id.setdefault(str(existing["id"]), existing)

        storage_dir = _ensure_storage_dir(settings)
        sec_filings_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        cik_value = company.get("cik")
        ticker_symbol = company.get("ticker")

        if (not cik_value or not str(cik_value).isdigit()) and ticker_symbol:
            try:
                general_info = get_eodhd_client().get_company_info(ticker_symbol, exchange=company.get("exchange") or "US")
                candidate_cik = general_info.get("CIK") or general_info.get("cik")
                if candidate_cik:
                    cik_value = str(candidate_cik)
                    company["cik"] = cik_value
                    fallback_companies[company_key]["cik"] = cik_value
            except Exception:
                pass

        if (not cik_value or not str(cik_value).isdigit()) and ticker_symbol:
            try:
                matches = await search_company_by_ticker_or_cik(ticker_symbol)
                if matches:
                    candidate_cik = matches[0].get("cik")
                    if candidate_cik:
                        cik_value = str(candidate_cik)
                        company["cik"] = cik_value
                        fallback_companies[company_key]["cik"] = cik_value
            except Exception as cik_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to resolve CIK for company %s: %s",
                    company_key,
                    cik_exc,
                )

        if cik_value:
            cik_value = str(cik_value)
            cik_digits = ''.join(ch for ch in cik_value if ch.isdigit())
            cik_value = cik_digits.zfill(10) if cik_digits else None

        if cik_value:
            try:
                sec_filings = get_company_filings(
                    cik=cik_value,
                    filing_types=request.filing_types or ["10-K", "10-Q"],
                    max_results=200,
                )
                for entry in sec_filings:
                    filing_type_value = entry.get("filing_type")
                    filing_date_value = entry.get("filing_date")
                    period_end_value = entry.get("period_end")

                    if filing_type_value and filing_date_value:
                        sec_filings_map[(filing_type_value, filing_date_value, "filing_date")] = entry
                    if filing_type_value and period_end_value:
                        sec_filings_map[(filing_type_value, period_end_value, "period_end")] = entry
            except Exception as sec_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to retrieve SEC filings for CIK %s: %s",
                    cik_value,
                    sec_exc,
                )
        else:
            logger.warning("CIK not available for company %s; SEC document download skipped", company_key)

        if not entries_to_ingest and sec_filings_map:
            unique_entries: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for entry in sec_filings_map.values():
                filing_type_value = entry.get("filing_type")
                reference_date = entry.get("filing_date") or entry.get("period_end")
                if not filing_type_value or not reference_date:
                    continue
                key = (filing_type_value, reference_date)
                unique_entries.setdefault(key, entry)

            sorted_entries = sorted(
                unique_entries.values(),
                key=lambda item: item.get("filing_date") or item.get("period_end") or "",
                reverse=True,
            )

            max_entries = 8
            if request.max_history_years:
                max_entries = max(2, request.max_history_years * 2)

            for entry in sorted_entries[:max_entries]:
                entries_to_ingest.append(
                    {
                        "filing_type": entry.get("filing_type"),
                        "date_str": entry.get("filing_date") or entry.get("period_end"),
                        "income_statement": {},
                        "balance_sheet": {},
                        "cash_flow": {},
                        "url": entry.get("url"),
                    }
                )

        def _maybe_add_filing(
            filing_type: str,
            date_str: str,
            income_statement: dict,
            balance_sheet: dict,
            cash_flow: dict,
            source_url: str,
        ) -> None:
            nonlocal saved_count, existing_pairs, company_filings

            if request.filing_types and filing_type not in request.filing_types:
                return

            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return

            if cutoff_date and filing_date < cutoff_date:
                return

            key = (filing_type, filing_date)
            if key in existing_pairs:
                return

            filing_id = uuid4()
            filing_id_str = str(filing_id)
            now = datetime.now(timezone.utc)

            filing_record = {
                "id": filing_id,
                "company_id": request.company_id,
                "filing_type": filing_type,
                "filing_date": filing_date,
                "period_end": filing_date,
                "url": source_url,
                "pages": None,
                "raw_file_path": f"eodhd_{ticker}_{filing_type.replace('-', '')}_{date_str}",
                "parsed_json_path": None,
                "status": "parsed",
                "error_message": None,
                "created_at": now,
                "updated_at": now,
            }

            sec_match = None
            if sec_filings_map:
                sec_match = sec_filings_map.get((filing_type, date_str, "filing_date"))
                if not sec_match:
                    sec_match = sec_filings_map.get((filing_type, date_str, "period_end"))
            local_document_path = None
            source_doc_url = None

            if sec_match:
                source_doc_url = sec_match.get("url")
                if source_doc_url:
                    target_path = _build_local_document_path(storage_dir, filing_id_str)
                    try:
                        if download_filing(source_doc_url, str(target_path)):
                            local_document_path = str(target_path)
                    except Exception as download_exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to download SEC filing %s: %s",
                            source_doc_url,
                            download_exc,
                        )

            if source_doc_url:
                filing_record["source_doc_url"] = source_doc_url
            if local_document_path:
                filing_record["local_document_path"] = local_document_path

            company_filings.append(filing_record)
            existing_pairs.add(key)
            saved_count += 1

            fallback_filings_by_id[str(filing_id)] = filing_record

            fallback_financial_statements[str(filing_id)] = {
                "filing_id": filing_id,
                "period_start": filing_date,
                "period_end": filing_date,
                "currency": "USD",
                "statements": {
                    "income_statement": income_statement,
                    "balance_sheet": balance_sheet,
                    "cash_flow": cash_flow,
                },
                "created_at": now,
                "updated_at": now,
            }

        for entry in entries_to_ingest:
            _maybe_add_filing(
                entry["filing_type"],
                entry["date_str"],
                entry.get("income_statement", {}),
                entry.get("balance_sheet", {}),
                entry.get("cash_flow", {}),
                entry.get("url", "https://www.sec.gov"),
            )

        company_filings.sort(key=lambda filing: filing["filing_date"], reverse=True)

        task_id = f"local-{uuid4()}"
        return FilingsFetchResponse(
            task_id=task_id,
            message=(
                f"Fetched {saved_count} filings for {company.get('name', ticker)}"
                if saved_count
                else "No new filings were fetched"
            ),
        )

    supabase = get_supabase_client()
    
    # Verify company exists
    try:
        company_response = supabase.table("companies").select("*").eq("id", str(request.company_id)).execute()
        if not company_response.data:
            raise HTTPException(status_code=404, detail="Company not found")
        
        company = company_response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verifying company: {str(e)}")
    
    # Create task
    try:
        task = fetch_filings_task.delay(
            company_id=str(request.company_id),
            ticker=company["ticker"],
            cik=company.get("cik"),
            filing_types=request.filing_types,
            max_history_years=request.max_history_years
        )
        
        # Store task status
        task_data = {
            "task_id": task.id,
            "task_type": "fetch_filings",
            "status": "pending",
            "progress": 0
        }
        supabase.table("task_status").insert(task_data).execute()
        
        return FilingsFetchResponse(
            task_id=task.id,
            message=f"Started fetching filings for {company['name']}"
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting fetch task: {str(e)}")


@router.get("/{filing_id}/document")
async def get_filing_document(filing_id: str, raw: bool = False):
    """Serve a reader-friendly view of the filing or raw content when requested."""
    settings = get_settings()
    context = _resolve_filing_context(filing_id, settings)
    filing = context["filing"]
    company = context["company"]

    ticker = company.get("ticker")
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker not available for filing")

    exchange = company.get("exchange") or "US"
    filing_type = (filing.get("filing_type") or "").upper()
    filing_date = filing.get("filing_date")

    local_document = _ensure_local_document(context, settings)
    local_exists = bool(local_document and local_document.exists())
    source_doc_url = filing.get("source_doc_url")

    if not raw and local_exists:
        return RedirectResponse(url=f"/api/{settings.api_version}/filings/{filing_id}/document?raw=1")

    if local_document and local_document.exists():
        suffix = local_document.suffix.lower()
        if suffix == ".pdf":
            media_type = "application/pdf"
        elif suffix in {".txt", ".text"}:
            media_type = "text/plain"
        else:
            media_type = "text/html"

        return FileResponse(
            path=local_document,
            media_type=media_type,
            headers={"Content-Disposition": "inline"},
        )

    if source_doc_url:
        return RedirectResponse(url=source_doc_url)

    try:
        fundamentals = _fetch_eodhd_document(ticker, exchange=exchange)
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "ticker": ticker,
                    "exchange": exchange,
                    "source": "eodhd",
                    "filing_type": filing_type,
                    "filing_date": filing_date,
                    "data": fundamentals,
                }
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to retrieve EODHD fundamentals for ticker %s (filing %s)",
            ticker,
            filing_id,
            exc_info=exc,
        )
        fallback_statement = fallback_financial_statements.get(str(filing_id))
        if fallback_statement:
            return JSONResponse(
                content=jsonable_encoder(
                    {
                        "ticker": ticker,
                        "exchange": exchange,
                        "source": "cache",
                        "filing_type": filing_type,
                        "filing_date": filing_date,
                        "data": fallback_statement,
                    }
                )
            )
        if context["source"] == "supabase":
            try:
                supabase = get_supabase_client()
                statement_response = (
                    supabase.table("financial_statements")
                    .select("*")
                    .eq("filing_id", filing.get("id"))
                    .execute()
                )
                if statement_response.data:
                    return JSONResponse(
                        content=jsonable_encoder(
                            {
                                "ticker": ticker,
                                "exchange": exchange,
                                "source": "supabase",
                                "filing_type": filing_type,
                                "filing_date": filing_date,
                                "data": statement_response.data,
                            }
                        )
                    )
            except Exception as supabase_error:  # noqa: BLE001
                logger.exception(
                    "Failed to retrieve financial statements from Supabase for filing %s",
                    filing_id,
                    exc_info=supabase_error,
                )
        raise HTTPException(status_code=502, detail="Unable to retrieve filing document from provider")


@router.get("/{filing_id}", response_model=Filing)
async def get_filing(filing_id: str):
    """Get filing details by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filing = fallback_filings_by_id.get(filing_id) or fallback_filings_by_id.get(str(filing_id))
        if not filing:
            raise HTTPException(status_code=404, detail="Filing not found")
        return _prepare_filing_response(filing, settings)

    supabase = get_supabase_client()
    
    try:
        response = supabase.table("filings").select("*").eq("id", filing_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Filing not found")
        return _prepare_filing_response(response.data[0], settings)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving filing: {str(e)}")


@router.get("/company/{company_id}", response_model=List[Filing])
async def list_company_filings(
    company_id: str,
    filing_type: str = None,
    limit: int = 50,
    offset: int = 0
):
    """List filings for a specific company."""
    settings = get_settings()

    if not _supabase_configured(settings):
        filings = fallback_filings.get(company_id, [])
        if filing_type:
            filings = [filing for filing in filings if filing["filing_type"] == filing_type]
        sliced = filings[offset:offset + limit]
        return [_prepare_filing_response(filing, settings) for filing in sliced]

    supabase = get_supabase_client()
    
    try:
        query = supabase.table("filings").select("*").eq("company_id", company_id)
        
        if filing_type:
            query = query.eq("filing_type", filing_type)
        
        response = query.order("filing_date", desc=True).range(offset, offset + limit - 1).execute()
        
        return [_prepare_filing_response(filing, settings) for filing in response.data]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing filings: {str(e)}")


@router.post("/{filing_id}/summary")
async def generate_filing_summary(
    filing_id: str,
    preferences: Optional[FilingSummaryPreferences] = Body(default=None),
):
    """
    Generate AI summary of a filing using Gemini.
    Returns cached summary if already generated.
    """
    settings = get_settings()
    preferences = preferences or FilingSummaryPreferences()
    target_length = _clamp_target_length(preferences.target_length)
    use_default_cache = preferences.mode == "default"

    # Check cache first
    if use_default_cache:
        cached_summary = fallback_filing_summaries.get(str(filing_id))
        if cached_summary:
            return JSONResponse(content={"filing_id": filing_id, "summary": cached_summary, "cached": True})
    
    # Get filing context
    try:
        context = _resolve_filing_context(filing_id, settings)
        filing = context["filing"]
        company = context["company"]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error resolving filing: {exc}")
    
    # Get document content
    local_document = _ensure_local_document(context, settings)
    statements = fallback_financial_statements.get(str(filing_id))

    document_text = None
    if local_document and local_document.exists():
        try:
            document_text = _load_document_excerpt(local_document)
        except Exception as read_exc:
            logger.warning(f"Failed to process local document for summary: {read_exc}")

    if not document_text:
        # Fallback to financial statements
        if statements:
            try:
                safe_statements = jsonable_encoder(statements)
                document_text = json.dumps(safe_statements, indent=2)
            except (TypeError, ValueError) as serialization_error:
                logger.warning(
                    "Failed to serialize financial statements for filing %s: %s",
                    filing_id,
                    serialization_error,
                )
                document_text = json.dumps(jsonable_encoder({"statements": statements}), indent=2)
        else:
            raise HTTPException(status_code=400, detail="No document content available for summarization")
    
    logger.debug(
        "Generating summary for filing %s (%s) using document=%s statements=%s",
        filing_id,
        filing.get("filing_type"),
        bool(local_document),
        bool(statements),
    )

    # Generate summary with Gemini
    try:
        if not settings.gemini_api_key or settings.gemini_api_key.strip() == "":
            raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")
        
        gemini_client = get_gemini_client()
        
        filing_type = filing.get("filing_type", "")
        filing_date = filing.get("filing_date", "")
        company_name = company.get("name", company.get("ticker", "Unknown"))
        
        financial_snapshot = _build_financial_snapshot(statements)
        calculated_metrics = _build_calculated_metrics(statements)
        metrics_lines = "\n".join(
            f"- {label}: {_format_metric_value(key, calculated_metrics[key])}"
            for key, label in [
                ("revenue", "Revenue"),
                ("operating_income", "Operating Income"),
                ("net_income", "Net Income"),
                ("diluted_eps", "Diluted EPS"),
                ("operating_cash_flow", "Operating Cash Flow"),
                ("capital_expenditures", "Capital Expenditures"),
                ("free_cash_flow", "Free Cash Flow"),
                ("cash", "Cash"),
                ("marketable_securities", "Marketable Securities"),
                ("total_assets", "Total Assets"),
                ("total_liabilities", "Total Liabilities"),
                ("operating_margin", "Operating Margin"),
                ("net_margin", "Net Margin"),
            ]
            if key in calculated_metrics
        ) or "- No structured metrics extracted; rely on filing text."
        total_liquidity = None
        if "cash" in calculated_metrics or "marketable_securities" in calculated_metrics:
            cash_val = calculated_metrics.get("cash") or 0
            securities_val = calculated_metrics.get("marketable_securities") or 0
            total_liquidity = cash_val + securities_val
            formatted_liquidity = _format_dollar(total_liquidity) or f"${total_liquidity:,.2f}"
            metrics_lines += f"\n- Liquidity (Cash + Marketable Securities): {formatted_liquidity}"
        context_excerpt = (
            document_text
            if len(document_text) <= MAX_GEMINI_CONTEXT_CHARS
            else document_text[:MAX_GEMINI_CONTEXT_CHARS]
        )
        truncated_note = "" if len(context_excerpt) == len(document_text) else "\n\nNote: Filing text truncated to fit model context."
        company_label = company.get("name") or company.get("ticker") or "the company"
        preference_block = _build_preference_instructions(preferences, company_label)
 
        prompt = f"""You are an expert equity research analyst preparing a memo based on an SEC {filing_type} for {company_name}.
 
 COMPANY CONTEXT
 - Company: {company_name}
 - Ticker: {company.get("ticker")}
 - Filing type: {filing_type}
 - Filed on: {filing_date}
 - Period end: {filing.get("period_end")}

 KEY FINANCIAL SNAPSHOT (reported amounts)
 {financial_snapshot if financial_snapshot else "- Not available; derive figures directly from the filing text."}

 CALCULATED METRICS (use these values in your analysis; do not mark them as unknown)
 {metrics_lines}

 FILING EXCERPTS (cleaned)
 {context_excerpt}{truncated_note}
 
 CUSTOM INVESTOR PREFERENCES
 {preference_block}
 
 Write a highly detailed summary that covers the following sections:
 1. Executive Summary – 2 short paragraphs addressing company-specific highlights, growth drivers, profitability, and cash generation.
 2. Financial Performance – narrative explanation using actual figures (revenue, margins, EPS, balance sheet strength, cash flow) from the metrics above or the filing text. Compute missing numbers (e.g., free cash flow) when inputs are supplied.
 3. Management Discussion & Analysis – detailed rundown of strategy, competitive position, management priorities, and MD&A highlights. If the excerpt lacks direct quotes, infer management’s likely emphasis based on the provided metrics and historic initiatives; do not claim the information is missing.
 4. Risk Factors – bullet list of the top 5 company-specific risks mentioned in the filing.
 5. Strategic Initiatives & Capital Allocation – paragraph-style discussion of investments, acquisitions, buybacks, and R&D priorities.
 6. Key Metrics Dashboard – bullet list that enumerates every metric from the calculated metrics block with its corresponding value; if a metric is not present in the block, explain why it's unavailable.
 
 Rules:
 - Use the provided numbers instead of responding "not disclosed" whenever they are present in the calculated metrics block or filing excerpt.
 - Keep sections 1, 2, 3, and 5 in narrative paragraph form tailored to this company; avoid bullet lists in those sections.
 - Do not hallucinate figures that are not in the source content; if genuinely missing, explain the gap in plain language.
 - Every metric listed in the calculated metrics block must be incorporated into the narrative or key metrics section with its value.
 - When free cash flow is not explicitly reported, derive it as operating cash flow minus capital expenditures (use the magnitude of capex even if presented as a negative number) and include the computed value.
 - Never respond that management commentary or guidance is unavailable; synthesize a viewpoint from the data if necessary and label it clearly as analysis.
 - Maintain professional markdown formatting with clear headings."""

        summary_text = _generate_summary_with_quality_control(
            gemini_client,
            prompt,
            target_length,
            quality_validators=[_validate_mdna_section],
        )
        summary_text = _finalize_summary_length(summary_text, target_length)
        
        # Cache only for default runs so user-specific prompts aren't reused globally
        if use_default_cache:
            fallback_filing_summaries[str(filing_id)] = summary_text
        
        return JSONResponse(content={
            "filing_id": filing_id,
            "summary": summary_text,
            "cached": False
        })
        
    except Exception as gemini_exc:
        logger.exception(f"Gemini summarization error for filing {filing_id}: {gemini_exc}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {gemini_exc}")


@router.post("/{filing_id}/parse")
async def parse_filing(filing_id: str):
    """
    Initiate background task to parse a filing.
    Returns a task ID for tracking progress.
    """
    from app.tasks.parse import parse_document_task
    
    settings = get_settings()

    if not _supabase_configured(settings):
        raise HTTPException(status_code=404, detail="Filings not available without Supabase configuration")

    supabase = get_supabase_client()
    
    # Verify filing exists
    try:
        filing_response = supabase.table("filings").select("*").eq("id", filing_id).execute()
        if not filing_response.data:
            raise HTTPException(status_code=404, detail="Filing not found")
        
        filing = filing_response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verifying filing: {str(e)}")
    
    # Create task
    try:
        task = parse_document_task.delay(filing_id=filing_id)
        
        # Store task status
        task_data = {
            "task_id": task.id,
            "task_type": "parse_document",
            "status": "pending",
            "progress": 0
        }
        supabase.table("task_status").insert(task_data).execute()
        
        return {
            "task_id": task.id,
            "message": f"Started parsing filing {filing_id}"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting parse task: {str(e)}")
