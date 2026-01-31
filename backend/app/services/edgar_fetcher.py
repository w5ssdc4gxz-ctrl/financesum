"""SEC EDGAR filing fetcher service - Enhanced with EODHD."""
import httpx
import asyncio
import json
import re
from functools import lru_cache
import requests  # Used for synchronous SEC calls
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from urllib.parse import urlparse
from app.config import get_settings
from app.services.eodhd_client import (
    EODHDClient,
    hydrate_country_with_eodhd,
    should_hydrate_country,
)
from app.services.country_resolver import extract_country_from_sec_submission

settings = get_settings()

@lru_cache(maxsize=1)
def _sec_ticker_map() -> Dict[str, str]:
    """Return a mapping of TICKER -> zero-padded CIK string.

    Uses the SEC-provided `company_tickers.json` mapping.
    """
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
        "Accept": "application/json",
    }
    response = requests.get(tickers_url, headers=headers, timeout=12)
    response.raise_for_status()
    payload = response.json() or {}

    mapping: Dict[str, str] = {}
    for _, company in (payload or {}).items():
        ticker = str(company.get("ticker") or "").upper().strip()
        cik_str = str(company.get("cik_str") or "").strip()
        if not ticker or not cik_str:
            continue
        digits = "".join(ch for ch in cik_str if ch.isdigit())
        if not digits:
            continue
        mapping[ticker] = digits.zfill(10)
    return mapping


def resolve_cik_from_ticker_sync(ticker: str) -> Optional[str]:
    """Best-effort synchronous ticker -> CIK resolution."""
    try:
        raw = (ticker or "").upper().strip()
        if not raw:
            return None

        # Try a few common ticker normalizations.
        variants: List[str] = []

        def _add(v: str) -> None:
            vv = (v or "").upper().strip()
            if vv and vv not in variants:
                variants.append(vv)

        _add(raw)

        # Common vendor formats: "AAPL:US", "AAPL US"
        if ":" in raw:
            _add(raw.split(":", 1)[0])
        if " " in raw:
            _add(raw.split()[0])

        # Exchange/country suffixes: "SHOP.TO", "ASML.AS"
        m = re.match(r"^([A-Z0-9][A-Z0-9._-]{0,14})[.:-]([A-Z]{2,4})$", raw)
        if m:
            base, suffix = m.group(1), m.group(2)
            common_suffixes = {
                "US",
                "NASDAQ",
                "NAS",
                "NMS",
                "NYSE",
                "NYQ",
                "NYS",
                "AMEX",
                "ASE",
                "ARCX",
                "BATS",
                "TO",
                "V",
                "L",
                "LN",
                "AS",
                "PA",
                "DE",
                "SW",
                "SS",
                "SZ",
                "HK",
                "T",
                "KS",
                "KQ",
                "SI",
                "AX",
            }
            if suffix in common_suffixes:
                _add(base)

        # SEC tickers sometimes use '-' for share classes; some vendors use '.' and vice versa.
        if "." in raw:
            _add(raw.replace(".", "-"))
        if "-" in raw:
            _add(raw.replace("-", "."))

        mapping = _sec_ticker_map()
        for key in variants:
            cik = mapping.get(key)
            if cik:
                return cik
        return None
    except Exception:
        return None


async def _ensure_country(company: Dict) -> Dict:
    ticker = company.get("ticker")
    if not ticker:
        return company

    if not should_hydrate_country(company.get("country")):
        return company

    hydrated = await asyncio.to_thread(hydrate_country_with_eodhd, ticker, company.get("exchange"))
    if hydrated:
        company["country"] = hydrated
    return company


