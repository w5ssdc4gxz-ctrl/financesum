from __future__ import annotations

import re
from typing import List, Tuple


# Operational/usage/volume signals that usually represent what the company *does*.
# These are intentionally broad across industries.
_OPERATIONAL_KEYWORDS: Tuple[str, ...] = (
    # Users / subs
    "paid memberships",
    "paid members",
    "memberships",
    "subscribers",
    "subscriptions",
    "monthly active users",
    "daily active users",
    "mau",
    "dau",
    "active users",
    "customers",
    "customer accounts",
    "accounts",
    "users",
    # Usage / engagement
    "hours viewed",
    "hours watched",
    "viewing hours",
    "streaming hours",
    "engagement",
    "watch time",
    # Commerce / ops
    "orders",
    "transactions",
    "shipments",
    "units shipped",
    "systems shipped",
    "systems sold",
    "tools shipped",
    "tools sold",
    "installed base",
    "install base",
    "wafer",
    "wafers",
    "wafer starts",
    "euv",
    "duv",
    "lithography",
    "deliveries",
    "vehicles delivered",
    "bookings",
    "order intake",
    "book-to-bill",
    "book to bill",
    "room nights",
    "rides",
    "trips",
    "gross bookings",
    # SaaS / subscriptions
    "annual recurring revenue",
    "arr",
    "net retention",
    "dollar-based net retention",
    "nrr",
    "gross retention",
    "churn",
    "net adds",
    # Marketplace / payments
    "gmv",
    "gross merchandise volume",
    "tpv",
    "total payment volume",
    "payment volume",
    "processed volume",
    "payments volume",
    "active merchants",
    "merchant locations",
    # Financial services / asset managers
    "assets under management",
    "aum",
    "assets under custody",
    "auc",
    "assets under administration",
    "aua",
    "deposits",
    "loan originations",
    "loans",
    "net interest margin",
    "nim",
    "net interest income",
    "nii",
    # Insurance
    "gross written premium",
    "gwp",
    "premiums",
    "combined ratio",
    "loss ratio",
    "expense ratio",
    "policies",
    # Retail / restaurants
    "same-store sales",
    "same store sales",
    "comparable sales",
    "comp sales",
    "store count",
    "stores",
    "locations",
    # Travel / hospitality
    "revpar",
    "adr",
    "average daily rate",
    # Transportation
    "passengers",
    "available seat miles",
    "asm",
    "revenue passenger miles",
    "rpm",
    "casm",
    "rasm",
    # Energy / materials
    "production",
    "barrels",
    "boe",
    "throughput",
    "refining throughput",
    "ounces",
    "tonnes",
    "metric tons",
    "mt",
    # Capacity/utilization (often % but still operational)
    "occupancy",
    "load factor",
    "utilization",
    # SaaS unit economics (allowed only if explicit)
    "arpu",
    "arpa",
    "asp",
    "aov",
    "take rate",
)

_BANNED_GENERIC_KEYWORDS: Tuple[str, ...] = (
    "content obligations",
    "obligations",
    "contract liabilities",
    "contract liability",
    "deferred revenue",
    "liabilities",
    "liability",
    "commitments",
    "commitment",
    "debt",
    "lease",
    "leases",
)


_KPI_DEFINITION_PATTERNS: Tuple[re.Pattern[str], ...] = (
    # Explicit KPI headings / phrases.
    re.compile(r"\bkey\s+performance\s+indicators?\b", re.I),
    re.compile(r"\bkey\s+operating\s+metrics?\b", re.I),
    re.compile(r"\bkey\s+business\s+metrics?\b", re.I),
    re.compile(r"\boperating\s+metrics?\b", re.I),
    re.compile(r"\bkey\s+metrics?\b", re.I),
    # Definition-style language.
    re.compile(r"\bwe\s+(?:use|track|monitor|evaluate|manage)\s+(?:the\s+)?(?:following\s+)?(?:key|operating|business|performance)\s+metrics?\b", re.I),
    re.compile(r"\bwe\s+define\b", re.I),
    re.compile(r"\bwe\s+refer\s+to\b", re.I),
    re.compile(r"\bthis\s+metric\b", re.I),
)


