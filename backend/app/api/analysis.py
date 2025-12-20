"""Analysis API endpoints."""

import io
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.models.database import get_supabase_client
from app.models.schemas import (
    Analysis,
    AnalysisRunRequest,
    AnalysisRunResponse,
    TaskStatus,
)
from app.tasks.analyze import analyze_company_task
from app.config import get_settings
from app.api.companies import _supabase_configured
from app.services.summary_export import build_summary_docx, build_summary_pdf
from app.services.analysis_fallback import (
    get_analysis as fallback_get_analysis,
    get_analysis_status as fallback_get_analysis_status,
    get_task_status as fallback_get_task_status,
    list_company_analyses as fallback_list_company_analyses,
    run_analysis as fallback_run_analysis,
)
from app.services.local_cache import fallback_analysis_by_id, fallback_analyses
from app.utils.supabase_errors import is_supabase_table_missing_error

router = APIRouter()


class AnalysisExportRequest(BaseModel):
    format: Literal["pdf", "docx"] = Field(...)
    summary: str = Field(..., min_length=1, max_length=250_000)
    title: Optional[str] = Field(default=None, max_length=200)
    ticker: Optional[str] = Field(default=None, max_length=25)
    company_name: Optional[str] = Field(default=None, max_length=200)
    analysis_date: Optional[str] = Field(default=None, max_length=50)
    generated_at: Optional[str] = Field(default=None, max_length=50)
    filing_type: Optional[str] = Field(default=None, max_length=50)
    filing_date: Optional[str] = Field(default=None, max_length=50)


@router.post("/run", response_model=AnalysisRunResponse)
async def run_analysis(request: AnalysisRunRequest):
    """
    Initiate background task to run financial analysis on a company.
    Returns analysis ID and task ID for tracking progress.
    """
    settings = get_settings()

    if not _supabase_configured(settings):
        return fallback_run_analysis(request)

    supabase = get_supabase_client()
    
    # Verify company exists
    try:
        company_response = supabase.table("companies").select("*").eq("id", str(request.company_id)).execute()
        if not company_response.data:
            raise HTTPException(status_code=404, detail="Company not found")
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_run_analysis(request)
        raise HTTPException(status_code=500, detail=f"Error verifying company: {str(e)}")
    
    # Get filing IDs if not provided
    filing_ids = request.filing_ids
    if not filing_ids:
        try:
            # Get latest 10-K and 10-Q filings
            filings_response = supabase.table("filings")\
                .select("id")\
                .eq("company_id", str(request.company_id))\
                .in_("filing_type", ["10-K", "10-Q"])\
                .eq("status", "parsed")\
                .order("filing_date", desc=True)\
                .limit(8)\
                .execute()
            
            if not filings_response.data:
                raise HTTPException(status_code=400, detail="No parsed filings found for analysis")
            
            filing_ids = [f["id"] for f in filings_response.data]
        except HTTPException:
            raise
        except Exception as e:
            if is_supabase_table_missing_error(e):
                return fallback_run_analysis(request)
            raise HTTPException(status_code=500, detail=f"Error finding filings: {str(e)}")
    
    # Create analysis record
    try:
        analysis_data = {
            "company_id": str(request.company_id),
            "filing_ids": [str(fid) for fid in filing_ids],
            "status": "pending"
        }
        
        analysis_response = supabase.table("analyses").insert(analysis_data).execute()
        
        if not analysis_response.data:
            raise HTTPException(status_code=500, detail="Failed to create analysis record")
        
        analysis = analysis_response.data[0]
        analysis_id = analysis["id"]
        
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_run_analysis(request)
        raise HTTPException(status_code=500, detail=f"Error creating analysis: {str(e)}")
    
    # Start analysis task
    try:
        include_personas = None
        target_length = None
        complexity = "intermediate"
        
        if request.analysis_options:
            include_personas = request.analysis_options.get("include_personas")
            target_length = request.analysis_options.get("target_length")
            complexity = request.analysis_options.get("complexity", "intermediate")
        
        task = analyze_company_task.delay(
            analysis_id=analysis_id,
            company_id=str(request.company_id),
            filing_ids=[str(fid) for fid in filing_ids],
            include_personas=include_personas,
            target_length=target_length,
            complexity=complexity
        )
        
        # Store task status
        task_data = {
            "task_id": task.id,
            "task_type": "analyze_company",
            "status": "pending",
            "progress": 0
        }
        supabase.table("task_status").insert(task_data).execute()
        
        return AnalysisRunResponse(
            analysis_id=analysis_id,
            task_id=task.id,
            message=f"Started analysis for company"
        )
    
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_run_analysis(request)
        raise HTTPException(status_code=500, detail=f"Error starting analysis task: {str(e)}")