async def _enrich_with_yahoo(company: Dict, client: httpx.AsyncClient) -> Dict:
    """
    Enrich company data with sector/industry from Yahoo Finance.
    Returns the enriched company dict.
    """
    # If already has sector and industry, return as-is
    if company.get("sector") and company.get("industry"):
        return company

    ticker = company.get("ticker")
    if not ticker:
        return company

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

        response = await client.get(yahoo_url, headers=yahoo_headers, params=params, timeout=5.0)
        response.raise_for_status()
        data = response.json()

        quotes = data.get("quotes", [])
        if quotes:
            quote = quotes[0]
            # Only update if we find sector/industry
            if not company.get("sector"):
                company["sector"] = quote.get("sectorDisp") or quote.get("sector")
            if not company.get("industry"):
                company["industry"] = quote.get("industryDisp") or quote.get("industry")
            if not company.get("country"):
                yahoo_country = quote.get("country") or quote.get("longCountry")
                if yahoo_country:
                    company["country"] = yahoo_country

            print(f"✓ Enriched {ticker} with Yahoo Finance data")

    except Exception as e:
        print(f"Could not enrich {ticker} with Yahoo Finance: {e}")

    return company


async def search_company_by_ticker_or_cik(query: str) -> List[Dict]:
    """
    Search for company by ticker or CIK using EODHD API (enhanced) and SEC EDGAR.
    Returns list of company data dictionaries.
    """
    companies = []
    
    # Try EODHD first (faster and has more metadata)
    try:
        if settings.eodhd_api_key:
            eodhd_client = EODHDClient()
            # Run synchronous EODHD client in a separate thread to avoid blocking
            company_info = await asyncio.to_thread(eodhd_client.search_symbol, query)
            
            if company_info:
                return [{
                    "ticker": company_info["ticker"],
                    "cik": company_info.get("cik"),
                    "name": company_info["name"],
                    "exchange": company_info["exchange"],
                    "sector": company_info.get("sector"),
                    "industry": company_info.get("industry"),
                    "country": company_info.get("country")
                }]

            # If direct ticker lookup fails, fall back to EODHD's symbol search endpoint
            search_results = await asyncio.to_thread(eodhd_client.search_symbols, query, 10)
            if search_results:
                normalized: list[Dict] = []
                for match in search_results[:10]:
                    exchange = (match.get("exchange") or "US").strip().upper()
                    if exchange in {"NASDAQ", "NYSE", "AMEX", "ARCA", "NMS", "NYQ"}:
                        exchange = "US"

                    normalized.append(
                        {
                            "ticker": (match.get("ticker") or "").upper(),
                            "cik": match.get("cik"),
                            "name": match.get("name") or match.get("ticker") or query,
                            "exchange": exchange,
                            "sector": match.get("sector"),
                            "industry": match.get("industry"),
                            "country": match.get("country"),
                        }
                    )

                normalized = [c for c in normalized if c.get("ticker")]
                if normalized:
                    return normalized
    except Exception as e:
        print(f"EODHD search error (falling back to EDGAR): {e}")
    
    async with httpx.AsyncClient() as client:
        # Fallback to SEC EDGAR (if EODHD not available)
        tickers_url = "https://www.sec.gov/files/company_tickers.json"

        headers = {
            "User-Agent": settings.edgar_user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov",
        }

        try:
            response = await client.get(tickers_url, headers=headers, timeout=10.0)
            response.raise_for_status()

            companies_data = response.json()

            # Convert to list and search
            query_upper = query.upper()

            for key, company in companies_data.items():
                ticker = company.get("ticker", "").upper()
                cik = str(company.get("cik_str", "")).zfill(10)
                title = company.get("title", "")

                # Match by ticker or CIK
                if query_upper == ticker or query.zfill(10) == cik or query_upper in title.upper():
                    companies.append({
                        "ticker": ticker,
                        "cik": cik,
                        "name": title,
                        "exchange": "US",
                        "sector": None,
                        "industry": None,
                        "country": None
                    })

                    # If exact ticker match, enrich and return immediately
                    if query_upper == ticker:
                        enriched = await _enrich_with_yahoo(companies[-1], client)
                        hydrated = await _ensure_country(enriched)
                        return [hydrated]

            # Enrich all found companies with Yahoo Finance data in parallel
            # Limit to top 10 to avoid spamming Yahoo
            top_companies = companies[:10]
            if top_companies:
                enriched_companies = await asyncio.gather(*[_enrich_with_yahoo(c, client) for c in top_companies])
                hydrated_companies = await asyncio.gather(*[_ensure_country(c) for c in enriched_companies])
                return hydrated_companies
            
            return []

        except Exception as e:
            print(f"Error searching EDGAR: {e}")

        # Final fallback: Yahoo Finance public search API
        try:
            yahoo_url = "https://query2.finance.yahoo.com/v1/finance/search"
            yahoo_headers = {
                "User-Agent": "Mozilla/5.0 (compatible; FinanceSum/1.0; +https://financesum.local)",
                "Accept": "application/json",
            }
            params = {
                "q": query,
                "quotesCount": 10,
                "newsCount": 0,
            }

            response = await client.get(yahoo_url, headers=yahoo_headers, params=params, timeout=5.0)
            response.raise_for_status()
            data = response.json()

            quotes = data.get("quotes", [])
            for quote in quotes:
                quote_type = quote.get("quoteType")
                if quote_type not in {"EQUITY", "ETF"}:
                    continue

                ticker = quote.get("symbol", "").upper()
                if not ticker:
                    continue

                companies.append({
                    "ticker": ticker,
                    "cik": quote.get("cik") or quote.get("symbol"),
                    "name": quote.get("longname") or quote.get("shortname") or ticker,
                    "exchange": quote.get("exchange") or quote.get("exchDisp") or "US",
                    "sector": quote.get("sectorDisp") or quote.get("sector"),
                    "industry": quote.get("industryDisp") or quote.get("industry"),
                    "country": quote.get("country") or quote.get("longCountry"),
                })

            if companies:
                hydrated_companies = await asyncio.gather(*[_ensure_country(c) for c in companies[:10]])
                return hydrated_companies

        except Exception as e:
            print(f"Error searching Yahoo Finance: {e}")

    return companies