def build_kpi_definition_context(
    text: str,
    *,
    max_chars: int = 140_000,
    max_windows: int = 10,
    window_size: int = 2600,
) -> str:
    """Extract a context slice around KPI definition sections.

    Purpose: capture company-specific KPI tables/definitions even when the KPI name
    is not in the operational keyword list (e.g., branded/defined terms).
    """
    if not text:
        return ""

    s = (text or "").strip()
    if not s:
        return ""

    # Mirror the operational context behavior: cover head/middle/tail for long docs.
    head = s[:300_000]
    haystack = head
    if len(s) > 380_000:
        tail = s[-300_000:]
        mid_start = max(0, (len(s) // 2) - 150_000)
        mid = s[mid_start : mid_start + 300_000]
        haystack = f"{head}\n\n--- MIDDLE ---\n\n{mid}\n\n--- END ---\n\n{tail}"

    windows: List[Tuple[int, int]] = []
    for pat in _KPI_DEFINITION_PATTERNS:
        for m in pat.finditer(haystack):
            start = max(0, m.start() - window_size)
            end = min(len(haystack), m.end() + window_size)
            windows.append((start, end))
            if len(windows) >= max_windows * 3:
                break
        if len(windows) >= max_windows * 3:
            break

    if not windows:
        return ""

    windows.sort(key=lambda x: x[0])
    merged: List[Tuple[int, int]] = []
    cur_s, cur_e = windows[0]
    for s_i, e_i in windows[1:]:
        if s_i <= cur_e + 250:
            cur_e = max(cur_e, e_i)
            continue
        merged.append((cur_s, cur_e))
        cur_s, cur_e = s_i, e_i
    merged.append((cur_s, cur_e))
    merged = merged[:max_windows]

    parts: List[str] = []
    for s_i, e_i in merged:
        snippet = haystack[s_i:e_i].strip()
        if not snippet:
            continue
        # Don't include pure obligations blocks; these are the most common false positives.
        lower = snippet.lower()
        if any(k in lower for k in _BANNED_GENERIC_KEYWORDS):
            # Still allow if the block contains clear operational words.
            if not any(k in lower for k in ("users", "customers", "subscribers", "orders", "transactions", "arr", "mrr", "retention", "churn", "gmv", "tpv", "aum")):
                continue
        parts.append(snippet)

    joined = "\n\n---\n\n".join(parts).strip()
    if not joined:
        return ""
    return joined[:max_chars].rstrip()


def build_operational_spotlight_context(
    text: str,
    *,
    max_chars: int = 120_000,
    max_windows: int = 14,
    window_size: int = 1800,
) -> str:
    """Extract an operational-only context slice.

    Purpose: prevent Spotlight from selecting generic accounting disclosures by
    restricting the context to windows around operational KPI keywords.
    """
    if not text:
        return ""

    # Search both the head and tail of long texts so we don't miss KPI disclosures
    # that appear later in MD&A or supplemental sections. Also include a middle
    # slice for very long filings where KPI tables often sit mid-document.
    head = text[:300_000]
    haystack = head
    if len(text) > 380_000:
        tail = text[-300_000:]
        mid_start = max(0, (len(text) // 2) - 150_000)
        mid = text[mid_start : mid_start + 300_000]
        haystack = f"{head}\n\n--- MIDDLE ---\n\n{mid}\n\n--- END ---\n\n{tail}"
    pattern = re.compile("|".join(re.escape(k) for k in _OPERATIONAL_KEYWORDS), re.IGNORECASE)

    windows: List[Tuple[int, int]] = []
    for m in pattern.finditer(haystack):
        start = max(0, m.start() - window_size)
        end = min(len(haystack), m.end() + window_size)
        windows.append((start, end))
        if len(windows) >= max_windows * 3:
            break

    if not windows:
        return ""

    # Merge overlapping windows.
    windows.sort(key=lambda x: x[0])
    merged: List[Tuple[int, int]] = []
    cur_s, cur_e = windows[0]
    for s, e in windows[1:]:
        if s <= cur_e + 250:
            cur_e = max(cur_e, e)
            continue
        merged.append((cur_s, cur_e))
        cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))

    # Keep only the first N merged windows (most "early" matches).
    merged = merged[:max_windows]

    parts: List[str] = []
    for s, e in merged:
        snippet = haystack[s:e].strip()
        if snippet:
            parts.append(snippet)

    joined = "\n\n---\n\n".join(parts).strip()
    if not joined:
        return ""

    # Post-filter: drop obviously generic accounting disclosure lines unless they
    # also contain an operational keyword.
    op_pat = re.compile("|".join(re.escape(k) for k in _OPERATIONAL_KEYWORDS), re.IGNORECASE)
    banned_pat = re.compile("|".join(re.escape(k) for k in _BANNED_GENERIC_KEYWORDS), re.IGNORECASE)
    filtered_lines: List[str] = []
    for line in re.split(r"[\r\n]+", joined):
        raw = line.rstrip()
        if not raw.strip():
            filtered_lines.append(raw)
            continue
        if banned_pat.search(raw) and not op_pat.search(raw):
            continue
        filtered_lines.append(raw)
    joined = "\n".join(filtered_lines).strip()
    if not joined:
        return ""

    if max_chars and len(joined) > max_chars:
        return joined[:max_chars].rstrip()
    return joined
