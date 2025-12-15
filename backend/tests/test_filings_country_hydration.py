from uuid import uuid4

from app.api import filings as filings_api
from app.services.eodhd_client import extract_country_from_eodhd
from app.services import local_cache


def test_extract_country_from_eodhd_prefers_non_us_address_country():
    info = {
        "CountryName": "United States",
        "AddressData": {"Country": "Denmark"},
    }
    assert extract_country_from_eodhd(info) == "DK"


def test_extract_country_from_eodhd_uses_isin_prefix_over_us():
    info = {
        "CountryName": "United States",
        "ISIN": "IT0004965148",
    }
    assert extract_country_from_eodhd(info) == "IT"


def test_filings_ensure_company_country_prefers_sec_submission(monkeypatch):
    monkeypatch.setattr(filings_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(filings_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: "Denmark")
    monkeypatch.setattr(filings_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("hydrate_country_with_eodhd should not be called when SEC resolves the country")

    monkeypatch.setattr(filings_api, "hydrate_country_with_eodhd", _should_not_call)

    company_id = str(uuid4())
    company = {
        "id": company_id,
        "ticker": "NVO",
        "exchange": "US",
        "cik": "0000353278",
        "country": "US",
    }

    try:
        hydrated = filings_api._ensure_company_country(company, company_key=company_id)
        assert hydrated["country"] == "DK"
        assert local_cache.fallback_companies[company_id]["country"] == "DK"
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_filings_ensure_company_country_does_not_default_to_us_from_eodhd(monkeypatch):
    monkeypatch.setattr(filings_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(filings_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(filings_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(filings_api, "hydrate_country_with_eodhd", lambda *_args, **_kwargs: "United States")

    company_id = str(uuid4())
    company = {
        "id": company_id,
        "ticker": "SOME",
        "exchange": "US",
        "cik": None,
        "country": None,
    }

    try:
        hydrated = filings_api._ensure_company_country(company, company_key=company_id)
        assert hydrated.get("country") is None
        assert company_id not in local_cache.fallback_companies
    finally:
        local_cache.fallback_companies.pop(company_id, None)


def test_filings_ensure_company_country_clears_us_placeholder_when_only_eodhd_us(monkeypatch):
    monkeypatch.setattr(filings_api, "save_fallback_companies", lambda: None)
    monkeypatch.setattr(filings_api, "resolve_country_from_sec_submission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(filings_api, "resolve_country_from_yahoo_asset_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(filings_api, "hydrate_country_with_eodhd", lambda *_args, **_kwargs: "United States")

    company_id = str(uuid4())
    company = {
        "id": company_id,
        "ticker": "SOME",
        "exchange": "US",
        "cik": None,
        "name": "Some Company",
        "country": "US",
    }

    try:
        hydrated = filings_api._ensure_company_country(company, company_key=company_id)
        assert hydrated.get("country") is None
        assert local_cache.fallback_companies[company_id]["country"] is None
    finally:
        local_cache.fallback_companies.pop(company_id, None)