@router.get("/{analysis_id}", response_model=Analysis)
async def get_analysis(analysis_id: str):
    """Get analysis results by ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        return fallback_get_analysis(analysis_id)

    supabase = get_supabase_client()
    
    try:
        response = supabase.table("analyses").select("*").eq("id", analysis_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Analysis not found")
        
        return Analysis(**response.data[0])
    
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_get_analysis(analysis_id)
        raise HTTPException(status_code=500, detail=f"Error retrieving analysis: {str(e)}")


@router.get("/{analysis_id}/status")
async def get_analysis_status(analysis_id: str):
    """Get analysis status and progress."""
    settings = get_settings()

    if not _supabase_configured(settings):
        return fallback_get_analysis_status(analysis_id)

    supabase = get_supabase_client()
    
    try:
        response = supabase.table("analyses").select("id, status, health_score, score_band").eq("id", analysis_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Analysis not found")
        
        return response.data[0]
    
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_get_analysis_status(analysis_id)
        raise HTTPException(status_code=500, detail=f"Error retrieving analysis status: {str(e)}")


@router.get("/company/{company_id}", response_model=List[Analysis])
async def list_company_analyses(
    company_id: str,
    limit: int = 20,
    offset: int = 0
):
    """List analyses for a specific company."""
    settings = get_settings()

    if not _supabase_configured(settings):
        return fallback_list_company_analyses(company_id, limit=limit, offset=offset)

    supabase = get_supabase_client()
    
    try:
        response = supabase.table("analyses")\
            .select("*")\
            .eq("company_id", company_id)\
            .order("analysis_date", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()
        
        return [Analysis(**analysis) for analysis in response.data]
    
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_list_company_analyses(company_id, limit=limit, offset=offset)
        raise HTTPException(status_code=500, detail=f"Error listing analyses: {str(e)}")


@router.get("/task/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """Get task status by task ID."""
    settings = get_settings()

    if not _supabase_configured(settings):
        return fallback_get_task_status(task_id)

    supabase = get_supabase_client()
    
    try:
        response = supabase.table("task_status").select("*").eq("task_id", task_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return TaskStatus(**response.data[0])
    
    except HTTPException:
        raise
    except Exception as e:
        if is_supabase_table_missing_error(e):
            return fallback_get_task_status(task_id)
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {str(e)}")


@router.post("/{analysis_id}/export")
async def export_analysis(
    analysis_id: str,
    payload: AnalysisExportRequest = Body(...),
):
    """Export an analysis summary as a PDF or Word (DOCX) document.

    Note: we accept the markdown content from the client so exports work for
    both live analyses and locally cached dashboard snapshots.
    """
    metadata_lines: list[str] = [f"Analysis ID: {analysis_id}"]
    if payload.ticker:
        metadata_lines.append(f"Ticker: {payload.ticker}")
    if payload.company_name:
        metadata_lines.append(f"Company: {payload.company_name}")
    if payload.filing_type:
        metadata_lines.append(f"Filing Type: {payload.filing_type}")
    if payload.filing_date:
        metadata_lines.append(f"Filing Date: {payload.filing_date}")
    if payload.analysis_date:
        metadata_lines.append(f"Analysis Date: {payload.analysis_date}")
    if payload.generated_at:
        metadata_lines.append(f"Generated: {payload.generated_at}")

    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", analysis_id).strip("_")[:60] or "analysis"

    try:
        if payload.format == "pdf":
            pdf_bytes = build_summary_pdf(
                summary_md=payload.summary,
                title=payload.title or "AI Analysis",
                metadata_lines=metadata_lines,
            )
            return StreamingResponse(
                io.BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="analysis-{safe_id}.pdf"'
                },
            )

        docx_bytes = build_summary_docx(
            summary_md=payload.summary,
            title=payload.title or "AI Analysis",
            metadata_lines=metadata_lines,
        )
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="analysis-{safe_id}.docx"'
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to export analysis") from exc


@router.delete("/{analysis_id}", status_code=204)
async def delete_analysis(analysis_id: str):
    """Delete an analysis."""
    # If it's a client-side summary ID or not a UUID, just return success
    # as it doesn't exist in the backend analyses table.
    if analysis_id.startswith("summary-"):
        return None

    settings = get_settings()

    if not _supabase_configured(settings):
        if analysis_id in fallback_analysis_by_id:
            analysis = fallback_analysis_by_id.pop(analysis_id)
            company_id = analysis.get("company_id")
            if company_id and company_id in fallback_analyses:
                fallback_analyses[company_id] = [
                    a for a in fallback_analyses[company_id] if a["id"] != analysis_id
                ]
        return None

    supabase = get_supabase_client()

    try:
        supabase.table("analyses").delete().eq("id", analysis_id).execute()
    except Exception as e:
        # Ignore invalid input syntax for UUID errors, as it means the ID doesn't exist
        if "invalid input syntax for type uuid" in str(e):
            return None
            
        if is_supabase_table_missing_error(e):
            return None
        raise HTTPException(status_code=500, detail=f"Error deleting analysis: {str(e)}")


