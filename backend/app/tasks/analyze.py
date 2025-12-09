"""Celery tasks for analyzing companies."""
import json
from typing import List, Optional
from app.tasks.celery_app import celery_app
from app.models.database import get_supabase_client
from app.services.ratio_calculator import calculate_ratios
from app.services.health_scorer import calculate_health_score
from app.services.gemini_client import get_gemini_client
from app.services.persona_engine import get_persona_engine
from app.services.eodhd_client import normalize_eodhd_to_internal_format


@celery_app.task(bind=True)
def analyze_company_task(
    self,
    analysis_id: str,
    company_id: str,
    filing_ids: List[str],
    include_personas: Optional[List[str]] = None,
    target_length: Optional[int] = None,
    complexity: str = "intermediate"
):
    """
    Background task to analyze a company.
    
    Args:
        self: Celery task instance
        analysis_id: Analysis UUID
        company_id: Company UUID
        filing_ids: List of filing UUIDs to analyze
        include_personas: Optional list of persona IDs to generate
        target_length: Optional target length for the summary
        complexity: Complexity level of the summary
    """
    supabase = get_supabase_client()
    
    try:
        # Update analysis status
        supabase.table("analyses")\
            .update({"status": "processing"})\
            .eq("id", analysis_id)\
            .execute()
        
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'Loading company data...'})
        
        # Get company
        company_response = supabase.table("companies").select("*").eq("id", company_id).execute()
        if not company_response.data:
            raise ValueError("Company not found")
        
        company = company_response.data[0]
        company_name = company["name"]
        
        self.update_state(state='PROGRESS', meta={'progress': 20, 'status': 'Loading financial statements...'})
        
        # Get financial statements for the filings
        financial_statements = []
        for filing_id in filing_ids:
            stmt_response = supabase.table("financial_statements")\
                .select("*")\
                .eq("filing_id", filing_id)\
                .execute()
            
            if stmt_response.data:
                financial_statements.extend(stmt_response.data)
        
        if not financial_statements:
            raise ValueError("No financial statements found for analysis")
        
        # Merge financial data from multiple statements
        # If data is from EODHD (already structured), normalize it
        merged_financial_data = _merge_financial_statements(financial_statements)
        
        # Try to normalize from EODHD format if needed
        if financial_statements and "statements" in financial_statements[0]:
            first_statement = financial_statements[0]["statements"]
            # Check if this looks like EODHD data (has raw field names)
            if "totalRevenue" in str(first_statement):
                # Create pseudo-EODHD structure for normalization
                eodhd_structure = {
                    "income_statement": {"quarterly": {}},
                    "balance_sheet": {"quarterly": {}},
                    "cash_flow": {"quarterly": {}}
                }
                
                for stmt in financial_statements:
                    period = stmt.get("period_end", "unknown")
                    statements = stmt.get("statements", {})
                    
                    if "income_statement" in statements:
                        eodhd_structure["income_statement"]["quarterly"][period] = statements["income_statement"]
                    if "balance_sheet" in statements:
                        eodhd_structure["balance_sheet"]["quarterly"][period] = statements["balance_sheet"]
                    if "cash_flow" in statements:
                        eodhd_structure["cash_flow"]["quarterly"][period] = statements["cash_flow"]
                
                # Normalize to our internal format
                merged_financial_data = normalize_eodhd_to_internal_format(eodhd_structure)
        
        self.update_state(state='PROGRESS', meta={'progress': 30, 'status': 'Calculating ratios...'})
        
        # Calculate ratios
        ratios = calculate_ratios(merged_financial_data)
        
        self.update_state(state='PROGRESS', meta={'progress': 40, 'status': 'Calculating health score...'})
        
        # Calculate health score
        health_score_data = calculate_health_score(ratios, peer_data=None)
        
        self.update_state(state='PROGRESS', meta={'progress': 50, 'status': 'Generating AI summary...'})
        
        # Get MD&A and risk factors text if available
        mda_text = None
        risk_factors_text = None
        
        # Try to get parsed text from first filing
        if filing_ids:
            filing_response = supabase.table("filings").select("*").eq("id", filing_ids[0]).execute()
            if filing_response.data and filing_response.data[0].get("parsed_json_path"):
                try:
                    parsed_data = supabase.storage.from_("filings").download(
                        filing_response.data[0]["parsed_json_path"]
                    )
                    parsed_json = json.loads(parsed_data)
                    # Would extract MD&A and risk factors here if available
                except Exception as e:
                    print(f"Error loading parsed data: {e}")
        
        # Generate AI summary
        gemini_client = get_gemini_client()
        summary_data = gemini_client.generate_company_summary(
            company_name=company_name,
            financial_data=merged_financial_data,
            ratios=ratios,
            health_score=health_score_data["overall_score"],
            mda_text=mda_text,
            risk_factors_text=risk_factors_text,
            target_length=target_length,
            complexity=complexity
        )
        
        # Combine summary sections into markdown
        summary_md = f"""# Investment Analysis: {company_name}

## TL;DR
{summary_data.get('tldr', '')}

## Investment Thesis
{summary_data.get('thesis', '')}

## Top 5 Risks
{summary_data.get('risks', '')}

## Catalysts
{summary_data.get('catalysts', '')}

## Key KPIs to Monitor
{summary_data.get('kpis', '')}
"""
        
        self.update_state(state='PROGRESS', meta={'progress': 70, 'status': 'Generating persona views...'})
        
        # Generate persona analyses
        persona_summaries = {}
        
        if include_personas:
            persona_engine = get_persona_engine()
            
            # Build minimal context - just key facts, NOT the formatted summary
            # This prevents personas from mimicking the generic report structure
            brief_context = _build_minimal_context(
                company_name=company_name,
                ratios=ratios,
                health_score=health_score_data["overall_score"],
                mda_text=mda_text,
                risk_factors_text=risk_factors_text
            )
            
            for idx, persona_id in enumerate(include_personas):
                try:
                    persona_progress = 70 + int(20 * (idx / len(include_personas)))
                    self.update_state(
                        state='PROGRESS',
                        meta={
                            'progress': persona_progress,
                            'status': f'Generating {persona_id} view...'
                        }
                    )
                    
                    persona_analysis = persona_engine.generate_persona_analysis(
                        persona_id=persona_id,
                        company_name=company_name,
                        general_summary=brief_context,  # Pass minimal context, not formatted report
                        ratios=ratios,
                        financial_data=merged_financial_data,
                        target_length=target_length  # Pass user-specified target length
                    )
                    
                    persona_summaries[persona_id] = persona_analysis
                
                except Exception as e:
                    print(f"Error generating persona {persona_id}: {e}")
                    continue
        
        self.update_state(state='PROGRESS', meta={'progress': 90, 'status': 'Saving results...'})
        
        # Create provenance
        provenance = {
            "filing_ids": filing_ids,
            "analysis_date": None,  # Will be set by database
            "filings_used": len(filing_ids)
        }
        
        # Update analysis record
        update_data = {
            "status": "completed",
            "health_score": health_score_data["overall_score"],
            "score_band": health_score_data["score_band"],
            "ratios": ratios,
            "summary_md": summary_md,
            "investor_persona_summaries": persona_summaries,
            "provenance": provenance
        }
        
        supabase.table("analyses")\
            .update(update_data)\
            .eq("id", analysis_id)\
            .execute()
        
        # Update task status
        supabase.table("task_status")\
            .update({"status": "completed", "progress": 100})\
            .eq("task_id", self.request.id)\
            .execute()
        
        return {
            'status': 'completed',
            'message': 'Successfully completed analysis',
            'analysis_id': analysis_id,
            'health_score': health_score_data["overall_score"],
            'score_band': health_score_data["score_band"]
        }
    
    except Exception as e:
        # Update analysis status
        supabase.table("analyses")\
            .update({
                "status": "failed",
                "error_message": str(e)
            })\
            .eq("id", analysis_id)\
            .execute()
        
        # Update task status
        supabase.table("task_status")\
            .update({
                "status": "failed",
                "error_message": str(e)
            })\
            .eq("task_id", self.request.id)\
            .execute()
        
        raise


