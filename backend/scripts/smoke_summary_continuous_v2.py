#!/usr/bin/env python3
"""Smoke-test the Continuous Scaling Summary V2 pipeline directly."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.summary_agents import SECTION_ORDER, run_summary_agent_pipeline
from app.services.summary_budget_controller import (
    calculate_section_word_budgets,
    section_budget_tolerance_words,
)
from app.services.summary_post_processor import validate_summary
from app.services.word_surgery import count_words


NARRATIVE_DOC = (
    '"we remain focused on execution discipline and durable cash conversion." '
    '"pricing and reinvestment decisions will be balanced against margin durability." '
    '"enterprise demand remains healthy where deployment friction is easing." '
    "Management said renewal cohorts are stabilizing, backlog conversion is improving, "
    "and infrastructure spending is being matched against monetization milestones."
)

SECTION_BANKS: dict[str, list[str]] = {
    "Financial Health Rating": [
        "net leverage",
        "liquidity cushion",
        "working capital discipline",
        "refinancing flexibility",
        "cash conversion durability",
        "capex absorption",
        "balance sheet optionality",
        "debt service coverage",
        "downside resilience",
        "funding capacity",
        "capital allocation headroom",
        "liquidity runway",
    ],
    "Executive Summary": [
        "enterprise renewals",
        "pricing discipline",
        "backlog conversion",
        "usage monetization",
        "channel efficiency",
        "mix improvement",
        "cost containment",
        "demand visibility",
        "product attach",
        "execution pacing",
        "cohort retention",
        "margin durability",
    ],
    "Financial Performance": [
        "gross margin mix",
        "volume conversion",
        "price realization",
        "service attach",
        "cost absorption",
        "capacity utilization",
        "sales efficiency",
        "booking quality",
        "renewal timing",
        "cash conversion",
        "expense discipline",
        "segment momentum",
    ],
    "Management Discussion & Analysis": [
        "deployment sequencing",
        "headcount pacing",
        "go to market focus",
        "partner enablement",
        "data center timing",
        "product roadmap",
        "sales prioritization",
        "capital intensity",
        "operational cadence",
        "execution checkpoints",
        "commercial discipline",
        "return thresholds",
    ],
    "Closing Takeaway": [
        "margin stability",
        "free cash flow",
        "renewal conversion",
        "utilization trends",
        "pricing retention",
        "backlog release",
        "capex payback",
        "commercial execution",
        "mix quality",
        "operating leverage",
        "demand durability",
        "capital returns",
    ],
}


def _target_words_from_prompt(prompt: str) -> int:
    match = re.search(r"- Target (\d+) body words\.", prompt)
    base = int(match.group(1)) if match else 120
    return base


def _section_name_from_prompt(prompt: str) -> str:
    match = re.search(r"Write ONLY the body of the '(.+?)' section", prompt)
    if not match:
        raise ValueError("Unable to determine section name from prompt.")
    return match.group(1)


def _health_opening(prompt: str) -> str:
    match = re.search(r"Use this exact opening:\s*(.+?)\n", prompt)
    if match:
        return match.group(1).strip().rstrip(".")
    return "74/100 - Healthy"


def _expand_to_budget(section_name: str, text: str, target_words: int) -> str:
    if count_words(text) >= target_words:
        return text

    bank = SECTION_BANKS.get(section_name, ["filing signal", "operating context"])
    idx = 0
    sentences = [text.strip()]
    templates = {
        "Financial Health Rating": [
            "Balance-sheet markers include {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Funding flexibility still reflects {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Downside resilience depends on {0}, {1}, {2}, {3}, {4}, and {5}.",
        ],
        "Executive Summary": [
            "Priority indicators include {0}, {1}, {2}, {3}, {4}, and {5}.",
            "The investment case still turns on {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Near-term proof points include {0}, {1}, {2}, {3}, {4}, and {5}.",
        ],
        "Financial Performance": [
            "Operating evidence includes {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Quarterly conversion also reflects {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Margin durability still leans on {0}, {1}, {2}, {3}, {4}, and {5}.",
        ],
        "Management Discussion & Analysis": [
            "Execution watchpoints include {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Management priorities still center on {0}, {1}, {2}, {3}, {4}, and {5}.",
            "Return discipline depends on {0}, {1}, {2}, {3}, {4}, and {5}.",
        ],
        "Closing Takeaway": [
            "Decision triggers include {0}, {1}, {2}, {3}, {4}, and {5}.",
            "The next underwriting checks are {0}, {1}, {2}, {3}, {4}, and {5}.",
            "What changes the view is {0}, {1}, {2}, {3}, {4}, and {5}.",
        ],
    }.get(section_name, ["Relevant details include {0}, {1}, {2}, {3}, {4}, and {5}."])
    sentence_idx = 0
    while count_words(" ".join(sentences)) < max(target_words - 14, 1):
        chunk = [bank[(idx + offset) % len(bank)] for offset in range(6)]
        idx += 6
        template = templates[sentence_idx % len(templates)]
        sentence_idx += 1
        sentences.append(template.format(*chunk))

    text_out = " ".join(sentences).strip()
    while count_words(text_out) > target_words and len(sentences) > 1:
        sentences.pop()
        text_out = " ".join(sentences).strip()

    remaining = target_words - count_words(text_out)
    if remaining > 0:
        text_out = f"{text_out} {_exact_tail(section_name, remaining)}".strip()
    return text_out


def _build_risk_entry(
    *,
    risk_name: str,
    mechanism: str,
    impact: str,
    warning: str,
    sentence_templates: tuple[str, str, str],
    clause_sets: tuple[list[str], list[str], list[str]],
    tail_prefix: str,
    target_words: int,
) -> str:
    sentences = [
        sentence_templates[0].format(mechanism=mechanism, clause_a=clause_sets[0][0], clause_b=clause_sets[0][1]),
        sentence_templates[1].format(impact=impact, clause_a=clause_sets[1][0], clause_b=clause_sets[1][1]),
        sentence_templates[2].format(warning=warning, clause_a=clause_sets[2][0], clause_b=clause_sets[2][1]),
    ]
    if target_words > 150:
        sentences.append(
            "That setup matters because "
            + clause_sets[1][2]
            + ", "
            + clause_sets[1][3]
            + ", and "
            + clause_sets[1][4]
            + " if management cannot correct execution quickly."
        )

    extra_clause_sets = [list(clause_sets[0][2:]), list(clause_sets[1][2:]), list(clause_sets[2][2:])]
    if target_words > 150:
        extra_clause_sets[1] = list(clause_sets[1][5:])
    clause_indexes = [0, 0, 0]
    sentence_prefixes = [
        " while also ",
        ", with additional pressure if ",
        ", especially if ",
    ]

    current = " ".join(sentences)
    while count_words(current) < target_words:
        made_progress = False
        for idx in range(min(3, len(sentences))):
            extra_pool = extra_clause_sets[idx]
            extra_idx = clause_indexes[idx]
            if extra_idx >= len(extra_pool):
                continue
            sentences[idx] = sentences[idx][:-1] + sentence_prefixes[idx] + extra_pool[extra_idx] + "."
            clause_indexes[idx] += 1
            current = " ".join(sentences)
            made_progress = True
            if count_words(current) >= target_words:
                break
        if not made_progress:
            break

    current = " ".join(sentences)
    remaining = target_words - count_words(current)
    if remaining > 0:
        filler_tokens = " ".join(f"{tail_prefix}{idx}" for idx in range(remaining))
        sentences[2] = sentences[2][:-1] + f", plus markers {filler_tokens}."
    else:
        sentence_three_tokens = sentences[2].split()
        while count_words(" ".join(sentences)) > target_words and len(sentence_three_tokens) > 12:
            sentence_three_tokens.pop(-2 if len(sentence_three_tokens) > 2 else -1)
        sentences[2] = " ".join(sentence_three_tokens)
        if not sentences[2].endswith("."):
            sentences[2] = sentences[2].rstrip(",") + "."

    return f"**{risk_name}:** {' '.join(sentences)}"


def _retune_risk_entry(entry: str, delta_words: int, tail_prefix: str) -> str:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", entry.strip()) if part.strip()]
    if not sentences:
        return entry

    if delta_words > 0:
        filler_tokens = " ".join(f"{tail_prefix}{idx}" for idx in range(delta_words))
        sentences[-1] = sentences[-1][:-1] + f", plus markers {filler_tokens}."
        return " ".join(sentences)

    min_tokens_per_sentence = 8
    while delta_words < 0:
        made_progress = False
        for sentence_index in range(len(sentences) - 1, -1, -1):
            sentence_tokens = sentences[sentence_index].split()
            if len(sentence_tokens) <= min_tokens_per_sentence:
                continue
            sentence_tokens.pop(-2 if len(sentence_tokens) > 2 else -1)
            sentences[sentence_index] = " ".join(sentence_tokens)
            if not sentences[sentence_index].endswith("."):
                sentences[sentence_index] = sentences[sentence_index].rstrip(",") + "."
            delta_words += 1
            made_progress = True
            if delta_words >= 0:
                break
        if not made_progress:
            break
    return " ".join(sentences)


def _risk_body(target_words: int) -> str:
    risk_count = 2 if target_words < 110 else 3
    risks = [
        {
            "risk_name": "Deferred Enterprise Renewals",
            "mechanism": "larger customers delay deployments or pause seat expansions",
            "impact": "revenue visibility weakens and booked demand takes longer to become recognized revenue",
            "warning": "softer enterprise pipeline conversion or longer implementation backlogs",
            "sentence_templates": (
                "If {mechanism}, backlog conversion can slow through {clause_a} and {clause_b}.",
                "That pathway can mean {impact}, while {clause_a} and {clause_b} before management resets execution.",
                "An early-warning signal is {warning}, with evidence showing up through {clause_a} and {clause_b}.",
            ),
            "clause_sets": (
                [
                    "slower enterprise seat activation",
                    "pushed implementation schedules",
                    "lower services onboarding volume",
                    "weaker multiyear deal commencement",
                    "delayed billing start dates",
                    "reduced cross-sell timing certainty",
                ],
                [
                    "gross margin absorption can soften",
                    "sales efficiency can weaken",
                    "free cash flow timing can slip",
                    "forecast confidence can narrow",
                    "incremental CAC payback can lengthen",
                    "investor patience can shorten",
                ],
                [
                    "renewal cohorts extending",
                    "pipeline aging drifting higher",
                    "launch milestones moving right",
                    "services utilization falling",
                    "backlog release pacing slowing",
                    "fewer expansions converting on schedule",
                ],
            ),
            "tail_prefix": "ren",
        },
        {
            "risk_name": "AI Monetization Lag",
            "mechanism": "infrastructure spend rises faster than monetized usage or paid feature adoption",
            "impact": "operating leverage erodes before demand scales enough to re-tighten unit economics",
            "warning": "rising capex intensity without matching paid usage growth",
            "sentence_templates": (
                "If {mechanism}, return on new capacity can compress through {clause_a} and {clause_b}.",
                "That mismatch can mean {impact}, while {clause_a} and {clause_b} before the revenue model catches up.",
                "An early-warning signal is {warning}, with evidence showing up through {clause_a} and {clause_b}.",
            ),
            "clause_sets": (
                [
                    "heavier inference demand without paid upgrades",
                    "slower enterprise seat monetization",
                    "longer payback on accelerator clusters",
                    "higher serving costs on free workloads",
                    "more expensive product launches",
                    "lower contribution margins on new AI features",
                ],
                [
                    "cash conversion can flatten",
                    "return thresholds can be missed",
                    "buyback capacity can narrow",
                    "depreciation shields can mask weaker economics",
                    "valuation support can move toward lower cash-based multiples",
                    "pricing discipline can face heavier competitive pressure",
                ],
                [
                    "paid workload mix stalling",
                    "token usage outgrowing billable demand",
                    "GPU utilization lagging revenue conversion",
                    "new AI features scaling faster than pricing",
                    "hosting costs rising faster than attach rates",
                    "customer expansion not offsetting compute inflation",
                ],
            ),
            "tail_prefix": "aim",
        },
        {
            "risk_name": "Channel Execution Friction",
            "mechanism": "partner enablement slips or field execution slows in higher-value channels",
            "impact": "backlog builds while billings, deployment cadence, and service attach rates flatten",
            "warning": "slower partner-sourced bookings or weaker attach rates",
            "sentence_templates": (
                "If {mechanism}, commercial throughput can weaken through {clause_a} and {clause_b}.",
                "That disconnect can mean {impact}, while {clause_a} and {clause_b} before the sales motion stabilizes.",
                "An early-warning signal is {warning}, with evidence showing up through {clause_a} and {clause_b}.",
            ),
            "clause_sets": (
                [
                    "slower certification of channel partners",
                    "lower implementation velocity",
                    "reduced attach conversion on bundled offers",
                    "weaker partner-led pipeline coverage",
                    "less efficient territory handoffs",
                    "fewer expansion opportunities closing on time",
                ],
                [
                    "incremental margin capture can fade",
                    "revenue realization can slip",
                    "near-term cash conversion can weaken",
                    "sales productivity can drift lower",
                    "renewal quality can become more uneven",
                    "quarterly visibility can deteriorate faster than bookings imply",
                ],
                [
                    "slower partner sourced bookings",
                    "lower onboarding completion rates",
                    "weaker services attach",
                    "softer conversion from pilot to production",
                    "less consistent regional execution",
                    "higher discounting in indirect channels",
                ],
            ),
            "tail_prefix": "chn",
        },
    ]
    target_per_risk = max(42, target_words // risk_count)
    entries = [
        _build_risk_entry(
            risk_name=risk["risk_name"],
            mechanism=risk["mechanism"],
            impact=risk["impact"],
            warning=risk["warning"],
            sentence_templates=risk["sentence_templates"],
            clause_sets=risk["clause_sets"],
            tail_prefix=risk["tail_prefix"],
            target_words=target_per_risk,
        )
        for risk in risks[:risk_count]
    ]

    entry_prefixes = [risk["tail_prefix"] for risk in risks[:risk_count]]
    current = "\n\n".join(entries)
    delta = int(target_words) - count_words(current)
    adjust_index = len(entries) - 1
    while delta != 0 and adjust_index >= 0:
        before = count_words(entries[adjust_index])
        entries[adjust_index] = _retune_risk_entry(
            entries[adjust_index],
            delta_words=delta,
            tail_prefix=entry_prefixes[adjust_index],
        )
        after = count_words(entries[adjust_index])
        delta -= after - before
        adjust_index -= 1

    return "\n\n".join(entries)


def _exact_tail(section_name: str, remaining: int) -> str:
    prefix = "".join(part[:3] for part in section_name.lower().split())
    if remaining <= 1:
        return f"{prefix}x."
    if remaining == 2:
        return f"Watch {prefix}x."
    tokens = [f"{prefix}{i}" for i in range(max(1, remaining - 2))]
    return "Watchpoints include " + " ".join(tokens) + "."


def _metrics_lines_for_budget(target_words: int) -> str:
    lines = [
        ["→", "Revenue:", "$2.40B", "enterprise", "recurring", "backlog", "conversion"],
        ["→", "Operating", "Income:", "$0.70B", "margin", "discipline", "durability"],
        ["→", "Operating", "Margin:", "29.0%", "mix", "quality", "absorption"],
        ["→", "Free", "Cash", "Flow:", "$0.65B", "self-funded", "investment", "capacity"],
        ["→", "Current", "Ratio:", "2.3x", "liquidity", "cushion", "flexibility"],
    ]
    cursor = 0
    while count_words("\n".join(" ".join(line) for line in lines)) < target_words:
        line = lines[cursor % len(lines)]
        line.append(f"m{cursor}")
        cursor += 1

    while count_words("\n".join(" ".join(line) for line in lines)) > target_words:
        for line in reversed(lines):
            if len(line) > 4:
                line.pop()
                break
        else:
            break

    return "\n".join(" ".join(line) for line in lines)


def _section_body(section_name: str, prompt: str) -> str:
    target_words = _target_words_from_prompt(prompt)
    if section_name == "Financial Health Rating":
        base = (
            f"{_health_opening(prompt)}. Liquidity, leverage, and cash conversion still support operating flexibility. "
            "The filing indicates that balance-sheet capacity remains intact even while infrastructure investment continues."
        )
        base = (
            f"{base} Balance-sheet markers include covenant headroom, debt service coverage, refinancing flexibility, funding capacity, liquidity runway, and working capital discipline. "
            "Residual downside still depends on capex absorption, capital allocation headroom, supplier terms, receivables control, interest coverage, and tax cash timing."
        )
        remaining = target_words - count_words(base)
        return f"{base} {_exact_tail(section_name, remaining)}".strip() if remaining > 0 else base
    if section_name == "Executive Summary":
        base = (
            'The core question is whether growth can remain efficient while management funds the next investment cycle. '
            '"we remain focused on execution discipline and durable cash conversion." '
            "Financial Performance below tests that tension with the quarter's strongest evidence rather than repeating headline metrics."
        )
        remaining = target_words - count_words(base)
        return f"{base} {_exact_tail(section_name, remaining)}".strip() if remaining > 0 else base
    if section_name == "Financial Performance":
        base = (
            "Revenue quality improved as backlog converted more cleanly and pricing held in the higher-value portions of demand. "
            '"enterprise demand remains healthy where deployment friction is easing." '
            "Cash conversion remained good enough to keep the investment case tied to execution rather than balance-sheet stress."
        )
        remaining = target_words - count_words(base)
        return f"{base} {_exact_tail(section_name, remaining)}".strip() if remaining > 0 else base
    if section_name == "Management Discussion & Analysis":
        base = (
            '"pricing and reinvestment decisions will be balanced against margin durability." '
            "Management is sequencing infrastructure spend around visible customer demand instead of pushing capacity ahead of proof points. "
            "That matters because deployment pacing, sales focus, and return thresholds determine whether spending becomes durable operating leverage."
        )
        remaining = target_words - count_words(base)
        return f"{base} {_exact_tail(section_name, remaining)}".strip() if remaining > 0 else base
    if section_name == "Risk Factors":
        return _risk_body(target_words)
    if section_name == "Closing Takeaway":
        base = (
            "I HOLD Continuous V2 Smoke Corp because the core franchise is funding investment without obvious balance-sheet strain. "
            "I would upgrade to BUY if renewal conversion and margin stability both improve over the next two quarters. "
            "I would downgrade to SELL if free cash flow weakens materially while capex intensity keeps rising."
        )
        remaining = target_words - count_words(base)
        return f"{base} {_exact_tail(section_name, remaining)}".strip() if remaining > 0 else base
    raise ValueError(f"Unhandled section: {section_name}")


class FakeSummaryClient:
    def research_company_intelligence_with_web(self, **_: Any) -> dict[str, Any]:
        return {
            "business_identity": "Continuous V2 Smoke Corp sells enterprise infrastructure software with usage-linked monetization.",
            "competitive_moat": "High switching costs and embedded workflow adoption keep renewals durable.",
            "primary_kpis": [
                {
                    "name": "Enterprise Renewal Rate",
                    "why_it_matters": "It determines whether backlog converts into durable revenue.",
                    "filing_search_terms": ["renewal", "retention", "backlog"],
                    "metric_type": "percentage",
                },
                {
                    "name": "Free Cash Flow",
                    "why_it_matters": "It shows whether investment intensity is still self-funded.",
                    "filing_search_terms": ["free cash flow", "operating cash flow"],
                    "metric_type": "currency",
                },
            ],
            "key_competitors": ["Datadog", "Snowflake"],
            "competitive_dynamics": "Competition is strongest where feature expansion meets pricing discipline.",
            "investor_focus_areas": [
                "Can demand stay efficient while capex rises?",
                "Is backlog converting into monetized usage?",
                "Does free cash flow still fund reinvestment?",
            ],
            "industry_kpi_norms": "Healthy operators sustain renewals, hold pricing, and fund growth internally.",
            "raw_brief": "Smoke-test intelligence profile.",
        }

    def research_company_intelligence(self, **kwargs: Any) -> dict[str, Any]:
        return self.research_company_intelligence_with_web(**kwargs)

    def research_company_background(self, **_: Any) -> str:
        return "Continuous V2 Smoke Corp background."

    def research_company_current_context(self, **_: Any) -> str:
        return "Demand remains healthy, but management is pacing investment against monetization signals."

    def analyze_filing_with_context(self, **_: Any) -> dict[str, Any]:
        return {
            "central_tension": "Can management keep cash conversion durable while it funds the next AI infrastructure cycle?",
            "tension_evidence": "Demand is improving where deployment friction is easing, but spend is still elevated. The quarter matters because monetization must keep pace with infrastructure intensity.",
            "kpi_findings": [
                {
                    "kpi_name": "Enterprise Renewal Rate",
                    "current_value": "Stable",
                    "prior_value": "Stable",
                    "change": "Flat",
                    "change_direction": "stable",
                    "insight": "Stable renewals keep backlog conversion credible while spending rises.",
                    "source_quote": "enterprise demand remains healthy where deployment friction is easing.",
                },
                {
                    "kpi_name": "Free Cash Flow",
                    "current_value": "$650M",
                    "prior_value": "$610M",
                    "change": "+$40M",
                    "change_direction": "improved",
                    "insight": "Cash flow still funds reinvestment, which reduces financing risk.",
                    "source_quote": "we remain focused on execution discipline and durable cash conversion.",
                },
            ],
            "period_specific_insights": [
                "Infrastructure spending is being sequenced against monetization milestones.",
                "Enterprise deployment friction is easing.",
                "Backlog conversion improved during the period.",
            ],
            "management_quotes": [
                {
                    "quote": "we remain focused on execution discipline and durable cash conversion.",
                    "attribution": "Management",
                    "topic": "cash conversion",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "pricing and reinvestment decisions will be balanced against margin durability.",
                    "attribution": "Management",
                    "topic": "margin discipline",
                    "suggested_section": "Management Discussion & Analysis",
                },
                {
                    "quote": "enterprise demand remains healthy where deployment friction is easing.",
                    "attribution": "Management",
                    "topic": "demand",
                    "suggested_section": "Financial Performance",
                },
            ],
            "management_strategy_summary": "Management is pacing investment against monetization and renewal health.",
            "company_specific_risks": [
                {
                    "risk_name": "Deferred Enterprise Renewals",
                    "mechanism": "Large customers can delay deployments and slow revenue conversion.",
                    "early_warning": "Weaker renewal timing or lower pipeline conversion.",
                    "evidence_from_filing": "deployment friction is easing",
                },
                {
                    "risk_name": "AI Monetization Lag",
                    "mechanism": "Capex can rise faster than paid usage.",
                    "early_warning": "Higher capex intensity without monetized usage growth.",
                    "evidence_from_filing": "investment is being matched against monetization milestones",
                },
                {
                    "risk_name": "Channel Execution Friction",
                    "mechanism": "Partner execution can slow service attach and billings.",
                    "early_warning": "Slower partner-sourced bookings.",
                    "evidence_from_filing": "backlog conversion is improving",
                },
            ],
            "evidence_map": {
                "Executive Summary": [
                    "Cash conversion remains durable.",
                    "Management is balancing reinvestment against margin durability.",
                ],
                "Financial Performance": [
                    "Backlog conversion improved.",
                    "Pricing held in higher-value demand.",
                ],
                "Management Discussion & Analysis": [
                    "Investment pacing is tied to monetization milestones.",
                    "Deployment friction is easing.",
                ],
                "Risk Factors": [
                    "Renewals can be deferred.",
                    "Capex can outpace monetization.",
                ],
                "Closing Takeaway": [
                    "The franchise still funds its own investment cycle.",
                    "Renewal conversion and margin stability are the key triggers.",
                ],
            },
        }

    def compose_summary(self, *, prompt: str, **_: Any) -> str:
        return _section_body(_section_name_from_prompt(prompt), prompt)


def _extract_section_counts(summary_text: str, include_health_rating: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for section_name in SECTION_ORDER:
        if section_name == "Financial Health Rating" and not include_health_rating:
            continue
        pattern = rf"##\s*{re.escape(section_name)}\s*\n+([\s\S]*?)(?=\n##\s|\Z)"
        match = re.search(pattern, summary_text, flags=re.IGNORECASE)
        if match:
            counts[section_name] = count_words(match.group(1).strip())
    return counts


def build_smoke_report(target_length: int) -> dict[str, Any]:
    section_budgets = calculate_section_word_budgets(
        target_length,
        include_health_rating=True,
    )
    metrics_lines = _metrics_lines_for_budget(int(section_budgets.get("Key Metrics", 0) or 0))
    progress_events: list[tuple[str, int]] = []

    with patch("app.services.summary_agents._read_intelligence_cache", lambda _key: None), patch(
        "app.services.summary_agents._write_intelligence_cache",
        lambda *args, **kwargs: None,
    ):
        result = run_summary_agent_pipeline(
            company_name="Continuous V2 Smoke Corp",
            ticker="CV2",
            sector="Technology",
            industry="Infrastructure Software",
            filing_type="10-Q",
            filing_period="2025-09-30",
            filing_date="2025-09-30",
            target_length=int(target_length),
            context_excerpt=NARRATIVE_DOC,
            mda_excerpt=NARRATIVE_DOC,
            risk_factors_excerpt=(
                "Deferred renewals, monetization lag, and partner execution remain the key company-specific risks."
            ),
            company_kpi_context=(
                "Enterprise Renewal Rate: stable.\n"
                "Free Cash Flow: $650M."
            ),
            financial_snapshot=(
                "Revenue $2.4B. Operating income $0.70B. Free cash flow $0.65B. "
                "Cash balance $0.43B. Total debt $0.96B."
            ),
            metrics_lines=metrics_lines,
            prior_period_delta_block=(
                "Revenue improved modestly year over year while free cash flow conversion stayed resilient."
            ),
            filing_language_snippets=NARRATIVE_DOC,
            calculated_metrics={},
            health_score_data={"overall_score": 74, "score_band": "Healthy"},
            include_health_rating=True,
            section_budgets=section_budgets,
            preferences=None,
            persona_name=None,
            persona_requested=False,
            investor_focus=None,
            openai_client=FakeSummaryClient(),
            progress_callback=lambda status, pct: progress_events.append((status, pct)),
        )

    validation = validate_summary(
        result.summary_text,
        target_words=int(target_length),
        section_budgets=section_budgets,
        include_health_rating=True,
        risk_factors_excerpt=(
            "renewals monetization capex deployment pipeline conversion partner execution"
        ),
    )

    final_word_count = count_words(result.summary_text)
    section_counts = _extract_section_counts(result.summary_text, include_health_rating=True)
    section_ranges = {
        section_name: {
            "lower": max(
                1,
                budget - int(section_budget_tolerance_words(section_name, int(budget))),
            ),
            "upper": budget + int(section_budget_tolerance_words(section_name, int(budget))),
        }
        for section_name, budget in section_budgets.items()
    }

    return {
        "passed": bool(validation.passed),
        "target_length": int(target_length),
        "final_word_count": int(final_word_count),
        "lower_bound": int(validation.lower_bound),
        "upper_bound": int(validation.upper_bound),
        "section_budgets": section_budgets,
        "section_ranges": section_ranges,
        "section_word_counts": section_counts,
        "llm_calls": int(result.total_llm_calls),
        "progress_events": progress_events,
        "metadata": result.metadata,
        "global_failures": list(validation.global_failures),
        "section_failures": [
            {"section_name": failure.section_name, "message": failure.message}
            for failure in validation.section_failures
        ],
        "summary_text": result.summary_text,
    }


def run_smoke(target_length: int, print_summary: bool, emit_json: bool) -> int:
    report = build_smoke_report(target_length)

    if emit_json:
        payload = dict(report)
        if not print_summary:
            payload.pop("summary_text", None)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1

    print("Smoke status:", "PASS" if report["passed"] else "FAIL")
    print(f"Final word count: {report['final_word_count']}")
    print(f"Target band: {report['lower_bound']}-{report['upper_bound']}")
    print(f"LLM calls: {report['llm_calls']}")
    print(f"Progress events: {report['progress_events']}")
    print("Metadata:", json.dumps(report["metadata"], indent=2, sort_keys=True))
    if report["global_failures"] or report["section_failures"]:
        print("Validation failures:")
        for failure in report["global_failures"]:
            print(f"- {failure}")
        for failure in report["section_failures"]:
            print(f"- {failure['section_name']}: {failure['message']}")
    if print_summary:
        print("\nGenerated summary:\n")
        print(report["summary_text"])

    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Continuous Scaling Summary V2.")
    parser.add_argument("--target", type=int, default=900, help="Requested total word count.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report.",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Do not print the generated summary body.",
    )
    args = parser.parse_args()
    return run_smoke(
        target_length=int(args.target),
        print_summary=not args.no_summary,
        emit_json=bool(args.json),
    )


if __name__ == "__main__":
    raise SystemExit(main())