def _normalize_cik_value(cik: str) -> Optional[str]:
    if cik is None:
        return None
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(10)


def resolve_country_from_sec_submission(cik: str) -> Optional[str]:
    """
    Best-effort country resolution using the SEC submissions payload.

    This is particularly helpful for foreign issuers trading in the US where
    the exchange code does not indicate domicile.
    """
    cik_padded = _normalize_cik_value(cik)
    if not cik_padded:
        return None

    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
        "Accept": "application/json",
    }

    try:
        response = requests.get(submissions_url, headers=headers, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    return extract_country_from_sec_submission(payload)


def get_company_filings(
    cik: str,
    filing_types: Optional[List[str]] = None,
    max_results: int = 100
) -> List[Dict]:
    """
    Get filings for a company from SEC EDGAR.
    Note: Kept synchronous for now as it's usually called in a background task or cached context,
    but ideally should be async too.
    """
    cik_padded = str(cik).zfill(10)
    
    # SEC EDGAR Submissions API
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    
    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov"
    }
    
    try:
        # Using requests here as this function wasn't marked async in the interface
        # If we change this to async, we need to update callers.
        # For now, let's leave it but be aware it blocks.
        response = requests.get(submissions_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        recent_filings = data.get("filings", {}).get("recent", {})
        
        filings = []
        
        # Get arrays of filing data
        accession_numbers = recent_filings.get("accessionNumber", [])
        filing_dates = recent_filings.get("filingDate", [])
        report_dates = recent_filings.get("reportDate", [])
        forms = recent_filings.get("form", [])
        primary_docs = recent_filings.get("primaryDocument", [])
        
        for i in range(len(forms)):
            form_type = forms[i]
            
            # Filter by filing type if specified
            if filing_types and form_type not in filing_types:
                continue
            
            accession = accession_numbers[i].replace("-", "")
            filing_date = filing_dates[i]
            report_date = report_dates[i] if i < len(report_dates) else None
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            
            # Construct document URL
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_doc}"
            
            filings.append({
                "filing_type": form_type,
                "filing_date": filing_date,
                "period_end": report_date,
                "url": doc_url,
                "accession_number": accession_numbers[i]
            })
            
            if len(filings) >= max_results:
                break
        
        return filings
    
    except Exception as e:
        print(f"Error fetching filings: {e}")
        return []


