from fastapi.testclient import TestClient

from app.api import companies as companies_api
from app.main import app


def test_companies_lookup_returns_empty_list_when_no_results(monkeypatch):
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    async def _no_results(*_args, **_kwargs):
        return []

    monkeypatch.setattr(companies_api, "search_company_by_ticker_or_cik", _no_results)

    client = TestClient(app)

    response = client.post("/api/v1/companies/lookup", json={"query": "NO_SUCH_TICKER"})
    assert response.status_code == 200
    assert response.json() == {"companies": []}


def test_companies_lookup_get_returns_empty_list_when_no_results(monkeypatch):
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    async def _no_results(*_args, **_kwargs):
        return []

    monkeypatch.setattr(companies_api, "search_company_by_ticker_or_cik", _no_results)

    client = TestClient(app)

    response = client.get("/api/v1/companies/lookup", params={"query": "NO_SUCH_TICKER"})
    assert response.status_code == 200
    assert response.json() == {"companies": []}


def test_companies_lookup_searches_fallback_cache_by_name(monkeypatch):
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)

    async def _no_results(*_args, **_kwargs):
        return []

    monkeypatch.setattr(companies_api, "search_company_by_ticker_or_cik", _no_results)
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)

    monkeypatch.setattr(companies_api, "fallback_companies", {
        "dfb9fe51-4b49-4e35-9185-82540137665e": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "cik": "0000320193",
            "exchange": "US",
            "industry": None,
            "sector": None,
            "country": "US",
            "id": "dfb9fe51-4b49-4e35-9185-82540137665e",
            "created_at": "2025-11-14T15:07:47.287049",
            "updated_at": "2025-11-14T15:07:47.287049",
        }
    })

    client = TestClient(app)

    response = client.post("/api/v1/companies/lookup", json={"query": "apple"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["companies"]
    assert payload["companies"][0]["ticker"] == "AAPL"
