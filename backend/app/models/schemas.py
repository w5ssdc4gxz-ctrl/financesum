"""Pydantic schemas for API models."""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from uuid import UUID


# Company schemas
class CompanyBase(BaseModel):
    ticker: str
    name: str
    cik: Optional[str] = None
    exchange: Optional[str] = None
    industry: Optional[str] = None
    sector: Optional[str] = None
    country: str = "US"


class CompanyCreate(CompanyBase):
    pass


class Company(CompanyBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Filing schemas
class FilingBase(BaseModel):
    company_id: UUID
    filing_type: str
    filing_date: date
    period_end: Optional[date] = None
    url: Optional[str] = None
    pages: Optional[int] = None


class FilingCreate(FilingBase):
    pass


class Filing(FilingBase):
    id: UUID
    raw_file_path: Optional[str] = None
    parsed_json_path: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Financial Statement schemas
class FinancialStatementBase(BaseModel):
    filing_id: UUID
    period_start: date
    period_end: date
    currency: str = "USD"
    statements: Dict[str, Any]


class FinancialStatementCreate(FinancialStatementBase):
    pass


class FinancialStatement(FinancialStatementBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Analysis schemas
class AnalysisBase(BaseModel):
    company_id: UUID
    filing_ids: List[UUID]


class AnalysisCreate(AnalysisBase):
    include_personas: Optional[List[str]] = None


class RatiosData(BaseModel):
    # Profitability
    revenue_growth_yoy: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roa: Optional[float] = None
    roe: Optional[float] = None
    
    # Liquidity
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    dso: Optional[float] = None
    inventory_turnover: Optional[float] = None
    
    # Leverage
    debt_to_equity: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None
    interest_coverage: Optional[float] = None
    
    # Cash Flow
    fcf: Optional[float] = None
    fcf_margin: Optional[float] = None
    
    # Distress
    altman_z_score: Optional[float] = None


class PersonaSummary(BaseModel):
    persona_id: str
    persona_name: str
    stance: str  # Buy, Hold, Sell
    summary: str
    key_points: List[str]


class Analysis(AnalysisBase):
    id: UUID
    analysis_date: datetime
    health_score: Optional[float] = None
    score_band: Optional[str] = None
    ratios: Optional[Dict[str, Any]] = None
    summary_md: Optional[str] = None
    investor_persona_summaries: Optional[Dict[str, Any]] = None
    provenance: Optional[Dict[str, Any]] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Task Status schemas
class TaskStatusBase(BaseModel):
    task_id: str
    task_type: str
    status: str
    progress: int = 0


class TaskStatusCreate(TaskStatusBase):
    pass


class TaskStatus(TaskStatusBase):
    id: UUID
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Request/Response schemas
class CompanyLookupRequest(BaseModel):
    query: str  # Can be ticker, CIK, or company name


class CompanyLookupResponse(BaseModel):
    companies: List[Company]


class FilingsFetchRequest(BaseModel):
    company_id: UUID
    filing_types: Optional[List[str]] = ["10-K", "10-Q"]
    max_history_years: int = 40


class FilingsFetchResponse(BaseModel):
    task_id: str
    message: str


class AnalysisRunRequest(BaseModel):
    company_id: UUID
    filing_ids: Optional[List[UUID]] = None
    analysis_options: Optional[Dict[str, Any]] = None


class AnalysisRunResponse(BaseModel):
    analysis_id: UUID
    task_id: str
    message: str


class HealthRatingPreferences(BaseModel):
    enabled: bool = False
    framework: Optional[str] = Field(default=None, max_length=80)
    primary_factor_weighting: Optional[str] = Field(default=None, max_length=80)
    risk_tolerance: Optional[str] = Field(default=None, max_length=80)
    analysis_depth: Optional[str] = Field(default=None, max_length=80)
    display_style: Optional[str] = Field(default=None, max_length=80)


class FilingSummaryPreferences(BaseModel):
    mode: Literal["default", "custom"] = "default"
    investor_focus: Optional[str] = Field(default=None, max_length=5000)
    focus_areas: List[str] = Field(default_factory=list)
    tone: Optional[str] = Field(default=None, max_length=50)
    detail_level: Optional[str] = Field(default=None, max_length=50)
    output_style: Optional[str] = Field(default=None, max_length=50)
    target_length: Optional[int] = Field(default=None, ge=50, le=5000)
    complexity: Literal["simple", "intermediate", "expert"] = "intermediate"
    health_rating: Optional[HealthRatingPreferences] = None


class HealthScoreBreakdown(BaseModel):
    overall_score: float
    score_band: str
    components: Dict[str, float]
    percentile_ranks: Dict[str, float]