def download_filing(url: str, output_path: str) -> bool:
    """
    Download a filing from SEC EDGAR.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        print(f"Refusing to download invalid URL: {url}")
        return False

    if (parsed.scheme or "").lower() not in {"http", "https"} or not (
        (parsed.hostname or "").lower().endswith("sec.gov")
    ):
        print(f"Refusing to download non-SEC filing URL: {url}")
        return False

    headers = {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate"
    }

    def _looks_low_signal_filing(body: bytes) -> bool:
        """Heuristic: detect cover/boilerplate pages that lack the real filing content.

        Common case: 6-K / 8-K primaryDocument is a short cover page, while exhibits
        (press release / investor presentation) contain the actual numbers.
        """
        if not body:
            return True
        try:
            head = body[:120_000].decode("utf-8", errors="ignore")
        except Exception:
            return False
        upper = head.upper()
        # Cover-page boilerplate hints.
        boilerplate = (
            "INDICATE BY CHECK MARK",
            "PURSUANT TO RULE 13A-16 OR 15D-16",
            "COMMISSION FILE NUMBER",
            "SECURITIES AND EXCHANGE COMMISSION",
            "FORM 6-K",
            "FORM 8-K",
            "REPORT OF FOREIGN PRIVATE ISSUER",
        )
        has_boilerplate = any(b in upper for b in boilerplate)

        # Short primary docs (esp. 6-K/8-K) are often cover sheets that mention exhibits
        # but do not include the numeric content we need for KPI extraction. Use a
        # currency/scale heuristic to detect real content.
        has_currency_number = bool(re.search(r"[$€£]\s*\d", head))
        has_scale_number = bool(
            re.search(r"\b\d+(?:\.\d+)?\s*(?:BILLION|MILLION|THOUSAND)\b", upper)
        )
        has_kpi_keyword = bool(
            re.search(
                r"\b("
                r"BOOKINGS?|NET\s+BOOKINGS|BACKLOG|RPO|REMAINING\s+PERFORMANCE|"
                r"SUBSCRIB|MAU|DAU|USERS?|CUSTOMERS?|ACCOUNTS?|SHIPMENTS?|DELIVERIES?|"
                r"ORDERS?|TRANSACTIONS?|GMV|TPV|AUM|PAID\s+CLICKS|IMPRESSIONS"
                r")\b",
                upper,
            )
        )

        # Short boilerplate-heavy docs are usually cover pages. Even if they contain
        # a headline number, they rarely contain operational KPIs; prefer exhibits.
        if has_boilerplate and len(body) < 60_000:
            if not has_kpi_keyword:
                return True
            if not (has_currency_number or has_scale_number):
                return True

        # Many cover pages are only a few KB. But some exhibits are legitimately small
        # while still containing real KPIs, so require the text to look "empty" too.
        if len(body) < 35_000:
            alpha = sum(1 for ch in upper if "A" <= ch <= "Z")
            digits = sum(1 for ch in upper if "0" <= ch <= "9")
            # If we have meaningful prose + numbers, treat it as content.
            if alpha >= 2_500 and digits >= 40:
                return False
            return True
        return False

    def _dir_index_json_url(original_url: str) -> Optional[str]:
        try:
            parsed_local = urlparse(original_url)
        except Exception:
            return None
        if not parsed_local.path:
            return None
        # Expect: /Archives/edgar/data/<cik>/<accession>/<filename>
        parts = [p for p in parsed_local.path.split("/") if p]
        try:
            idx = parts.index("data")
        except ValueError:
            return None
        # Need at least: data/<cik>/<accession>/<file>
        if len(parts) < idx + 4:
            return None
        base_path = "/" + "/".join(parts[: idx + 3])  # includes accession directory
        return f"{parsed_local.scheme}://{parsed_local.netloc}{base_path}/index.json"

    def _choose_best_exhibit_url(original_url: str) -> Optional[str]:
        index_url = _dir_index_json_url(original_url)
        if not index_url:
            return None
        try:
            resp = requests.get(index_url, headers=headers, timeout=20)
            if resp.status_code >= 400:
                return None
            data = resp.json() or {}
        except Exception:
            return None

        items = (data.get("directory") or {}).get("item") or []
        if not isinstance(items, list) or not items:
            return None

        try:
            parsed_local = urlparse(original_url)
            original_name = (parsed_local.path or "").split("/")[-1]
        except Exception:
            original_name = ""

        def _score_item(it: dict) -> tuple[int, int]:
            name = str(it.get("name") or "")
            size_raw = it.get("size")
            try:
                size = int(size_raw) if str(size_raw).strip() else 0
            except Exception:
                size = 0

            lower = name.lower()
            bonus = 0
            # Prefer content exhibits / press releases over the index and over the cover doc.
            if lower.endswith(("index.html", "index-headers.html")):
                bonus -= 50
            if original_name and lower == original_name.lower():
                bonus -= 25
            # High-signal content keywords.
            # Press releases / results exhibits are usually best for KPI extraction.
            if any(tok in lower for tok in ("press", "release", "earnings", "results")):
                bonus += 55
            if "quarter" in lower or "quarterly" in lower:
                bonus += 15
            if any(tok in lower for tok in ("ex99", "ex-99", "exhibit", "99")):
                bonus += 25
            # Investor presentations can be useful, but are often image-heavy. Prefer them
            # only when we don't have a press release/results exhibit.
            if "investor" in lower:
                bonus += 8
            if "presentation" in lower:
                bonus += 12
            if "financialstatements" in lower:
                bonus += 10
            # Prefer HTML content.
            if lower.endswith((".htm", ".html")):
                bonus += 10
            # Allow PDFs (investor decks / exhibits). Keep a small preference for HTML
            # so we don't accidentally select image-heavy presentations when a press
            # release HTML is available.
            if lower.endswith(".pdf"):
                bonus += 6
            return (bonus, size)

        candidate_items = [
            it
            for it in items
            if isinstance(it, dict)
            and str(it.get("name") or "")
            .lower()
            .endswith((".htm", ".html", ".pdf"))
        ]
        if not candidate_items:
            return None

        best = max(candidate_items, key=_score_item)
        best_name = str(best.get("name") or "").strip()
        if not best_name:
            return None

        # Build the URL in the same directory.
        try:
            parsed_local = urlparse(original_url)
            base = parsed_local.path.rsplit("/", 1)[0]
            return f"{parsed_local.scheme}://{parsed_local.netloc}{base}/{best_name}"
        except Exception:
            return None
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)

        # If the downloaded doc is likely just a cover page, try to replace it with
        # the most relevant exhibit/attachment in the accession directory.
        if _looks_low_signal_filing(response.content):
            upgraded_url = _choose_best_exhibit_url(url)
            if upgraded_url and upgraded_url != url:
                try:
                    upgraded = requests.get(upgraded_url, headers=headers, timeout=30)
                    upgraded.raise_for_status()
                    if not _looks_low_signal_filing(upgraded.content):
                        with open(output_path, "wb") as f:
                            f.write(upgraded.content)
                except Exception:
                    # Best-effort only; keep original.
                    pass
        
        return True
    
    except Exception as e:
        print(f"Error downloading filing: {e}")
        return False
