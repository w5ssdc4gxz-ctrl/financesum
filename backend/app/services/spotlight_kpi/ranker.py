from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .types import SpotlightKpiCandidate


_NEGATIVE_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\brevenue\s+mix\b", re.I),
    re.compile(r"\bmix\b", re.I),
    re.compile(r"\bby\s+(region|geography|product|segment|category)\b", re.I),
    re.compile(r"\bbreakdown\b", re.I),
    re.compile(r"\bsegment\b", re.I),
    re.compile(r"\bgeographic\b", re.I),
    re.compile(r"\bdeferred\s+revenue\b", re.I),
    re.compile(r"\bcontract\s+liabilit", re.I),
    re.compile(r"\bcontent\s+obligations?\b", re.I),
    re.compile(r"\bobligations?\b", re.I),
    re.compile(r"\bcommitments?\b", re.I),
    re.compile(r"\bliabilit", re.I),
    re.compile(r"\bleases?\b", re.I),
    re.compile(r"\bdebt\b", re.I),
    # Backlog/RPO is often an accounting construct and not the "signature KPI" users expect.
    re.compile(r"\b(backlog|rpo|remaining performance obligations)\b", re.I),
)

_GENERIC_FINANCIAL_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*revenue\s*$", re.I),
    re.compile(r"\btotal\s+revenue\b", re.I),
    re.compile(r"\bnet\s+income\b", re.I),
    re.compile(r"\bgross\s+profit\b", re.I),
    re.compile(r"\boperating\s+income\b", re.I),
    re.compile(r"\bfree\s+cash\s+flow\b", re.I),
    re.compile(r"\bEPS\b", re.I),
    # Accounting / GAAP line items frequently mistaken as "KPIs"
    re.compile(r"\bstock[- ]based\s+compensation\b", re.I),
    re.compile(r"\bshare[- ]based\s+(compensation|payment)\b", re.I),
    re.compile(r"\bexcess\s+tax\s+benefits?\b", re.I),
    re.compile(r"\b(deferred\s+tax|valuation\s+allowance|effective\s+tax\s+rate)\b", re.I),
    re.compile(r"\b(depreciation|amortization)\b", re.I),
    re.compile(r"\binterest\s+(expense|income)\b", re.I),
)

# Names that are often too generic to serve as the single "Spotlight KPI" unless
# they contain a product/platform/segment qualifier (e.g., "Prime Members",
# "iPhone Units", "Active Merchants", "Monthly Active Users").
#
# We do NOT ban these outright (many filings only disclose a bare total), but we
# heavily de-prioritize them so a more distinctive KPI wins whenever available.
_TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(total\s+)?customers?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?users?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?subscribers?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?members?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?accounts?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?orders?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?transactions?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?shipments?\s*$", re.I),
    re.compile(r"^\s*(total\s+)?deliver(?:y|ies)\s*$", re.I),
    re.compile(r"^\s*(total\s+)?units?\s*$", re.I),
)

_OPERATIONAL_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmonthly\s+active\s+users?\b|\bMAUs?\b", re.I),
    re.compile(r"\bdaily\s+active\s+users?\b|\bDAUs?\b", re.I),
    re.compile(r"\bpaid\s+subscribers?\b|\bsubscribers?\b", re.I),
    re.compile(r"\bmemberships?\b|\bpaid\s+members\b", re.I),
    re.compile(r"\bcustomers?\b|\baccounts?\b", re.I),
    re.compile(r"\borders?\b|\btransactions?\b", re.I),
    re.compile(r"\bshipments?\b|\bunits\s+shipped\b", re.I),
    re.compile(r"\bdeliver(?:y|ies)\b|\bvehicles?\s+delivered\b", re.I),
    re.compile(r"\bbookings?\b", re.I),
    re.compile(r"\bstore\s+count\b|\blocations?\b", re.I),
    # Unit economics / monetization signals (still operationally representative)
    re.compile(r"\b(ARR|MRR)\b|\b(recurring\s+revenue)\b", re.I),
    re.compile(r"\b(net|dollar[- ]based)\s+retention\b|\b(NRR|NDR)\b", re.I),
    re.compile(r"\bchurn\b", re.I),
    re.compile(r"\bARPU\b|\bARPA\b|\bARPPU\b|\bASP\b|\bAOV\b|\baverage\s+revenue\s+per\b", re.I),
    re.compile(r"\btake\s+rate\b", re.I),
    # Marketplace / payments volumes
    re.compile(r"\b(GMV|GMS|GTV)\b|\bgross\s+merchandise\s+volume\b", re.I),
    re.compile(r"\bTPV\b|\b(total\s+payment\s+volume|payment\s+volume|processed\s+volume)\b", re.I),
    # Asset management
    re.compile(r"\bAUM\b|\bassets\s+under\s+management\b", re.I),
)