def _build_minimal_context(
    company_name: str,
    ratios: dict,
    health_score: float,
    mda_text: str = None,
    risk_factors_text: str = None
) -> str:
    """
    Build minimal context for persona analysis.
    
    This provides just the key facts without any formatted structure,
    allowing personas to interpret data through their own lens.
    """
    lines = []
    
    # Just the raw facts - no formatting, no interpretation
    lines.append(f"Company: {company_name}")
    
    # Key metrics as raw data points
    if ratios.get("revenue_growth_yoy") is not None:
        growth = ratios["revenue_growth_yoy"]
        lines.append(f"Revenue growth: {growth*100:.1f}% YoY")
    
    if ratios.get("gross_margin") is not None:
        lines.append(f"Gross margin: {ratios['gross_margin']*100:.1f}%")
    
    if ratios.get("operating_margin") is not None:
        lines.append(f"Operating margin: {ratios['operating_margin']*100:.1f}%")
    
    if ratios.get("fcf") is not None:
        fcf = ratios["fcf"]
        fcf_str = f"${fcf/1e9:.1f}B" if abs(fcf) >= 1e9 else f"${fcf/1e6:.0f}M"
        lines.append(f"Free cash flow: {fcf_str}")
    
    if ratios.get("roe") is not None:
        lines.append(f"ROE: {ratios['roe']*100:.1f}%")
    
    if ratios.get("debt_to_equity") is not None:
        lines.append(f"Debt/Equity: {ratios['debt_to_equity']:.2f}x")
    
    # Include MD&A snippet if available (unformatted)
    if mda_text:
        snippet = mda_text[:500].replace('\n', ' ').strip()
        lines.append(f"\nManagement commentary excerpt: {snippet}...")
    
    return "\n".join(lines)


def _merge_financial_statements(statements: List[dict]) -> dict:
    """
    Merge multiple financial statements into a single structure.
    
    Args:
        statements: List of financial statement records
    
    Returns:
        Merged financial data
    """
    merged = {
        "income_statement": {},
        "balance_sheet": {},
        "cash_flow": {}
    }
    
    for statement in statements:
        stmt_data = statement.get("statements", {})
        
        for statement_type in ["income_statement", "balance_sheet", "cash_flow"]:
            if statement_type in stmt_data:
                # Merge line items
                for line_item, values in stmt_data[statement_type].items():
                    if line_item not in merged[statement_type]:
                        merged[statement_type][line_item] = values
                    else:
                        # Merge values (keep both periods)
                        if isinstance(values, dict) and isinstance(merged[statement_type][line_item], dict):
                            merged[statement_type][line_item].update(values)
    
    return merged

