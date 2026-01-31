from app.services import edgar_fetcher


def test_resolve_cik_from_ticker_strips_common_suffixes(monkeypatch):
    monkeypatch.setattr(
        edgar_fetcher,
        "_sec_ticker_map",
        lambda: {"AAPL": "0000320193", "BRK-B": "0001067983"},
    )

    assert edgar_fetcher.resolve_cik_from_ticker_sync("AAPL") == "0000320193"
    assert edgar_fetcher.resolve_cik_from_ticker_sync("AAPL:US") == "0000320193"
    assert edgar_fetcher.resolve_cik_from_ticker_sync("AAPL.US") == "0000320193"
    assert edgar_fetcher.resolve_cik_from_ticker_sync("AAPL US") == "0000320193"

    # Share-class normalization: some sources use BRK.B or BRK-B.
    assert edgar_fetcher.resolve_cik_from_ticker_sync("BRK.B") == "0001067983"
    assert edgar_fetcher.resolve_cik_from_ticker_sync("BRK-B") == "0001067983"

