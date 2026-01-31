from __future__ import annotations

from app.services import edgar_fetcher


class _Resp:
    def __init__(self, *, status_code: int = 200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data
        self.content = b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_get_company_filings_fetches_historical_submissions_file(monkeypatch):
    submissions_url = "https://data.sec.gov/submissions/CIK0000000001.json"
    historical_url = "https://data.sec.gov/submissions/CIK0000000001-submissions-001.json"

    main_payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-24-000001"],
                "filingDate": ["2024-02-01"],
                "reportDate": ["2023-12-31"],
                "form": ["10-K"],
                "primaryDocument": ["form10k.htm"],
            },
            "files": [
                {
                    "name": "CIK0000000001-submissions-001.json",
                    "filingCount": 100,
                    "filingFrom": "1990-01-01",
                    "filingTo": "2015-01-01",
                }
            ],
        }
    }

    historical_payload = {
        "accessionNumber": ["0000000001-10-000001"],
        "filingDate": ["2010-02-01"],
        "reportDate": ["2009-12-31"],
        "form": ["10-K"],
        "primaryDocument": ["form10k.htm"],
    }

    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        calls.append(url)
        if url == submissions_url:
            return _Resp(json_data=main_payload)
        if url == historical_url:
            return _Resp(json_data=historical_payload)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(edgar_fetcher.requests, "get", fake_get)

    filings = edgar_fetcher.get_company_filings(
        "1",
        filing_types=["10-K"],
        max_results=10,
        target_date="2010-02-01",
    )

    assert submissions_url in calls
    assert historical_url in calls

    assert any(f.get("filing_date") == "2010-02-01" for f in filings)
    assert any(
        f.get("url")
        == "https://www.sec.gov/Archives/edgar/data/1/000000000110000001/form10k.htm"
        for f in filings
    )


def test_get_company_filings_skips_historical_when_target_is_recent(monkeypatch):
    submissions_url = "https://data.sec.gov/submissions/CIK0000000001.json"
    historical_url = "https://data.sec.gov/submissions/CIK0000000001-submissions-001.json"

    main_payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-24-000001"],
                "filingDate": ["2024-02-01"],
                "reportDate": ["2023-12-31"],
                "form": ["10-K"],
                "primaryDocument": ["form10k.htm"],
            },
            "files": [
                {
                    "name": "CIK0000000001-submissions-001.json",
                    "filingCount": 100,
                    "filingFrom": "1990-01-01",
                    "filingTo": "2015-01-01",
                }
            ],
        }
    }

    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        calls.append(url)
        if url == submissions_url:
            return _Resp(json_data=main_payload)
        if url == historical_url:
            raise AssertionError("Historical fetch should not run for recent targets")
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(edgar_fetcher.requests, "get", fake_get)

    filings = edgar_fetcher.get_company_filings(
        "1",
        filing_types=["10-K"],
        max_results=10,
        target_date="2024-02-01",
    )

    assert calls == [submissions_url]
    assert len(filings) == 1
    assert filings[0]["filing_date"] == "2024-02-01"
