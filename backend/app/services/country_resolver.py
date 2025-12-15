"""Country normalization and inference helpers.

This module is intentionally dependency-free so it can run in restricted
environments (no network, no extra packages). It focuses on producing a stable
"country" value that the dashboard map can immediately plot.
"""

from __future__ import annotations

import re
from typing import Optional


# US state and territory postal abbreviations (used in SEC company name suffixes like "... /DE/").
US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
    "VI",
    "GU",
    "AS",
    "MP",
}

# US state and territory names as returned by SEC submission payloads.
US_STATE_NAMES = {
    "ALABAMA",
    "ALASKA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "FLORIDA",
    "GEORGIA",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW HAMPSHIRE",
    "NEW JERSEY",
    "NEW MEXICO",
    "NEW YORK",
    "NORTH CAROLINA",
    "NORTH DAKOTA",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "RHODE ISLAND",
    "SOUTH CAROLINA",
    "SOUTH DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGINIA",
    "WASHINGTON",
    "WEST VIRGINIA",
    "WISCONSIN",
    "WYOMING",
    "DISTRICT OF COLUMBIA",
    "AMERICAN SAMOA",
    "GUAM",
    "NORTHERN MARIANA ISLANDS",
    "PUERTO RICO",
    "U.S. VIRGIN ISLANDS",
    "US VIRGIN ISLANDS",
    "VIRGIN ISLANDS",
}


# Common ISO3 -> ISO2 mapping for SEC/Yahoo/EODHD variants we see in practice.
ISO3_TO_ISO2 = {
    "USA": "US",
    "CAN": "CA",
    "MEX": "MX",
    "GBR": "GB",
    "DEU": "DE",
    "FRA": "FR",
    "ITA": "IT",
    "ESP": "ES",
    "NLD": "NL",
    "CHE": "CH",
    "SWE": "SE",
    "NOR": "NO",
    "DNK": "DK",
    "POL": "PL",
    "CHN": "CN",
    "JPN": "JP",
    "IND": "IN",
    "KOR": "KR",
    "SGP": "SG",
    "HKG": "HK",
    "TWN": "TW",
    "IDN": "ID",
    "THA": "TH",
    "MYS": "MY",
    "PHL": "PH",
    "VNM": "VN",
    "ARE": "AE",
    "SAU": "SA",
    "ISR": "IL",
    "TUR": "TR",
    "AUS": "AU",
    "NZL": "NZ",
    "BRA": "BR",
    "ARG": "AR",
    "CHL": "CL",
    "COL": "CO",
    "PER": "PE",
    "ZAF": "ZA",
    "EGY": "EG",
    "NGA": "NG",
    "KEN": "KE",
    "RUS": "RU",
    "CZE": "CZ",
}


# Normalization targets for values used by the frontend map + logo helpers.
# Prefer ISO2 codes for stability.
COUNTRY_ALIASES_TO_ISO2 = {
    # US
    "US": "US",
    "U S": "US",
    "U.S": "US",
    "U.S.": "US",
    "USA": "US",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "UNITEDSTATES": "US",
    "UNITEDSTATESOFAMERICA": "US",
    # Canada
    "CA": "CA",
    "CANADA": "CA",
    # Mexico
    "MX": "MX",
    "MEXICO": "MX",
    # UK / GB
    "GB": "GB",
    "UK": "GB",
    "U.K.": "GB",
    "U.K": "GB",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "WALES": "GB",
    # Europe
    "DE": "DE",
    "GERMANY": "DE",
    "FR": "FR",
    "FRANCE": "FR",
    "IT": "IT",
    "ITALY": "IT",
    "ES": "ES",
    "SPAIN": "ES",
    "NL": "NL",
    "NETHERLANDS": "NL",
    "CH": "CH",
    "SWITZERLAND": "CH",
    "SE": "SE",
    "SWEDEN": "SE",
    "NO": "NO",
    "NORWAY": "NO",
    "DK": "DK",
    "DENMARK": "DK",
    "PL": "PL",
    "POLAND": "PL",
    "CZ": "CZ",
    "CZECH REPUBLIC": "CZ",
    "RU": "RU",
    "RUSSIA": "RU",
    "RUSSIAN FEDERATION": "RU",
    # Asia
    "CN": "CN",
    "CHINA": "CN",
    "JP": "JP",
    "JAPAN": "JP",
    "IN": "IN",
    "INDIA": "IN",
    "KR": "KR",
    "SOUTH KOREA": "KR",
    "REPUBLIC OF KOREA": "KR",
    "KOREA, REPUBLIC OF": "KR",
    "KP": "KP",
    "NORTH KOREA": "KP",
    "SG": "SG",
    "SINGAPORE": "SG",
    "HK": "HK",
    "HONG KONG": "HK",
    "HONG KONG SAR": "HK",
    "TW": "TW",
    "TAIWAN": "TW",
    "ID": "ID",
    "INDONESIA": "ID",
    "TH": "TH",
    "THAILAND": "TH",
    "MY": "MY",
    "MALAYSIA": "MY",
    "PH": "PH",
    "PHILIPPINES": "PH",
    "VN": "VN",
    "VIETNAM": "VN",
    # Middle East
    "AE": "AE",
    "UAE": "AE",
    "UNITED ARAB EMIRATES": "AE",
    "SA": "SA",
    "SAUDI ARABIA": "SA",
    "IL": "IL",
    "ISRAEL": "IL",
    "TR": "TR",
    "TURKEY": "TR",
    # Oceania
    "AU": "AU",
    "AUSTRALIA": "AU",
    "NZ": "NZ",
    "NEW ZEALAND": "NZ",
    # South America
    "BR": "BR",
    "BRAZIL": "BR",
    "AR": "AR",
    "ARGENTINA": "AR",
    "CL": "CL",
    "CHILE": "CL",
    "CO": "CO",
    "COLOMBIA": "CO",
    "PE": "PE",
    "PERU": "PE",
    # Africa
    "ZA": "ZA",
    "SOUTH AFRICA": "ZA",
    "EG": "EG",
    "EGYPT": "EG",
    "NG": "NG",
    "NIGERIA": "NG",
    "KE": "KE",
    "KENYA": "KE",
    # Channel Islands (fallback to UK for map display)
    "JERSEY": "GB",
}


