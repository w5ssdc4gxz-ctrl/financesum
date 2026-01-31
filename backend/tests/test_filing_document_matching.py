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