def _normalize_ws(text: str) -> str:
    lowered = re.sub(r"\s+", " ", (text or "").strip()).lower()
    return re.sub(r"[^a-z0-9%$€£ ]+", "", lowered)


def _source_quote_in_context(source_quote: str, context_text: str) -> bool:
    if not source_quote or not context_text:
        return False
    q = _normalize_ws(source_quote)
    if not q:
        return False
    ctx = _normalize_ws(context_text)
    if q in ctx:
        return True

    # Some filings contain odd line-breaks / repeated whitespace around iXBRL spans.
    # The model may still copy an "exact" quote from its view, but our context slice
    # can differ slightly after extraction/normalization. For long quotes, accept a
    # strong partial match as long as BOTH ends appear in order.
    #
    # This stays conservative by:
    # - only enabling for long quotes
    # - requiring both prefix and suffix to match
    # - requiring order (suffix after prefix)
    if len(q) >= 220 and len(ctx) >= 500:
        prefix = q[:160].strip()
        suffix = q[-160:].strip()
        if prefix and suffix:
            i = ctx.find(prefix)
            if i >= 0:
                j = ctx.find(suffix, i + len(prefix))
                if j >= 0:
                    return True

    return False


def _coerce_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if isinstance(v, str):
        cleaned = v.strip()
        cleaned = cleaned.replace(",", "")
        cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "")
        cleaned = cleaned.replace("%", "")
        mult = 1.0
        lower = cleaned.lower()
        if lower.endswith("b"):
            mult = 1_000_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("m"):
            mult = 1_000_000.0
            cleaned = cleaned[:-1]
        elif lower.endswith("k"):
            mult = 1_000.0
            cleaned = cleaned[:-1]
        try:
            return float(cleaned) * mult
        except ValueError:
            return None
    return None


def _is_banned_candidate(name: str, candidate: SpotlightKpiCandidate) -> bool:
    chart_type = str(candidate.get("chart_type") or "").strip().lower()
    # Donuts/mixes are allowed ONLY as a fallback when no operational KPI exists.
    # We don't ban them here; we handle them in the selection logic.
    if any(p.search(name) for p in _NEGATIVE_NAME_PATTERNS):
        # Allow mix-y names only when a proper segment breakdown exists.
        segs = candidate.get("segments")
        if not (isinstance(segs, list) and len(segs) >= 2):
            return True
    if any(p.search(name) for p in _GENERIC_FINANCIAL_NAME_PATTERNS):
        return True
    # Model can explicitly flag itself as banned
    flags = candidate.get("ban_flags")
    if isinstance(flags, list) and any(str(f).strip() for f in flags):
        return True
    return False


def _rule_score(name: str, unit: Optional[str]) -> int:
    score = 0
    if any(p.search(name) for p in _OPERATIONAL_NAME_PATTERNS):
        score += 25
    if unit and unit.strip().lower() in ("users", "subscribers", "customers", "accounts", "units"):
        score += 10
    return score


def _segments_look_meaningful(candidate: SpotlightKpiCandidate) -> bool:
    segs = candidate.get("segments")
    if not isinstance(segs, list) or len(segs) < 2:
        return False
    labels = []
    total = 0.0
    for seg in segs[:10]:
        if not isinstance(seg, dict):
            continue
        label = str(seg.get("label") or "").strip()
        try:
            value = float(seg.get("value"))
        except Exception:
            continue
        if not label or value <= 0:
            continue
        labels.append(label)
        total += float(value)
    if len(labels) < 2 or total <= 0:
        return False
    generic = {"other", "total", "consolidated", "eliminations", "all other"}
    meaningful = [l for l in labels if _normalize_ws(l) not in generic and len(l) <= 48]
    return len(meaningful) >= 2


