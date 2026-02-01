from __future__ import annotations

from typing import List, Optional


def build_spotlight_kpi_prompt(
    *,
    company_name: str,
    filing_text: str,
    summary_snippet: str = "",
    candidate_quotes: Optional[List[str]] = None,
    max_candidates: int = 6,
    strict: bool = False,
) -> str:
    quotes_block = ""
    if candidate_quotes:
        joined = "\n".join(f'{idx + 1}. "{q}"' for idx, q in enumerate(candidate_quotes) if q)
        if joined.strip():
            quotes_block = (
                "CANDIDATE QUOTES (optional helpers):\n"
                "- If you use one as `source_quote`, copy it EXACTLY (no ellipses, no edits).\n"
                "- You may also quote ANY other exact line/sentence from the filing text.\n"
                f"{joined}\n\n"
            )

    strict_block = ""
    if strict:
        strict_block = (
            "STRICT MODE (follow exactly):\n"
            "- Do NOT return liabilities/obligations/commitments/contract liabilities/debt/leases.\n"
            "- Avoid total-currency KPIs that are just financial statements (revenue, net income, cash, debt).\n"
            "- If no true operating/usage/volume KPI exists, prefer business-model KPIs like ARR/MRR/retention/churn/ARPU/take-rate, then GMV/TPV/AUM/bookings.\n"
            "- Do NOT return backlog/RPO/remaining performance obligations (often an accounting construct). If you cannot find a better operating/business-model KPI, return no candidates.\n\n"
        )

    return f"""
You are extracting a SINGLE "Company Spotlight" KPI for {company_name}.

Goal:
- Return the SINGLE most representative KPI disclosed in the filing text that best captures what the company actually DOES.
- Prefer true operating/usage/volume KPIs (users/subscribers/customers/units/orders/shipments/transactions/volume/capacity/engagement/usage).
- If the filing does not disclose an operating KPI, prefer business-model KPIs (ARR/MRR/retention/churn/ARPU/take-rate, then GMV/TPV/AUM/bookings).
- Do NOT use backlog/RPO/remaining performance obligations as a Spotlight KPI. If you cannot find a better operating/business-model KPI, return no candidates.
- The Spotlight KPI should be DISTINCTIVE to this company’s business model: prefer product/platform/brand/segment-specific operating metrics over generic totals that most companies report.
- You MUST search across the entire provided filing text, not just the beginning.
- Do your reasoning privately; only output the required JSON.

Hard requirements:
- The KPI MUST be explicitly stated in the provided text with a numeric value.
- `source_quote` MUST be copied EXACTLY from the provided text (no ellipses, no paraphrasing).
- Use the exact metric label from the filing text; do NOT rename it (e.g., don't change "transactions" to "orders").
- If a table/header says values are "in thousands" / "in millions" / "in billions", convert to the real absolute value in dollars (do NOT leave it in thousands).
- `unit` must be a real unit (e.g., "%", "$", "users", "subscribers", "transactions"). Do NOT use scale words like "million"/"billion" as the unit.
- Prefer a LEVEL metric (e.g., "Paid Subscribers", "Monthly Active Users", "Vehicles Delivered") over a pure % change metric.
- If you cannot find a true company-specific operating KPI, prefer business-model KPIs (ARR/MRR/retention/churn/ARPU/take-rate, then GMV/TPV/AUM/bookings). If you still cannot find a valid KPI, return no candidates.

Avoid (do NOT return these as the Spotlight KPI):
- Named revenue line-items (e.g., revenue by product/service/segment) unless you have exhausted operating/usage/volume and business-model KPIs. If you must return a revenue line-item, it should be a last-resort fallback.
- Generic financial-statement metrics: revenue, gross profit, operating income, net income, EPS, cash, debt, margins.
- Liabilities/commitments schedules: obligations, contract liabilities, deferred revenue timing, content obligations, leases, debt maturities.
- Accounting schedules and maturity tables (deferred revenue timing, contract liabilities, revenue recognition schedules).
- Derived ratios unless the filing treats it as a primary industry KPI (ARPU/RevPAR/load factor are OK if explicitly stated).
- Generic “safe” KPIs that most companies have: backlog/RPO, total revenue, total customers (without a business-specific qualifier), generic margins.

Exception (fallback only, when no operating KPI exists):
- A segment/product revenue MIX is allowed ONLY when:
  - It has 2+ meaningful segments (not just "Other"), AND
  - It is clearly a business segment/product breakdown (not obligations/debt/leases), AND
  - You provide `segments` with numeric values that appear in the text.

Output rules:
- Return JSON only (no markdown).
- Return up to {int(max_candidates)} candidates in descending order of quality.
- For each candidate, include:
  - name (short, no "Mix"/"Breakdown")
  - value (number)
  - unit (null OR a short unit string like "%", "$", "users", "subscribers", "customers", "accounts", "units", "orders", "transactions", "rides", "trips")
  - prior_value (null if not explicitly given)
  - chart_type: one of "metric" | "bar" | "trend" | "gauge" | "donut"
  - description (1-2 short lines explaining what it measures and why it matters for the business; keep it concise)
  - source_quote (exact)
  - segments (null OR a list of {{"label": str, "value": number}} ONLY when chart_type is "donut")
  - representativeness_score (0-100)
  - uniqueness_score (0-100)  # how distinctive this KPI is to THIS company's business model (not industry-wide)
  - company_specificity_score (0-100)
  - verifiability_score (0-100)
  - ban_flags: [] (or list of reasons if you think it's disallowed)

Selection procedure (follow silently):
1) Scan the filing text for operational KPI phrases and tables (users/subscribers/customers/orders/shipments/deliveries/GMV/TPV/bookings/usage).
2) Collect at least 5 candidate KPI lines with explicit numbers.
3) Prefer the KPI that best represents the business and is most verifiable (clear label + number).
   - Prefer KPIs with business-specific qualifiers (product/platform/brand terms) over generic totals.
4) If you include a prior value, it must also be explicitly stated in the same quote or an adjacent quote.
5) Ensure `source_quote` is a verbatim substring of the filing text.
6) Only return a donut/mix if no better operating/business-model KPI exists.

{strict_block}{quotes_block}SUMMARY (may omit operational KPIs; use as weak context only):
{(summary_snippet or "N/A").strip()}

FILING TEXT (authoritative):
{(filing_text or "").strip()}

RETURN FORMAT:
{{
  "candidates": [
    {{
      "name": "Customers",
      "value": 250000000,
      "unit": "customers",
      "prior_value": null,
      "chart_type": "metric",
      "description": "Represents the scale of the customer base.",
      "source_quote": "We ended the quarter with 250 million customers.",
      "segments": null,
      "representativeness_score": 92,
      "uniqueness_score": 80,
      "company_specificity_score": 95,
      "verifiability_score": 90,
      "ban_flags": []
    }}
  ]
}}
""".strip()
