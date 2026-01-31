from __future__ import annotations

from pathlib import Path

import pytest

from app.services import edgar_fetcher


class _Resp:
    def __init__(self, *, status_code: int = 200, content: bytes = b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json_data = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_download_filing_upgrades_cover_doc_to_press_release(tmp_path, monkeypatch):
    """6-K/8-K primary docs are often short cover pages; pick exhibit HTML instead."""
    cover_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/form6-kquarterlyfilings.htm"
    index_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/index.json"
    press_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/pressreleasequarterlyresul.htm"
    txt_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/fullsubmission.txt"

    cover_bytes = (b"SECURITIES AND EXCHANGE COMMISSION\n" b"FORM 6-K\n" b"Indicate by check mark\n")
    press_bytes = (b"Quarterly results\nNet bookings were \xe2\x82\xac5.0 billion\n" + (b"x" * 50_000))
    txt_bytes = (b"Complete submission text\nNet bookings were \xe2\x82\xac5.0 billion\n" + (b"y" * 70_000))

    index_payload = {
        "directory": {
            "item": [
                {"name": "form6-kquarterlyfilings.htm", "type": "text.gif", "size": "11745"},
                {"name": "pressreleasequarterlyresul.htm", "type": "text.gif", "size": "50019"},
                {"name": "fullsubmission.txt", "type": "text.gif", "size": "80000"},
            ]
        }
    }

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        if url == cover_url:
            return _Resp(content=cover_bytes)
        if url == index_url:
            return _Resp(json_data=index_payload)
        if url == press_url:
            return _Resp(content=press_bytes)
        if url == txt_url:
            return _Resp(content=txt_bytes)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(edgar_fetcher.requests, "get", fake_get)

    out = Path(tmp_path) / "filing.html"
    ok = edgar_fetcher.download_filing(cover_url, str(out))
    assert ok is True

    written = out.read_bytes()
    assert b"Net bookings" in written
    assert len(written) > len(cover_bytes)


def test_download_filing_force_best_exhibit_even_when_not_low_signal(tmp_path, monkeypatch):
    """When forced, always prefer the best exhibit from index.json."""
    original_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/form10q.htm"
    index_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/index.json"
    press_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/pressreleasequarterlyresul.htm"
    txt_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/fullsubmission.txt"

    # Original looks like a real filing (no 6-K/8-K boilerplate, long enough).
    original_bytes = (
        b"FORM 10-Q\nMANAGEMENT DISCUSSION & ANALYSIS\nSome content...\n"
        + (b"x" * 70_000)
    )
    press_bytes = b"Quarterly results\nNet bookings were $5.0 billion\n" + (b"y" * 50_000)
    txt_bytes = b"Complete submission text\nNet bookings were $5.0 billion\n" + (b"z" * 70_000)

    index_payload = {
        "directory": {
            "item": [
                {"name": "form10q.htm", "type": "text.gif", "size": str(len(original_bytes))},
                {"name": "pressreleasequarterlyresul.htm", "type": "text.gif", "size": str(len(press_bytes))},
                {"name": "fullsubmission.txt", "type": "text.gif", "size": str(len(txt_bytes))},
            ]
        }
    }

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        if url == original_url:
            return _Resp(content=original_bytes)
        if url == index_url:
            return _Resp(json_data=index_payload)
        if url == press_url:
            return _Resp(content=press_bytes)
        if url == txt_url:
            return _Resp(content=txt_bytes)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(edgar_fetcher.requests, "get", fake_get)

    out = Path(tmp_path) / "filing.html"
    ok = edgar_fetcher.download_filing(original_url, str(out), force_best_exhibit=True)
    assert ok is True
    assert b"Net bookings" in out.read_bytes()


def test_download_filing_force_best_exhibit_respects_max_size(tmp_path, monkeypatch):
    original_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/form10q.htm"
    index_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/index.json"
    press_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/pressreleasequarterlyresul.htm"
    txt_url = "https://www.sec.gov/Archives/edgar/data/937966/000162828025045043/fullsubmission.txt"

    original_bytes = b"FORM 10-Q\nContent...\n" + (b"x" * 80_000)
    press_bytes = b"Quarterly results\nNet bookings were $5.0 billion\n" + (b"y" * 80_000)
    txt_bytes = b"Complete submission text\nNet bookings were $5.0 billion\n" + (b"z" * 20_000)

    # Mark the press exhibit as too large in the index so it gets filtered out.
    index_payload = {
        "directory": {
            "item": [
                {"name": "form10q.htm", "type": "text.gif", "size": str(len(original_bytes))},
                {"name": "pressreleasequarterlyresul.htm", "type": "text.gif", "size": "80000000"},
                {"name": "fullsubmission.txt", "type": "text.gif", "size": str(len(txt_bytes))},
            ]
        }
    }

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        if url == original_url:
            return _Resp(content=original_bytes)
        if url == index_url:
            return _Resp(json_data=index_payload)
        if url == press_url:
            return _Resp(content=press_bytes)
        if url == txt_url:
            return _Resp(content=txt_bytes)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(edgar_fetcher.requests, "get", fake_get)

    out = Path(tmp_path) / "filing.html"
    ok = edgar_fetcher.download_filing(
        original_url,
        str(out),
        force_best_exhibit=True,
        max_exhibit_size_bytes=50_000_000,
    )
    assert ok is True
    written = out.read_bytes()
    assert b"Complete submission text" in written