def pick_best_spotlight_kpi(
    candidates: List[SpotlightKpiCandidate],
    *,
    context_text: str,
) -> Optional[SpotlightKpiCandidate]:
    """Pick the best single Spotlight KPI from Gemini candidates.

    Enforces:
    - quote must exist in context
    - must not be a mix/breakdown/generic financial KPI
    - prefer operational/representative metrics
    """
    scored: List[Tuple[int, SpotlightKpiCandidate]] = []
    mix_scored: List[Tuple[int, SpotlightKpiCandidate]] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        source_quote = str(c.get("source_quote") or "").strip()
        if not name or not source_quote:
            continue
        if not _source_quote_in_context(source_quote, context_text):
            continue
        chart_type = str(c.get("chart_type") or "").strip().lower()
        is_mix = chart_type in ("donut", "breakdown", "pie")
        if _is_banned_candidate(name, c):
            continue

        value = _coerce_float(c.get("value"))
        if value is None:
            continue

        unit = c.get("unit")
        if isinstance(unit, str):
            unit = unit.strip() or None

        # Penalize total currency "big number" disclosures unless they are a per-unit monetization KPI.
        unit_lower = (unit or "").lower() if isinstance(unit, str) else ""
        name_lower = name.lower()
        is_currency = unit_lower in ("$", "€", "£", "usd", "eur", "gbp")
        is_per_unit_money = any(token in name_lower for token in ("arpu", "arpa", "asp", "aov", "average revenue per"))
        is_business_model_money = any(
            token in name_lower
            for token in (
                "backlog",
                "rpo",
                "remaining performance obligations",
                "bookings",
                "gross bookings",
                "gmv",
                "gross merchandise volume",
                "tpv",
                "total payment volume",
                "aum",
                "assets under management",
            )
        )
        penalty = 0
        # Strongly de-prioritize bare totals ("Customers", "Users", etc.) unless
        # the filing provides no better candidate.
        if any(p.search(name) for p in _TOO_GENERIC_SPOTLIGHT_NAME_PATTERNS):
            penalty -= 22
        # Backlog/RPO is often an accounting construct; keep it as a last-resort fallback.
        if any(token in name_lower for token in ("backlog", "rpo", "remaining performance")):
            penalty -= 12
        # Revenue line-items (product/subscription/etc.) are frequently present but
        # usually less representative than true operating KPIs (users/volume/units).
        # Keep them as last-resort fallbacks, but heavily de-prioritize them.
        is_revenue_line = ("revenue" in name_lower) and (not is_mix) and (not is_business_model_money) and (not is_per_unit_money)
        if is_revenue_line:
            penalty -= 35
        if (
            (not is_mix)
            and is_currency
            and abs(float(value)) >= 1_000_000
            and not is_per_unit_money
            and not is_business_model_money
        ):
            penalty -= 50

        # Model self ratings (0..100) are treated as hints, not authority.
        rep = c.get("representativeness_score")
        uniq = c.get("uniqueness_score")
        spec = c.get("company_specificity_score")
        ver = c.get("verifiability_score")
        hint = 0
        for val in (rep, uniq, spec, ver):
            if isinstance(val, int):
                hint += max(0, min(100, val))
        # Encourage models that self-report high specificity/verification.
        hint = int(hint / 40)  # ~0..10 (now includes uniqueness)

        base = _rule_score(name, unit)
        total = base + hint + penalty
        item = {**c, "value": float(value), "unit": unit}

        # Mix candidates are only considered if no operational KPI wins.
        if is_mix:
            if _segments_look_meaningful(c):
                # Prefer more segments (up to 6) and higher self-rated representativeness.
                seg_count = len(c.get("segments") or [])
                mix_bonus = min(6, seg_count) * 3
                mix_scored.append((total + mix_bonus, item))
            continue

        scored.append((total, item))

    if not scored:
        if not mix_scored:
            return None
        mix_scored.sort(key=lambda x: x[0], reverse=True)
        return mix_scored[0][1]

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]
