from app.services import web_research


def test_cache_key_scopes_quarterly_by_year_quarter() -> None:
    key_q1 = web_research._cache_key(
        "Microsoft Corp",
        "MSFT",
        filing_type="10-Q",
        filing_date="2015-03-31",
    )
    key_q2 = web_research._cache_key(
        "Microsoft Corp",
        "MSFT",
        filing_type="10-Q",
        filing_date="2015-06-30",
    )
    assert key_q1 != key_q2


def test_cache_key_scopes_annual_by_year() -> None:
    key_2015_a = web_research._cache_key(
        "Microsoft Corp",
        "MSFT",
        filing_type="10-K",
        filing_date="2015-06-30",
    )
    key_2015_b = web_research._cache_key(
        "Microsoft Corp",
        "MSFT",
        filing_type="10-K",
        filing_date="2015-12-31",
    )
    key_2016 = web_research._cache_key(
        "Microsoft Corp",
        "MSFT",
        filing_type="10-K",
        filing_date="2016-06-30",
    )
    assert key_2015_a == key_2015_b
    assert key_2015_a != key_2016


def test_legacy_cache_hit_rehydrates_period_scoped_key(monkeypatch) -> None:
    company_name = "Microsoft Corp"
    ticker = "MSFT"
    filing_type = "10-Q"
    filing_date = "2015-03-31"
    new_key = web_research._cache_key(
        company_name,
        ticker,
        filing_type=filing_type,
        filing_date=filing_date,
    )
    legacy_key = web_research._legacy_cache_key(company_name, ticker)

    cache = {legacy_key: "legacy dossier"}
    writes = {}

    monkeypatch.setattr(
        web_research,
        "_read_cache",
        lambda cache_key: cache.get(cache_key),
    )
    monkeypatch.setattr(
        web_research,
        "_write_cache",
        lambda cache_key, _company, _ticker, dossier_text: writes.setdefault(
            cache_key, dossier_text
        ),
    )

    dossier = web_research.get_company_research_dossier(
        company_name=company_name,
        ticker=ticker,
        filing_type=filing_type,
        filing_date=filing_date,
        force_refresh=False,
    )

    assert dossier == "legacy dossier"
    assert writes.get(new_key) == "legacy dossier"
