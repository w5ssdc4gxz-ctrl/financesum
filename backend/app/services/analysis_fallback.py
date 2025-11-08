"""Fallback analysis service used when Supabase is unavailable."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException

from app.models.schemas import (
    Analysis,
    AnalysisRunRequest,
    AnalysisRunResponse,
    TaskStatus,
)
from app.services.eodhd_client import normalize_eodhd_to_internal_format
from app.services.health_scorer import calculate_health_score
from app.services.local_cache import (
    fallback_analyses,
    fallback_analysis_by_id,
    fallback_companies,
    fallback_filings,
    fallback_financial_statements,
    fallback_task_status,
)
from app.services.ratio_calculator import calculate_ratios
from app.services.sample_data import sample_filings_by_ticker
from app.services.gemini_client import get_gemini_client
from app.services.persona_engine import get_persona_engine


def run_analysis(request: AnalysisRunRequest) -> AnalysisRunResponse:
    """Execute a synchronous analysis using cached filings."""
    try:
        company_id = str(request.company_id)
        company = fallback_companies.get(company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not available in local cache. Fetch filings first.")

        filing_ids = _resolve_filing_ids(company_id, request.filing_ids)

        financial_statements = _load_financial_statements(filing_ids)
        if not financial_statements:
            ticker = (company.get("ticker") or "").upper()
            sample_entries = sample_filings_by_ticker.get(ticker)
            if not sample_entries:
                raise HTTPException(status_code=400, detail="No financial statements found for analysis")

            financial_statements = []
            for filing_id, entry in zip(filing_ids, sample_entries):
                statement_record = {
                    "filing_id": filing_id,
                    "period_start": entry.get("filing_date"),
                    "period_end": entry.get("filing_date"),
                    "currency": "USD",
                    "statements": {
                        "income_statement": entry.get("income_statement", {}),
                        "balance_sheet": entry.get("balance_sheet", {}),
                        "cash_flow": entry.get("cash_flow", {}),
                    },
                }
                financial_statements.append(statement_record)
                fallback_financial_statements[str(filing_id)] = statement_record

        merged_financial_data = _merge_financial_statements(financial_statements)

        if financial_statements and isinstance(financial_statements[0].get("statements"), dict):
            first_statement = financial_statements[0]["statements"]
            if "totalRevenue" in str(first_statement):
                eodhd_structure = _build_eodhd_structure(financial_statements)
                merged_financial_data = normalize_eodhd_to_internal_format(eodhd_structure)

        ratios = calculate_ratios(merged_financial_data)
        health_score_data = calculate_health_score(ratios, peer_data=None)

        now = datetime.now(timezone.utc)
        analysis_id = str(uuid4())
        analysis_record = _build_analysis_record(
            analysis_id=analysis_id,
            company_id=company_id,
            company_name=company.get("name", ""),
            filing_ids=filing_ids,
            ratios=ratios,
            health_score_data=health_score_data,
            analysis_options=request.analysis_options,
            generated_at=now,
        )

        _store_analysis(company_id, analysis_record)

        task_record = _build_task_record(
            analysis_id=analysis_id,
            company_id=company_id,
            health_score_data=health_score_data,
            generated_at=now,
        )
        fallback_task_status[task_record["task_id"]] = task_record

        return AnalysisRunResponse(
            analysis_id=analysis_id,
            task_id=task_record["task_id"],
            message="Completed analysis in local mode",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fallback analysis error: {exc}")


def get_analysis(analysis_id: str) -> Analysis:
    """Return a cached analysis."""

    record = fallback_analysis_by_id.get(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return Analysis(**record)


def list_company_analyses(company_id: str, limit: Optional[int] = None, offset: int = 0) -> List[Analysis]:
    """Return cached analyses for a company, most recent first."""

    analyses = fallback_analyses.get(company_id, [])

    start = max(offset, 0)
    end = start + limit if limit is not None else None
    sliced = analyses[start:end]

    return [Analysis(**record) for record in sliced]


def get_analysis_status(analysis_id: str) -> Dict[str, Any]:
    """Return status information for a cached analysis."""

    record = fallback_analysis_by_id.get(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return {
        "id": record["id"],
        "status": record["status"],
        "health_score": record.get("health_score"),
        "score_band": record.get("score_band"),
    }


def get_task_status(task_id: str) -> TaskStatus:
    """Return cached task status."""

    record = fallback_task_status.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatus(**record)


def _resolve_filing_ids(company_id: str, requested_ids: Optional[List]) -> List[str]:
    filings = fallback_filings.get(company_id, [])

    if requested_ids:
        resolved = [str(filing_id) for filing_id in requested_ids]
        missing = [fid for fid in resolved if fid not in fallback_financial_statements]
        if missing:
            raise HTTPException(status_code=400, detail="Selected filings are not available in local cache")
        return resolved

    parsed_filings = [
        filing for filing in filings if filing.get("status") == "parsed" and filing.get("filing_type") in {"10-K", "10-Q"}
    ]

    parsed_filings.sort(key=lambda item: item.get("filing_date"), reverse=True)
    selected = parsed_filings[:8]

    if not selected:
        raise HTTPException(status_code=400, detail="No parsed filings found for analysis")

    return [str(filing["id"]) for filing in selected]


def _load_financial_statements(filing_ids: List[str]) -> List[Dict[str, Any]]:
    statements = []
    for filing_id in filing_ids:
        statement = fallback_financial_statements.get(filing_id)
        if statement:
            statements.append(statement)
    return statements


def _merge_financial_statements(statements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {
        "income_statement": {},
        "balance_sheet": {},
        "cash_flow": {},
    }

    for statement in statements:
        stmt_data = statement.get("statements", {})
        for section in ("income_statement", "balance_sheet", "cash_flow"):
            section_data = stmt_data.get(section)
            if not isinstance(section_data, dict):
                continue
            for line_item, values in section_data.items():
                existing = merged[section].get(line_item)
                if existing is None:
                    merged[section][line_item] = values
                elif isinstance(existing, dict) and isinstance(values, dict):
                    merged[section][line_item].update(values)
                else:
                    merged[section][line_item] = values

    return merged


def _build_eodhd_structure(statements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    structure = {
        "income_statement": {"quarterly": {}},
        "balance_sheet": {"quarterly": {}},
        "cash_flow": {"quarterly": {}},
    }

    for statement in statements:
        period = statement.get("period_end") or statement.get("filing_date")
        if not period:
            continue

        statements_map = statement.get("statements", {})
        for section in ("income_statement", "balance_sheet", "cash_flow"):
            section_data = statements_map.get(section)
            if isinstance(section_data, dict):
                structure[section]["quarterly"][str(period)] = section_data

    return structure


def _build_analysis_record(
    *,
    analysis_id: str,
    company_id: str,
    company_name: str,
    filing_ids: List[str],
    ratios: Dict[str, Any],
    health_score_data: Dict[str, Any],
    analysis_options: Optional[Dict[str, Any]],
    generated_at: datetime,
) -> Dict[str, Any]:
    summary_md, persona_summaries = _build_summary(
        company_name,
        health_score_data,
        ratios,
        len(filing_ids),
        filing_ids,
    )

    record = {
        "id": analysis_id,
        "company_id": company_id,
        "filing_ids": filing_ids,
        "status": "completed",
        "analysis_date": generated_at,
        "created_at": generated_at,
        "updated_at": generated_at,
        "health_score": health_score_data.get("overall_score"),
        "score_band": health_score_data.get("score_band"),
        "ratios": ratios,
        "summary_md": summary_md,
        "investor_persona_summaries": persona_summaries,
        "provenance": {
            "filing_ids": filing_ids,
            "generated_at": generated_at.isoformat(),
            "mode": "local",
            "analysis_options": analysis_options,
        },
        "error_message": None,
    }

    return record


def _store_analysis(company_id: str, record: Dict[str, Any]) -> None:
    fallback_analysis_by_id[record["id"]] = record
    company_analyses = fallback_analyses.setdefault(company_id, [])
    company_analyses.insert(0, record)


def _build_task_record(
    *,
    analysis_id: str,
    company_id: str,
    health_score_data: Dict[str, Any],
    generated_at: datetime,
) -> Dict[str, Any]:
    task_id = f"local-task-{uuid4()}"
    record = {
        "id": str(uuid4()),
        "task_id": task_id,
        "task_type": "analyze_company",
        "status": "completed",
        "progress": 100,
        "result": {
            "analysis_id": analysis_id,
            "overall_score": health_score_data.get("overall_score"),
            "score_band": health_score_data.get("score_band"),
        },
        "error_message": None,
        "created_at": generated_at,
        "updated_at": generated_at,
        "analysis_id": analysis_id,
        "company_id": company_id,
    }
    return record


def _build_summary(
    company_name: str,
    health_score_data: Dict[str, Any],
    ratios: Dict[str, Any],
    filings_count: int,
    filing_ids: List[str],
) -> Tuple[str, Dict[str, Any]]:
    overall = health_score_data.get("overall_score")
    band = health_score_data.get("score_band")

    tldr_lines = [
        f"{company_name} earns a local health score of {overall:.1f}/100 ({band})." if overall is not None and band else f"{company_name} analysis generated in local mode.",
        f"Based on {filings_count} recent filings stored locally." if filings_count else "No filings were available; analysis may be incomplete.",
        "Results include quantitative ratios only; AI-generated narratives are omitted in local mode.",
    ]

    highlights_map = [
        ("Revenue growth YoY", ratios.get("revenue_growth_yoy"), True),
        ("Gross margin", ratios.get("gross_margin"), True),
        ("Operating margin", ratios.get("operating_margin"), True),
        ("Net margin", ratios.get("net_margin"), True),
        ("Return on equity", ratios.get("roe"), True),
        ("Current ratio", ratios.get("current_ratio"), False),
        ("Debt to equity", ratios.get("debt_to_equity"), False),
        ("Free cash flow margin", ratios.get("fcf_margin"), True),
    ]

    highlights = "\n".join(
        f"- {label}: {_format_ratio(value, as_percent)}" for label, value, as_percent in highlights_map if value is not None
    ) or "- Ratio details unavailable in local cache."

    thesis_points = [
        f"Margins trend: gross margin {_format_ratio(ratios.get('gross_margin'), True)} and net margin {_format_ratio(ratios.get('net_margin'), True)}.",
        f"Balance sheet snapshot: current ratio {_format_ratio(ratios.get('current_ratio'), False)} with debt-to-equity {_format_ratio(ratios.get('debt_to_equity'), False)}.",
        "Monitor free cash flow strength and successive filings for sustained performance.",
    ]

    risks = [
        "Local mode omits qualitative disclosures from MD&A and risk factors.",
        "Persona insights require Supabase and Gemini integration.",
        "Ratios rely on cached data that may lag actual filings.",
        "Validate assumptions against the latest SEC filings before making decisions.",
        "Market conditions and peer benchmarks are not evaluated here.",
    ]

    catalysts = [
        "Upcoming quarterly filings to refresh ratios and score.",
        "Management commentary once qualitative parsing is available.",
        "Integration with live data sources for peer benchmarking.",
    ]

    kpis = [
        "Revenue growth YoY",
        "Operating margin",
        "Net margin",
        "Free cash flow margin",
        "Debt to equity",
    ]

    gemini_summary, persona_outputs = _generate_ai_sections(
        company_name=company_name,
        ratios=ratios,
        health_score=overall,
        narrative="\n".join(thesis_points),
        filing_ids=filing_ids,
    )

    summary_md = f"""# Investment Analysis: {company_name}

