"""Yahoo Finance helpers (best-effort, unauthenticated endpoints)."""

from __future__ import annotations

from typing import Any, Optional

import requests


def resolve_country_from_yahoo_asset_profile(ticker: str) -> Optional[str]:
    """
    Best-effort: resolve HQ country using Yahoo's quoteSummary assetProfile.

    This is helpful for foreign issuers trading in the US where "region" from the
    search endpoint often reports "United States" even when domicile is abroad.
    """
    cleaned = (ticker or "").strip().upper()
    if not cleaned:
        return None

    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{cleaned}"
    params = {"modules": "assetProfile,summaryProfile"}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FinanceSum/1.0; +https://financesum.local)",
        "Accept": "application/json",
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        payload: Any = response.json()
    except Exception:
        return None

    result = payload.get("quoteSummary", {}).get("result")
    if not isinstance(result, list) or not result:
        return None

    first = result[0] if isinstance(result[0], dict) else {}
    if not isinstance(first, dict):
        return None

    for module_key in ("assetProfile", "summaryProfile"):
        module = first.get(module_key)
        if not isinstance(module, dict):
            continue
        country = module.get("country")
        if isinstance(country, str) and country.strip():
            return country.strip()

    return None
