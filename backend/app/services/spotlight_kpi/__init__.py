"""Spotlight KPI extraction and ranking helpers."""

from .gemini_flash import extract_spotlight_kpis_via_gemini_flash
from .ranker import pick_best_spotlight_kpi
from .context import build_operational_spotlight_context
from .pdf_pipeline import extract_company_specific_spotlight_kpi_from_pdf
from .kpi_pipeline_v3 import extract_kpi_from_pdf, extract_kpi_from_file, PipelineConfig
from .kpi_pipeline_evidence import extract_kpi_with_evidence_from_file, EvidencePipelineConfig

# NOTE: The 2-pass pipeline is kept for experimentation and unit tests.
# Production Spotlight extraction uses `kpi_pipeline_evidence` (file-native), `text_pipeline`,
# and `regex_fallback` via `app.services.spotlight_kpi.service`.
from .kpi_pipeline_2pass import (
    extract_kpi_2pass,
    extract_kpi_2pass_from_text,
    Pipeline2PassConfig,
)
from .regex_fallback import extract_kpis_with_regex, extract_single_best_kpi_with_regex

__all__ = [
    "extract_spotlight_kpis_via_gemini_flash",
    "pick_best_spotlight_kpi",
    "build_operational_spotlight_context",
    "extract_company_specific_spotlight_kpi_from_pdf",
    "extract_kpi_from_pdf",
    "extract_kpi_from_file",
    "PipelineConfig",
    "extract_kpi_with_evidence_from_file",
    "EvidencePipelineConfig",
    # New 2-pass pipeline
    "extract_kpi_2pass",
    "extract_kpi_2pass_from_text",
    "Pipeline2PassConfig",
    # Regex fallback
    "extract_kpis_with_regex",
    "extract_single_best_kpi_with_regex",
]
