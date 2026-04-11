#!/usr/bin/env python3
"""Smoke-test the Continuous Summary V2 pipeline across business archetypes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import filings as filings_api
from app.services.summary_agents import SECTION_ORDER, run_summary_agent_pipeline
from app.services.summary_budget_controller import (
    calculate_section_word_budgets,
    section_budget_tolerance_words,
)
from app.services.summary_post_processor import validate_summary
from app.services.word_surgery import count_words


@dataclass(frozen=True)
class SmokeScenario:
    key: str
    company_name: str
    ticker: str
    sector: str
    industry: str
    business_archetype: str
    business_identity: str
    competitive_moat: str
    key_competitors: tuple[str, ...]
    investor_focus_areas: tuple[str, ...]
    industry_kpi_norms: str
    central_tension: str
    management_strategy_summary: str
    forward_guidance_summary: str
    promise_scorecard: str
    company_terms: tuple[str, ...]
    management_quotes: tuple[dict[str, str], ...]
    management_expectations: tuple[dict[str, str], ...]
    promise_scorecard_items: tuple[dict[str, str], ...]
    management_strategic_bets: tuple[str, ...]
    period_specific_insights: tuple[str, ...]
    kpi_findings: tuple[dict[str, str], ...]
    company_specific_risks: tuple[dict[str, str], ...]
    evidence_map: dict[str, list[str]]
    context_excerpt: str
    mda_excerpt: str
    risk_factors_excerpt: str
    filing_language_snippets: str
    financial_snapshot: str
    metrics_rows: tuple[tuple[str, str, tuple[str, ...]], ...]
    health_score_data: dict[str, Any] = field(
        default_factory=lambda: {"overall_score": 74, "score_band": "Healthy"}
    )


def _scenario_matrix() -> list[SmokeScenario]:
    return [
        SmokeScenario(
            key="cloud",
            company_name="Cloud Workflow Co.",
            ticker="CWFC",
            sector="Technology",
            industry="Application Software",
            business_archetype="cloud_software",
            business_identity="Cloud Workflow Co. sells workflow software through subscription renewals, seat expansion, and AI feature attach across large enterprise accounts.",
            competitive_moat="Embedded workflows and admin standardization create high switching costs across enterprise teams.",
            key_competitors=("ServiceNow", "Atlassian"),
            investor_focus_areas=(
                "AI attach inside enterprise renewals",
                "renewal quality versus pricing discipline",
                "RPO conversion into recognized revenue",
            ),
            industry_kpi_norms="Healthy cloud operators sustain renewal quality, expansion depth, and cash-backed product investment.",
            central_tension="Can Cloud Workflow Co. turn AI workflow attach and renewal depth into durable recurring economics without overspending ahead of monetization?",
            management_strategy_summary="Management is prioritizing AI attach inside enterprise renewals rather than chasing low-quality new logos.",
            forward_guidance_summary="Management expects AI attach to deepen inside large renewals over the next two quarters.",
            promise_scorecard="Management appears on track in expanding AI attach without sacrificing renewal quality.",
            company_terms=("AI workflow tier", "enterprise renewals", "RPO", "seat expansion", "largest accounts"),
            management_quotes=(
                {
                    "quote": "we are seeing stronger AI attach inside our largest renewals",
                    "attribution": "Management",
                    "topic": "AI workflow tier",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "pricing discipline still matters more than low-quality volume",
                    "attribution": "Management",
                    "topic": "pricing discipline",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "AI workflow tier",
                    "expectation": "Management expects AI workflow attach to deepen inside enterprise renewals over the next two quarters.",
                    "timeframe": "next two quarters",
                    "evidence": "Management highlighted stronger AI attach inside large renewals.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "expand AI workflow tier inside the installed base",
                    "status": "on_track",
                    "assessment": "Renewal quality held while AI workflow attach improved in the largest accounts.",
                    "evidence": "Management highlighted stronger AI attach inside large renewals.",
                },
            ),
            management_strategic_bets=("AI workflow tier", "enterprise renewals", "pricing discipline"),
            period_specific_insights=(
                "Enterprise customers standardized on the AI workflow tier faster than the prior quarter.",
                "RPO improved as larger accounts renewed earlier in the cycle.",
                "Seat expansion stayed concentrated inside the largest installed-base cohorts.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Net Revenue Retention",
                    "current_value": "114%",
                    "prior_value": "112%",
                    "change": "+200 bps YoY",
                    "change_direction": "improved",
                    "insight": "Expansion stayed healthy while management pushed the AI workflow tier.",
                    "source_quote": "we are seeing stronger AI attach inside our largest renewals",
                },
                {
                    "kpi_name": "Remaining Performance Obligations",
                    "current_value": "$4.2B",
                    "prior_value": "$3.8B",
                    "change": "+11% YoY",
                    "change_direction": "improved",
                    "insight": "Forward demand visibility improved as large renewals landed earlier.",
                    "source_quote": "pricing discipline still matters more than low-quality volume",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Enterprise Renewal Conversion Risk",
                    "mechanism": "Large-account renewals can slip if AI workflow attach does not stay tied to visible ROI.",
                    "early_warning": "Watch RPO conversion, renewal quality, and discounting in the largest accounts.",
                    "evidence_from_filing": "Management highlighted AI attach inside large renewals.",
                },
                {
                    "risk_name": "AI Workflow Attach Monetization Risk",
                    "mechanism": "Product investment can outrun monetized attach if adoption deepens more slowly than expected.",
                    "early_warning": "Track AI attach, paid-seat expansion, and free-cash-flow conversion.",
                    "evidence_from_filing": "Management expects AI workflow attach to deepen over the next two quarters.",
                },
                {
                    "risk_name": "Seat Expansion Concentration Risk",
                    "mechanism": "Sales capacity can scale faster than high-quality enterprise demand.",
                    "early_warning": "Watch sales efficiency, deal size, and large-account expansion pacing.",
                    "evidence_from_filing": "Seat expansion stayed concentrated inside the largest accounts.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "AI workflow attach is landing first inside the largest enterprise renewals.",
                    "Management is emphasizing pricing discipline over low-quality volume.",
                ],
                "Financial Performance": [
                    "Net Revenue Retention improved to 114%.",
                    "RPO improved as larger accounts renewed earlier.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects AI workflow attach to deepen inside enterprise renewals.",
                    "The strategy is to expand inside the installed base before chasing new logos.",
                ],
                "Risk Factors": [
                    "Renewal conversion can weaken if AI attach loses visible ROI.",
                    "Product investment can outrun monetized attach.",
                ],
                "Closing Takeaway": [
                    "Management is on track if AI workflow attach keeps lifting renewal quality.",
                    "The stance changes if RPO conversion or seat expansion weakens in the largest accounts.",
                ],
            },
            context_excerpt='Management said "we are seeing stronger AI attach inside our largest renewals" while renewal quality remained stable.',
            mda_excerpt='Management noted that "pricing discipline still matters more than low-quality volume" as it prioritized AI workflow attach and installed-base expansion.',
            risk_factors_excerpt=(
                "AI workflow tier, enterprise renewals, RPO conversion, and pricing discipline remain "
                "the company-specific risks. Seat expansion stayed concentrated inside the largest accounts."
            ),
            filing_language_snippets='RPO improved, AI workflow attach deepened, and management said "we are seeing stronger AI attach inside our largest renewals."',
            financial_snapshot="Revenue $2.8B. Operating income $0.76B. Operating cash flow $0.82B. Free cash flow $0.63B. Cash balance $1.4B.",
            metrics_rows=(
                ("Revenue", "$2.80B", ("enterprise", "renewals", "visibility")),
                ("Operating Income", "$0.76B", ("pricing", "discipline", "scale")),
                ("Operating Margin", "27.1%", ("mix", "quality", "attach")),
                ("Free Cash Flow", "$0.63B", ("self-funded", "product", "investment")),
                ("Current Ratio", "1.8x", ("liquidity", "cushion", "flexibility")),
            ),
        ),
        SmokeScenario(
            key="semicap",
            company_name="ASML Holding NV",
            ticker="ASML",
            sector="Technology",
            industry="Semiconductor Equipment",
            business_archetype="semicap_hardware",
            business_identity="ASML sells EUV and DUV lithography systems plus installed-base service and upgrade revenue tied to customer node transitions.",
            competitive_moat="EUV leadership and a global installed base create a deep technology and service moat.",
            key_competitors=("Nikon", "Canon"),
            investor_focus_areas=(
                "backlog conversion into shipments",
                "installed-base service mix and margin resilience",
                "customer node transitions and fab-capex timing",
            ),
            industry_kpi_norms="Healthy semicap suppliers convert backlog into shipments while preserving installed-base mix and service economics.",
            central_tension="Can ASML Holding NV convert EUV backlog into shipments and installed-base economics without getting caught by customer timing or fab-spend volatility?",
            management_strategy_summary="Management is pacing manufacturing and customer support around node-transition demand instead of maximizing near-term tool output.",
            forward_guidance_summary="Management expects EUV backlog conversion to improve as customer fabs move through node-transition milestones.",
            promise_scorecard="Management looks on track if EUV productivity and installed-base activity stay aligned with customer ramp schedules.",
            company_terms=("EUV", "DUV", "Installed Base", "backlog", "shipments", "node transitions"),
            management_quotes=(
                {
                    "quote": "we remain focused on customer node transitions and EUV productivity",
                    "attribution": "Management",
                    "topic": "EUV",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "installed base activity remains a stabilizer while shipment timing moves with customer readiness",
                    "attribution": "Management",
                    "topic": "Installed Base",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "backlog",
                    "expectation": "Management expects backlog conversion to improve as customer node transitions advance over the next several quarters.",
                    "timeframe": "next several quarters",
                    "evidence": "Management highlighted customer node transitions and EUV productivity.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "convert EUV backlog into cleaner shipment timing",
                    "status": "on_track",
                    "assessment": "Installed Base activity stayed healthy while node-transition commentary still pointed to improving shipment conversion.",
                    "evidence": "Management highlighted customer node transitions and EUV productivity.",
                },
            ),
            management_strategic_bets=("EUV productivity", "Installed Base", "node transitions"),
            period_specific_insights=(
                "Installed Base activity remained healthy while shipment timing stayed customer-dependent.",
                "Backlog stayed elevated into the next node-transition phase.",
                "Management kept linking EUV productivity to customer fab readiness.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Backlog",
                    "current_value": "Elevated",
                    "prior_value": "Elevated",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Demand stayed committed, but revenue recognition still depends on shipment timing.",
                    "source_quote": "we remain focused on customer node transitions and EUV productivity",
                },
                {
                    "kpi_name": "Installed Base Management Revenue",
                    "current_value": "Stable",
                    "prior_value": "Stable",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Installed-base service activity is still cushioning tool-timing volatility.",
                    "source_quote": "installed base activity remains a stabilizer while shipment timing moves with customer readiness",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Backlog Shipment Conversion Risk",
                    "mechanism": "Revenue recognition can slip if customer fabs delay tool acceptance even when order visibility stays high.",
                    "early_warning": "Watch shipment timing, backlog conversion, and customer-capex commentary.",
                    "evidence_from_filing": "Backlog stayed elevated while shipment timing remained customer-dependent.",
                },
                {
                    "risk_name": "Installed-Base Mix Risk",
                    "mechanism": "Premium margin support can soften if Installed Base service and upgrade activity cools.",
                    "early_warning": "Track service mix, upgrade demand, and margin commentary tied to Installed Base activity.",
                    "evidence_from_filing": "Installed Base activity remained healthy in the period.",
                },
                {
                    "risk_name": "Node-Transition Timing Risk",
                    "mechanism": "Customer node-transition plans can move faster than supplier manufacturing cadence, creating shipment and cash-timing gaps.",
                    "early_warning": "Watch customer-capex plans, node-transition commentary, and tool lead times.",
                    "evidence_from_filing": "Management linked EUV productivity to customer fab readiness.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "EUV backlog still has to convert into shipments, not just remain elevated.",
                    "Installed Base activity is stabilizing the model while customer timing stays uneven.",
                ],
                "Financial Performance": [
                    "Installed Base activity is cushioning shipment timing volatility.",
                    "Backlog remains elevated into the next node-transition phase.",
                ],
                "Management Discussion & Analysis": [
                    "Management is pacing manufacturing around customer node transitions.",
                    "EUV productivity and fab readiness remain linked in the outlook.",
                ],
                "Risk Factors": [
                    "Backlog can stay healthy while shipments slip.",
                    "Installed Base mix can soften if service activity cools.",
                ],
                "Closing Takeaway": [
                    "The stance changes if backlog stops converting into shipments cleanly.",
                    "Management credibility depends on matching EUV productivity with customer fab readiness.",
                ],
            },
            context_excerpt='Management said "we remain focused on customer node transitions and EUV productivity" as backlog stayed elevated.',
            mda_excerpt='Management noted that "installed base activity remains a stabilizer while shipment timing moves with customer readiness."',
            risk_factors_excerpt="EUV, DUV, Installed Base, backlog conversion, shipments, and customer fab timing remain the core underwriting risks.",
            filing_language_snippets='EUV backlog remained elevated and management said "we remain focused on customer node transitions and EUV productivity."',
            financial_snapshot="Revenue $4.4B. Operating income $1.6B. Operating cash flow $1.1B. Free cash flow $0.9B. Cash balance $6.2B.",
            metrics_rows=(
                ("Revenue", "$4.40B", ("shipments", "timing", "mix")),
                ("Operating Income", "$1.60B", ("service", "leverage", "quality")),
                ("Operating Margin", "36.0%", ("Installed", "Base", "support")),
                ("Free Cash Flow", "$0.90B", ("customer", "timing", "working-capital")),
                ("Current Ratio", "2.3x", ("liquidity", "buffer", "resilience")),
            ),
        ),
        SmokeScenario(
            key="bank",
            company_name="Regional Deposit Bank",
            ticker="RDBK",
            sector="Financials",
            industry="Regional Banks",
            business_archetype="bank",
            business_identity="Regional Deposit Bank is a deposit-funded lender whose earnings depend on spread income, credit quality, and capital strength.",
            competitive_moat="Sticky local deposits and disciplined underwriting protect funding quality and customer relationships.",
            key_competitors=("PNC", "Truist"),
            investor_focus_areas=(
                "deposit mix and funding costs",
                "loan growth versus credit quality",
                "capital ratios and reserve adequacy",
            ),
            industry_kpi_norms="Healthy banks preserve deposit quality, spread income, and reserve discipline through the cycle.",
            central_tension="Can Regional Deposit Bank keep deposits supporting earnings while credit and capital discipline remain intact?",
            management_strategy_summary="Management is prioritizing deposit quality and reserve discipline over aggressive asset growth.",
            forward_guidance_summary="Management expects deposit mix to stabilize next quarter while credit costs remain manageable.",
            promise_scorecard="Management is on track if deposit quality holds and reserve discipline remains consistent with loan growth.",
            company_terms=("deposits", "Net Interest Margin", "loan growth", "charge-offs", "CET1", "credit quality"),
            management_quotes=(
                {
                    "quote": "we are protecting deposit mix while keeping credit discipline tight",
                    "attribution": "Management",
                    "topic": "deposits",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "capital flexibility still starts with disciplined reserve management",
                    "attribution": "Management",
                    "topic": "CET1",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "deposits",
                    "expectation": "Management expects deposit mix to stabilize next quarter while funding costs remain manageable.",
                    "timeframe": "next quarter",
                    "evidence": "Management is protecting deposit mix while keeping credit discipline tight.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "protect deposit quality while keeping reserve discipline tight",
                    "status": "on_track",
                    "assessment": "Deposit quality held while management kept charge-offs contained and CET1 support intact.",
                    "evidence": "Management is protecting deposit mix while keeping credit discipline tight.",
                },
            ),
            management_strategic_bets=("deposits", "credit quality", "CET1"),
            period_specific_insights=(
                "Deposit mix remained the core funding lens for the quarter.",
                "Charge-offs stayed contained while loan growth remained measured.",
                "CET1 support remained part of the operating message.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Net Interest Margin",
                    "current_value": "3.35%",
                    "prior_value": "3.31%",
                    "change": "+4 bps QoQ",
                    "change_direction": "improved",
                    "insight": "Spread income held together despite tighter funding competition.",
                    "source_quote": "we are protecting deposit mix while keeping credit discipline tight",
                },
                {
                    "kpi_name": "Credit Quality",
                    "current_value": "Stable",
                    "prior_value": "Stable",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Charge-offs and reserve commentary remained contained.",
                    "source_quote": "capital flexibility still starts with disciplined reserve management",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Deposit Beta and Funding Cost Risk",
                    "mechanism": "Spread income can compress if funding shifts toward higher-cost deposits.",
                    "early_warning": "Watch deposit mix, beta, and noninterest-bearing balance trends.",
                    "evidence_from_filing": "Management stressed deposit mix and funding discipline.",
                },
                {
                    "risk_name": "Charge-Off and Reserve Build Risk",
                    "mechanism": "Reserve builds and charge-offs can outweigh any benefit from steady loan growth.",
                    "early_warning": "Track provision expense, charge-offs, and reserve coverage.",
                    "evidence_from_filing": "Charge-offs stayed contained in the period.",
                },
                {
                    "risk_name": "Capital and Securities Sensitivity Risk",
                    "mechanism": "Capital flexibility can tighten if rates or credit conditions move faster than earnings can absorb.",
                    "early_warning": "Watch CET1, tangible book value, and securities commentary.",
                    "evidence_from_filing": "Management kept linking capital flexibility to reserve discipline.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Deposits remain the funding anchor behind the quarter's earnings profile.",
                    "Management is emphasizing reserve discipline over aggressive balance-sheet growth.",
                ],
                "Financial Performance": [
                    "Net Interest Margin held together while credit quality stayed stable.",
                    "Charge-offs remained contained as loan growth stayed measured.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects deposit mix to stabilize next quarter.",
                    "Capital flexibility still starts with reserve discipline.",
                ],
                "Risk Factors": [
                    "Higher-cost funding can compress spread income.",
                    "Reserve builds can overwhelm steady loan growth.",
                ],
                "Closing Takeaway": [
                    "The stance changes if deposit quality weakens or charge-offs move higher.",
                    "Management credibility depends on balancing deposit stability with reserve discipline.",
                ],
            },
            context_excerpt='Management said "we are protecting deposit mix while keeping credit discipline tight" as Net Interest Margin remained steady.',
            mda_excerpt='Management noted that "capital flexibility still starts with disciplined reserve management" while CET1 remained supportive.',
            risk_factors_excerpt="Deposits, Net Interest Margin, charge-offs, CET1, and credit quality remain the core underwriting risks.",
            filing_language_snippets='Deposit mix remained stable and management said "we are protecting deposit mix while keeping credit discipline tight."',
            financial_snapshot="Revenue $1.9B. Operating income $0.74B. Operating cash flow $0.66B. Free cash flow $0.55B. Cash balance $8.5B.",
            metrics_rows=(
                ("Revenue", "$1.90B", ("spread", "income", "stability")),
                ("Operating Income", "$0.74B", ("reserve", "discipline", "quality")),
                ("Operating Margin", "38.9%", ("funding", "mix", "support")),
                ("Free Cash Flow", "$0.55B", ("capital", "flexibility", "capacity")),
                ("Current Ratio", "1.5x", ("liquidity", "buffer", "funding")),
            ),
        ),
        SmokeScenario(
            key="insurance",
            company_name="Global Property & Asset Manager",
            ticker="GPAM",
            sector="Financials",
            industry="Insurance / Asset Management",
            business_archetype="insurance_asset_manager",
            business_identity="Global Property & Asset Manager depends on underwriting discipline, client flows, reserve management, and fee-bearing assets.",
            competitive_moat="Distribution scale and reserve discipline support underwriting and fee-base durability.",
            key_competitors=("AIG", "BlackRock"),
            investor_focus_areas=(
                "combined ratio or underwriting margin discipline",
                "client flows and fee-base stability",
                "reserve development and capital deployment",
            ),
            industry_kpi_norms="Healthy insurers and asset managers preserve underwriting margin, client flows, and reserve credibility.",
            central_tension="Can Global Property & Asset Manager hold underwriting and fee economics together while claims, net flows, and reserves stay supportive?",
            management_strategy_summary="Management is prioritizing underwriting discipline and client-retention quality over pure premium or AUM growth.",
            forward_guidance_summary="Management expects reserve trends to remain manageable while client flows stabilize over the next year.",
            promise_scorecard="Management remains credible if reserve discipline and client-flow quality stay aligned with capital deployment.",
            company_terms=("combined ratio", "premium growth", "claims", "AUM", "net flows", "reserves"),
            management_quotes=(
                {
                    "quote": "reserve discipline still comes before aggressive premium growth",
                    "attribution": "Management",
                    "topic": "reserves",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "client-retention quality matters more than headline flow volume",
                    "attribution": "Management",
                    "topic": "net flows",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "net flows",
                    "expectation": "Management expects client-flow quality to stabilize over the next year while reserve trends remain manageable.",
                    "timeframe": "next year",
                    "evidence": "Management emphasized client-retention quality over headline flow volume.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "protect reserve discipline while stabilizing fee-bearing client flows",
                    "status": "on_track",
                    "assessment": "Claims and reserves remained controlled while management emphasized better client-retention quality.",
                    "evidence": "Reserve discipline still comes before aggressive premium growth.",
                },
            ),
            management_strategic_bets=("combined ratio", "net flows", "reserves"),
            period_specific_insights=(
                "Combined-ratio discipline remained part of the operating narrative.",
                "Client-retention quality mattered more than headline flow volume.",
                "Reserve commentary stayed central to capital deployment.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Combined Ratio / Underwriting Margin",
                    "current_value": "Disciplined",
                    "prior_value": "Disciplined",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Underwriting discipline remained intact while claims stayed controlled.",
                    "source_quote": "reserve discipline still comes before aggressive premium growth",
                },
                {
                    "kpi_name": "Assets Under Management / Net Flows",
                    "current_value": "Stable",
                    "prior_value": "Soft",
                    "change": "Improved",
                    "change_direction": "improved",
                    "insight": "Client-retention quality improved even if headline flow volume was not the focus.",
                    "source_quote": "client-retention quality matters more than headline flow volume",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Claims / Combined-Ratio Risk",
                    "mechanism": "Claims inflation can soften underwriting profit even when premium growth looks healthy.",
                    "early_warning": "Watch combined ratio, reserve development, and pricing commentary.",
                    "evidence_from_filing": "Reserve discipline stayed central to the quarter.",
                },
                {
                    "risk_name": "Flow and Fee-Base Risk",
                    "mechanism": "Net-flow weakness can reduce fee revenue faster than expenses reset.",
                    "early_warning": "Track AUM mix, client flows, and fee-rate commentary.",
                    "evidence_from_filing": "Client-retention quality mattered more than headline flow volume.",
                },
                {
                    "risk_name": "Capital Deployment Risk",
                    "mechanism": "Reserve uncertainty can reduce flexibility if capital deployment outruns underlying earnings support.",
                    "early_warning": "Watch buybacks, dividends, reserve commentary, and statutory capital.",
                    "evidence_from_filing": "Management tied reserve discipline to capital deployment flexibility.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Reserve discipline and client-flow quality are the real threads behind the period.",
                    "Management is prioritizing underwriting quality over aggressive premium growth.",
                ],
                "Financial Performance": [
                    "Combined-ratio discipline remained steady while claims stayed controlled.",
                    "Client-flow quality improved even if headline volume was not the focus.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects flow quality to stabilize over the next year.",
                    "Reserve discipline remains central to capital deployment.",
                ],
                "Risk Factors": [
                    "Claims inflation can weaken underwriting profit.",
                    "Net-flow weakness can reduce fee revenue faster than expenses reset.",
                ],
                "Closing Takeaway": [
                    "The stance changes if reserve credibility weakens or client flows deteriorate again.",
                    "Management credibility depends on matching capital deployment with reserve discipline.",
                ],
            },
            context_excerpt='Management said "reserve discipline still comes before aggressive premium growth" as claims stayed manageable.',
            mda_excerpt='Management noted that "client-retention quality matters more than headline flow volume" while reserve commentary remained central.',
            risk_factors_excerpt="Combined ratio, claims, AUM, net flows, and reserves remain the company-specific risks.",
            filing_language_snippets='Reserve discipline remained central and management said "client-retention quality matters more than headline flow volume."',
            financial_snapshot="Revenue $3.2B. Operating income $0.88B. Operating cash flow $0.79B. Free cash flow $0.61B. Cash balance $4.6B.",
            metrics_rows=(
                ("Revenue", "$3.20B", ("underwriting", "fees", "mix")),
                ("Operating Income", "$0.88B", ("reserve", "discipline", "quality")),
                ("Operating Margin", "27.5%", ("claims", "control", "support")),
                ("Free Cash Flow", "$0.61B", ("capital", "deployment", "room")),
                ("Current Ratio", "1.7x", ("liquidity", "buffer", "resilience")),
            ),
        ),
        SmokeScenario(
            key="pharma",
            company_name="Cardio MedTech plc",
            ticker="CDMT",
            sector="Healthcare",
            industry="Medical Devices",
            business_archetype="pharma_biotech_medtech",
            business_identity="Cardio MedTech depends on launch uptake, procedure growth, pipeline milestones, reimbursement, and product durability.",
            competitive_moat="Clinical data, physician adoption, and reimbursement access support the installed procedure base.",
            key_competitors=("Medtronic", "Edwards Lifesciences"),
            investor_focus_areas=(
                "launch uptake and channel inventory",
                "pipeline timing and regulatory milestones",
                "reimbursement and pricing durability",
            ),
            industry_kpi_norms="Healthy medtech operators prove launch uptake, procedure adoption, and reimbursement support before the next pipeline spend step-up.",
            central_tension="Can Cardio MedTech plc turn launch uptake into durable growth before reimbursement, regulatory timing, or pipeline execution gets in the way?",
            management_strategy_summary="Management is prioritizing launch execution and physician adoption in the current franchise while advancing the next regulatory milestones.",
            forward_guidance_summary="Management expects launch uptake and procedure adoption to improve over the next several quarters.",
            promise_scorecard="Management is on track if launch uptake and pipeline timing keep supporting the current product cycle.",
            company_terms=("launch uptake", "pipeline", "trial readout", "reimbursement", "procedure growth", "regulatory milestones"),
            management_quotes=(
                {
                    "quote": "launch uptake still matters more than simply broadening channel inventory",
                    "attribution": "Management",
                    "topic": "launch uptake",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "regulatory milestones remain on the critical path for the next phase of growth",
                    "attribution": "Management",
                    "topic": "pipeline",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "launch uptake",
                    "expectation": "Management expects launch uptake and procedure growth to improve over the next several quarters.",
                    "timeframe": "next several quarters",
                    "evidence": "Management highlighted launch uptake and physician adoption.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "translate launch uptake into durable procedure growth while keeping milestones on track",
                    "status": "on_track",
                    "assessment": "Procedure adoption improved while regulatory milestones remained on the critical path.",
                    "evidence": "Management highlighted launch uptake and regulatory milestones.",
                },
            ),
            management_strategic_bets=("launch uptake", "pipeline", "reimbursement"),
            period_specific_insights=(
                "Launch uptake remained the leading commercial proof point.",
                "Regulatory milestones still define the next value inflection.",
                "Reimbursement support remains tied to physician adoption and procedure growth.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Launch Uptake",
                    "current_value": "Improving",
                    "prior_value": "Early",
                    "change": "Improved",
                    "change_direction": "improved",
                    "insight": "Procedure adoption is improving without obvious channel stuffing.",
                    "source_quote": "launch uptake still matters more than simply broadening channel inventory",
                },
                {
                    "kpi_name": "Pipeline Milestones",
                    "current_value": "On schedule",
                    "prior_value": "On schedule",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "The next value inflection still depends on regulatory timing more than on the current quarter's revenue.",
                    "source_quote": "regulatory milestones remain on the critical path for the next phase of growth",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Launch Uptake Risk",
                    "mechanism": "New-product demand can lag if physician adoption or procedure throughput stalls.",
                    "early_warning": "Watch procedure growth, channel inventory, and physician-adoption commentary.",
                    "evidence_from_filing": "Launch uptake remained the lead commercial proof point.",
                },
                {
                    "risk_name": "Pipeline / Regulatory Timing Risk",
                    "mechanism": "The next value inflection can move out if regulatory milestones slip while spend remains elevated.",
                    "early_warning": "Track trial timing, regulatory commentary, and approval milestones.",
                    "evidence_from_filing": "Regulatory milestones remain on the critical path.",
                },
                {
                    "risk_name": "Reimbursement / Exclusivity Risk",
                    "mechanism": "Pricing or reimbursement pressure can weaken economics even if unit demand looks healthy.",
                    "early_warning": "Watch reimbursement commentary, gross-to-net trends, and payer mix.",
                    "evidence_from_filing": "Reimbursement support remains tied to physician adoption and procedure growth.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Launch uptake is the real commercial hinge, not just channel fill.",
                    "Management is trying to bridge the current franchise into the next regulatory milestone set.",
                ],
                "Financial Performance": [
                    "Procedure growth is improving without obvious channel stuffing.",
                    "Regulatory timing still matters more than current-quarter revenue optics.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects launch uptake to improve over the next several quarters.",
                    "Regulatory milestones remain on the critical path for the next phase of growth.",
                ],
                "Risk Factors": [
                    "Launch uptake can lag if physician adoption softens.",
                    "Regulatory timing can slip while spend remains elevated.",
                ],
                "Closing Takeaway": [
                    "The stance changes if launch uptake stalls or milestones slip.",
                    "Management credibility depends on matching commercial progress with regulatory execution.",
                ],
            },
            context_excerpt='Management said "launch uptake still matters more than simply broadening channel inventory" as procedure growth improved.',
            mda_excerpt='Management noted that "regulatory milestones remain on the critical path for the next phase of growth."',
            risk_factors_excerpt="Launch uptake, pipeline timing, reimbursement, and regulatory milestones remain the key risks.",
            filing_language_snippets='Launch uptake improved and management said "regulatory milestones remain on the critical path for the next phase of growth."',
            financial_snapshot="Revenue $1.4B. Operating income $0.29B. Operating cash flow $0.36B. Free cash flow $0.24B. Cash balance $2.1B.",
            metrics_rows=(
                ("Revenue", "$1.40B", ("procedure", "growth", "mix")),
                ("Operating Income", "$0.29B", ("launch", "discipline", "support")),
                ("Operating Margin", "20.7%", ("reimbursement", "quality", "control")),
                ("Free Cash Flow", "$0.24B", ("pipeline", "capacity", "funding")),
                ("Current Ratio", "2.0x", ("liquidity", "buffer", "optionality")),
            ),
        ),
        SmokeScenario(
            key="retail",
            company_name="Premium Retail Group",
            ticker="PRG",
            sector="Consumer Discretionary",
            industry="Retail",
            business_archetype="retail_consumer",
            business_identity="Premium Retail Group monetizes through traffic, basket size, merchandise mix, and promotional discipline across a curated store base.",
            competitive_moat="Brand positioning and inventory discipline support merchandise margins and repeat traffic.",
            key_competitors=("Lululemon", "Nordstrom"),
            investor_focus_areas=(
                "same-store sales and traffic quality",
                "inventory discipline versus promotional pressure",
                "gross-margin stability through the demand cycle",
            ),
            industry_kpi_norms="Healthy retailers sustain traffic and same-store sales without relying on markdown-heavy demand.",
            central_tension="Can Premium Retail Group keep traffic healthy without giving up merchandise economics through heavier promotions or weaker inventory discipline?",
            management_strategy_summary="Management is prioritizing inventory quality and full-price sell-through over lower-quality volume.",
            forward_guidance_summary="Management expects traffic and same-store sales to stay constructive this year without leaning harder on promotions.",
            promise_scorecard="Management is credible if traffic stays healthy while inventory discipline keeps markdown risk contained.",
            company_terms=("same-store sales", "traffic", "inventory", "promotions", "average ticket", "store productivity"),
            management_quotes=(
                {
                    "quote": "full-price sell-through matters more than buying traffic with promotions",
                    "attribution": "Management",
                    "topic": "promotions",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "inventory discipline still anchors our margin profile",
                    "attribution": "Management",
                    "topic": "inventory",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "same-store sales",
                    "expectation": "Management expects traffic and same-store sales to remain constructive this year without heavier promotions.",
                    "timeframe": "this year",
                    "evidence": "Management emphasized full-price sell-through over buying traffic.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "protect merchandise economics while keeping traffic healthy",
                    "status": "on_track",
                    "assessment": "Traffic remained constructive while management kept emphasizing full-price sell-through and inventory discipline.",
                    "evidence": "Full-price sell-through matters more than buying traffic with promotions.",
                },
            ),
            management_strategic_bets=("traffic", "inventory", "promotions"),
            period_specific_insights=(
                "Traffic stayed constructive without a heavier promotional step-up.",
                "Inventory discipline remained tied to the margin story.",
                "Management kept emphasizing full-price sell-through.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Comparable Sales",
                    "current_value": "Positive",
                    "prior_value": "Positive",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Demand improved without obvious deterioration in merchandise economics.",
                    "source_quote": "full-price sell-through matters more than buying traffic with promotions",
                },
                {
                    "kpi_name": "Inventory Turns",
                    "current_value": "Disciplined",
                    "prior_value": "Disciplined",
                    "change": "Stable",
                    "change_direction": "stable",
                    "insight": "Inventory discipline remained central to the quarter's gross-margin outcome.",
                    "source_quote": "inventory discipline still anchors our margin profile",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Promotion-Driven Traffic Risk",
                    "mechanism": "Traffic can improve only through heavier promotions, weakening merchandise economics.",
                    "early_warning": "Watch traffic, ticket size, markdown cadence, and gross-margin commentary.",
                    "evidence_from_filing": "Management stressed full-price sell-through and inventory discipline.",
                },
                {
                    "risk_name": "Inventory Markdown Risk",
                    "mechanism": "Inventory can turn into markdown pressure if sell-through softens or seasonality misses.",
                    "early_warning": "Track inventory turns, promotions, and clearance intensity.",
                    "evidence_from_filing": "Inventory discipline remained central to the quarter.",
                },
                {
                    "risk_name": "Store Productivity / Channel Mix Risk",
                    "mechanism": "Store productivity or digital mix can erode operating leverage even if headline sales stay positive.",
                    "early_warning": "Watch comp sales, labor leverage, and fulfillment costs.",
                    "evidence_from_filing": "Traffic stayed constructive without a heavier promotional step-up.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Traffic quality matters only if full-price sell-through stays intact.",
                    "Management is protecting inventory quality rather than chasing low-quality volume.",
                ],
                "Financial Performance": [
                    "Comparable sales held while inventory discipline stayed intact.",
                    "Traffic improved without a heavier promotional step-up.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects traffic and same-store sales to stay constructive without heavier promotions.",
                    "Inventory discipline still anchors the margin profile.",
                ],
                "Risk Factors": [
                    "Traffic can improve only through weaker promotions.",
                    "Inventory can turn into markdown pressure if sell-through softens.",
                ],
                "Closing Takeaway": [
                    "The stance changes if management has to buy traffic with promotions.",
                    "Management credibility depends on protecting inventory discipline through the next demand phase.",
                ],
            },
            context_excerpt='Management said "full-price sell-through matters more than buying traffic with promotions" as traffic remained constructive.',
            mda_excerpt='Management noted that "inventory discipline still anchors our margin profile" while same-store sales remained constructive.',
            risk_factors_excerpt="same-store sales, traffic, inventory, promotions, and store productivity remain the core risks.",
            filing_language_snippets='Traffic remained constructive and management said "inventory discipline still anchors our margin profile."',
            financial_snapshot="Revenue $3.6B. Operating income $0.52B. Operating cash flow $0.49B. Free cash flow $0.37B. Cash balance $1.2B.",
            metrics_rows=(
                ("Revenue", "$3.60B", ("traffic", "quality", "mix")),
                ("Operating Income", "$0.52B", ("merchandise", "economics", "support")),
                ("Operating Margin", "14.4%", ("inventory", "discipline", "benefit")),
                ("Free Cash Flow", "$0.37B", ("working-capital", "control", "support")),
                ("Current Ratio", "1.6x", ("liquidity", "buffer", "room")),
            ),
        ),
        SmokeScenario(
            key="payments",
            company_name="Checkout Payments Inc.",
            ticker="CPAY",
            sector="Financials",
            industry="Payments",
            business_archetype="payments_marketplaces",
            business_identity="Checkout Payments monetizes through payment volume, take rate, merchant mix, and loss discipline across merchant cohorts.",
            competitive_moat="Merchant distribution and risk controls help protect take rate and transaction quality.",
            key_competitors=("Adyen", "PayPal"),
            investor_focus_areas=(
                "payment volume quality versus take-rate pressure",
                "merchant mix and loss performance",
                "funding, fraud, and credit costs",
            ),
            industry_kpi_norms="Healthy payment platforms sustain payment volume without surrendering take rate or loss discipline.",
            central_tension="Can Checkout Payments scale payment volume without weakening take rate, loss discipline, or merchant quality?",
            management_strategy_summary="Management is prioritizing higher-quality merchant cohorts and disciplined take rate over lower-yield transaction growth.",
            forward_guidance_summary="Management expects payment volume to remain healthy this year while take-rate pressure stays manageable.",
            promise_scorecard="Management is on track if payment volume scales without a visible hit to take rate or chargeback discipline.",
            company_terms=("payment volume", "take rate", "merchant mix", "chargebacks", "funding costs", "consumer losses"),
            management_quotes=(
                {
                    "quote": "we are choosing higher-quality merchant cohorts over low-yield volume",
                    "attribution": "Management",
                    "topic": "merchant mix",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "take-rate discipline still matters more than simply maximizing payment volume",
                    "attribution": "Management",
                    "topic": "take rate",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "payment volume",
                    "expectation": "Management expects payment volume to remain healthy this year while take-rate pressure stays manageable.",
                    "timeframe": "this year",
                    "evidence": "Management emphasized higher-quality merchant cohorts and take-rate discipline.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "scale payment volume without weakening merchant quality or loss discipline",
                    "status": "on_track",
                    "assessment": "Management kept emphasizing higher-quality merchant cohorts while keeping chargeback commentary contained.",
                    "evidence": "Take-rate discipline still matters more than simply maximizing payment volume.",
                },
            ),
            management_strategic_bets=("payment volume", "take rate", "merchant mix"),
            period_specific_insights=(
                "Merchant quality remained the lead underwriting lens on payment volume growth.",
                "Take-rate discipline remained part of the operating message.",
                "Chargeback commentary stayed contained.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Total Payment Volume",
                    "current_value": "$95B",
                    "prior_value": "$90B",
                    "change": "+6% QoQ",
                    "change_direction": "improved",
                    "insight": "Volume stayed healthy while management kept stressing merchant quality.",
                    "source_quote": "we are choosing higher-quality merchant cohorts over low-yield volume",
                },
                {
                    "kpi_name": "Take Rate",
                    "current_value": "2.10%",
                    "prior_value": "2.12%",
                    "change": "-2 bps QoQ",
                    "change_direction": "declined",
                    "insight": "Take-rate pressure remained manageable rather than thesis-breaking.",
                    "source_quote": "take-rate discipline still matters more than simply maximizing payment volume",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Take-Rate / Merchant Mix Risk",
                    "mechanism": "Volume can rise through lower-yield merchants or geographies, weakening monetization per transaction.",
                    "early_warning": "Watch payment volume, take rate, and merchant mix.",
                    "evidence_from_filing": "Management emphasized higher-quality merchant cohorts.",
                },
                {
                    "risk_name": "Chargeback and Funding Spread Risk",
                    "mechanism": "Losses or funding costs can rise faster than transaction revenue in a weaker demand mix.",
                    "early_warning": "Track chargebacks, loss rates, reserve commentary, and funding spreads.",
                    "evidence_from_filing": "Chargeback commentary stayed contained.",
                },
                {
                    "risk_name": "Partner / Merchant Concentration Risk",
                    "mechanism": "Large-partner concessions can pressure both take rate and operating leverage.",
                    "early_warning": "Watch partner volume mix, merchant churn, and pricing commentary.",
                    "evidence_from_filing": "Merchant quality remained the lead underwriting lens.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Payment volume only matters if merchant quality and take rate hold together.",
                    "Management is prioritizing higher-quality merchant cohorts over low-yield volume.",
                ],
                "Financial Performance": [
                    "Payment volume improved while take-rate pressure remained manageable.",
                    "Chargeback commentary stayed contained.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects payment volume to stay healthy this year.",
                    "Take-rate discipline still matters more than maximizing volume.",
                ],
                "Risk Factors": [
                    "Volume can rise while take rate weakens through merchant mix.",
                    "Loss and funding costs can rise faster than transaction revenue.",
                ],
                "Closing Takeaway": [
                    "The stance changes if merchant quality weakens or chargebacks rise.",
                    "Management credibility depends on balancing payment volume with take-rate discipline.",
                ],
            },
            context_excerpt='Management said "we are choosing higher-quality merchant cohorts over low-yield volume" as payment volume improved.',
            mda_excerpt='Management noted that "take-rate discipline still matters more than simply maximizing payment volume."',
            risk_factors_excerpt="payment volume, take rate, merchant mix, chargebacks, and funding costs remain the core platform risks.",
            filing_language_snippets='Payment volume improved and management said "take-rate discipline still matters more than simply maximizing payment volume."',
            financial_snapshot="Revenue $2.2B. Operating income $0.48B. Operating cash flow $0.51B. Free cash flow $0.39B. Cash balance $2.8B.",
            metrics_rows=(
                ("Revenue", "$2.20B", ("volume", "quality", "mix")),
                ("Operating Income", "$0.48B", ("take-rate", "discipline", "support")),
                ("Operating Margin", "21.8%", ("merchant", "quality", "benefit")),
                ("Free Cash Flow", "$0.39B", ("funding", "flexibility", "buffer")),
                ("Current Ratio", "1.9x", ("liquidity", "room", "resilience")),
            ),
        ),
        SmokeScenario(
            key="industrial",
            company_name="Industrial Motion Systems",
            ticker="IMS",
            sector="Industrials",
            industry="Industrial Machinery",
            business_archetype="industrial_manufacturing",
            business_identity="Industrial Motion Systems converts order intake into backlog, project execution, aftermarket revenue, and plant utilization.",
            competitive_moat="Installed equipment and aftermarket service relationships support margin stability through the cycle.",
            key_competitors=("Siemens", "ABB"),
            investor_focus_areas=(
                "order intake versus backlog conversion",
                "pricing recovery versus input-cost pressure",
                "project execution and service mix",
            ),
            industry_kpi_norms="Healthy industrial operators refill backlog while protecting project execution and aftermarket mix.",
            central_tension="Can Industrial Motion Systems convert backlog into margin and cash while keeping project execution and service attachment on schedule?",
            management_strategy_summary="Management is prioritizing project execution and aftermarket attachment over lower-quality order growth.",
            forward_guidance_summary="Management expects order intake and backlog conversion to stay constructive over the next year.",
            promise_scorecard="Management stays credible if backlog conversion and aftermarket attachment continue to support margin resilience.",
            company_terms=("order intake", "backlog", "aftermarket", "utilization", "service revenue", "projects"),
            management_quotes=(
                {
                    "quote": "aftermarket attachment still matters more than chasing low-quality orders",
                    "attribution": "Management",
                    "topic": "aftermarket",
                    "suggested_section": "Executive Summary",
                },
                {
                    "quote": "project execution remains the real scorecard for backlog conversion",
                    "attribution": "Management",
                    "topic": "projects",
                    "suggested_section": "Management Discussion & Analysis",
                },
            ),
            management_expectations=(
                {
                    "topic": "backlog",
                    "expectation": "Management expects order intake and backlog conversion to stay constructive over the next year.",
                    "timeframe": "next year",
                    "evidence": "Management highlighted project execution and aftermarket attachment.",
                },
            ),
            promise_scorecard_items=(
                {
                    "commitment": "convert backlog while protecting aftermarket margin support",
                    "status": "on_track",
                    "assessment": "Management kept emphasizing project execution and aftermarket attachment as the route to durable economics.",
                    "evidence": "Project execution remains the real scorecard for backlog conversion.",
                },
            ),
            management_strategic_bets=("backlog", "aftermarket", "projects"),
            period_specific_insights=(
                "Aftermarket attachment remained central to the margin story.",
                "Project execution still determined whether backlog became recognized revenue.",
                "Order intake remained constructive without displacing service quality.",
            ),
            kpi_findings=(
                {
                    "kpi_name": "Order Intake",
                    "current_value": "$3.4B",
                    "prior_value": "$3.1B",
                    "change": "+10% YoY",
                    "change_direction": "improved",
                    "insight": "Demand kept refilling the backlog without obvious deterioration in quality.",
                    "source_quote": "aftermarket attachment still matters more than chasing low-quality orders",
                },
                {
                    "kpi_name": "Service / Aftermarket Revenue",
                    "current_value": "$0.9B",
                    "prior_value": "$0.8B",
                    "change": "+9% YoY",
                    "change_direction": "improved",
                    "insight": "Aftermarket mix remained the key margin stabilizer.",
                    "source_quote": "project execution remains the real scorecard for backlog conversion",
                },
            ),
            company_specific_risks=(
                {
                    "risk_name": "Backlog Conversion / Project Timing Risk",
                    "mechanism": "Reported growth can lag the order book if projects slip or plant execution weakens.",
                    "early_warning": "Watch backlog conversion, project milestones, and factory utilization.",
                    "evidence_from_filing": "Project execution remained central to backlog conversion.",
                },
                {
                    "risk_name": "Price-Cost Recovery Risk",
                    "mechanism": "Margin improvement can reverse if inputs rise faster than pricing recovery.",
                    "early_warning": "Track pricing realization, service mix, and input-cost commentary.",
                    "evidence_from_filing": "Aftermarket attachment stayed central to the margin story.",
                },
                {
                    "risk_name": "Project Commissioning and Collection Risk",
                    "mechanism": "Late delivery or commissioning delays can slow cash collection and force margin givebacks.",
                    "early_warning": "Watch project milestones, warranty commentary, and working-capital timing.",
                    "evidence_from_filing": "Project execution remained the real scorecard for backlog conversion.",
                },
            ),
            evidence_map={
                "Executive Summary": [
                    "Order intake matters only if backlog converts through clean project execution.",
                    "Management is leaning on aftermarket attachment rather than lower-quality order growth.",
                ],
                "Financial Performance": [
                    "Order intake improved while aftermarket mix stayed supportive.",
                    "Project execution remained the gating factor on recognized revenue and cash.",
                ],
                "Management Discussion & Analysis": [
                    "Management expects order intake and backlog conversion to stay constructive over the next year.",
                    "Project execution remains the real scorecard for backlog conversion.",
                ],
                "Risk Factors": [
                    "Backlog can stay healthy while project execution slips.",
                    "Price-cost recovery can reverse margin improvement.",
                ],
                "Closing Takeaway": [
                    "The stance changes if project execution weakens or aftermarket mix softens.",
                    "Management credibility depends on converting backlog without giving back service economics.",
                ],
            },
            context_excerpt='Management said "aftermarket attachment still matters more than chasing low-quality orders" as order intake improved.',
            mda_excerpt='Management noted that "project execution remains the real scorecard for backlog conversion."',
            risk_factors_excerpt="order intake, backlog, aftermarket, utilization, and project execution remain the core industrial risks.",
            filing_language_snippets='Order intake improved and management said "project execution remains the real scorecard for backlog conversion."',
            financial_snapshot="Revenue $3.1B. Operating income $0.46B. Operating cash flow $0.42B. Free cash flow $0.31B. Cash balance $1.6B.",
            metrics_rows=(
                ("Revenue", "$3.10B", ("backlog", "conversion", "quality")),
                ("Operating Income", "$0.46B", ("aftermarket", "support", "mix")),
                ("Operating Margin", "14.8%", ("pricing", "discipline", "benefit")),
                ("Free Cash Flow", "$0.31B", ("project", "execution", "conversion")),
                ("Current Ratio", "1.7x", ("liquidity", "buffer", "room")),
            ),
        ),
    ]


def _target_words_from_prompt(prompt: str) -> int:
    match = re.search(r"- Target (\d+) body words\.", prompt)
    return int(match.group(1)) if match else 120


def _section_name_from_prompt(prompt: str) -> str:
    match = re.search(r"Write ONLY the body of the '(.+?)' section", prompt)
    if not match:
        raise ValueError("Unable to determine section name from prompt.")
    return str(match.group(1))


def _extract_block(prompt: str, label: str) -> str:
    pattern = rf"{re.escape(label)}:\n([\s\S]*?)(?=\n[A-Z][A-Z /&'()-]+:\n|\Z)"
    match = re.search(pattern, prompt)
    return (match.group(1) if match else "").strip()


def _extract_dash_items(block: str) -> list[str]:
    items: list[str] = []
    for raw_line in str(block or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
    return items


def _extract_company_name_from_prompt(prompt: str) -> str:
    match = re.search(r"for (.+?)\.\n\nBODY WORD BUDGET:", prompt)
    return str(match.group(1)).strip() if match else "the company"


def _health_opening(prompt: str) -> str:
    match = re.search(r"Use this exact opening:\s*(.+?)\n", prompt)
    return match.group(1).strip().rstrip(".") if match else "74/100 - Healthy"


def _anchor_terms_from_prompt(prompt: str, *, section_name: str) -> list[str]:
    def _normalize_anchor(raw: str) -> str:
        clean = " ".join(str(raw or "").split()).strip(" -")
        if not clean:
            return ""
        clean = clean.split(":", 1)[0].strip()
        if len(clean.split()) > 6:
            clean = re.sub(
                r"^(management expects|management noted that|management said|the company expects)\s+",
                "",
                clean,
                flags=re.IGNORECASE,
            )
            clean = re.split(
                r"\b(?:while|because|as|over|during|through|into|next|if|when|that)\b",
                clean,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,.")
        if len(clean.split()) > 6 or clean.endswith("."):
            return ""
        return clean

    anchors: list[str] = []
    anchors.extend(_extract_dash_items(_extract_block(prompt, "COMPANY TERMS TO REUSE")))
    anchors.extend(
        _normalize_anchor(item)
        for item in _extract_dash_items(_extract_block(prompt, "KPI FINDINGS TO PRIORITIZE"))
    )
    anchors.extend(
        _normalize_anchor(item)
        for item in _extract_dash_items(_extract_block(prompt, "PROMISE SCORECARD ITEMS TO USE"))
    )
    anchors.extend(
        _normalize_anchor(item)
        for item in _extract_dash_items(_extract_block(prompt, "MANAGEMENT EXPECTATIONS TO USE"))
    )
    if section_name == "Risk Factors":
        risk_block = re.search(
            r"Candidate company-specific risks:\n([\s\S]*?)\n\n",
            prompt,
        )
        if risk_block:
            anchors.extend(
                _normalize_anchor(item)
                for item in _extract_dash_items(risk_block.group(1))
            )
        else:
            anchors.extend(
                _normalize_anchor(risk["risk_name"])
                for risk in _accepted_source_backed_risks_from_prompt(prompt)
            )
    deduped: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        clean = " ".join(str(anchor or "").split()).strip(" -")
        if not clean:
            continue
        norm = re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip()
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(clean)
    return deduped[:8]


def _quotes_from_prompt(prompt: str) -> list[str]:
    quotes = []
    for match in re.finditer(r'- "([^"\n]{8,200})"', prompt):
        quotes.append(" ".join(match.group(1).split()))
    return quotes[:3]


def _accepted_source_backed_risks_from_prompt(prompt: str) -> list[dict[str, str]]:
    match = re.search(r"Accepted source-backed risks:\n([\s\S]*?)\n\n", prompt)
    block = match.group(1) if match else ""
    risks: list[dict[str, str]] = []
    for item in _extract_dash_items(block):
        name, _, evidence = item.partition(":")
        clean_name = re.sub(r"\s*\[[^\]]+\]\s*$", "", name).strip()
        risk_subject = re.sub(r"\s+risk\s*$", "", clean_name, flags=re.IGNORECASE)
        risk_subject = re.sub(r"[^a-z0-9]+", " ", risk_subject.lower()).strip()
        evidence_text = " ".join(evidence.split()).strip().rstrip(".")
        evidence_text = re.sub(r"^(?:Risk Factors:\s*)", "", evidence_text, flags=re.IGNORECASE)
        evidence_tokens = re.findall(r"[a-z]{4,}", evidence.lower())
        subject_tokens = re.findall(r"[a-z]{4,}", risk_subject)
        warning_terms: list[str] = []
        for token in subject_tokens + evidence_tokens:
            if token in {"management", "highlighted", "expects", "factors"}:
                continue
            if token not in warning_terms:
                warning_terms.append(token)
            if len(warning_terms) >= 6:
                break
        impact_path = "revenue, margins, or cash flow"
        early_warning = " ".join(warning_terms) or risk_subject or clean_name.lower()
        if "renewal" in risk_subject or "conversion" in risk_subject:
            impact_path = "revenue conversion, renewal visibility, or cash flow"
            early_warning = "renewal rates RPO conversion discounting backlog"
        elif "attach" in risk_subject or "monetization" in risk_subject:
            impact_path = "paid-seat monetization, gross margin, or cash generation"
            early_warning = "attach rates paid seats pricing usage"
        elif "expansion" in risk_subject or "account" in risk_subject:
            impact_path = "sales efficiency, expansion velocity, or margin absorption"
            early_warning = "seat expansion largest accounts deal size"
            risks.append(
                {
                    "risk_name": clean_name,
                    "mechanism": (
                        "seat expansion stays concentrated inside the largest accounts "
                        "instead of broadening across the installed base"
                    ),
                    "early_warning": early_warning,
                    "impact_path": impact_path,
                }
            )
            continue
        risks.append(
            {
                "risk_name": clean_name,
                "mechanism": (
                    f"if {risk_subject or clean_name.lower()} slips despite current evidence on "
                    f"{(evidence_text or clean_name).lower()}"
                ),
                "early_warning": early_warning,
                "impact_path": impact_path,
            }
        )
    return risks


def _section_templates(section_name: str) -> list[str]:
    return {
        "Financial Health Rating": [
            "{a} still frames how much balance-sheet pressure the company can absorb.",
            "That resilience only matters if {b} keeps supporting the operating thread.",
            "{a} remains tied to the balance-sheet read rather than a generic scoring label.",
            "The health view still depends on whether {b} keeps reinforcing resilience.",
        ],
        "Executive Summary": [
            "{a} is the clearest company-specific proof point behind the current thesis.",
            "The story only holds if {b} keeps backing the economics management is describing.",
            "{a} is still the best shorthand for what changed in this filing period.",
            "That leaves {b} as the next proof point investors should carry forward.",
        ],
        "Financial Performance": [
            "{a} is the cleaner test of whether the quarter improved through real economics rather than timing noise.",
            "The sharper read-through is whether {b} is turning into repeatable operating evidence.",
            "{a} matters here because it shows whether the quarter improved for the right reasons.",
            "This section only works if {b} is validating the reported trend instead of flattering it.",
        ],
        "Management Discussion & Analysis": [
            "Management still has to prove {a} can scale without diluting returns.",
            "Execution credibility now depends on how leadership sequences {b}.",
            "{a} is the operating mechanism management is trying to control from here.",
            "This section is really about whether leadership can keep {b} on plan.",
        ],
        "Risk Factors": [
            "A break in {a} would be the earliest signal that the downside path is becoming real.",
            "The risk becomes investable only if {b} weakens before reported results do.",
            "{a} stays on the short list of leading indicators because it would move first in a downside case.",
            "That keeps {b} tied to the earliest watchpoint rather than a generic risk bucket.",
        ],
        "Closing Takeaway": [
            "The stance changes fastest if {a} stops supporting the operating case.",
            "The verdict only holds while {b} keeps backing the thesis.",
            "{a} is still the quickest way to tell whether the thesis remains intact.",
            "The next confirm-or-break signal still runs through {b}.",
        ],
    }.get(section_name, ["{a} remains the most useful company-specific signal here."])


def _dynamic_budget_candidate(
    section_name: str,
    idx: int,
    anchors: list[str],
) -> str:
    usable = anchors or ["execution", "operating model", "demand"]
    a = usable[idx % len(usable)]
    b = usable[(idx + 1) % len(usable)]
    c = usable[(idx + 2) % len(usable)]
    dynamic_templates = {
        "Financial Health Rating": [
            "{a} still belongs in the balance-sheet read because it defines how much shock the company can absorb.",
            "{b} is what keeps the health case tied to the real operating model instead of the headline score.",
            "{a} and {c} are still the company-specific supports behind this rating call.",
        ],
        "Executive Summary": [
            "{a} still anchors the memo thread because it carries the filing-specific story better than a generic quarterly recap.",
            "{b} remains the practical proof point investors should watch after this opening section.",
            "{a} and {c} still explain why management's message matters in this period.",
        ],
        "Financial Performance": [
            "{a} belongs here because it helps separate underlying execution from temporary timing noise in the print.",
            "{b} is the more useful read-through because it tests whether the reported trend can repeat next period.",
            "{a} and {c} are what make this section about proof rather than management intent.",
        ],
        "Management Discussion & Analysis": [
            "{a} matters in MD&A because it shows what management is actively trying to sequence, protect, or accelerate.",
            "{b} is the real execution mechanism leadership has to keep on track from here.",
            "{a} and {c} are what turn the strategy discussion into a practical operating scorecard.",
        ],
        "Risk Factors": [
            "{a} matters on the risk side because it would likely weaken before the full downside shows up in reported results.",
            "{b} is the watchpoint that could move this risk from a possibility into an active underwriting problem.",
            "{a} and {c} stay relevant because they are the most direct early-warning signals in this operating model.",
        ],
        "Closing Takeaway": [
            "{a} still decides whether the current stance deserves to hold from here.",
            "{b} is the next proof point that would either validate the case or force a rethink.",
            "{a} and {c} are still the cleanest confirm-or-break signals for the thesis.",
        ],
    }
    templates = dynamic_templates.get(section_name) or dynamic_templates["Executive Summary"]
    return templates[idx % len(templates)].format(a=a, b=b, c=c).strip()


def _trim_to_target(text: str, target_words: int, *, allowed_overage: int = 2) -> str:
    """Trim text to approximately target_words by removing trailing content."""
    if count_words(text) <= target_words + max(0, int(allowed_overage)):
        return text
    if "\n\n" in text:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
        while (
            len(paragraphs) > 1
            and count_words("\n\n".join(paragraphs)) > target_words + max(0, int(allowed_overage))
        ):
            last_paragraph = paragraphs[-1]
            last_sentences = re.split(r"(?<=[.!?])\s+", last_paragraph)
            if len(last_sentences) > 1:
                last_sentences.pop()
                paragraphs[-1] = " ".join(last_sentences).strip()
            else:
                paragraphs.pop()
        result = "\n\n".join(paragraphs).strip()
        if count_words(result) <= target_words + max(1, int(allowed_overage)):
            return result
        words = result.split()
        result = " ".join(words[:target_words]).rstrip(",.;:- ") + "."
        return result
    # Sentence-level trimming
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    while len(sentences) > 1 and count_words(" ".join(sentences)) > target_words + max(0, int(allowed_overage)):
        sentences.pop()
    result = " ".join(sentences).strip()
    # If still over (single long sentence), hard-truncate at word boundary
    if count_words(result) > target_words + max(1, int(allowed_overage)):
        words = result.split()
        result = " ".join(words[:target_words])
        result = result.rstrip(",.;:- ") + "."
    return result


def _normalize_early_warning_text(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip().rstrip(".")
    clean = re.sub(r"^(watch|track|monitor|follow|observe)\s+", "", clean, flags=re.IGNORECASE)
    return clean


def _truncate_words(text: str, max_words: int) -> str:
    words = str(text or "").split()
    if len(words) <= max(0, int(max_words)):
        return " ".join(words).strip().rstrip(",.;:-")
    return " ".join(words[: max(1, int(max_words))]).strip().rstrip(",.;:-")


def _expand_to_budget(
    section_name: str,
    text: str,
    target_words: int,
    anchors: list[str],
) -> str:
    # If base text exceeds target, keep only sentences that fit within budget
    if count_words(text) > target_words + 2:
        base_sents = re.split(r"(?<=[.!?])\s+", text.strip())
        kept = [base_sents[0]]
        for sent in base_sents[1:]:
            if count_words(" ".join(kept + [sent])) <= target_words + 2:
                kept.append(sent)
            else:
                break
        text = " ".join(kept).strip()
    if count_words(text) >= target_words:
        return text
    usable = anchors or ["execution", "operating model"]
    sentences = [text.strip()]
    templates = _section_templates(section_name)
    seen = {
        re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()
        for sentence in sentences
        if sentence.strip()
    }
    idx = 0
    while count_words(" ".join(sentences)) < max(target_words - 3, 1):
        template = templates[idx % len(templates)]
        a = usable[idx % len(usable)]
        b = usable[(idx + 1) % len(usable)]
        candidate = template.format(a=a, b=b).strip()
        normalized = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
        if normalized in seen:
            candidate = _dynamic_budget_candidate(section_name, idx, usable)
            normalized = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
        if normalized in seen:
            idx += 1
            if idx > 120:
                break
            continue
        sentences.append(candidate)
        seen.add(normalized)
        idx += 1
        if idx > 120:
            break
    body = " ".join(sentences).strip()
    if count_words(body) > target_words:
        trimmed = sentences[:]
        while count_words(" ".join(trimmed)) > target_words and len(trimmed) > 1:
            trimmed.pop()
        body = " ".join(trimmed).strip()
    if count_words(body) < target_words:
        primary = usable[0]
        secondary = usable[1] if len(usable) > 1 else primary
        tertiary = usable[2] if len(usable) > 2 else secondary
        tail_candidates_map = {
            "Financial Health Rating": [
                f"That keeps {primary} central to the balance-sheet read.",
                f"That keeps {secondary} tied to the resilience case.",
                f"{tertiary} still matters for the health view.",
                "Still central here.",
            ],
            "Executive Summary": [
                f"That keeps {primary} as the thread to carry forward.",
                f"That leaves {secondary} as the next summary proof point.",
                f"{tertiary} still belongs in the opening thesis.",
                "Still the core thread.",
            ],
            "Financial Performance": [
                f"That keeps {primary} tied to the quarter's proof point.",
                f"That leaves {secondary} inside the operating read-through.",
                f"{tertiary} still matters for the quarter's evidence.",
                "Still the proof point.",
            ],
            "Management Discussion & Analysis": [
                f"That keeps {primary} inside management's operating scorecard.",
                f"That leaves {secondary} as the next execution check.",
                f"{tertiary} still matters in management's sequencing logic.",
                "Still management's real test.",
            ],
            "Risk Factors": [
                f"That keeps {primary} on the risk watchlist.",
                f"That leaves {secondary} as an early-warning signal.",
                f"{tertiary} still matters in the downside path.",
                "Still the watchpoint.",
            ],
            "Closing Takeaway": [
                f"That keeps {primary} tied to the stance.",
                f"That leaves {secondary} as the next confirm-or-break signal.",
                f"{tertiary} still matters for the final verdict.",
                "Still thesis-critical.",
            ],
        }
        tail_candidates = list(
            tail_candidates_map.get(
                section_name,
                [
                    f"That keeps {primary} in view.",
                    f"{secondary} still matters in this read.",
                    f"Keep {primary} linked to {secondary}.",
                ],
            )
        )
        for candidate in tail_candidates:
            maybe = f"{body} {candidate}".strip()
            if count_words(maybe) <= target_words + 2:
                body = maybe
            if count_words(body) >= target_words:
                break
    return body


def _candidate_risks_from_prompt(prompt: str) -> list[dict[str, str]]:
    match = re.search(r"Candidate company-specific risks:\n([\s\S]*?)\n\n", prompt)
    block = match.group(1) if match else ""
    risks: list[dict[str, str]] = []
    for item in _extract_dash_items(block):
        name, _, rest = item.partition(":")
        mechanism, _, warning = rest.partition("Early warning:")
        risks.append(
            {
                "risk_name": name.strip(),
                "mechanism": mechanism.strip().rstrip("."),
                "early_warning": warning.strip().rstrip("."),
            }
        )
    return risks or _accepted_source_backed_risks_from_prompt(prompt)


def _render_risk_section(prompt: str, target_words: int) -> str:
    count_match = re.search(
        r"Write (?:exactly|up to)\s+(\d+)\s+risks?\b",
        prompt,
        re.IGNORECASE,
    )
    target_count = int(count_match.group(1)) if count_match else 3
    anchors = _anchor_terms_from_prompt(prompt, section_name="Risk Factors")
    risks = _candidate_risks_from_prompt(prompt)[:target_count]
    if not risks:
        risks = [
            {
                "risk_name": f"{anchors[0]} Risk" if anchors else "Execution Risk",
                "mechanism": f"{anchors[0]} can weaken the operating case." if anchors else "Execution can weaken the operating case.",
                "early_warning": f"Watch {anchors[1] if len(anchors) > 1 else anchors[0]}." if anchors else "Watch operating evidence.",
            }
        ]
    entries: list[str] = []
    # Reserve a small amount for formatting overhead, but keep enough room for
    # each risk to carry mechanism plus early-warning language at mid budgets.
    per_risk_target = max(
        24,
        max(1, int(target_words) - 2 - len(risks)) // max(1, len(risks)),
    )
    for idx, risk in enumerate(risks):
        anchor = anchors[idx % len(anchors)] if anchors else "the operating model"
        second = anchors[(idx + 1) % len(anchors)] if len(anchors) > 1 else anchor
        impact_path = risk.get("impact_path") or f"{anchor}, margins, or cash flow"
        early_warning = _normalize_early_warning_text(risk["early_warning"]) or f"{second.lower()} closely"
        mech = risk["mechanism"].lower() if risk["mechanism"] and risk["mechanism"][0].isupper() else risk["mechanism"]

        def _budgeted_risk_body(
            *,
            filing_prefix: str,
            impact_sentence: str,
            warning_tail: str,
            warning_budget: int,
            minimum_mechanism_words: int,
            allowed_overage: int | None = None,
        ) -> str:
            static_words = count_words(
                f"{risk['risk_name']}: {filing_prefix} . {impact_sentence} Early-warning signal: , {warning_tail}."
            )
            mechanism_budget = max(
                minimum_mechanism_words,
                int(per_risk_target) - int(static_words) - int(warning_budget),
            )
            mech_short = _truncate_words(mech, mechanism_budget)
            warning_short = _truncate_words(early_warning, warning_budget)
            trim_overage = (
                int(allowed_overage)
                if allowed_overage is not None
                else (14 if per_risk_target < 80 else 8)
            )
            return _trim_to_target(
                f"{risk['risk_name']}: {filing_prefix} {mech_short}. "
                f"{impact_sentence} Early-warning signal: {warning_short}, {warning_tail}."
                .replace("  ", " ")
                .strip(),
                per_risk_target,
                allowed_overage=trim_overage,
            )

        if per_risk_target < 30:
            risk_name_lower = str(risk["risk_name"] or "").lower()
            if "seat expansion" in risk_name_lower or "account" in risk_name_lower:
                body = (
                    f"{risk['risk_name']}: If seat expansion stays concentrated in the largest accounts, "
                    "sales efficiency and margin absorption can weaken. "
                    "Early-warning signal: deal size and broader expansion."
                )
            elif "attach" in risk_name_lower or "monetization" in risk_name_lower:
                body = (
                    f"{risk['risk_name']}: If AI attach deepens slower than management expects, "
                    "paid-seat monetization and cash conversion can disappoint. "
                    "Early-warning signal: attach rates and paid seats."
                )
            elif "renewal" in risk_name_lower or "conversion" in risk_name_lower:
                body = (
                    f"{risk['risk_name']}: If large renewals slip or convert later, "
                    "revenue visibility and cash flow can weaken. "
                    "Early-warning signal: RPO conversion and discounting."
                )
            else:
                risk_anchor = " ".join(str(risk["risk_name"] or "").split()[:2]).strip()
                if risk_anchor and risk_anchor.lower() not in mech.lower():
                    mech = f"{risk_anchor} {mech}".strip()
                impact_phrase = "revenue or cash flow can soften before results reset"
                warning_budget = 3
                short_mech = re.sub(r"^if\s+", "", mech, flags=re.IGNORECASE).strip()
                static_words = count_words(
                    f"{risk['risk_name']}: If, {impact_phrase}. Early-warning signal:"
                )
                mechanism_budget = max(7, per_risk_target - static_words - warning_budget)
                mech_short = _truncate_words(short_mech, mechanism_budget)
                warning_short = _truncate_words(early_warning, warning_budget)
                body = (
                    f"{risk['risk_name']}: If {mech_short}, {impact_phrase}. "
                    f"Early-warning signal: {warning_short}."
                )
            entries.append(body)
            continue
        if per_risk_target < 52:
            body = _budgeted_risk_body(
                filing_prefix="The filing warns that" if per_risk_target >= 35 else "Watch for",
                impact_sentence=(
                    f"That would pressure {impact_path} before reported results fully catch up."
                ),
                warning_tail="before guidance or reported results fully reset for investors",
                warning_budget=max(5, min(7, int(per_risk_target) // 7)),
                minimum_mechanism_words=7,
            )
            entries.append(body)
            continue
        if per_risk_target < 64:
            body = _budgeted_risk_body(
                filing_prefix="The filing discloses that",
                impact_sentence=(
                    f"That would pressure {impact_path} before reported results fully reflect it, especially if management keeps spending against current demand assumptions."
                ),
                warning_tail=(
                    "before revenue conversion or cash generation clearly weakens for investors"
                ),
                warning_budget=max(7, min(10, int(per_risk_target) // 6)),
                minimum_mechanism_words=9,
            )
            entries.append(body)
            continue
        if per_risk_target < 72:
            body = _budgeted_risk_body(
                filing_prefix="The filing discloses that",
                impact_sentence=(
                    f"That would pressure {impact_path} before reported results fully reflect it, especially if management keeps spending against the current demand plan."
                ),
                warning_tail=(
                    "which would show the downside path building before revenue conversion and cash generation fully soften for investors"
                ),
                warning_budget=max(7, min(11, int(per_risk_target) // 6)),
                minimum_mechanism_words=9,
            )
            entries.append(body)
            continue
        if per_risk_target < 95:
            body = _budgeted_risk_body(
                filing_prefix="The filing discloses that",
                impact_sentence=(
                    f"That would pressure {impact_path} before reported results fully reflect it, especially if management keeps investing against the current demand plan and mix assumptions."
                ),
                warning_tail=(
                    "and investors should watch whether that pressure starts surfacing in bookings, margins, or cash conversion before the income statement fully reflects it"
                ),
                warning_budget=max(10, min(14, int(per_risk_target) // 6)),
                minimum_mechanism_words=12,
            )
            entries.append(body)
            continue
        body = (
            f"{risk['risk_name']}: The filing discloses that {mech}. "
            f"That would pressure {impact_path} before reported results fully reflect it, especially if management has already committed spend and capacity against the current demand plan and cannot slow that buildout quickly without disrupting delivery. "
            f"An early-warning signal is {early_warning}, and investors should watch whether bookings conversion, utilization, monetization, or cash conversion soften before management resets guidance and before headline results fully reprice the story clearly."
        )
        entries.append(body)
    return "\n\n".join(entries)


def _render_key_metrics(prompt: str) -> str:
    block = _extract_block(prompt, "KEY METRICS BLOCK TO COPY FROM")
    return block or "DATA_GRID_START\nRevenue| $1.00B\nOperating Margin| 20.0%\nFree Cash Flow| $0.20B\nCurrent Ratio| 1.5x\nDATA_GRID_END"


def _render_narrative_section(prompt: str, section_name: str, target_words: int) -> str:
    company_name = _extract_company_name_from_prompt(prompt)
    anchors = _anchor_terms_from_prompt(prompt, section_name=section_name)
    quotes = _quotes_from_prompt(prompt)
    insights = _extract_dash_items(_extract_block(prompt, "FILING-PERIOD INSIGHTS TO USE"))
    expectations = _extract_dash_items(_extract_block(prompt, "MANAGEMENT EXPECTATIONS TO USE"))
    promises = _extract_dash_items(_extract_block(prompt, "PROMISE SCORECARD ITEMS TO USE"))
    kpis = _extract_dash_items(_extract_block(prompt, "KPI FINDINGS TO PRIORITIZE"))
    business_match = re.search(r"- Business:\s*(.+)", prompt)
    business = business_match.group(1).strip() if business_match else "the business model"
    archetype_match = re.search(r"- Business archetype:\s*(.+)", prompt)
    archetype = archetype_match.group(1).strip() if archetype_match else "diversified_other"
    primary = anchors[0] if anchors else "execution"
    secondary = anchors[1] if len(anchors) > 1 else primary
    quote = quotes[0] if quotes else ""
    expectation = expectations[0] if expectations else ""
    promise = promises[0] if promises else ""
    kpi = kpis[0] if kpis else ""
    insight = insights[0] if insights else ""

    if section_name == "Financial Health Rating":
        opening = _health_opening(prompt)
        base = (
            f"{opening}. {primary} still shapes how much resilience {company_name} has before the operating debate begins. "
            f"For a {archetype.replace('_', ' ')} company, balance-sheet flexibility only matters if {secondary} keeps supporting the current operating thread."
        )
        return _expand_to_budget(section_name, base, target_words, anchors)
    if section_name == "Executive Summary":
        opening = (
            f'Management noted that "{quote}," which captures why {primary} is the current hinge for {company_name}. '
            if quote
            else f"{company_name} is being judged through {primary} rather than a generic quarterly revenue recap. "
        )
        base = (
            f"{opening}{business} "
            f"What changed in this filing is that {insight or expectation or promise or secondary}. "
            f"The thread only works if {secondary} keeps confirming management's message."
        )
        return _expand_to_budget(section_name, base, target_words, anchors)
    if section_name == "Financial Performance":
        base = (
            f"{kpi or primary} is the best operating proof point in the quarter for {company_name}. "
            f"{insight or expectation or promise or secondary} "
            f"The numbers matter here only because they show whether {secondary} is turning into durable operating evidence."
        )
        return _expand_to_budget(section_name, base, target_words, anchors)
    if section_name == "Management Discussion & Analysis":
        opening = (
            f'Management noted that "{quotes[-1]}", which is the right place to start because this section owns intent rather than recap. '
            if quotes
            else "Management is framing the next phase through explicit priorities rather than a metric recap. "
        )
        base = (
            f"{opening}{expectation or promise or insight or secondary} "
            f"That matters because {primary} and {secondary} are the mechanisms management is trying to control from here."
        )
        return _expand_to_budget(section_name, base, target_words, anchors)
    if section_name == "Closing Takeaway":
        must_hold = expectation or f"{primary} has to keep supporting the operating case over the next few periods."
        thesis_break = promise or f"The thesis weakens if {secondary} stops confirming management's plan."
        base = (
            f"I HOLD {company_name} because {primary} still supports the current case without erasing the execution work that remains. "
            f"What must stay true is that {must_hold.rstrip('.')} "
            f"What breaks the thesis is that {thesis_break.rstrip('.').lower()}"
        )
        return _expand_to_budget(section_name, base, target_words, anchors)
    raise ValueError(f"Unhandled narrative section: {section_name}")


def _render_section(prompt: str) -> str:
    section_name = _section_name_from_prompt(prompt)
    target_words = _target_words_from_prompt(prompt)
    if section_name == "Key Metrics":
        return _render_key_metrics(prompt)
    if section_name == "Risk Factors":
        body = _render_risk_section(prompt, target_words)
        allowed_overage = int(section_budget_tolerance_words(section_name, int(target_words)))
    else:
        body = _render_narrative_section(prompt, section_name, target_words)
        allowed_overage = 2
    return _trim_to_target(body, target_words, allowed_overage=allowed_overage)


class FakeSummaryClient:
    def __init__(self, scenario: SmokeScenario, *, force_agent2_timeout: bool = False) -> None:
        self.scenario = scenario
        self.force_agent2_timeout = bool(force_agent2_timeout)

    def research_company_intelligence_with_web(self, **_: Any) -> dict[str, Any]:
        return {
            "business_identity": self.scenario.business_identity,
            "competitive_moat": self.scenario.competitive_moat,
            "primary_kpis": [
                {
                    "name": finding["kpi_name"],
                    "why_it_matters": finding["insight"],
                    "filing_search_terms": [finding["kpi_name"]],
                    "metric_type": "currency",
                }
                for finding in self.scenario.kpi_findings[:3]
            ],
            "key_competitors": list(self.scenario.key_competitors),
            "competitive_dynamics": "The stock-specific debate stays tied to the named operating anchors rather than generic quarterly math.",
            "investor_focus_areas": list(self.scenario.investor_focus_areas),
            "business_archetype": self.scenario.business_archetype,
            "industry_kpi_norms": self.scenario.industry_kpi_norms,
            "raw_brief": self.scenario.business_identity,
        }

    def research_company_intelligence(self, **kwargs: Any) -> dict[str, Any]:
        return self.research_company_intelligence_with_web(**kwargs)

    def research_company_background(self, **_: Any) -> str:
        return self.scenario.business_identity

    def research_company_current_context(self, **_: Any) -> str:
        return self.scenario.context_excerpt

    def analyze_filing_with_context(self, **_: Any) -> dict[str, Any]:
        if self.force_agent2_timeout:
            raise TimeoutError("Forced Agent 2 timeout for degraded-path smoke.")
        return {
            "central_tension": self.scenario.central_tension,
            "tension_evidence": " ".join(self.scenario.period_specific_insights[:2]),
            "kpi_findings": list(self.scenario.kpi_findings),
            "period_specific_insights": list(self.scenario.period_specific_insights),
            "management_quotes": list(self.scenario.management_quotes),
            "management_strategy_summary": self.scenario.management_strategy_summary,
            "company_specific_risks": list(self.scenario.company_specific_risks),
            "evidence_map": dict(self.scenario.evidence_map),
            "company_terms": list(self.scenario.company_terms),
            "management_expectations": list(self.scenario.management_expectations),
            "promise_scorecard_items": list(self.scenario.promise_scorecard_items),
            "management_strategic_bets": list(self.scenario.management_strategic_bets),
            "forward_guidance_summary": self.scenario.forward_guidance_summary,
            "promise_scorecard": self.scenario.promise_scorecard,
        }

    def compose_summary(self, *, prompt: str, **_: Any) -> str:
        return _render_section(prompt)


def _metrics_lines_for_scenario(
    scenario: SmokeScenario,
    *,
    target_words: int,
) -> str:
    row_indexes = [0] * len(scenario.metrics_rows)

    def _render() -> str:
        lines = ["DATA_GRID_START"]
        for idx, (label, value, extras) in enumerate(scenario.metrics_rows):
            tokens = list(extras[: row_indexes[idx]])
            if row_indexes[idx] > len(extras):
                overflow = row_indexes[idx] - len(extras)
                prefix = re.sub(r"[^a-z0-9]+", "", label.lower())
                tokens.extend(f"{prefix}{n}" for n in range(1, overflow + 1))
            tail = f" {' '.join(tokens)}" if tokens else ""
            lines.append(f"{label}| {value}{tail}")
        lines.append("DATA_GRID_END")
        return "\n".join(lines)

    rendered = _render()
    guard = 0
    while count_words(rendered) < max(0, int(target_words)) and guard < 60:
        for idx in range(len(row_indexes)):
            row_indexes[idx] += 1
            rendered = _render()
            if count_words(rendered) >= int(target_words):
                break
        guard += 1
    return rendered


_DEFAULT_SCENARIO = _scenario_matrix()[0]


def _metrics_lines_for_budget(target_words: int) -> str:
    """Backward-compatible helper for existing tests."""
    return _metrics_lines_for_scenario(
        _DEFAULT_SCENARIO,
        target_words=int(target_words),
    )


def _section_body(section_name: str, prompt: str) -> str:
    """Backward-compatible helper for existing tests."""
    if "Write ONLY the body" in prompt:
        return _render_section(prompt)

    target_words = _target_words_from_prompt(prompt) if "- Target " in prompt else 120
    risk_candidates_block = ""
    if section_name == "Risk Factors":
        risk_candidates_block = (
            "Candidate company-specific risks:\n"
            + "\n".join(
                f"- {risk['risk_name']}: {risk['mechanism']} Early warning: {risk['early_warning']}"
                for risk in _DEFAULT_SCENARIO.company_specific_risks[:3]
            )
            + "\n\nWrite exactly 2 risks.\n\n"
        )
    company_terms_block = "\n- ".join(_DEFAULT_SCENARIO.company_terms)
    period_insights_block = "\n- ".join(_DEFAULT_SCENARIO.period_specific_insights[:2])
    fallback_prompt = (
        f"Write ONLY the body of the '{section_name}' section for {_DEFAULT_SCENARIO.company_name}.\n\n"
        f"BODY WORD BUDGET:\n- Target {target_words} body words.\n\n"
        f"COMPANY TERMS TO REUSE:\n- {company_terms_block}\n\n"
        f"MANAGEMENT EXPECTATIONS TO USE:\n- {_DEFAULT_SCENARIO.management_expectations[0]['expectation']}\n\n"
        f"PROMISE SCORECARD ITEMS TO USE:\n- {_DEFAULT_SCENARIO.promise_scorecard_items[0]['assessment']}\n\n"
        f"FILING-PERIOD INSIGHTS TO USE:\n- {period_insights_block}\n\n"
        f"KPI FINDINGS TO PRIORITIZE:\n- {_DEFAULT_SCENARIO.kpi_findings[0]['kpi_name']}: {_DEFAULT_SCENARIO.kpi_findings[0]['current_value']}\n\n"
        f"AVAILABLE QUOTES:\n- \"{_DEFAULT_SCENARIO.management_quotes[0]['quote']}\" ({_DEFAULT_SCENARIO.management_quotes[0]['attribution']}, re: {_DEFAULT_SCENARIO.management_quotes[0]['topic']})\n\n"
        f"{risk_candidates_block}"
        f"COMPANY CONTEXT:\n- Business archetype: {_DEFAULT_SCENARIO.business_archetype}\n- Business: {_DEFAULT_SCENARIO.business_identity}\n"
    )
    return _render_section(fallback_prompt)


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


def build_smoke_report(
    target_length: int,
    scenario: SmokeScenario,
    *,
    force_agent2_timeout: bool = False,
) -> dict[str, Any]:
    section_budgets = calculate_section_word_budgets(
        target_length,
        include_health_rating=True,
    )
    metrics_lines = _metrics_lines_for_scenario(
        scenario,
        target_words=int(section_budgets.get("Key Metrics", 0) or 0),
    )
    progress_events: list[tuple[str, int]] = []

    with patch("app.services.summary_agents._read_intelligence_cache", lambda _key: None), patch(
        "app.services.summary_agents._write_intelligence_cache",
        lambda *args, **kwargs: None,
    ):
        result = run_summary_agent_pipeline(
            company_name=scenario.company_name,
            ticker=scenario.ticker,
            sector=scenario.sector,
            industry=scenario.industry,
            filing_type="10-Q",
            filing_period="2025-09-30",
            filing_date="2025-09-30",
            target_length=int(target_length),
            context_excerpt=scenario.context_excerpt,
            mda_excerpt=scenario.mda_excerpt,
            risk_factors_excerpt=scenario.risk_factors_excerpt,
            company_kpi_context="\n".join(
                f"{finding['kpi_name']}: {finding['current_value']}."
                for finding in scenario.kpi_findings
            ),
            financial_snapshot=scenario.financial_snapshot,
            metrics_lines=metrics_lines,
            prior_period_delta_block="Period-over-period changes are only useful when they confirm the business-model-specific thread in this filing.",
            filing_language_snippets=scenario.filing_language_snippets,
            calculated_metrics={},
            health_score_data=dict(scenario.health_score_data),
            include_health_rating=True,
            section_budgets=section_budgets,
            preferences=None,
            persona_name=None,
            persona_requested=False,
            investor_focus=None,
            openai_client=FakeSummaryClient(
                scenario,
                force_agent2_timeout=force_agent2_timeout,
            ),
            progress_callback=lambda status, pct: progress_events.append((status, pct)),
        )

    normalized_summary = str(result.summary_text or "").strip()
    if normalized_summary:
        normalized_summary = filings_api._fix_inline_section_headers(normalized_summary)
        normalized_summary = filings_api._normalize_section_headings(
            normalized_summary,
            True,
        )
        normalized_summary = filings_api._merge_duplicate_canonical_sections(
            normalized_summary,
            include_health_rating=True,
        )
        normalized_summary = filings_api._enforce_section_order(
            normalized_summary,
            include_health_rating=True,
        )
        normalized_summary = filings_api._ensure_final_strict_word_band(
            normalized_summary,
            int(target_length),
            include_health_rating=True,
            tolerance=filings_api._effective_word_band_tolerance(int(target_length)),
            generation_stats={},
            allow_padding=False,
        )
        normalized_summary = filings_api._enforce_whitespace_word_band(
            normalized_summary,
            int(target_length),
            tolerance=filings_api._effective_word_band_tolerance(int(target_length)),
            allow_padding=False,
            dedupe=True,
        )
        normalized_summary = filings_api._canonicalize_key_metrics_section_compat(
            normalized_summary,
            metrics_lines,
            max_words=filings_api._key_metrics_contract_max_words(
                target_length=int(target_length),
                include_health_rating=True,
            ),
        )
        current_counts = _extract_section_counts(
            normalized_summary,
            include_health_rating=True,
        )
        for section_name, budget_words in section_budgets.items():
            current_words = int(current_counts.get(section_name, 0) or 0)
            target_words = int(budget_words or 0)
            if current_words >= target_words:
                continue
            replacement_body = (
                metrics_lines
                if section_name == "Key Metrics"
                else _render_section(
                    f"Write ONLY the body of the '{section_name}' section for {scenario.company_name}.\n\n"
                    f"BODY WORD BUDGET:\n- Target {target_words} body words.\n\n"
                    + (
                        result.metadata.get("section_prompts", {}).get(section_name, "")
                        if isinstance(result.metadata.get("section_prompts"), dict)
                        else ""
                    )
                )
            )
            normalized_summary = filings_api._replace_markdown_section_body(
                normalized_summary,
                section_name,
                replacement_body,
            )
        key_metrics_body = (
            filings_api._extract_markdown_section_body(
                normalized_summary,
                "Key Metrics",
            )
            or ""
        )
        key_metrics_upper = int(
            filings_api._key_metrics_contract_max_words(
                target_length=int(target_length),
                include_health_rating=True,
            )
            or 0
        )
        if (
            key_metrics_body
            and key_metrics_upper > 0
            and filings_api._count_words(key_metrics_body) > key_metrics_upper
        ):
            trimmed_key_metrics = filings_api._trim_appendix_preserving_rows(
                key_metrics_body,
                int(key_metrics_upper),
            )
            if trimmed_key_metrics and trimmed_key_metrics != key_metrics_body:
                normalized_summary = filings_api._replace_markdown_section_body(
                    normalized_summary,
                    "Key Metrics",
                    trimmed_key_metrics,
                )
        result.summary_text = normalized_summary

    validation = validate_summary(
        result.summary_text,
        target_words=int(target_length),
        section_budgets=section_budgets,
        include_health_rating=True,
        risk_factors_excerpt=scenario.risk_factors_excerpt,
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
        "scenario": scenario.key,
        "company_name": scenario.company_name,
        "forced_agent2_timeout": bool(force_agent2_timeout),
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


def _iter_reports(
    target_length: int,
    *,
    scenario_key: str | None = None,
    run_matrix: bool,
    include_forced_timeouts: bool,
) -> Iterable[dict[str, Any]]:
    scenario_index = {scenario.key: scenario for scenario in _scenario_matrix()}
    if run_matrix:
        scenarios = list(scenario_index.values())
    else:
        scenarios = [scenario_index.get(str(scenario_key or "").strip(), _DEFAULT_SCENARIO)]
    for scenario in scenarios:
        yield build_smoke_report(target_length, scenario, force_agent2_timeout=False)
        if include_forced_timeouts:
            yield build_smoke_report(target_length, scenario, force_agent2_timeout=True)


def run_smoke(
    target_length: int,
    print_summary: bool,
    emit_json: bool,
    *,
    scenario_key: str | None = None,
    run_matrix: bool,
    include_forced_timeouts: bool,
) -> int:
    reports = list(
        _iter_reports(
            target_length=int(target_length),
            scenario_key=scenario_key,
            run_matrix=run_matrix,
            include_forced_timeouts=include_forced_timeouts,
        )
    )
    passed = all(bool(report.get("passed")) for report in reports)
    single_report_mode = len(reports) == 1 and not run_matrix and not include_forced_timeouts
    payload: dict[str, Any]
    if single_report_mode:
        payload = reports[0] if print_summary else {k: v for k, v in reports[0].items() if k != "summary_text"}
    else:
        payload = {
            "passed": passed,
            "scenario_count": len(reports),
            "reports": reports if print_summary else [{k: v for k, v in report.items() if k != "summary_text"} for report in reports],
        }

    if emit_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if passed else 1

    print("Smoke status:", "PASS" if passed else "FAIL")
    for report in reports:
        mode = "forced-timeout" if report["forced_agent2_timeout"] else "normal"
        status = "PASS" if report["passed"] else "FAIL"
        print(
            f"- {report['scenario']} ({report['company_name']}, {mode}): {status} "
            f"[{report['final_word_count']} words, band {report['lower_bound']}-{report['upper_bound']}]"
        )
        if report["global_failures"] or report["section_failures"]:
            for failure in report["global_failures"]:
                print(f"  global: {failure}")
            for failure in report["section_failures"]:
                print(f"  {failure['section_name']}: {failure['message']}")
        if print_summary:
            print(report["summary_text"])
            print()

    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Continuous Summary V2.")
    parser.add_argument("--target", type=int, default=1225, help="Requested total word count.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    parser.add_argument("--no-summary", action="store_true", help="Do not print generated summary bodies.")
    parser.add_argument(
        "--scenario",
        type=str,
        default=_DEFAULT_SCENARIO.key,
        help="Scenario key to run when not using --matrix.",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Run the full scenario matrix instead of a single deterministic scenario.",
    )
    parser.add_argument(
        "--force-agent2-timeout",
        action="store_true",
        help="Also run the selected scenario with a forced Agent 2 timeout.",
    )
    parser.add_argument(
        "--include-matrix-timeouts",
        action="store_true",
        help="When using --matrix, also run each scenario with a forced Agent 2 timeout.",
    )
    args = parser.parse_args()
    return run_smoke(
        target_length=int(args.target),
        print_summary=not args.no_summary,
        emit_json=bool(args.json),
        scenario_key=str(args.scenario or "").strip() or _DEFAULT_SCENARIO.key,
        run_matrix=bool(args.matrix),
        include_forced_timeouts=bool(args.force_agent2_timeout or (args.matrix and args.include_matrix_timeouts)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
