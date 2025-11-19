#!/usr/bin/env python3
"""Script to enrich existing companies with sector/industry data from Yahoo Finance."""

import json
import requests
from pathlib import Path


def enrich_with_yahoo(ticker: str) -> dict:
    """Fetch sector/industry from Yahoo Finance for a ticker."""
    try:
        yahoo_url = "https://query2.finance.yahoo.com/v1/finance/search"
        yahoo_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FinanceSum/1.0; +https://financesum.local)",
            "Accept": "application/json",
        }
        params = {
            "q": ticker,
            "quotesCount": 1,
            "newsCount": 0,
        }

        response = requests.get(yahoo_url, headers=yahoo_headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        quotes = data.get("quotes", [])
        if quotes:
            quote = quotes[0]
            return {
                "sector": quote.get("sectorDisp") or quote.get("sector"),
                "industry": quote.get("industryDisp") or quote.get("industry")
            }
    except Exception as e:
        print(f"Error enriching {ticker}: {e}")

    return {"sector": None, "industry": None}


def main():
    """Enrich all companies in the local cache."""
    companies_file = Path(__file__).parent / "data" / "local_cache" / "companies.json"

    if not companies_file.exists():
        print(f"❌ Companies file not found: {companies_file}")
        return

    print(f"📂 Reading companies from {companies_file}")

    with open(companies_file, "r") as f:
        companies = json.load(f)

    print(f"Found {len(companies)} companies")

    updated_count = 0
    for company_id, company_data in companies.items():
        ticker = company_data.get("ticker")
        current_sector = company_data.get("sector")
        current_industry = company_data.get("industry")

        # Skip if already has sector and industry
        if current_sector and current_industry:
            print(f"  ✓ {ticker}: Already has sector/industry")
            continue

        print(f"  🔄 Enriching {ticker}...")
        enrichment = enrich_with_yahoo(ticker)

        if enrichment["sector"] or enrichment["industry"]:
            company_data["sector"] = enrichment["sector"]
            company_data["industry"] = enrichment["industry"]
            updated_count += 1
            print(f"  ✅ {ticker}: sector={enrichment['sector']}, industry={enrichment['industry']}")
        else:
            print(f"  ⚠️  {ticker}: Could not fetch sector/industry")

    if updated_count > 0:
        print(f"\n💾 Saving updated companies ({updated_count} enriched)...")
        with open(companies_file, "w") as f:
            json.dump(companies, f, indent=2)
        print(f"✅ Successfully updated {companies_file}")
    else:
        print("\n No updates needed")


if __name__ == "__main__":
    main()
