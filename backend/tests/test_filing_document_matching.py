from pathlib import Path
from types import SimpleNamespace

from app.api import filings as filings_api


def test_pick_best_sec_filing_match_prefers_correct_period_over_latest():
    candidates = [
        {
            "filing_type": "10-Q",
            "filing_date": "2025-11-05",
            "period_end": "2025-09-30",
            "url": "https://sec.gov/2025.htm",
        },
        {
            "filing_type": "10-Q",
            "filing_date": "2017-05-05",
            "period_end": "2017-03-31",
            "url": "https://sec.gov/2017.htm",
        },
    ]
    best = filings_api._pick_best_sec_filing_match(candidates, target_date="2017-03-31")
    assert best and best.get("url") == "https://sec.gov/2017.htm"


def test_pick_best_sec_filing_match_returns_none_when_too_far():
    candidates = [
        {
            "filing_type": "10-Q",
            "filing_date": "2025-11-05",
            "period_end": "2025-09-30",
            "url": "https://sec.gov/2025.htm",
        },
    ]
    best = filings_api._pick_best_sec_filing_match(
        candidates, target_date="2010-01-01", max_diff_days=180
    )
    assert best is None


def test_ensure_local_document_sanity_check_updates_mismatched_source_url(
    tmp_path, monkeypatch
):
    # Enable the sanity-check branch (normally skipped during tests to avoid network).
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    settings = SimpleNamespace(data_dir=str(tmp_path))
    persisted_updates: list[dict] = []

    def fake_persist(_context, _filing_id_str, updates):
        persisted_updates.append(dict(updates or {}))

    monkeypatch.setattr(filings_api, "_persist_filing_field_updates", fake_persist)

    def fake_get_company_filings(**_kwargs):
        return [
            {
                "filing_type": "10-K",
                "filing_date": "2024-02-01",
                "period_end": "2023-12-31",
                "url": "https://www.sec.gov/2024.htm",
            },
            {
                "filing_type": "10-K",
                "filing_date": "2017-02-01",
                "period_end": "2016-12-31",
                "url": "https://www.sec.gov/2016.htm",
            },
        ]

    monkeypatch.setattr(filings_api, "get_company_filings", fake_get_company_filings)

    download_urls: list[str] = []

    def fake_download(url: str, dest_path: str) -> bool:
        download_urls.append(url)
        Path(dest_path).write_text(
            "CONFORMED PERIOD OF REPORT: 20161231\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(filings_api, "download_filing", fake_download)

    context = {
        "company": {"id": "c1", "ticker": "TEST", "cik": "0000000001", "country": "US"},
        "filing": {
            "id": "f1",
            "filing_type": "10-K",
            "filing_date": "2017-02-01",
            "period_end": "2016-12-31",
            # Mismatched: points at a different (newer) period.
            "source_doc_url": "https://www.sec.gov/2024.htm",
        },
    }

    resolved = filings_api._ensure_local_document(context, settings, allow_network=True)
    assert resolved and resolved.exists()
    assert download_urls and download_urls[-1] == "https://www.sec.gov/2016.htm"
    assert context["filing"].get("source_doc_url") == "https://www.sec.gov/2016.htm"
    assert any("source_doc_url" in u for u in persisted_updates)


def test_ensure_local_document_rejects_cached_doc_with_wrong_as_of_year(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    settings = SimpleNamespace(data_dir=str(tmp_path))
    cached_path = tmp_path / "cached_wrong_year.html"
    cached_path.write_text(
        "As of January 28, 2024, our remaining performance obligations were $15.3 billion.\n",
        encoding="utf-8",
    )

    persisted_updates: list[dict] = []

    def fake_persist(_context, _filing_id_str, updates):
        persisted_updates.append(dict(updates or {}))

    monkeypatch.setattr(filings_api, "_persist_filing_field_updates", fake_persist)

    def fake_get_company_filings(**_kwargs):
        return [
            {
                "filing_type": "10-K",
                "filing_date": "2017-02-01",
                "period_end": "2016-12-31",
                "url": "https://www.sec.gov/2016.htm",
            }
        ]

    monkeypatch.setattr(filings_api, "get_company_filings", fake_get_company_filings)

    download_urls: list[str] = []

    def fake_download(url: str, dest_path: str) -> bool:
        download_urls.append(url)
        Path(dest_path).write_text(
            "CONFORMED PERIOD OF REPORT: 20161231\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(filings_api, "download_filing", fake_download)

    context = {
        "company": {"id": "c1", "ticker": "TEST", "cik": "0000000001", "country": "US"},
        "filing": {
            "id": "f2",
            "filing_type": "10-K",
            "filing_date": "2017-02-01",
            "period_end": "2016-12-31",
            "source_doc_url": "https://www.sec.gov/2024.htm",
            "local_document_path": str(cached_path),
        },
    }

    resolved = filings_api._ensure_local_document(context, settings, allow_network=True)
    assert resolved and resolved.exists()
    assert download_urls and download_urls[-1] == "https://www.sec.gov/2016.htm"
    assert any(
        u.get("local_document_path") is None and u.get("source_doc_url") is None
        for u in persisted_updates
    )
    assert "2024" not in resolved.read_text(encoding="utf-8")
