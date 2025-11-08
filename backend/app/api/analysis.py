"""Analysis API endpoints."""
from fastapi import APIRouter, HTTPException
from typing import List
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
from app.services.analysis_fallback import (
    get_analysis as fallback_get_analysis,
    get_analysis_status as fallback_get_analysis_status,
    get_task_status as fallback_get_task_status,
    list_company_analyses as fallback_list_company_analyses,
    run_analysis as fallback_run_analysis,
)

router = APIRouter()


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
        raise HTTPException(status_code=500, detail=f"Error creating analysis: {str(e)}")
    
    # Start analysis task
    try:
        include_personas = None
        if request.analysis_options:
            include_personas = request.analysis_options.get("include_personas")
        
        task = analyze_company_task.delay(
            analysis_id=analysis_id,
            company_id=str(request.company_id),
            filing_ids=[str(fid) for fid in filing_ids],
            include_personas=include_personas
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
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {str(e)}")



