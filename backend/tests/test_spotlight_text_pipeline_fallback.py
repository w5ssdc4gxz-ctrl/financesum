from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import local_cache
from app.services.spotlight_kpi import service as spotlight_service


class _FakeGeminiTextPipelineClient:
    """Fake Gemini client that supports the text_pipeline 4-pass calls."""

    def stream_generate_content(self, prompt: str, *, stage_name: str = "", **_kwargs):
        if "Pass 1" in stage_name:
            return (
                '{"metrics":[{"name_as_written":"Monthly Active Users (MAUs)","definition_or_context":null,'
                '"excerpt":"We ended the quarter with 250 million MAUs."}]}'
            )
        if "Pass 2" in stage_name:
            return (
                '{"kept":[{"name_as_written":"Monthly Active Users (MAUs)","reason_kept":"Core usage KPI",'
                '"excerpt":"We ended the quarter with 250 million MAUs."}],"removed":[]}'
            )
        if "Pass 3" in stage_name:
            return (
                '{"company_specific_kpi":{"kpi_name":"Monthly Active Users (MAUs)","what_it_measures":"Active users",'
                '"why_it_represents_this_company":"Usage driven model","why_not_generic":"Company reports MAUs",'
                '"scores":{"uniqueness":4,"representativeness":4,"signal_quality":4},'
                '"supporting_excerpt":"We ended the quarter with 250 million MAUs."},'
                '"fallback_if_none":{"company_specific_kpi":null,"reason":null}}'
            )
        if "Pass 4" in stage_name:
            return '{"status":"approved","reason":"Clear evidence","confidence":0.8}'
        return "{}"


class _FakeGeminiTextPipelineClientNoFallback:
    """Same as _FakeGeminiTextPipelineClient but omits fallback_if_none in Pass 3."""

    def stream_generate_content(self, prompt: str, *, stage_name: str = "", **_kwargs):
        if "Pass 1" in stage_name:
            return (
                '{"metrics":[{"name_as_written":"Monthly Active Users (MAUs)","definition_or_context":null,'
                '"excerpt":"We ended the quarter with 250 million MAUs."}]}'
            )
        if "Pass 2" in stage_name:
            return (
                '{"kept":[{"name_as_written":"Monthly Active Users (MAUs)","reason_kept":"Core usage KPI",'
                '"excerpt":"We ended the quarter with 250 million MAUs."}],"removed":[]}'
            )
        if "Pass 3" in stage_name:
            return (
                '{"company_specific_kpi":{"kpi_name":"Monthly Active Users (MAUs)","what_it_measures":"Active users",'
                '"why_it_represents_this_company":"Usage driven model","why_not_generic":"Company reports MAUs",'
                '"scores":{"uniqueness":4,"representativeness":4,"signal_quality":4},'
                '"supporting_excerpt":"We ended the quarter with 250 million MAUs."}}'
            )
        if "Pass 4" in stage_name:
            return '{"status":"approved","reason":"Clear evidence","confidence":0.8}'
        return "{}"


def test_spotlight_endpoint_uses_text_pipeline_when_file_upload_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SPOTLIGHT_FILE_PIPELINE_MAX_UPLOAD_BYTES", "0")
    get_settings.cache_clear()

    filing_id = "spotlight-text-fallback-filing"
    company_id = "spotlight-text-fallback-company"

    monkeypatch.setattr(
        spotlight_service, "get_gemini_client", lambda: _FakeGeminiTextPipelineClient()
    )

    doc_path = tmp_path / "filing.html"
    doc_path.write_text(
        "MANAGEMENT DISCUSSION & ANALYSIS\nWe ended the quarter with 250 million MAUs.\n",
        encoding="utf-8",
    )

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2026-01-01",
        "period_end": "2025-12-31",
        "local_document_path": str(doc_path),
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "FAKE",
        "name": "Fake Corp",
    }

    client = TestClient(app)
    resp = client.get(f"/api/v1/filings/{filing_id}/spotlight")

    try:
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        kpi = payload.get("company_kpi")
        assert isinstance(kpi, dict)
        assert str(kpi.get("name") or "").startswith("Monthly Active Users")
        assert float(kpi.get("value")) == 250_000_000.0
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)


def test_spotlight_text_pipeline_tolerates_missing_pass3_fallback_field(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SPOTLIGHT_FILE_PIPELINE_MAX_UPLOAD_BYTES", "0")
    get_settings.cache_clear()

    filing_id = "spotlight-text-fallback-missing-fallback"
    company_id = "spotlight-text-fallback-missing-fallback-company"

    monkeypatch.setattr(
        spotlight_service,
        "get_gemini_client",
        lambda: _FakeGeminiTextPipelineClientNoFallback(),
    )

    doc_path = tmp_path / "filing.html"
    doc_path.write_text(
        "MANAGEMENT DISCUSSION & ANALYSIS\nWe ended the quarter with 250 million MAUs.\n",
        encoding="utf-8",
    )

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2026-01-01",
        "period_end": "2025-12-31",
        "local_document_path": str(doc_path),
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "FAKE",
        "name": "Fake Corp",
    }

    client = TestClient(app)
    resp = client.get(f"/api/v1/filings/{filing_id}/spotlight")

    try:
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        kpi = payload.get("company_kpi")
        assert isinstance(kpi, dict)
        assert str(kpi.get("name") or "").startswith("Monthly Active Users")
        assert float(kpi.get("value")) == 250_000_000.0
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
