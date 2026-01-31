from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import local_cache


def test_spotlight_endpoint_returns_null_when_no_gemini_and_no_regex_kpi(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SPOTLIGHT_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()

    filing_id = "spotlight-endpoint-no-kpi"
    company_id = "spotlight-endpoint-no-kpi-company"

    doc_path = tmp_path / "filing.html"
    doc_path.write_text(
        "MANAGEMENT DISCUSSION & ANALYSIS\nThis was a solid quarter.\n",
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
        "ticker": "SPOT",
        "name": "Spotlight Test Corp",
    }

    client = TestClient(app)
    resp = client.get(f"/api/v1/filings/{filing_id}/spotlight")

    try:
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload.get("filing_id") == filing_id
        assert payload.get("company_kpi") is None
        assert payload.get("status") == "no_kpi"
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)


def test_spotlight_endpoint_returns_regex_kpi_when_no_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SPOTLIGHT_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()

    filing_id = "spotlight-endpoint-regex-kpi"
    company_id = "spotlight-endpoint-regex-kpi-company"

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
        "ticker": "SPOT",
        "name": "Spotlight Test Corp",
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
        assert kpi.get("company_specific") is True
        assert kpi.get("source_filing_id") == filing_id
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
