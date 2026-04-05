from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import local_cache
from app.api.filings import _resolve_filing_context


class _DummyResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _DummyQuery:
    def __init__(self, response):
        self._response = response

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def lt(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def execute(self):
        return self._response


class _DummySupabase:
    def __init__(self, response):
        self._response = response

    def table(self, _name: str):
        return _DummyQuery(self._response)


def _configure_supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-a-placeholder-key")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    get_settings.cache_clear()


def test_dashboard_overview_falls_back_on_supabase_error_payload(monkeypatch):
    _configure_supabase_env(monkeypatch)

    from app.api import dashboard as dashboard_module

    dummy = _DummySupabase(_DummyResponse({"message": "Invalid API key"}))
    monkeypatch.setattr(dashboard_module, "get_supabase_client", lambda: dummy)

    client = TestClient(app)
    resp = client.get("/api/v1/dashboard/overview")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "history" in payload
    assert "stats" in payload
    assert "companies" in payload


def test_list_company_analyses_falls_back_on_supabase_error_payload(monkeypatch):
    _configure_supabase_env(monkeypatch)

    from app.api import analysis as analysis_module

    dummy = _DummySupabase(_DummyResponse({"message": "Invalid API key"}))
    monkeypatch.setattr(analysis_module, "get_supabase_client", lambda: dummy)

    client = TestClient(app)
    resp = client.get("/api/v1/analysis/company/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_resolve_filing_context_uses_fallback_on_supabase_error_payload(monkeypatch, tmp_path):
    _configure_supabase_env(monkeypatch)

    from app.api import filings as filings_module

    dummy = _DummySupabase(_DummyResponse({"message": "Invalid API key"}))
    monkeypatch.setattr(filings_module, "get_supabase_client", lambda: dummy)

    filing_id = "supabase-error-fallback-filing"
    company_id = "supabase-error-fallback-company"

    doc_path = tmp_path / "filing.html"
    doc_path.write_text("MANAGEMENT DISCUSSION & ANALYSIS\nTest.\n", encoding="utf-8")

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
        "ticker": "TEST",
        "name": "Fallback Test Corp",
    }

    try:
        ctx = _resolve_filing_context(filing_id, get_settings())
        assert ctx["source"] == "fallback"
        assert ctx["filing"]["id"] == filing_id
        assert ctx["company"]["id"] == company_id
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)

