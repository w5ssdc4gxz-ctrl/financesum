from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import companies as companies_api
from app.main import app
from app.services.country_resolver import (
    extract_country_from_sec_submission,
    infer_country_from_company_name,
    infer_country_from_exchange,
    infer_country_from_ticker,
    normalize_country,
)
from app.services import local_cache


def test_normalize_country_aliases():
    assert normalize_country("United States of America") == "US"
    assert normalize_country("United States") == "US"
    assert normalize_country("USA") == "US"
    assert normalize_country("China") == "CN"
    assert normalize_country("  gb  ") == "GB"
    assert normalize_country("British Columbia, Canada") == "CA"
    assert normalize_country("Ontario, Canada") == "CA"
    assert normalize_country("Jersey") == "GB"


def test_infer_country_from_company_name_suffix():
    assert infer_country_from_company_name("BANK OF MONTREAL /CAN/") == "CA"
    assert infer_country_from_company_name("NEWMONT Corp /DE/") == "US"
    assert infer_country_from_company_name("Moncler S.p.A.") == "IT"


def test_infer_country_from_exchange():
    assert infer_country_from_exchange("LSE") == "GB"
    assert infer_country_from_exchange("NASDAQ") == "US"
    assert infer_country_from_exchange("MIL") == "IT"
    assert infer_country_from_exchange("Milan") == "IT"


def test_infer_country_from_ticker_suffix():
    assert infer_country_from_ticker("MONC.MI") == "IT"
    assert infer_country_from_ticker("VOD.L") == "GB"


def test_extract_country_from_sec_submission_payload():
    foreign_payload = {"addresses": {"business": {"stateOrCountryDescription": "Denmark"}}}
    assert extract_country_from_sec_submission(foreign_payload) == "DK"

    domestic_payload = {"addresses": {"business": {"stateOrCountryDescription": "California"}}}
    assert extract_country_from_sec_submission(domestic_payload) == "US"

    mixed_payload = {
        "addresses": {"business": {"stateOrCountryDescription": "California"}},
        "stateOfIncorporationDescription": "Denmark",
    }
    assert extract_country_from_sec_submission(mixed_payload) == "DK"


def test_get_company_infers_country_without_network(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)

    # Avoid any real EODHD calls; if inference works, this should never run.
    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("hydrate_country_with_retry should not be called for /CAN/ inference")

    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", _should_not_call)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "BMO",
        "name": "BANK OF MONTREAL /CAN/",
        "cik": "0000927971",
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] == "CA"
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_infers_country_from_name_even_with_us_placeholder(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("hydrate_country_with_retry should not be called for /CAN/ inference")

    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", _should_not_call)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "BMO",
        "name": "BANK OF MONTREAL /CAN/",
        "cik": "0000927971",
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": "US",
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] == "CA"
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_infers_country_from_ticker_suffix(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(companies_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", lambda *_args, **_kwargs: None)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MONC.MI",
        "name": "Moncler",
        "cik": None,
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": "US",
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] == "IT"
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_falls_back_to_exchange_country_when_hydration_fails(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(companies_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", lambda *_args, **_kwargs: None)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MNST",
        "name": "Monster Beverage Corp",
        "cik": "0000865752",
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] is None
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_clears_us_placeholder_when_no_domicile_signal(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(companies_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", lambda *_args, **_kwargs: "United States")
    monkeypatch.setattr(companies_api, "queue_for_hydration", lambda *_args, **_kwargs: None)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MNST",
        "name": "Monster Beverage Corp",
        "cik": None,
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": "US",
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] is None
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_uses_sec_submission_to_resolve_foreign_domicile(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(companies_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: "DK")
    monkeypatch.setattr(companies_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("hydrate_country_with_retry should not be called when SEC resolves the country")

    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", _should_not_call)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "NVO",
        "name": "Novo Nordisk A/S",
        "cik": "0000353278",
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": "US",
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] == "DK"
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_get_company_uses_yahoo_asset_profile_when_sec_missing(monkeypatch):
    monkeypatch.setattr(companies_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(companies_api, "_supabase_configured", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(companies_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(companies_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: "Denmark")

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("hydrate_country_with_retry should not be called when Yahoo resolves the country")

    monkeypatch.setattr(companies_api, "hydrate_country_with_retry", _should_not_call)

    company_id = str(uuid4())
    now = datetime.utcnow()
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "NVO",
        "name": "Novo Nordisk A/S",
        "cik": None,
        "exchange": "US",
        "industry": None,
        "sector": None,
        "country": "US",
        "created_at": now,
        "updated_at": now,
    }

    try:
        client = TestClient(app)
        response = client.get(f"/api/v1/companies/{company_id}")
        assert response.status_code == 200
        assert response.json()["country"] == "DK"
    finally:
        local_cache.fallback_companies.pop(company_id, None)