EXCHANGE_TO_ISO2 = {
    # US (do NOT map a generic "US" exchange placeholder to domicile)
    "NASDAQ": "US",
    "NYSE": "US",
    "NYQ": "US",
    "NMS": "US",
    "AMEX": "US",
    "ARCA": "US",
    # UK
    "LSE": "GB",
    "LON": "GB",
    "LONX": "GB",
    # Canada
    "TSX": "CA",
    "TSXV": "CA",
    # Japan
    "TSE": "JP",
    "JPX": "JP",
    # Hong Kong
    "HKEX": "HK",
    "HK": "HK",
    # Australia
    "ASX": "AU",
    # India
    "NSE": "IN",
    "BSE": "IN",
    # Switzerland
    "SIX": "CH",
    "SWX": "CH",
    # Singapore
    "SGX": "SG",
    # Germany
    "FWB": "DE",
    "XETRA": "DE",
    # China
    "SSE": "CN",
    "SZSE": "CN",
    # Korea
    "KRX": "KR",
    "KOSDAQ": "KR",
    # Mexico
    "BMV": "MX",
    # Brazil
    "B3": "BR",
    # South Africa
    "JSE": "ZA",
    # New Zealand
    "NZX": "NZ",
    # Indonesia
    "IDX": "ID",
    # Malaysia
    "KLSE": "MY",
    # Italy (Borsa Italiana / Milan)
    "MIL": "IT",
    "MI": "IT",
    "MTA": "IT",
    "BIT": "IT",
    "BORSA ITALIANA": "IT",
}

# Yahoo/Google style ticker suffix → domicile hints (e.g. MONC.MI for Milan/Italy).
TICKER_SUFFIX_TO_ISO2 = {
    "MI": "IT",
    "L": "GB",
    "TO": "CA",
    "V": "CA",
    "T": "JP",
    "HK": "HK",
    "AX": "AU",
}


_SEC_SUFFIX_RE = re.compile(r"/([A-Z]{2,3})/\s*$")

# Company legal suffix hints for domicile inference (used only as a fallback).
_LEGAL_SUFFIX_HINTS: list[tuple[re.Pattern[str], str]] = [
    # Italy
    (re.compile(r"\bS\.?P\.?A\.?\b\.?\s*$", re.IGNORECASE), "IT"),
    (re.compile(r"\bS\.?R\.?L\.?\b\.?\s*$", re.IGNORECASE), "IT"),
    # Denmark (sometimes also Norway, but A/S is most commonly Danish)
    (re.compile(r"\bA/S\s*$", re.IGNORECASE), "DK"),
    # Sweden
    (re.compile(r"\bAB\s*$", re.IGNORECASE), "SE"),
    # Norway
    (re.compile(r"\bASA\s*$", re.IGNORECASE), "NO"),
    # Japan
    (re.compile(r"\bK\.?K\.?\s*$", re.IGNORECASE), "JP"),
    # Australia
    (re.compile(r"\bPTY\.?\s+LTD\.?\s*$", re.IGNORECASE), "AU"),
    # Netherlands
    (re.compile(r"\bN\.?V\.?\s*$", re.IGNORECASE), "NL"),
    # UK
    (re.compile(r"\bPLC\s*$", re.IGNORECASE), "GB"),
]


def _normalize_country_simple(raw: str) -> Optional[str]:
    upper = raw.upper().strip()
    collapsed = re.sub(r"\s+", " ", upper).replace(".", "").strip()
    compact = collapsed.replace(" ", "")

    if compact in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[compact]
    if collapsed in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[collapsed]

    if collapsed in COUNTRY_ALIASES_TO_ISO2:
        return COUNTRY_ALIASES_TO_ISO2[collapsed]
    if compact in COUNTRY_ALIASES_TO_ISO2:
        return COUNTRY_ALIASES_TO_ISO2[compact]

    # Preserve ISO2 codes as uppercase.
    if len(compact) == 2 and compact.isalpha():
        return compact

    return None