## TL;DR
{gemini_summary.get("tldr") or chr(10).join(tldr_lines)}

## Investment Thesis
{gemini_summary.get("thesis") or chr(10).join(f"- {point}" for point in thesis_points)}

## Top 5 Risks
{gemini_summary.get("risks") or chr(10).join(f"- {point}" for point in risks)}

## Catalysts
{gemini_summary.get("catalysts") or chr(10).join(f"- {point}" for point in catalysts)}

## Key KPIs to Monitor
{gemini_summary.get("kpis") or chr(10).join(f"- {item}" for item in kpis)}

## Financial Highlights
{highlights}

_Generated in local fallback mode with Gemini analysis._
"""

    return summary_md, persona_outputs


def _format_ratio(value: Optional[float], as_percent: bool) -> str:
    if value is None:
        return "n/a"
    try:
        if as_percent:
            return f"{value * 100:.1f}%"
        return f"{value:.2f}"
    except Exception:
        return "n/a"


def _generate_fallback_summary(
    company_name: str,
    ratios: Dict[str, Any],
    health_score: Optional[float],
    narrative: str,
) -> Dict[str, str]:
    """Generate a basic summary when Gemini is unavailable."""
    score_text = f" with a health score of {health_score:.1f}/100" if health_score else ""
    
    return {
        "tldr": f"{company_name}{score_text} shows strong profitability margins and cash flow generation. Analysis based on recent financial statements.",
        "thesis": narrative,
        "risks": "Set GEMINI_API_KEY in .env to generate AI-powered risk analysis, thesis, and investor persona views.",
        "catalysts": "AI-generated catalysts require Gemini API key configuration.",
        "kpis": "Revenue growth, operating margin, net margin, free cash flow margin, debt to equity.",
    }


def _generate_ai_sections(
    company_name: str,
    ratios: Dict[str, Any],
    health_score: Optional[float],
    narrative: str,
    filing_ids: List[str],
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Generates AI-generated sections for the analysis."""
    ai_summary: Dict[str, str] = {}
    persona_outputs: Dict[str, Any] = {}

    try:
        from app.config import get_settings
        settings = get_settings()
        if not settings.gemini_api_key or settings.gemini_api_key.strip() == "":
            print("GEMINI_API_KEY not configured; using basic summary")
            ai_summary = _generate_fallback_summary(company_name, ratios, health_score, narrative)
        else:
            gemini_client = get_gemini_client()
            ai_summary = gemini_client.generate_company_summary(
                company_name=company_name,
                financial_data={"filings": filing_ids},
                ratios=ratios,
                health_score=health_score or 0,
                mda_text=None,
                risk_factors_text=None,
            )
    except Exception as exc:
        import traceback
        print(f"Gemini summary error: {exc}")
        traceback.print_exc()
        ai_summary = _generate_fallback_summary(company_name, ratios, health_score, narrative)

    try:
        persona_engine = get_persona_engine()
        persona_outputs = persona_engine.generate_multiple_personas(
            persona_ids=persona_engine.get_all_persona_ids(),
            company_name=company_name,
            general_summary=ai_summary.get("full_summary")
            or ai_summary.get("thesis")
            or narrative,
            ratios=ratios,
        )
    except Exception as exc:
        print(f"Persona generation error: {exc}")

    return ai_summary, persona_outputs