def normalize_country(country: Optional[str]) -> Optional[str]:
    """Normalize country values to a stable ISO2-ish representation when possible."""
    if not country:
        return None

    raw = str(country).strip()
    if not raw:
        return None

    direct = _normalize_country_simple(raw)
    if direct:
        return direct

    # Handle composite values like "British Columbia, Canada" or "Ontario, Canada".
    cleaned = re.sub(r"[()\\[\\]]", " ", raw).strip()
    for sep in (",", "|", "/"):
        if sep not in cleaned:
            continue
        parts = [part.strip() for part in cleaned.split(sep) if part.strip()]
        for candidate in reversed(parts):
            normalized = _normalize_country_simple(candidate)
            if normalized:
                return normalized

    # Last-chance: search for any known alias inside the string.
    searchable = re.sub(r"[^A-Za-z]+", " ", raw).strip()
    searchable_upper = re.sub(r"\s+", " ", searchable.upper()).strip()
    if searchable_upper:
        for alias, iso2 in sorted(COUNTRY_ALIASES_TO_ISO2.items(), key=lambda item: len(item[0]), reverse=True):
            alias_text = re.sub(r"[^A-Za-z]+", " ", alias).strip().upper()
            alias_text = re.sub(r"\s+", " ", alias_text).strip()
            if not alias_text:
                continue
            if re.search(rf"\\b{re.escape(alias_text)}\\b", searchable_upper):
                return iso2

    return raw


def normalize_country_from_sec(value: Optional[str]) -> Optional[str]:
    """
    Normalize country-like values coming from SEC submission payloads.

    The SEC submissions API uses `stateOrCountryDescription` for both domestic
    (US state names) and foreign (country names).
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    upper = re.sub(r"\s+", " ", raw.upper().replace(".", " ").strip())

    if upper in US_STATE_NAMES:
        return "US"
    if upper in US_STATE_CODES:
        # SEC sometimes uses state codes (e.g., "DE") which would otherwise collide with ISO2.
        return "US"

    return normalize_country(raw)


def extract_country_from_sec_submission(payload: object) -> Optional[str]:
    """
    Extract a normalized country code from an SEC submissions payload.

    Prefers `addresses.business.stateOrCountryDescription` which often contains
    the true domicile country for foreign issuers; falls back to mailing and
    state-of-incorporation fields.
    """
    if not isinstance(payload, dict):
        return None

    addresses = payload.get("addresses") or {}
    candidates: list[Optional[str]] = []
    for section in ("business", "mailing"):
        block = addresses.get(section) or {}
        if isinstance(block, dict):
            candidates.append(block.get("stateOrCountryDescription"))
            candidates.append(block.get("stateOrCountry"))
            candidates.append(block.get("country"))

    candidates.append(payload.get("stateOfIncorporationDescription"))
    candidates.append(payload.get("stateOfIncorporation"))

    normalized_candidates: list[str] = []
    for value in candidates:
        normalized = normalize_country_from_sec(value)
        if normalized:
            normalized_candidates.append(normalized)

    if not normalized_candidates:
        return None

    # Prefer a non-US value when available (foreign issuers can have US mailing/business
    # addresses even when domicile/incorporation is abroad).
    for candidate in normalized_candidates:
        if candidate != "US":
            return candidate

    return normalized_candidates[0]


def infer_country_from_exchange(exchange: Optional[str]) -> Optional[str]:
    if not exchange:
        return None
    key = str(exchange).strip().upper()
    if not key:
        return None
    # Handle common Yahoo display names
    if key in {"MILAN", "MILANO"}:
        return "IT"
    return EXCHANGE_TO_ISO2.get(key)


def infer_country_from_ticker(ticker: Optional[str]) -> Optional[str]:
    """
    Infer country from dotted ticker symbols (e.g. Yahoo Finance).

    Examples:
      - "MONC.MI" -> "IT" (Borsa Italiana)
      - "VOD.L" -> "GB" (London)
    """
    if not ticker:
        return None
    raw = str(ticker).strip().upper()
    if "." not in raw:
        return None
    suffix = raw.split(".")[-1].strip()
    if not suffix:
        return None
    return TICKER_SUFFIX_TO_ISO2.get(suffix)


def infer_country_from_company_name(name: Optional[str]) -> Optional[str]:
    """
    Infer country from SEC-style company name suffixes.

    Examples:
      - "BANK OF MONTREAL /CAN/" -> "CA"
      - "NEWMONT Corp /DE/" -> "US" (Delaware, state code)
    """
    if not name:
        return None
    text = str(name).strip().upper()
    match = _SEC_SUFFIX_RE.search(text)
    if not match:
        # Fallback: legal suffixes like "S.p.A." (Italy), "AB" (Sweden), etc.
        for pattern, country in _LEGAL_SUFFIX_HINTS:
            if pattern.search(text):
                return country
        return None
    token = match.group(1)
    if len(token) == 3:
        return ISO3_TO_ISO2.get(token)
    if len(token) == 2:
        if token in US_STATE_CODES:
            return "US"
        return normalize_country(token)
    return None
