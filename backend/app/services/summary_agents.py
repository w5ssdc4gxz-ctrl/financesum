"""Three-agent summary pipeline for filing summaries.

Replaces the monolithic single-LLM-call generation with a focused pipeline:

    Agent 1 — Company Intelligence Agent
        Identifies what makes a company unique + which KPIs matter.
        Output is cached (30-day TTL) in the existing company_research_cache table.

    Agent 2 — Filing Analysis Agent
        Reads the filing with company context and extracts company-specific
        insights, KPI findings, management quotes, and an evidence map.
        NOT cached (per-filing).

    Agent 3 — Summary Composition Agent
        Writes the final memo using curated insights from Agents 1 & 2.
        Uses the EXACT word target (no overshoot).

Public API
----------
run_summary_agent_pipeline(...)  → PipelineResult
    The pipeline orchestrator called by filings.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.services.risk_evidence import (
    RiskEvidenceCandidate,
    assess_risk_overlap,
    build_risk_evidence_candidates,
    candidate_is_strictly_acceptable,
    candidate_to_evidence_line,
    extract_anchor_terms,
    is_filing_fragment_risk_name,
    is_filing_structure_line,
    is_fragment_quote,
    is_generic_risk_name,
    is_metric_only_risk_name,
    looks_boilerplate_risk_body,
    looks_numeric_led,
    score_risk_evidence_candidate,
)
from app.services.prompt_pack import (
    ANTI_BOREDOM_RULES,
    QUOTE_BEHAVIOR_SPEC,
    SECTION_ORDER,
    SECTION_TEMPLATES,
)
from app.services.summary_budget_controller import (
    CANONICAL_SECTION_ORDER,
    compute_depth_plan,
    compute_scale_factor,
    describe_paragraph_range,
    describe_sentence_range,
    get_closing_takeaway_shape,
    get_financial_health_shape,
    get_risk_factors_shape,
    risk_budget_target_count,
    section_budget_tolerance_words,
    total_word_tolerance_words,
)
from app.services.repetition_guard import check_repetition, detect_similar_paragraphs
from app.services.summary_post_processor import (
    PostProcessResult,
    post_process_summary,
    validate_summary,
)
from app.services.word_surgery import count_words

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache constants (for Agent 1)
# ---------------------------------------------------------------------------
INTELLIGENCE_CACHE_TABLE = "company_research_cache"
INTELLIGENCE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

BUSINESS_ARCHETYPES: Tuple[str, ...] = (
    "cloud_software",
    "semicap_hardware",
    "industrial_manufacturing",
    "retail_consumer",
    "payments_marketplaces",
    "bank",
    "insurance_asset_manager",
    "pharma_biotech_medtech",
    "energy_materials_utilities",
    "telecom_media_ads",
    "diversified_other",
)

_ARCHETYPE_CONFIG: Dict[str, Dict[str, Any]] = {
    "cloud_software": {
        "keywords": (
            "arr",
            "annual recurring revenue",
            "net revenue retention",
            "remaining performance obligations",
            "subscription",
            "cloud",
            "seats",
            "usage",
            "enterprise agreements",
            "copilot",
            "saas",
        ),
        "identity": "subscription or usage-linked software platform monetized through renewals, seat expansion, and product attach",
        "focus_areas": (
            "renewal quality and expansion depth",
            "pricing power versus product investment",
            "backlog or RPO conversion into recognized revenue",
        ),
        "default_terms": (
            "renewals",
            "backlog",
            "usage monetization",
            "enterprise accounts",
            "product attach",
            "pricing discipline",
        ),
        "fallback_kpis": (
            (
                "Annual Recurring Revenue",
                "Tracks whether the recurring revenue base is compounding fast enough to support valuation and reinvestment.",
                ("arr", "annual recurring revenue", "subscription revenue"),
                "currency",
            ),
            (
                "Net Revenue Retention",
                "Shows whether existing customers are expanding despite pricing and competitive pressure.",
                ("net revenue retention", "ndr", "expansion"),
                "percentage",
            ),
            (
                "Remaining Performance Obligations",
                "Measures forward demand visibility and backlog conversion potential.",
                ("remaining performance obligations", "rpo", "backlog"),
                "currency",
            ),
        ),
    },
    "semicap_hardware": {
        "keywords": (
            "euv",
            "duv",
            "lithography",
            "wafer",
            "scanner",
            "installed base",
            "backlog",
            "shipment",
            "node",
            "semi",
            "semiconductor equipment",
        ),
        "identity": "capital-equipment supplier whose economics depend on tool demand, shipment timing, service mix, and customer fab investment cycles",
        "focus_areas": (
            "backlog conversion into shipments and revenue",
            "installed-base service mix and margin resilience",
            "customer node transitions and fab-capex timing",
        ),
        "default_terms": (
            "EUV",
            "DUV",
            "installed base",
            "backlog",
            "shipments",
            "node transitions",
        ),
        "fallback_kpis": (
            (
                "Backlog",
                "Shows how much future demand is already committed and how exposed revenue is to customer timing shifts.",
                ("backlog", "order book", "bookings"),
                "currency",
            ),
            (
                "Installed Base Management Revenue",
                "Captures the stickier service and upgrade revenue stream that cushions shipment volatility.",
                ("installed base", "service revenue", "field option", "upgrade"),
                "currency",
            ),
            (
                "System Sales / Shipments",
                "Measures how quickly demand is converting into recognized tool revenue.",
                ("shipments", "system sales", "recognized revenue"),
                "currency",
            ),
        ),
    },
    "industrial_manufacturing": {
        "keywords": (
            "backlog",
            "order intake",
            "book-to-bill",
            "aftermarket",
            "utilization",
            "project",
            "service revenue",
            "manufacturing",
            "industrial",
        ),
        "identity": "industrial operator whose cash generation depends on backlog conversion, pricing recovery, plant utilization, and service attachment",
        "focus_areas": (
            "order intake versus backlog conversion",
            "pricing recovery versus input-cost pressure",
            "project execution and service mix",
        ),
        "default_terms": (
            "order intake",
            "backlog",
            "aftermarket",
            "utilization",
            "service revenue",
            "projects",
        ),
        "fallback_kpis": (
            (
                "Order Intake",
                "Shows whether demand is refilling the backlog fast enough to sustain plant loading.",
                ("order intake", "orders", "book-to-bill"),
                "currency",
            ),
            (
                "Backlog",
                "Indicates the revenue base that still has to convert through execution.",
                ("backlog", "order book"),
                "currency",
            ),
            (
                "Service / Aftermarket Revenue",
                "Higher service mix usually supports margin stability through the cycle.",
                ("service revenue", "aftermarket", "maintenance"),
                "currency",
            ),
        ),
    },
    "retail_consumer": {
        "keywords": (
            "same-store sales",
            "comparable sales",
            "inventory",
            "traffic",
            "promotions",
            "store count",
            "sell-through",
            "average ticket",
            "consumer",
            "retail",
        ),
        "identity": "consumer-facing operator monetized through traffic, basket size, merchandise mix, and promotional discipline",
        "focus_areas": (
            "comparable sales and traffic quality",
            "inventory discipline versus promotional pressure",
            "gross-margin stability through the demand cycle",
        ),
        "default_terms": (
            "same-store sales",
            "traffic",
            "inventory",
            "promotions",
            "average ticket",
            "store productivity",
        ),
        "fallback_kpis": (
            (
                "Comparable Sales",
                "Tests whether demand is improving in the existing store base rather than through footprint growth alone.",
                ("comparable sales", "same-store sales", "comp sales"),
                "percentage",
            ),
            (
                "Inventory Turns",
                "Signals whether working capital and markdown risk are staying under control.",
                ("inventory", "inventory turns", "markdown"),
                "ratio",
            ),
            (
                "Gross Margin",
                "Shows whether promotions and mix are eroding merchandise economics.",
                ("gross margin", "merchandise margin", "promotions"),
                "percentage",
            ),
        ),
    },
    "payments_marketplaces": {
        "keywords": (
            "tpv",
            "gpv",
            "take rate",
            "merchant",
            "transactions",
            "chargebacks",
            "consumer credit",
            "marketplace",
            "payment volume",
            "bnpl",
        ),
        "identity": "payments or marketplace platform that monetizes transaction volume, merchant mix, take rate, and loss discipline",
        "focus_areas": (
            "payment volume quality versus take-rate pressure",
            "merchant/acquirer mix and loss performance",
            "funding, fraud, and credit costs",
        ),
        "default_terms": (
            "payment volume",
            "take rate",
            "merchant mix",
            "chargebacks",
            "funding costs",
            "consumer losses",
        ),
        "fallback_kpis": (
            (
                "Total Payment Volume",
                "Measures whether the platform is still capturing transaction share and merchant activity.",
                ("payment volume", "tpv", "gpv"),
                "currency",
            ),
            (
                "Take Rate",
                "Shows whether monetization is holding as competition and merchant mix change.",
                ("take rate", "transaction margin", "net revenue yield"),
                "percentage",
            ),
            (
                "Transaction Loss / Credit Loss Rate",
                "Tests whether growth is being purchased with weaker underwriting or fraud discipline.",
                ("charge-offs", "loss rate", "fraud", "chargebacks"),
                "percentage",
            ),
        ),
    },
    "bank": {
        "keywords": (
            "net interest margin",
            "nim",
            "cet1",
            "deposits",
            "loans",
            "charge-offs",
            "allowance",
            "nonperforming",
            "credit quality",
            "bank",
        ),
        "identity": "deposit-funded lender whose earnings depend on spread income, credit quality, and capital strength",
        "focus_areas": (
            "deposit mix and funding costs",
            "loan growth versus credit quality",
            "capital ratios and reserve adequacy",
        ),
        "default_terms": (
            "net interest margin",
            "deposits",
            "loan growth",
            "charge-offs",
            "CET1",
            "credit quality",
        ),
        "fallback_kpis": (
            (
                "Net Interest Margin",
                "Measures whether the bank is preserving spread income as rates and funding costs move.",
                ("net interest margin", "nim", "net interest income"),
                "percentage",
            ),
            (
                "Deposit Growth / Mix",
                "Deposit stability determines funding quality and margin resilience.",
                ("deposits", "deposit mix", "noninterest-bearing"),
                "currency",
            ),
            (
                "Credit Quality",
                "Provisioning, charge-offs, and nonperforming assets show whether growth is still being underwritten prudently.",
                ("charge-offs", "nonperforming", "allowance", "provision"),
                "percentage",
            ),
        ),
    },
    "insurance_asset_manager": {
        "keywords": (
            "combined ratio",
            "aum",
            "premium",
            "claims",
            "reserve",
            "spread income",
            "asset manager",
            "insurance",
            "book value",
        ),
        "identity": "insurer or asset manager whose economics depend on underwriting discipline, client flows, and reserve/capital management",
        "focus_areas": (
            "combined ratio or underwriting margin discipline",
            "client flows and fee base stability",
            "reserve development and capital deployment",
        ),
        "default_terms": (
            "combined ratio",
            "premium growth",
            "claims",
            "AUM",
            "fee flows",
            "reserves",
        ),
        "fallback_kpis": (
            (
                "Combined Ratio / Underwriting Margin",
                "Measures whether pricing is outrunning claims and expense pressure.",
                ("combined ratio", "underwriting margin", "loss ratio"),
                "percentage",
            ),
            (
                "Assets Under Management / Net Flows",
                "Shows whether the fee base is compounding or being eroded by outflows and market mix.",
                ("assets under management", "aum", "net flows"),
                "currency",
            ),
            (
                "Capital / Reserve Strength",
                "Reserve development and statutory capital determine how much flexibility management really has.",
                ("reserves", "capital", "book value", "rbc"),
                "ratio",
            ),
        ),
    },
    "pharma_biotech_medtech": {
        "keywords": (
            "pipeline",
            "trial",
            "phase",
            "launch",
            "fda",
            "reimbursement",
            "patent",
            "biotech",
            "pharma",
            "medtech",
        ),
        "identity": "life-sciences business whose value depends on product launches, pipeline milestones, reimbursement, and patent durability",
        "focus_areas": (
            "launch uptake and channel inventory",
            "pipeline timing and regulatory milestones",
            "reimbursement, pricing, and exclusivity risk",
        ),
        "default_terms": (
            "pipeline",
            "launch uptake",
            "trial readout",
            "reimbursement",
            "patent life",
            "regulatory milestones",
        ),
        "fallback_kpis": (
            (
                "Launch Uptake",
                "Early prescription or procedure adoption shows whether new products can offset legacy erosion.",
                ("launch", "uptake", "demand"),
                "count",
            ),
            (
                "Pipeline Milestones",
                "The next value inflection often depends more on trials and approvals than on current-quarter revenue.",
                ("pipeline", "phase", "approval", "trial"),
                "count",
            ),
            (
                "Gross-to-Net / Pricing / Reimbursement",
                "Reimbursement and pricing pressure determine how much launch demand translates into real economics.",
                ("reimbursement", "pricing", "gross-to-net"),
                "percentage",
            ),
        ),
    },
    "energy_materials_utilities": {
        "keywords": (
            "production",
            "realized price",
            "reserves",
            "throughput",
            "utility",
            "commodity",
            "hedge",
            "refining",
            "project",
            "materials",
        ),
        "identity": "resource or utility operator whose cash generation depends on production reliability, realized pricing, and project or rate-base execution",
        "focus_areas": (
            "volume reliability versus price realization",
            "cost inflation and project execution",
            "capital intensity versus return discipline",
        ),
        "default_terms": (
            "production volumes",
            "realized pricing",
            "project execution",
            "rate base",
            "turnarounds",
            "hedging",
        ),
        "fallback_kpis": (
            (
                "Production / Throughput",
                "Volume reliability is the first driver of whether price and margin assumptions can hold.",
                ("production", "throughput", "utilization"),
                "count",
            ),
            (
                "Realized Price / Rate Base Growth",
                "Shows whether the operator is translating market conditions or regulated investment into actual economics.",
                ("realized price", "rate base", "yield"),
                "currency",
            ),
            (
                "Project / Capex Execution",
                "Returns can deteriorate quickly if large projects slip or overrun.",
                ("capex", "project", "turnaround"),
                "currency",
            ),
        ),
    },
    "telecom_media_ads": {
        "keywords": (
            "arpu",
            "subscribers",
            "churn",
            "ad load",
            "content spend",
            "engagement",
            "wireless",
            "broadband",
            "media",
            "ads",
        ),
        "identity": "connectivity, media, or ad-supported platform whose economics depend on subscriber retention, monetization per user, and content/network discipline",
        "focus_areas": (
            "subscriber growth and churn quality",
            "ARPU or monetization per user",
            "content or network spend versus monetization lift",
        ),
        "default_terms": (
            "ARPU",
            "subscribers",
            "churn",
            "ad monetization",
            "content spend",
            "network investment",
        ),
        "fallback_kpis": (
            (
                "Subscriber / Account Growth",
                "Shows whether the distribution base is still expanding profitably.",
                ("subscribers", "accounts", "adds"),
                "count",
            ),
            (
                "ARPU / Monetization Per User",
                "Tests whether engagement is translating into better monetization rather than just higher cost-to-serve.",
                ("arpu", "average revenue per user", "monetization"),
                "currency",
            ),
            (
                "Churn / Retention",
                "Retention determines whether customer acquisition and content/network spending are actually compounding.",
                ("churn", "retention", "disconnects"),
                "percentage",
            ),
        ),
    },
    "diversified_other": {
        "keywords": (),
        "identity": "operating business whose value depends on execution discipline, cash generation, and the specific product, customer, or regulatory terms highlighted in the filing",
        "focus_areas": (
            "which operating terms in the filing actually explain demand and margin behavior",
            "whether management expectations are being met",
            "which concrete exposures could change the current view",
        ),
        "default_terms": (
            "operating mix",
            "customer demand",
            "product mix",
            "pricing",
            "execution",
            "cash generation",
        ),
        "fallback_kpis": (),
    },
}

_GENERIC_COMPANY_TERM_STOPWORDS: Set[str] = {
    "about",
    "across",
    "adjusted",
    "assets",
    "balance sheet",
    "business",
    "capital",
    "cash",
    "cash conversion",
    "cash flow",
    "company",
    "customers",
    "demand",
    "earnings",
    "equity",
    "expenses",
    "financial",
    "financing",
    "flexibility",
    "free cash flow",
    "funding",
    "future",
    "general",
    "growth",
    "guidance",
    "income",
    "industry",
    "investment",
    "investments",
    "investor",
    "liabilities",
    "liquidity",
    "management",
    "margin",
    "margins",
    "market",
    "metrics",
    "operations",
    "outlook",
    "period",
    "performance",
    "pricing",
    "productivity",
    "profit",
    "profitability",
    "quarter",
    "quarters",
    "reinvestment",
    "results",
    "revenue",
    "risk",
    "sales",
    "section",
    "segments",
    "strategy",
    "terms",
    "thesis",
    "working capital",
    "year",
    "years",
}

_FALLBACK_TERM_LEADING_NOISE_RE = re.compile(
    r"^(?:and|or|but|while|with|without|as|at|by|for|from|in|into|of|on|over|through|to|via|versus|vs\.?|that|this|these|those|our|their|its|the|a|an)\s+",
    re.IGNORECASE,
)
_FALLBACK_TERM_TRAILING_STATUS_RE = re.compile(
    r"\b(?:improved|improve|improving|declined|decline|declining|remained|remain|remaining|stayed|stay|staying|still|stable|constructive|manageable|contained|healthy|elevated|supportive|stronger|weaker|better|worse|positive|negative|up|down|higher|lower|faster|slower|earlier|later|readiness|ready)\b$",
    re.IGNORECASE,
)
_FALLBACK_GENERIC_SINGLE_TOKENS: Set[str] = {
    "account",
    "activity",
    "bank",
    "business",
    "capital",
    "company",
    "conversion",
    "deposit",
    "dynamics",
    "evidence",
    "execution",
    "filing",
    "focus",
    "management",
    "merchant",
    "model",
    "order",
    "payment",
    "platform",
    "process",
    "product",
    "program",
    "project",
    "quality",
    "results",
    "service",
    "timing",
    "volume",
}

_FALLBACK_MANAGEMENT_ATTRIBUTION_RE = re.compile(
    r"\b("
    r"management|leadership|executives?|ceo|cfo|chief executive|chief financial|"
    r"noted|said|stated|indicated|explained|emphasized|highlighted|cautioned|"
    r"expects?|believes?|plans?|priorit(?:y|ies|ize|ized|izing)|guidance|outlook"
    r")\b",
    re.IGNORECASE,
)
_FALLBACK_MANAGEMENT_SIGNAL_RE = re.compile(
    r"\b("
    r"adoption|attach|backlog|capacity|continue|customer|customers|demand|deploy|"
    r"expand|expect|focus|guidance|improve|investment|launch|margin|monetiz|"
    r"next|outlook|pipeline|plan|pricing|priorit|renewal|ship|strategy|usage|will"
    r")\b",
    re.IGNORECASE,
)
_FALLBACK_ACCOUNTING_DISCLOSURE_RE = re.compile(
    r"\b("
    r"accumulated|amortized|as of|cash equivalents?|classified as short[- ]term|"
    r"condensed consolidated|fair value|marketable securities|maturities beyond one year|"
    r"note\s+\d+|stock[- ]based compensation|tax (?:benefit|expense|provision)|unaudited"
    r")\b",
    re.IGNORECASE,
)
_FALLBACK_RISK_LEGAL_QUOTE_RE = re.compile(
    r"\b("
    r"actual results?|adversely affect|could differ materially|may differ materially|"
    r"risk factors?|risks and uncertainties|safe harbor|subject to"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data models — Agent 1 output
# ---------------------------------------------------------------------------


@dataclass
class KPIDescriptor:
    """A company-specific Key Performance Indicator descriptor."""

    name: str  # e.g. "Paid Subscribers"
    why_it_matters: str  # 1-sentence explanation
    filing_search_terms: List[str]  # Terms to find in filing text
    metric_type: str = "currency"  # "count"|"currency"|"percentage"|"ratio"


@dataclass
class CompanyIntelligenceProfile:
    """Structured output from Agent 1 — Company Intelligence Agent."""

    business_identity: str  # What they do, how they make money
    competitive_moat: str  # Specific competitive advantage
    primary_kpis: List[KPIDescriptor]  # 3-5 company-specific KPIs
    key_competitors: List[str]  # 2-4 competitor names
    competitive_dynamics: str  # 1-2 sentences
    investor_focus_areas: List[str]  # 3-5 analytical questions
    industry_kpi_norms: str  # What "good" looks like
    raw_brief: str  # Backward-compatible flat text
    business_archetype: str = "diversified_other"
    from_cache: bool = False


# ---------------------------------------------------------------------------
# Data models — Agent 2 output
# ---------------------------------------------------------------------------


@dataclass
class KPIFinding:
    """An actual KPI value found in the filing."""

    kpi_name: str
    current_value: str
    prior_value: Optional[str] = None
    change: Optional[str] = None
    change_direction: Optional[str] = None  # "improved"|"declined"|"stable"
    insight: str = ""
    source_quote: str = ""


@dataclass
class ManagementQuote:
    """A verbatim quote extracted from the filing."""

    quote: str
    attribution: str  # "CEO", "CFO", "Management"
    topic: str  # e.g. "growth strategy"
    suggested_section: str  # Which memo section it fits


@dataclass
class CompanyRisk:
    """A company-specific risk with mechanism and early warning."""

    risk_name: str
    mechanism: str
    early_warning: str
    evidence_from_filing: str
    source_section: str = ""
    source_quote: str = ""


@dataclass
class ManagementExpectation:
    """A concrete forward-looking management expectation grounded in the filing."""

    topic: str
    expectation: str
    timeframe: str
    evidence: str


@dataclass
class PromiseScorecardItem:
    """A management commitment assessment for promise-vs-delivery writing."""

    commitment: str
    status: str
    assessment: str
    evidence: str


@dataclass
class FilingAnalysis:
    """Structured output from Agent 2 — Filing Analysis Agent."""

    central_tension: str  # ONE strategic question
    tension_evidence: str  # 2-3 supporting sentences
    kpi_findings: List[KPIFinding]
    period_specific_insights: List[str]  # 3-5 unique facts
    management_quotes: List[ManagementQuote]
    management_strategy_summary: str
    company_specific_risks: List[CompanyRisk]
    evidence_map: Dict[str, List[str]]  # section_name → data points
    company_terms: List[str] = field(default_factory=list)
    management_expectations: List[ManagementExpectation] = field(default_factory=list)
    promise_scorecard_items: List[PromiseScorecardItem] = field(default_factory=list)
    management_strategic_bets: List[str] = field(default_factory=list)
    forward_guidance_summary: str = ""
    promise_scorecard: str = ""
    decisive_watch_metrics: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data models — Agent 3 composition controls
# ---------------------------------------------------------------------------


@dataclass
class SectionBlueprint:
    """Section-level narrative ownership derived from filing analysis."""

    section_name: str
    section_job: str
    section_question: str
    primary_evidence: List[str] = field(default_factory=list)
    secondary_evidence: List[str] = field(default_factory=list)
    banned_overlap: List[str] = field(default_factory=list)
    subtle_handoff: str = ""


@dataclass
class NarrativeBlueprint:
    """Memo-wide narrative spine used to differentiate section roles."""

    memo_thread: str
    section_blueprints: Dict[str, SectionBlueprint] = field(default_factory=dict)


@dataclass
class ThreadCandidate:
    """A possible memo thread candidate before arbitration."""

    source: str
    candidate_text: str
    anchor: str
    anchor_class: str
    support_evidence: List[str] = field(default_factory=list)
    accepted: bool = False
    rejection_reason: str = ""
    score: float = 0.0
    score_reasons: List[str] = field(default_factory=list)


@dataclass
class ThreadDecision:
    """Validated memo thread used by all downstream section writers."""

    final_thread: str
    anchor: str
    anchor_class: str
    aha_insight: str
    support_evidence: List[str] = field(default_factory=list)
    score: float = 0.0
    score_reasons: List[str] = field(default_factory=list)
    rejected_threads: List[ThreadCandidate] = field(default_factory=list)


@dataclass
class InstructionCheck:
    """Concrete section-instruction compliance check."""

    section_name: str
    check_type: str
    target: str
    guidance: str


@dataclass
class SectionPlan:
    """Hard section ownership plan for the section writers."""

    section_name: str
    job: str
    question: str
    owned_evidence: List[str] = field(default_factory=list)
    callback_evidence: List[str] = field(default_factory=list)
    forbidden_themes: List[str] = field(default_factory=list)
    forbidden_openings: List[str] = field(default_factory=list)
    tone_mode: str = ""
    readability_mode: str = ""
    instruction_checks: List[InstructionCheck] = field(default_factory=list)


@dataclass
class EditorialFailure:
    """Targeted quality failure found by the editorial judge."""

    section_name: str
    code: str
    message: str
    severity: float = 1.0


@dataclass
class SectionMemory:
    """Tracks which themes and anchors earlier sections already used."""

    used_claims: List[str] = field(default_factory=list)
    used_theme_keys: List[str] = field(default_factory=list)
    used_anchor_metrics: List[str] = field(default_factory=list)
    used_company_terms: List[str] = field(default_factory=list)
    used_management_topics: List[str] = field(default_factory=list)
    used_promise_items: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Full result from the 3-agent pipeline."""

    summary_text: str
    company_intelligence: CompanyIntelligenceProfile
    filing_analysis: FilingAnalysis
    agent_timings: Dict[str, float] = field(default_factory=dict)
    total_llm_calls: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class SummarySectionBalanceError(Exception):
    """Raised when Continuous V2 cannot satisfy section-balance requirements."""

    def __init__(self, detail: Dict[str, Any]):
        self.detail = detail
        super().__init__(str(detail.get("detail") or "Summary section balance failed."))


_GENERIC_AGENT_RISK_NAME_RE = re.compile(
    r"\b("
    r"margin\s*/?\s*reinvestment risk|cash conversion\s*/?\s*(?:capex )?risk|"
    r"liquidity\s*/?\s*funding risk|reinvestment risk|capex risk|funding risk|"
    r"margin risk|liquidity risk|cash flow risk|execution risk|demand risk|growth risk|"
    r"unit[- ]economics reset risk|infrastructure utilization risk|"
    r"capital allocation constraint risk|"
    r"operating model \w+ risk|"
    r"revenue (?:concentration|mix|diversification) risk|"
    r"margin durability risk|cash conversion sustainability risk|"
    r"balance sheet flexibility risk|operating leverage risk"
    r")\b",
    re.IGNORECASE,
)
_RISK_PRIORITY_TRIGGER_RE = re.compile(
    r"\b("
    r"if|when|unless|over the next|within the next|next\s+(?:quarter|two quarters|year|12 months)|"
    r"watch|trigger|threshold|backlog|bookings|shipment|renewal|churn|utilization|capacity|"
    r"approval|launch|pricing|working capital|refinancing|license|licensing|remedy|"
    r"power availability|deployment pacing|lead times?|proceedings?"
    r")\b",
    re.IGNORECASE,
)
_RISK_PRIORITY_LOW_SIGNAL_RE = re.compile(
    r"\b("
    r"general economic|macroeconomic|geopolitical|competition(?: from)?|competitive pressure|"
    r"cybersecurity|cyber threats?|climate change|weather events?|foreign currency|"
    r"interest rates?|key personnel|regulatory environment|compliance with laws"
    r")\b",
    re.IGNORECASE,
)
_THREAD_INVALID_ANCHOR_TERMS = frozenset({
    "common stock",
    "class a common stock",
    "class b common stock",
    "stockholders equity",
    "share capital",
    "treasury stock",
    "additional paid in capital",
    "cash",
    "cash and cash equivalents",
    "debt",
    "total debt",
    "assets",
    "total assets",
    "liabilities",
    "total liabilities",
    "property and equipment",
    "accounts payable",
})
_THREAD_INVALID_ANCHOR_RE = re.compile(
    r"\b("
    r"common stock|class [ab] common stock|share capital|treasury stock|"
    r"additional paid-in capital|additional paid in capital|stockholders'? equity|"
    r"cash(?: and cash equivalents)?|debt|total debt|total assets|assets|"
    r"total liabilities|liabilities|property and equipment|accounts payable|"
    r"statement of operations|balance sheet|cash flow statement"
    r")\b",
    re.IGNORECASE,
)
_FORWARD_LOOKING_RE = re.compile(
    r"\b(next|ahead|forward|outlook|expect|expects|expected|guidance|guide|future|over the next|within the next)\b",
    re.IGNORECASE,
)
_AHA_SIGNAL_RE = re.compile(
    r"\b(the real implication|the underappreciated point|what the market may be missing|"
    r"what matters now|the market is still underwriting|this means|that means|implies that)\b",
    re.IGNORECASE,
)
_EXEC_DECISION_OPENING_RE = re.compile(
    r"\b(the key|the main|the real|the takeaway|this filing|the story|the case|"
    r"comes down to|depends on|turns on|hinges on|matters because|the filing makes clear)\b",
    re.IGNORECASE,
)
_WATCHPOINT_RE = re.compile(
    r"\b(proof point|watch|watchpoint|checkpoint|trigger|threshold|metric|"
    r"operating checkpoint|must stay true|breaks the thesis)\b",
    re.IGNORECASE,
)
_TIMELINE_RE = re.compile(
    r"\b(next|within the next|over the next|this quarter|next quarter|next two quarters|"
    r"next year|12 months|q[1-4]|fy\d{2}|month|months|quarter|quarters|year|years)\b",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(may|might|could|arguably|perhaps|somewhat|appears to|seems to|potentially)\b",
    re.IGNORECASE,
)
_AWKWARD_LINKER_RE = re.compile(
    r"\b(furthermore|moreover|additionally|therefore|consequently|accordingly)\b",
    re.IGNORECASE,
)
_ANALYST_FOG_RE = re.compile(
    r"\b(underwriting thread|underwriting case|underwriting setup|capital absorption|"
    r"visibility inflection|monetization runway|earnings power translation|"
    r"balance sheet optionality|forward visibility constraints|cash drag)\b",
    re.IGNORECASE,
)
_FINANCIAL_PERFORMANCE_REINVESTMENT_ECHO_RE = re.compile(
    r"\b("
    r"margin strength (?:is )?fund(?:ing|s) (?:the )?(?:investment|reinvestment|buildout)|"
    r"cash generation (?:is )?fund(?:ing|s) (?:the )?(?:investment|reinvestment|buildout)|"
    r"free cash flow (?:is )?fund(?:ing|s) (?:the )?(?:investment|reinvestment|buildout)|"
    r"operating leverage (?:is )?(?:enabling|funding|supporting) (?:the )?(?:investment|reinvestment|buildout)|"
    r"self[- ]fund(?:ed|ing) (?:investment|reinvestment|buildout)"
    r")\b",
    re.IGNORECASE,
)
_FINANCIAL_PERFORMANCE_METRIC_PATTERNS: Dict[str, re.Pattern[str]] = {
    "revenue": re.compile(r"\brevenue\b", re.IGNORECASE),
    "margin": re.compile(r"\b(?:gross|operating|net|ebitda|fcf)\s+margin\b", re.IGNORECASE),
    "cash_flow": re.compile(r"\b(?:free\s+cash\s+flow|operating\s+cash\s+flow|fcf|ocf|cash conversion)\b", re.IGNORECASE),
    "profitability": re.compile(r"\b(?:operating income|net income|gross profit|ebitda)\b", re.IGNORECASE),
    "operating_kpi": re.compile(r"\b(?:backlog|bookings|renewal|utilization|conversion|attach|capacity)\b", re.IGNORECASE),
}
_RISK_PRIORITY_NEAR_TERM_EVENT_RE = re.compile(
    r"\b(inquiry|investigation|enforcement|settlement|hearing|trial|ruling|deadline|ban|"
    r"subpoena|audit finding|regulatory remedy|consent decree|non-renewal|contract renewal)\b",
    re.IGNORECASE,
)
_RISK_PRIORITY_COMPLIANCE_RE = re.compile(
    r"\b(anti-corruption|supplier code|code of conduct|ethics policy|policy violation|"
    r"bribery|sanctions compliance|labor standards|compliance policy)\b",
    re.IGNORECASE,
)
_RISK_PRIORITY_PRICED_IN_RE = re.compile(
    r"\b(priced in|already expected|already reflected|well known|widely understood)\b",
    re.IGNORECASE,
)
_REPEATED_LEADIN_STEMS = (
    "that leaves",
    "this leaves",
    "what matters now",
    "the key issue",
    "the next question is",
)
_INSTRUCTION_THEME_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("must_be_forward_looking", "future outlook"),
    ("must_be_forward_looking", "forward outlook"),
    ("must_be_forward_looking", "what happens next"),
    ("must_be_forward_looking", "next 12 months"),
    ("must_use_management_view", "management expects"),
    ("must_use_management_view", "what management thinks"),
    ("must_use_management_view", "what management expects"),
    ("must_use_management_view", "management is prioritizing"),
    ("must_include_watch_metric", "watch metric"),
    ("must_include_watch_metric", "watch metrics"),
    ("must_include_watch_metric", "what changed"),
    ("must_prioritize_angle", "focus on"),
    ("must_prioritize_angle", "prioritize"),
    ("must_emphasize_theme", "emphasize"),
    ("must_emphasize_theme", "highlight"),
    ("must_avoid_angle", "avoid"),
    ("must_avoid_angle", "do not"),
    ("must_avoid_angle", "don't"),
)
_SECTIONED_EDITORIAL_FAILURE_CODES = frozenset({
    "thread_anchor_invalid",
    "thread_not_resolved",
    "repeated_leadin",
    "repeated_clause_family",
    "section_overlap",
    "instruction_miss",
    "exec_opening_soft",
    "aha_not_surfaced",
    "exec_missing_proof_point",
    "financial_performance_metric_drift",
    "financial_performance_redundancy",
    "soft_section_ending",
    "risk_not_actionable",
    "closing_soft",
    "tone_drift",
    "readability_drift",
})


def _normalize_business_archetype(value: str) -> str:
    normalized = _normalize_phrase_key(value).replace(" ", "_")
    if normalized in BUSINESS_ARCHETYPES:
        return normalized
    return "diversified_other"


def _archetype_config(archetype: str) -> Dict[str, Any]:
    normalized = _normalize_business_archetype(archetype)
    return dict(_ARCHETYPE_CONFIG.get(normalized) or _ARCHETYPE_CONFIG["diversified_other"])


def _fallback_kpis_for_archetype(archetype: str) -> List[KPIDescriptor]:
    config = _archetype_config(archetype)
    descriptors: List[KPIDescriptor] = []
    for name, why_it_matters, search_terms, metric_type in config.get("fallback_kpis") or ():
        descriptors.append(
            KPIDescriptor(
                name=str(name),
                why_it_matters=str(why_it_matters),
                filing_search_terms=[str(item) for item in tuple(search_terms or ())],
                metric_type=str(metric_type or "currency"),
            )
        )
    return descriptors


def _infer_business_archetype(
    *,
    sector: str = "",
    industry: str = "",
    business_identity: str = "",
    primary_kpis: Optional[List[KPIDescriptor]] = None,
    investor_focus_areas: Optional[List[str]] = None,
    context_text: str = "",
) -> str:
    combined = " ".join(
        part
        for part in (
            sector,
            industry,
            business_identity,
            " ".join(kpi.name for kpi in list(primary_kpis or []) if getattr(kpi, "name", "")),
            " ".join(
                " ".join(getattr(kpi, "filing_search_terms", []) or [])
                for kpi in list(primary_kpis or [])
            ),
            " ".join(str(item) for item in list(investor_focus_areas or [])),
            context_text,
        )
        if str(part or "").strip()
    ).lower()
    if not combined:
        return "diversified_other"

    best_archetype = "diversified_other"
    best_score = 0
    for archetype in BUSINESS_ARCHETYPES:
        if archetype == "diversified_other":
            continue
        score = 0
        for keyword in tuple(_archetype_config(archetype).get("keywords") or ()):
            lowered_keyword = str(keyword or "").strip().lower()
            if not lowered_keyword:
                continue
            if lowered_keyword in combined:
                score += 3 if " " in lowered_keyword else 1
        if archetype in combined:
            score += 2
        if score > best_score:
            best_score = score
            best_archetype = archetype
    return best_archetype if best_score > 0 else "diversified_other"


def _refine_company_risk_name(
    *,
    risk_name: str,
    mechanism: str,
    early_warning: str,
    evidence_from_filing: str,
    company_terms: List[str],
) -> str:
    cleaned = str(risk_name or "").strip()
    if not cleaned:
        return ""

    candidate = RiskEvidenceCandidate(
        risk_name=cleaned,
        source_section="Risk Factors",
        source_quote=str(evidence_from_filing or "").strip(),
        source_anchor_terms=tuple(
            extract_anchor_terms(
                " ".join(
                    part
                    for part in (
                        cleaned,
                        mechanism,
                        early_warning,
                        evidence_from_filing,
                    )
                    if part
                ),
                company_terms=company_terms,
                limit=6,
            )
        ),
        mechanism_seed=str(mechanism or "").strip(),
        early_warning_seed=str(early_warning or "").strip(),
    )
    ok, _reason = candidate_is_strictly_acceptable(candidate, company_terms=company_terms)
    return cleaned if ok else ""


_SECTION_THEME_PATTERNS: Dict[str, re.Pattern[str]] = {
    "cash conversion": re.compile(
        r"cash\s+(?:conversion|converts?|converting)|operating[- ]cash[- ]flow\s+to\s+free[- ]cash[- ]flow",
        re.IGNORECASE,
    ),
    "free cash flow": re.compile(r"free\s+cash\s+flow|\bfcf\b", re.IGNORECASE),
    "reinvestment": re.compile(
        r"reinvestment|capex|capital\s+intensity|infrastructure\s+spend|capacity\s+build",
        re.IGNORECASE,
    ),
    "margin durability": re.compile(
        r"operating\s+margin|margin\s+(?:durability|retention|profile)|operating\s+leverage|profitability",
        re.IGNORECASE,
    ),
    "balance-sheet flexibility": re.compile(
        r"balance[- ]sheet|liquidity|funding|refinancing|cash\s+cushion|optionality",
        re.IGNORECASE,
    ),
    "capital allocation": re.compile(
        r"capital[- ]allocation|buybacks?|dividends?|m&a|de[- ]risk|shareholder\s+returns?",
        re.IGNORECASE,
    ),
    "management credibility": re.compile(
        r"credibility|on\s+track|delivered|missed|commitment|commitments|execution\s+quality",
        re.IGNORECASE,
    ),
    "guidance": re.compile(
        r"guidance|outlook|expects?|plans?|targets?|next\s+(?:quarter|period|half|year)|ahead",
        re.IGNORECASE,
    ),
}

_DEFAULT_SECTION_METRIC_PATTERNS: Dict[str, re.Pattern[str]] = {
    "Revenue": re.compile(r"\brevenue\b", re.IGNORECASE),
    "Operating Income": re.compile(r"\boperating\s+income\b|\bebit\b", re.IGNORECASE),
    "Operating Margin": re.compile(r"\boperating\s+margin\b", re.IGNORECASE),
    "Net Margin": re.compile(r"\bnet\s+margin\b", re.IGNORECASE),
    "Operating Cash Flow": re.compile(
        r"\boperating\s+cash\s+flow\b|\bocf\b", re.IGNORECASE
    ),
    "Free Cash Flow": re.compile(r"\bfree\s+cash\s+flow\b|\bfcf\b", re.IGNORECASE),
    "Capex": re.compile(
        r"\bcapex\b|capital\s+expenditures?|property\s+and\s+equipment",
        re.IGNORECASE,
    ),
    "Cash": re.compile(r"\bcash(?:\s+\+\s+securities)?\b", re.IGNORECASE),
    "Debt": re.compile(r"\bdebt\b|short[- ]term\s+debt|long[- ]term\s+debt", re.IGNORECASE),
    "Liabilities": re.compile(r"\bliabilit(?:y|ies)\b", re.IGNORECASE),
}


def _normalize_phrase_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _dedupe_ordered_strings(items: List[str], *, limit: Optional[int] = None) -> List[str]:
    unique: List[str] = []
    seen: Set[str] = set()
    max_items = int(limit or 0)
    for raw in items:
        value = " ".join(str(raw or "").split()).strip()
        if not value:
            continue
        canon = _normalize_phrase_key(value)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        unique.append(value)
        if max_items > 0 and len(unique) >= max_items:
            break
    return unique


def _theme_patterns_for_analysis(
    analysis: FilingAnalysis,
) -> Dict[str, re.Pattern[str]]:
    patterns: Dict[str, re.Pattern[str]] = dict(_SECTION_THEME_PATTERNS)
    for risk in analysis.company_specific_risks[:4]:
        risk_name = str(risk.risk_name or "").strip()
        if not risk_name:
            continue
        patterns.setdefault(
            risk_name,
            re.compile(re.escape(risk_name), re.IGNORECASE),
        )
    return patterns


def _metric_patterns_for_analysis(
    analysis: FilingAnalysis,
) -> Dict[str, re.Pattern[str]]:
    patterns: Dict[str, re.Pattern[str]] = dict(_DEFAULT_SECTION_METRIC_PATTERNS)
    for finding in analysis.kpi_findings[:8]:
        label = str(finding.kpi_name or "").strip()
        if not label:
            continue
        patterns.setdefault(label, re.compile(re.escape(label), re.IGNORECASE))
    return patterns


def _extract_theme_keys_from_text(text: str, analysis: FilingAnalysis) -> List[str]:
    body = str(text or "")
    matches = [
        theme_name
        for theme_name, pattern in _theme_patterns_for_analysis(analysis).items()
        if pattern.search(body)
    ]
    return _dedupe_ordered_strings(matches, limit=8)


def _extract_anchor_metrics_from_text(text: str, analysis: FilingAnalysis) -> List[str]:
    body = str(text or "")
    matches = [
        label
        for label, pattern in _metric_patterns_for_analysis(analysis).items()
        if pattern.search(body)
    ]
    return _dedupe_ordered_strings(matches, limit=8)


def _extract_company_terms_from_text(text: str, analysis: FilingAnalysis) -> List[str]:
    lowered = " ".join(str(text or "").lower().split())
    matches = [
        str(term).strip()
        for term in analysis.company_terms or []
        if str(term or "").strip() and str(term).strip().lower() in lowered
    ]
    return _dedupe_ordered_strings(matches, limit=10)


def _candidate_management_topics(analysis: FilingAnalysis) -> List[str]:
    topics: List[str] = []
    topics.extend(
        str(item.topic or "").strip()
        for item in analysis.management_expectations or []
        if str(item.topic or "").strip()
    )
    topics.extend(
        str(item.topic or "").strip()
        for item in analysis.management_quotes or []
        if str(item.topic or "").strip()
    )
    topics.extend(
        str(item).strip()
        for item in analysis.management_strategic_bets or []
        if str(item or "").strip()
    )
    if analysis.forward_guidance_summary:
        topics.append(str(analysis.forward_guidance_summary).strip())
    return _dedupe_ordered_strings(topics, limit=12)


def _extract_management_topics_from_text(
    text: str,
    analysis: FilingAnalysis,
) -> List[str]:
    lowered = " ".join(str(text or "").lower().split())
    matches = [
        topic
        for topic in _candidate_management_topics(analysis)
        if topic.lower() in lowered
        or any(
            token in lowered
            for token in _normalize_phrase_key(topic).split()
            if len(token) >= 5
        )
    ]
    return _dedupe_ordered_strings(matches, limit=8)


def _extract_promise_items_from_text(text: str, analysis: FilingAnalysis) -> List[str]:
    lowered = " ".join(str(text or "").lower().split())
    matches: List[str] = []
    for item in analysis.promise_scorecard_items or []:
        commitment = str(item.commitment or "").strip()
        assessment = str(item.assessment or "").strip()
        if commitment and (
            commitment.lower() in lowered
            or any(
                token in lowered
                for token in _normalize_phrase_key(commitment).split()
                if len(token) >= 5
            )
        ):
            matches.append(commitment)
            continue
        if assessment and assessment.lower() in lowered:
            matches.append(commitment or assessment)
    return _dedupe_ordered_strings(matches, limit=6)


def _build_section_memory_from_bodies(
    section_bodies: Dict[str, str],
    analysis: FilingAnalysis,
) -> SectionMemory:
    memory = SectionMemory()
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if section_name != "Key Metrics"
    ]
    for section_name in ordered_sections:
        body = str(section_bodies.get(section_name) or "").strip()
        if not body:
            continue
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", body)
            if sentence.strip()
        ]
        if sentences:
            memory.used_claims.append(sentences[0])
        memory.used_theme_keys.extend(_extract_theme_keys_from_text(body, analysis))
        memory.used_anchor_metrics.extend(
            _extract_anchor_metrics_from_text(body, analysis)
        )
        memory.used_company_terms.extend(
            _extract_company_terms_from_text(body, analysis)
        )
        memory.used_management_topics.extend(
            _extract_management_topics_from_text(body, analysis)
        )
        memory.used_promise_items.extend(
            _extract_promise_items_from_text(body, analysis)
        )

    memory.used_claims = _dedupe_ordered_strings(memory.used_claims, limit=8)
    memory.used_theme_keys = _dedupe_ordered_strings(memory.used_theme_keys, limit=10)
    memory.used_anchor_metrics = _dedupe_ordered_strings(
        memory.used_anchor_metrics, limit=10
    )
    memory.used_company_terms = _dedupe_ordered_strings(
        memory.used_company_terms, limit=10
    )
    memory.used_management_topics = _dedupe_ordered_strings(
        memory.used_management_topics, limit=10
    )
    memory.used_promise_items = _dedupe_ordered_strings(
        memory.used_promise_items, limit=6
    )
    return memory


def _coerce_section_memory(
    *,
    section_memory: Optional[Any],
    analysis: FilingAnalysis,
    used_claims: Optional[List[str]] = None,
) -> SectionMemory:
    if isinstance(section_memory, SectionMemory):
        memory = section_memory
    elif isinstance(section_memory, dict):
        memory = SectionMemory(
            used_claims=list(section_memory.get("used_claims") or []),
            used_theme_keys=list(section_memory.get("used_theme_keys") or []),
            used_anchor_metrics=list(section_memory.get("used_anchor_metrics") or []),
            used_company_terms=list(section_memory.get("used_company_terms") or []),
            used_management_topics=list(
                section_memory.get("used_management_topics") or []
            ),
            used_promise_items=list(section_memory.get("used_promise_items") or []),
        )
    else:
        memory = SectionMemory(used_claims=list(used_claims or []))

    if used_claims:
        memory.used_claims = _dedupe_ordered_strings(
            list(memory.used_claims) + list(used_claims),
            limit=8,
        )

    synthesized_text = "\n".join(memory.used_claims)
    if synthesized_text:
        if not memory.used_theme_keys:
            memory.used_theme_keys = _extract_theme_keys_from_text(
                synthesized_text, analysis
            )
        if not memory.used_anchor_metrics:
            memory.used_anchor_metrics = _extract_anchor_metrics_from_text(
                synthesized_text, analysis
            )
        if not memory.used_company_terms:
            memory.used_company_terms = _extract_company_terms_from_text(
                synthesized_text, analysis
            )
        if not memory.used_management_topics:
            memory.used_management_topics = _extract_management_topics_from_text(
                synthesized_text, analysis
            )
        if not memory.used_promise_items:
            memory.used_promise_items = _extract_promise_items_from_text(
                synthesized_text, analysis
            )
    return memory


def _kpi_finding_prompt_line(finding: KPIFinding) -> str:
    line = f"{finding.kpi_name}: {finding.current_value}"
    if finding.change:
        line += f" ({finding.change})"
    if finding.insight:
        line += f" - {finding.insight}"
    return line


def _risk_prompt_line(risk: CompanyRisk) -> str:
    line = f"{risk.risk_name}: {risk.mechanism}"
    if risk.early_warning:
        line += f" Early warning: {risk.early_warning}"
    if risk.source_section and risk.source_quote:
        line += f" Source: {risk.source_section} — {risk.source_quote}"
    elif risk.evidence_from_filing:
        line += f" Filing evidence: {risk.evidence_from_filing}"
    return line


def _risk_source_evidence_line(risk: CompanyRisk) -> str:
    section = str(risk.source_section or "").strip() or "Risk Factors"
    quote = " ".join(str(risk.source_quote or risk.evidence_from_filing or "").split()).strip()
    if section and quote:
        return f"{section}: {quote}"
    return quote or _risk_prompt_line(risk)


def _risk_candidate_from_company_risk(
    risk: CompanyRisk,
    *,
    company_terms: Sequence[str],
) -> Optional[RiskEvidenceCandidate]:
    source_quote = str(risk.source_quote or risk.evidence_from_filing or "").strip()
    candidate = RiskEvidenceCandidate(
        risk_name=str(risk.risk_name or "").strip(),
        source_section=str(risk.source_section or "").strip() or "Risk Factors",
        source_quote=source_quote,
        source_anchor_terms=tuple(
            extract_anchor_terms(source_quote, company_terms=company_terms, limit=6)
        ),
        mechanism_seed=str(risk.mechanism or "").strip(),
        early_warning_seed=str(risk.early_warning or "").strip(),
    )
    ok, _reason = candidate_is_strictly_acceptable(candidate, company_terms=company_terms)
    return candidate if ok else None


def _risk_body_for_scoring(risk: CompanyRisk) -> str:
    return " ".join(
        part
        for part in (
            str(risk.mechanism or "").strip(),
            str(risk.early_warning or "").strip(),
            str(risk.source_quote or "").strip(),
            str(risk.evidence_from_filing or "").strip(),
        )
        if part
    )


def _risk_management_signal_chunks(analysis: FilingAnalysis) -> List[Tuple[int, str]]:
    chunks: List[Tuple[int, str]] = []
    for quote in analysis.management_quotes or []:
        blob = " ".join(
            part
            for part in (
                str(getattr(quote, "topic", "") or "").strip(),
                str(getattr(quote, "quote", "") or "").strip(),
            )
            if part
        )
        if blob:
            chunks.append((5, blob))
    for item in analysis.management_expectations or []:
        blob = " ".join(
            part
            for part in (
                str(getattr(item, "topic", "") or "").strip(),
                str(getattr(item, "expectation", "") or "").strip(),
                str(getattr(item, "evidence", "") or "").strip(),
            )
            if part
        )
        if blob:
            chunks.append((5, blob))
    for item in analysis.promise_scorecard_items or []:
        blob = " ".join(
            part
            for part in (
                str(getattr(item, "commitment", "") or "").strip(),
                str(getattr(item, "assessment", "") or "").strip(),
                str(getattr(item, "evidence", "") or "").strip(),
            )
            if part
        )
        if blob:
            chunks.append((4, blob))
    for weight, blob in (
        (3, str(analysis.management_strategy_summary or "").strip()),
        (3, str(analysis.forward_guidance_summary or "").strip()),
        (2, str(analysis.promise_scorecard or "").strip()),
    ):
        if blob:
            chunks.append((weight, blob))
    return chunks


def _risk_management_echo_score(
    risk: CompanyRisk,
    *,
    analysis: FilingAnalysis,
    company_terms: Sequence[str],
) -> Tuple[int, bool]:
    risk_blob = " ".join(
        part
        for part in (
            str(risk.risk_name or "").strip(),
            _risk_body_for_scoring(risk),
        )
        if part
    )
    anchor_keys = {
        _normalize_phrase_key(term)
        for term in extract_anchor_terms(risk_blob, company_terms=company_terms, limit=6)
        if str(term or "").strip()
    }
    risk_tokens = {
        token
        for token in re.findall(r"[a-z]{4,}", risk_blob.lower())
        if token not in _GENERIC_COMPANY_TERM_STOPWORDS
    }
    score = 0
    echoed = False
    for weight, chunk in _risk_management_signal_chunks(analysis):
        chunk_key = _normalize_phrase_key(chunk)
        if not chunk_key:
            continue
        chunk_tokens = {
            token
            for token in re.findall(r"[a-z]{4,}", chunk_key)
            if token not in _GENERIC_COMPANY_TERM_STOPWORDS
        }
        matches_anchor = any(
            anchor_key and anchor_key in chunk_key and len(anchor_key.split()) >= 1
            for anchor_key in anchor_keys
        )
        shared_tokens = risk_tokens & chunk_tokens
        if not matches_anchor and len(shared_tokens) < 2:
            continue
        echoed = True
        score += int(weight)
        if matches_anchor and len(shared_tokens) >= 2:
            score += 1
    return score, echoed


def _risk_priority_profile(
    risk: CompanyRisk,
    *,
    analysis: FilingAnalysis,
    company_terms: Sequence[str],
) -> Optional[Dict[str, Any]]:
    candidate = _risk_candidate_from_company_risk(risk, company_terms=company_terms)
    if candidate is None:
        return None
    risk_blob = _risk_body_for_scoring(risk)
    anchor_count = len(
        extract_anchor_terms(
            " ".join(
                part
                for part in (
                    str(risk.risk_name or "").strip(),
                    risk_blob,
                )
                if part
            ),
            company_terms=company_terms,
            limit=6,
        )
    )
    base_score = score_risk_evidence_candidate(candidate, company_terms=company_terms)
    management_echo_score, management_echo = _risk_management_echo_score(
        risk,
        analysis=analysis,
        company_terms=company_terms,
    )
    has_trigger = bool(_RISK_PRIORITY_TRIGGER_RE.search(risk_blob))
    has_transmission = bool(_TRANSMISSION_RE.search(risk_blob))
    filing_backed_blob = " ".join(
        part
        for part in (
            str(risk.risk_name or "").strip(),
            str(risk.evidence_from_filing or "").strip(),
            str(risk.source_quote or "").strip(),
        )
        if part
    )
    has_near_term_event = bool(
        _RISK_PRIORITY_NEAR_TERM_EVENT_RE.search(filing_backed_blob)
    )
    is_generic_compliance = bool(_RISK_PRIORITY_COMPLIANCE_RE.search(risk_blob))
    is_already_priced = bool(_RISK_PRIORITY_PRICED_IN_RE.search(risk_blob))
    source_section = str(risk.source_section or "").strip() or "Risk Factors"

    probability_score = 0
    magnitude_score = 0
    asymmetry_score = 0

    if management_echo:
        probability_score += 5
    if has_trigger:
        probability_score += 4
    if has_near_term_event:
        probability_score += 3
    if source_section in {"Risk Factors", "Risk"}:
        probability_score += 2

    if has_transmission:
        magnitude_score += 4
    if anchor_count >= 2:
        magnitude_score += 2

    if not is_already_priced:
        asymmetry_score += 2
    if has_near_term_event:
        asymmetry_score += 1

    score = int(base_score) + int(management_echo_score)
    score += int(probability_score * 3)
    score += int(magnitude_score * 2)
    score += int(asymmetry_score)
    if management_echo and source_section in {"Risk Factors", "Risk"}:
        score += 4
    if anchor_count >= 2:
        score += 2
    if _GENERIC_AGENT_RISK_NAME_RE.search(str(risk.risk_name or "")):
        score -= 6
    if _RISK_PRIORITY_LOW_SIGNAL_RE.search(risk_blob) and anchor_count <= 1 and not management_echo:
        score -= 7
    if is_generic_compliance and not has_near_term_event:
        probability_score -= 4
        asymmetry_score -= 2
        score -= 12

    return {
        "risk": risk,
        "score": int(score),
        "probability_score": int(probability_score),
        "magnitude_score": int(magnitude_score),
        "asymmetry_score": int(asymmetry_score),
        "anchor_count": int(anchor_count),
        "management_echo": bool(management_echo),
        "has_trigger": bool(has_trigger),
        "has_transmission": bool(has_transmission),
        "has_near_term_event": bool(has_near_term_event),
        "is_generic_compliance": bool(is_generic_compliance),
        "is_already_priced": bool(is_already_priced),
        "source_section": source_section,
    }


def _risk_profile_is_material(
    profile: Mapping[str, Any],
    *,
    top_score: int,
) -> bool:
    score = int(profile.get("score", 0) or 0)
    anchor_count = int(profile.get("anchor_count", 0) or 0)
    management_echo = bool(profile.get("management_echo"))
    has_trigger = bool(profile.get("has_trigger"))
    has_transmission = bool(profile.get("has_transmission"))
    source_section = str(profile.get("source_section") or "")

    if management_echo and anchor_count >= 1 and (has_trigger or has_transmission or score >= 18):
        return True
    if source_section in {"Risk Factors", "Risk"} and anchor_count >= 1 and (has_trigger or has_transmission):
        return True
    if score >= max(18, int(top_score) - 5) and anchor_count >= 2:
        return True
    return False


def _accepted_company_risks(analysis: FilingAnalysis) -> List[CompanyRisk]:
    company_terms = list(analysis.company_terms or [])
    scored: List[Dict[str, Any]] = []

    for risk in analysis.company_specific_risks or []:
        profile = _risk_priority_profile(
            risk,
            analysis=analysis,
            company_terms=company_terms,
        )
        if profile is not None:
            scored.append(profile)

    if not scored:
        return []

    scored.sort(
        key=lambda item: (
            -int(item.get("probability_score", 0) or 0),
            -int(item.get("magnitude_score", 0) or 0),
            -int(item.get("asymmetry_score", 0) or 0),
            -int(item.get("score", 0) or 0),
            not bool(item.get("management_echo")),
            not bool(item.get("has_trigger")),
            not bool(item.get("has_near_term_event")),
            not bool(item.get("has_transmission")),
            bool(item.get("is_generic_compliance")),
            -int(item.get("anchor_count", 0) or 0),
            str(getattr(item.get("risk"), "risk_name", "") or "").lower(),
            str(item.get("source_section") or "").lower(),
        )
    )

    top_score = int(scored[0].get("score", 0) or 0)

    accepted: List[CompanyRisk] = []
    for profile in scored:
        risk = profile["risk"]
        if not _risk_profile_is_material(profile, top_score=top_score):
            continue
        risk_body = _risk_body_for_scoring(risk)
        if any(
            (
                overlap := assess_risk_overlap(
                    risk_name=str(risk.risk_name or "").strip(),
                    risk_body=risk_body,
                    other_risk_name=str(existing.risk_name or "").strip(),
                    other_risk_body=_risk_body_for_scoring(existing),
                )
            ).exact_name_match
            or overlap.names_overlap
            or overlap.bodies_overlap
            for existing in accepted
        ):
            continue
        accepted.append(risk)
    if not accepted:
        fallback_profile = next(
            (
                profile
                for profile in scored
                if int(profile.get("score", 0) or 0) >= 14
            ),
            None,
        )
        if fallback_profile is not None:
            accepted.append(fallback_profile["risk"])
    return accepted


def _accepted_company_risk_lines(analysis: FilingAnalysis) -> List[str]:
    return [
        _risk_source_evidence_line(risk)
        for risk in _accepted_company_risks(analysis)
    ]


def _build_narrative_blueprint(
    *,
    company_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
) -> NarrativeBlueprint:
    memo_thread = str(filing_analysis.central_tension or "").strip()
    if not memo_thread:
        memo_thread = str(filing_analysis.management_strategy_summary or "").strip()
    if not memo_thread and company_intelligence.business_identity:
        fallback_terms = list(filing_analysis.company_terms[:3] or [])
        archetype_terms = list(
            _archetype_config(
                getattr(company_intelligence, "business_archetype", "")
                or "diversified_other"
            ).get("default_terms")
            or ()
        )
        anchor = fallback_terms[0] if fallback_terms else (archetype_terms[0] if archetype_terms else "execution")
        constraint = (
            fallback_terms[1]
            if len(fallback_terms) > 1
            else (archetype_terms[1] if len(archetype_terms) > 1 else "return discipline")
        )
        memo_thread = (
            f"{company_name} needs to prove that {anchor} can support durable economics "
            f"while management keeps {constraint} disciplined."
        )

    def _pick(items: List[str], limit: int) -> List[str]:
        return _dedupe_ordered_strings(items, limit=limit)

    exec_primary = _pick(
        list(filing_analysis.evidence_map.get("Executive Summary") or [])
        + list(filing_analysis.period_specific_insights[:2] or [])
        + [
            str(getattr(item, "expectation", "") or "").strip()
            for item in filing_analysis.management_expectations[:1]
        ],
        3,
    )
    perf_primary = _pick(
        list(filing_analysis.evidence_map.get("Financial Performance") or [])
        + [_kpi_finding_prompt_line(item) for item in filing_analysis.kpi_findings[:3]],
        4,
    )
    mdna_primary = _pick(
        list(filing_analysis.evidence_map.get("Management Discussion & Analysis") or [])
        + list(filing_analysis.management_strategic_bets[:3] or [])
        + [
            str(getattr(item, "expectation", "") or "").strip()
            for item in filing_analysis.management_expectations[:2]
        ]
        + [
            str(getattr(item, "assessment", "") or "").strip()
            for item in filing_analysis.promise_scorecard_items[:2]
        ],
        5,
    )
    risk_primary = _pick(
        list(filing_analysis.evidence_map.get("Risk Factors") or [])
        + [_risk_source_evidence_line(item) for item in filing_analysis.company_specific_risks[:3]],
        4,
    )
    closing_primary = _pick(
        list(filing_analysis.evidence_map.get("Closing Takeaway") or [])
        + [
            str(getattr(item, "assessment", "") or "").strip()
            for item in filing_analysis.promise_scorecard_items[:2]
        ]
        + [
            str(getattr(item, "expectation", "") or "").strip()
            for item in filing_analysis.management_expectations[:2]
        ],
        4,
    )

    section_blueprints = {
        "Executive Summary": SectionBlueprint(
            section_name="Executive Summary",
            section_job="State the company-specific thread once: management's message, how the company makes money, and what changed this filing period.",
            section_question="What is management really saying in this filing, and why does it matter for this company right now?",
            primary_evidence=exec_primary,
            secondary_evidence=_pick(
                list(filing_analysis.evidence_map.get("Financial Performance") or []), 2
            ),
            banned_overlap=[
                "Do not do a full performance walkthrough.",
                "Do not re-list risk factors or resolve the final recommendation.",
                "Do not repeat the thesis as a question more than once.",
            ],
            subtle_handoff="End by pointing toward the operating proof the quarter must show, without naming the next section.",
        ),
        "Financial Performance": SectionBlueprint(
            section_name="Financial Performance",
            section_job="Test the thread with the 2-3 decisive numbers that actually prove or challenge it.",
            section_question="Which numbers from this period most directly confirm or challenge the memo thread?",
            primary_evidence=perf_primary,
            secondary_evidence=_pick(exec_primary + list(filing_analysis.period_specific_insights), 2),
            banned_overlap=[
                "Do not recap management strategy in detail.",
                "Do not reuse more than brief callback language from the Executive Summary.",
                "Do not turn the section into a metrics list with no causal interpretation.",
            ],
            subtle_handoff="Close by shifting from numeric proof to the management choices that explain whether this performance can persist.",
        ),
        "Management Discussion & Analysis": SectionBlueprint(
            section_name="Management Discussion & Analysis",
            section_job="Explain management intent, strategic bets, forward expectations, and promise-vs-delivery with management voice up front.",
            section_question="What is management trying to make happen next, and has it earned credibility on that plan?",
            primary_evidence=mdna_primary,
            secondary_evidence=_pick(perf_primary + closing_primary, 2),
            banned_overlap=[
                "Do not open with a metric recap or repeat the Financial Performance section.",
                "Do not restate the memo thread as a question.",
                "Do not use generic big-tech capital-allocation prose with no management attribution.",
            ],
            subtle_handoff="Close by naming the vulnerability or watchpoint that matters if management's plan slips.",
        ),
        "Risk Factors": SectionBlueprint(
            section_name="Risk Factors",
            section_job="Name the concrete downside paths that could break the memo thread.",
            section_question="Which company-specific exposures could stop the thread from resolving in management's favor?",
            primary_evidence=risk_primary,
            secondary_evidence=_pick(mdna_primary + perf_primary, 2),
            banned_overlap=[
                "Do not re-summarize the quarter or repeat the same operating argument from earlier sections.",
                "Do not name symptoms like margin pressure as the risk itself.",
                "Do not turn the section into another performance recap.",
            ],
            subtle_handoff="Close by pointing toward the indicators investors should monitor, without explicitly naming the next section.",
        ),
        "Closing Takeaway": SectionBlueprint(
            section_name="Closing Takeaway",
            section_job="Resolve the thread through management credibility, the current stance, and the next proof points.",
            section_question="Has management earned enough credibility to support the stance, and what proof points would change that view?",
            primary_evidence=closing_primary,
            secondary_evidence=_pick(perf_primary + risk_primary, 2),
            banned_overlap=[
                "Do not replay the Financial Performance section.",
                "Do not re-list all of management's strategy points.",
                "Do not use generic balance-sheet filler instead of credibility and triggers.",
            ],
            subtle_handoff="Close decisively on the stance and the next proof points; do not add a teaser sentence.",
        ),
    }

    if any(item for item in filing_analysis.evidence_map.get("Financial Health Rating") or []):
        section_blueprints["Financial Health Rating"] = SectionBlueprint(
            section_name="Financial Health Rating",
            section_job="Set the balance-sheet and cash-conversion backdrop for the operating thread.",
            section_question="How much resilience does the company have before the operating debate even starts?",
            primary_evidence=_pick(
                list(filing_analysis.evidence_map.get("Financial Health Rating") or [])
                + [_kpi_finding_prompt_line(item) for item in filing_analysis.kpi_findings[:2]],
                3,
            ),
            secondary_evidence=_pick(exec_primary, 1),
            banned_overlap=[
                "Do not restate the entire Executive Summary.",
                "Do not resolve the recommendation here.",
            ],
            subtle_handoff="End by setting the balance-sheet backdrop for the operating analysis without naming later sections.",
        )

    return NarrativeBlueprint(
        memo_thread=memo_thread,
        section_blueprints=section_blueprints,
    )


def _preference_lookup(preferences: Any, key: str, default: Any = None) -> Any:
    if preferences is None:
        return default
    if isinstance(preferences, dict):
        return preferences.get(key, default)
    return getattr(preferences, key, default)


def _preference_list(preferences: Any, key: str) -> List[str]:
    raw = _preference_lookup(preferences, key, []) or []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    return [str(item).strip() for item in list(raw) if str(item or "").strip()]


def _normalize_style_preferences(
    preferences: Any,
) -> Tuple[str, str, str, List[str]]:
    tone = str(_preference_lookup(preferences, "tone", "objective") or "objective").strip().lower()
    detail_level = str(
        _preference_lookup(preferences, "detail_level", _preference_lookup(preferences, "detailLevel", "balanced"))
        or "balanced"
    ).strip().lower()
    output_style = str(
        _preference_lookup(preferences, "output_style", _preference_lookup(preferences, "outputStyle", "narrative"))
        or "narrative"
    ).strip().lower()
    focus_areas = _preference_list(preferences, "focus_areas") or _preference_list(preferences, "focusAreas")
    return tone, detail_level, output_style, focus_areas


def _format_style_preferences_for_agent_2(preferences: Any) -> str:
    tone, detail_level, output_style, focus_areas = _normalize_style_preferences(
        preferences
    )
    lines = [
        f"- Tone: {tone or 'objective'}",
        f"- Detail level: {detail_level or 'balanced'}",
        f"- Output style: {output_style or 'narrative'}",
    ]
    if focus_areas:
        lines.append(
            "- Focus areas: "
            + ", ".join(str(item).strip() for item in list(focus_areas or [])[:4])
        )
    return "\n".join(lines)


def _format_section_instruction_briefs(
    section_instructions: Optional[Dict[str, str]],
) -> str:
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in dict(section_instructions or {}).items()
        if str(key or "").strip() and str(value or "").strip()
    }
    if not normalized:
        return "(No per-section user instructions supplied)"
    lines: List[str] = []
    for section_name in SECTION_ORDER:
        if section_name == "Key Metrics":
            continue
        text = normalized.get(section_name)
        if text:
            lines.append(f"- {section_name}: {text}")
    return "\n".join(lines) if lines else "(No per-section user instructions supplied)"


def _normalized_thread_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _is_invalid_thread_anchor(anchor: str) -> bool:
    normalized = _normalized_thread_key(anchor)
    if not normalized:
        return True
    if normalized in _THREAD_INVALID_ANCHOR_TERMS:
        return True
    return bool(_THREAD_INVALID_ANCHOR_RE.search(anchor or ""))


def _candidate_anchor_from_text(
    text: str,
    *,
    analysis: FilingAnalysis,
) -> Tuple[str, str]:
    candidate_text = str(text or "").strip()
    lowered = _normalized_thread_key(candidate_text)
    if not lowered:
        return "", "invalid"

    for risk in list(analysis.company_specific_risks or []):
        name = str(risk.risk_name or "").strip()
        if name and _normalized_thread_key(name) in lowered:
            return name, "company_specific_risk"
    for finding in list(analysis.kpi_findings or []):
        name = str(finding.kpi_name or "").strip()
        if name and _normalized_thread_key(name) in lowered:
            return name, "operating_kpi"
    for expectation in list(analysis.management_expectations or []):
        topic = str(expectation.topic or "").strip()
        if topic and _normalized_thread_key(topic) in lowered:
            return topic, "management_strategic_bet"
    for bet in list(analysis.management_strategic_bets or []):
        bet_text = str(bet or "").strip()
        if bet_text and _normalized_thread_key(bet_text) in lowered:
            return bet_text, "management_strategic_bet"
    for term in list(analysis.company_terms or []):
        term_text = str(term or "").strip()
        term_key = _normalized_thread_key(term_text)
        if term_text and term_key and term_key in lowered and not _is_invalid_thread_anchor(term_text):
            return term_text, "product_or_segment"

    if _THREAD_INVALID_ANCHOR_RE.search(candidate_text):
        return _THREAD_INVALID_ANCHOR_RE.search(candidate_text).group(0), "invalid"
    if re.search(r"\b(risk|regulation|trial|renewal|backlog|pricing|capacity|utilization|shipment|demand|monetization)\b", candidate_text, re.IGNORECASE):
        return candidate_text, "operating_driver"
    return candidate_text, "operating_driver"


def _thread_alignment_terms(
    *,
    focus_areas: Sequence[str],
    investor_focus: Optional[str],
    section_instructions: Optional[Dict[str, str]] = None,
) -> List[str]:
    terms: List[str] = []
    for blob in list(focus_areas or []) + [str(investor_focus or "")]:
        terms.extend(
            token
            for token in re.findall(r"[a-z0-9]{4,}", str(blob or "").lower())
        )
    for text in list(dict(section_instructions or {}).values()):
        terms.extend(
            token for token in re.findall(r"[a-z0-9]{4,}", str(text or "").lower())
        )
    deduped: List[str] = []
    seen: Set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped[:16]


def _score_thread_candidate(
    candidate: ThreadCandidate,
    *,
    filing_analysis: FilingAnalysis,
    alignment_terms: Sequence[str],
) -> ThreadCandidate:
    score = 0.0
    reasons: List[str] = []
    candidate_blob = " ".join(
        part
        for part in (
            candidate.candidate_text,
            candidate.anchor,
            " ".join(candidate.support_evidence or []),
        )
        if str(part or "").strip()
    ).lower()

    if candidate.accepted:
        score += 3.0
        reasons.append("valid_anchor")
    if candidate.anchor_class in {
        "operating_kpi",
        "product_or_segment",
        "management_strategic_bet",
        "company_specific_risk",
    }:
        score += 2.0
        reasons.append(f"anchor_class:{candidate.anchor_class}")
    elif candidate.anchor_class == "operating_driver":
        score += 1.0
        reasons.append("anchor_class:operating_driver")

    support_count = len(
        [item for item in list(candidate.support_evidence or []) if str(item or "").strip()]
    )
    if support_count:
        score += min(2.0, 0.75 * float(support_count))
        reasons.append(f"support_evidence:{support_count}")

    company_specific_hits = 0
    for term in list(filing_analysis.company_terms or [])[:10]:
        normalized = _normalized_thread_key(term)
        if normalized and normalized in candidate_blob:
            company_specific_hits += 1
    if company_specific_hits:
        score += min(2.5, 0.7 * float(company_specific_hits))
        reasons.append(f"company_specific_hits:{company_specific_hits}")

    forward_hits = len(
        re.findall(
            r"\b(next|outlook|guidance|expects?|plans?|targets?|priorit(?:y|ies|ize)|roadmap|commitment)\b",
            candidate_blob,
            re.IGNORECASE,
        )
    )
    if forward_hits:
        score += min(1.5, 0.5 * float(forward_hits))
        reasons.append("future_relevance")

    alignment_hits = sum(1 for term in alignment_terms if term and term in candidate_blob)
    if alignment_hits:
        score += min(2.0, 0.4 * float(alignment_hits))
        reasons.append(f"alignment_hits:{alignment_hits}")

    if _is_invalid_thread_anchor(candidate.anchor):
        score -= 10.0
        reasons.append("invalid_anchor_penalty")

    candidate.score = float(score)
    candidate.score_reasons = reasons
    if not candidate.accepted and not candidate.rejection_reason:
        candidate.rejection_reason = "Anchor is not a valid operating or company-specific driver."
    return candidate


def _build_thread_candidates(
    *,
    company_name: str,
    filing_analysis: FilingAnalysis,
    focus_areas: Sequence[str],
    investor_focus: Optional[str],
    section_instructions: Optional[Dict[str, str]] = None,
) -> List[ThreadCandidate]:
    candidates: List[ThreadCandidate] = []

    def _append(source: str, text: str, evidence: Optional[List[str]] = None) -> None:
        body = str(text or "").strip()
        if not body:
            return
        anchor, anchor_class = _candidate_anchor_from_text(body, analysis=filing_analysis)
        accepted = bool(anchor) and anchor_class != "invalid" and not _is_invalid_thread_anchor(anchor)
        rejection_reason = ""
        if not accepted:
            rejection_reason = "Anchor is not a valid operating or company-specific driver."
        candidates.append(
            _score_thread_candidate(
                ThreadCandidate(
                    source=source,
                    candidate_text=body,
                    anchor=anchor or body,
                    anchor_class=anchor_class,
                    support_evidence=list(evidence or [])[:3],
                    accepted=accepted,
                    rejection_reason=rejection_reason,
                ),
                filing_analysis=filing_analysis,
                alignment_terms=_thread_alignment_terms(
                    focus_areas=focus_areas,
                    investor_focus=investor_focus,
                    section_instructions=section_instructions,
                ),
            )
        )

    _append(
        "central_tension",
        filing_analysis.central_tension,
        [filing_analysis.tension_evidence] if filing_analysis.tension_evidence else [],
    )
    if filing_analysis.management_expectations:
        first_expectation = filing_analysis.management_expectations[0]
        _append(
            "management_expectation",
            first_expectation.expectation or first_expectation.topic,
            [first_expectation.evidence],
        )
    if filing_analysis.kpi_findings:
        kpi = filing_analysis.kpi_findings[0]
        _append(
            "kpi_finding",
            f"{kpi.kpi_name} is the decisive proof point because {kpi.insight or kpi.current_value}",
            [kpi.source_quote or kpi.insight],
        )
    if filing_analysis.management_strategic_bets:
        _append(
            "strategic_bet",
            filing_analysis.management_strategic_bets[0],
            [filing_analysis.management_strategy_summary],
        )
    if filing_analysis.company_specific_risks:
        risk = filing_analysis.company_specific_risks[0]
        _append(
            "company_specific_risk",
            f"{risk.risk_name} is the downside path that would change the current view if {risk.mechanism}",
            [risk.source_quote or risk.evidence_from_filing],
        )
    if investor_focus:
        _append("investor_focus", investor_focus, [investor_focus])
    for area in list(focus_areas or [])[:2]:
        _append("focus_area", area, [area])
    return candidates


def _select_aha_insight(
    filing_analysis: FilingAnalysis,
    *,
    fallback_text: str,
) -> str:
    contrast_re = re.compile(
        r"\b("
        r"now|no longer|not just|not merely|rather than|instead of|shift|shifted|"
        r"inflect(?:ed|ion|s)?|turned?|moved?|fund(?:ing|s)|self[- ]fund(?:ed|ing)|"
        r"validate(?:d|s)|from\b.+\bto"
        r")\b",
        re.IGNORECASE,
    )
    implication_re = re.compile(
        r"\b("
        r"because|which means|that means|therefore|so that|this matters|"
        r"changes the story|funds?|monetiz|conversion|margin|cash flow|"
        r"reinvestment|pricing|mix|utilization|renewal|backlog"
        r")\b",
        re.IGNORECASE,
    )
    generic_re = re.compile(
        r"\b("
        r"management said|management expects|management noted|the next filing|"
        r"watch for|improved|continued|remains|stays"
        r")\b",
        re.IGNORECASE,
    )
    candidates: List[Tuple[int, int, str]] = []
    source_texts: List[str] = []
    source_texts.extend([str(item or "").strip() for item in list(filing_analysis.period_specific_insights or [])])
    source_texts.extend(
        [
            str(item.insight or item.change or item.current_value or "").strip()
            for item in list(filing_analysis.kpi_findings or [])
            if str(item.insight or item.change or item.current_value or "").strip()
        ]
    )
    source_texts.extend(
        [
            str(filing_analysis.management_strategy_summary or "").strip(),
            str(filing_analysis.tension_evidence or "").strip(),
            str(filing_analysis.central_tension or "").strip(),
            str(fallback_text or "").strip(),
        ]
    )
    for idx, raw in enumerate(source_texts):
        candidate = re.sub(r"\s+", " ", str(raw or "").strip()).strip()
        if not candidate or candidate.endswith("?"):
            continue
        score = 0
        if contrast_re.search(candidate):
            score += 5
        if implication_re.search(candidate):
            score += 3
        if re.search(r"\b(than|not|instead|while)\b", candidate, re.IGNORECASE):
            score += 2
        if generic_re.search(candidate):
            score -= 1
        score += min(2, max(0, len(re.findall(r"[a-zA-Z]{4,}", candidate)) // 8))
        candidates.append((score, -idx, candidate))
    if candidates:
        return max(candidates)[2]
    return str(fallback_text or "").strip()


def _fallback_thread_decision(
    *,
    company_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
) -> ThreadDecision:
    fallback_text = _build_fallback_central_tension(
        company_name=company_name,
        company_intelligence=company_intelligence,
        company_terms=filing_analysis.company_terms,
    )
    anchor, anchor_class = _candidate_anchor_from_text(
        fallback_text,
        analysis=filing_analysis,
    )
    if not anchor or _is_invalid_thread_anchor(anchor):
        anchor = (
            str(filing_analysis.management_expectations[0].topic or "").strip()
            if filing_analysis.management_expectations
            else str(filing_analysis.kpi_findings[0].kpi_name or "").strip()
            if filing_analysis.kpi_findings
            else "operating execution"
        )
        anchor_class = "operating_driver"
        fallback_text = (
            f"The key question is whether {company_name} can turn {anchor} into durable economics "
            "without losing execution discipline."
        )
    aha_insight = _select_aha_insight(
        filing_analysis,
        fallback_text=fallback_text,
    )
    return ThreadDecision(
        final_thread=fallback_text,
        anchor=anchor,
        anchor_class=anchor_class,
        aha_insight=aha_insight,
        support_evidence=[item for item in [
            filing_analysis.tension_evidence,
            filing_analysis.management_strategy_summary,
            aha_insight,
        ] if str(item or "").strip()][:3],
        score=0.0,
        score_reasons=["Fallback thread decision used because no scored candidate passed arbitration."],
        rejected_threads=[],
    )


def _arbitrate_thread(
    *,
    company_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    focus_areas: Sequence[str],
    investor_focus: Optional[str],
    section_instructions: Optional[Dict[str, str]] = None,
) -> ThreadDecision:
    candidates = _build_thread_candidates(
        company_name=company_name,
        filing_analysis=filing_analysis,
        focus_areas=focus_areas,
        investor_focus=investor_focus,
        section_instructions=section_instructions,
    )
    accepted_candidates = [
        candidate for candidate in candidates if bool(candidate.accepted)
    ]
    if accepted_candidates:
        accepted_candidates.sort(
            key=lambda item: (
                -float(item.score or 0.0),
                -len(list(item.support_evidence or [])),
                str(item.anchor_class or ""),
                str(item.source or ""),
            )
        )
        winner = accepted_candidates[0]
        aha_insight = _select_aha_insight(
            filing_analysis,
            fallback_text=winner.candidate_text,
        )
        return ThreadDecision(
            final_thread=winner.candidate_text,
            anchor=winner.anchor,
            anchor_class=winner.anchor_class,
            aha_insight=aha_insight,
            support_evidence=list(winner.support_evidence or []),
            score=float(winner.score or 0.0),
            score_reasons=list(winner.score_reasons or []),
            rejected_threads=[item for item in candidates if item is not winner],
        )
    fallback = _fallback_thread_decision(
        company_name=company_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
    )
    fallback.rejected_threads = list(candidates)
    return fallback


def _instruction_target_from_text(text: str, phrase: str) -> str:
    lowered = str(text or "").lower()
    idx = lowered.find(phrase)
    if idx < 0:
        return ""
    remainder = str(text or "")[idx + len(phrase):].strip(" :,-")
    if not remainder:
        return ""
    snippet = re.split(r"[.;\n]", remainder, maxsplit=1)[0]
    return re.sub(r"\s+", " ", snippet).strip()


def _build_instruction_checks(
    *,
    section_name: str,
    instruction_text: str,
) -> List[InstructionCheck]:
    text = str(instruction_text or "").strip()
    if not text:
        return []
    lowered = text.lower()
    checks: List[InstructionCheck] = []
    for check_type, phrase in _INSTRUCTION_THEME_PATTERNS:
        if phrase not in lowered:
            continue
        target = _instruction_target_from_text(text, phrase) or text
        guidance = f"{phrase.title()} {target}".strip()
        checks.append(
            InstructionCheck(
                section_name=section_name,
                check_type=check_type,
                target=target,
                guidance=guidance,
            )
        )
    if (
        any(token in lowered for token in ("guidance", "expects", "outlook", "future"))
        and not any(check.check_type == "must_be_forward_looking" for check in checks)
    ):
        checks.append(
            InstructionCheck(
                section_name=section_name,
                check_type="must_be_forward_looking",
                target=text,
                guidance="Use explicit forward-looking management language and next-step expectations.",
            )
        )
    if (
        any(token in lowered for token in ("management", "leadership", "ceo", "cfo"))
        and not any(check.check_type == "must_use_management_view" for check in checks)
    ):
        checks.append(
            InstructionCheck(
                section_name=section_name,
                check_type="must_use_management_view",
                target=text,
                guidance="Use explicit management expectations, priorities, or attributed guidance.",
            )
        )
    if (
        any(token in lowered for token in ("metric", "watch", "checkpoint"))
        and not any(check.check_type == "must_include_watch_metric" for check in checks)
    ):
        checks.append(
            InstructionCheck(
                section_name=section_name,
                check_type="must_include_watch_metric",
                target=text,
                guidance="Name the decisive metric or operating checkpoint investors should watch next.",
            )
        )
    if not checks:
        checks.append(
            InstructionCheck(
                section_name=section_name,
                check_type="must_prioritize_angle",
                target=text,
                guidance=text,
            )
        )
    return checks[:4]


def _tone_mode_for_preferences(tone: str, section_name: str) -> str:
    base = "Write like a premium analyst note: sharp, plain-English, evidence-led, and slightly conversational without slang."
    if tone == "bullish":
        return base + " Sound constructive with conviction, but never promotional."
    if tone == "bearish":
        return base + " Sound skeptical and decisive, but never theatrical."
    if tone == "cautiously optimistic":
        return base + " Sound constructive but disciplined about what still has to prove out."
    if section_name == "Risk Factors":
        return base + " In risks, prioritize decision-useful downside language over filing boilerplate."
    return base + " Sound direct, investor-facing, and easy to parse."


def _readability_mode_for_preferences(
    detail_level: str,
    output_style: str,
) -> str:
    parts = [
        "Keep sentence openings varied and clean.",
        "Prefer one clear causal point per sentence.",
        "Avoid stacked subordinate clauses and hedge words.",
    ]
    if detail_level in {"snapshot", "headline_only"}:
        parts.append("Favor compact sentences and fast synthesis over exhaustive detail.")
    elif detail_level in {"deep dive", "forensic_deep_dive"}:
        parts.append("Allow depth, but keep conclusions cleaner than the evidence blocks.")
    else:
        parts.append("Use balanced depth with crisp transitions and no filler recap.")
    if output_style == "mixed":
        parts.append("Keep the prose paragraph-based, but make first sentences scan like premium callouts.")
    return " ".join(parts)


def _build_section_plans(
    *,
    narrative_blueprint: NarrativeBlueprint,
    thread_decision: ThreadDecision,
    tone: str,
    detail_level: str,
    output_style: str,
    section_instructions: Optional[Dict[str, str]] = None,
) -> Dict[str, SectionPlan]:
    section_plans: Dict[str, SectionPlan] = {}
    section_instructions = section_instructions or {}
    for section_name, blueprint in narrative_blueprint.section_blueprints.items():
        instruction_checks = _build_instruction_checks(
            section_name=section_name,
            instruction_text=str(section_instructions.get(section_name) or ""),
        )
        owned_evidence = _dedupe_ordered_strings(
            list(blueprint.primary_evidence or []) + [thread_decision.anchor],
            limit=5,
        )
        callback_evidence = _dedupe_ordered_strings(
            list(blueprint.secondary_evidence or []) + list(thread_decision.support_evidence or []),
            limit=4,
        )
        section_plans[section_name] = SectionPlan(
            section_name=section_name,
            job=str(blueprint.section_job or "").strip(),
            question=str(blueprint.section_question or "").strip(),
            owned_evidence=owned_evidence,
            callback_evidence=callback_evidence,
            forbidden_themes=_dedupe_ordered_strings(
                list(blueprint.banned_overlap or []),
                limit=6,
            ),
            forbidden_openings=list(_REPEATED_LEADIN_STEMS),
            tone_mode=_tone_mode_for_preferences(tone, section_name),
            readability_mode=_readability_mode_for_preferences(detail_level, output_style),
            instruction_checks=instruction_checks,
        )
    return section_plans


def _serialize_thread_candidate(candidate: ThreadCandidate) -> Dict[str, Any]:
    return {
        "source": candidate.source,
        "candidate_text": candidate.candidate_text,
        "anchor": candidate.anchor,
        "anchor_class": candidate.anchor_class,
        "support_evidence": list(candidate.support_evidence or []),
        "accepted": bool(candidate.accepted),
        "rejection_reason": candidate.rejection_reason,
        "score": float(candidate.score or 0.0),
        "score_reasons": list(candidate.score_reasons or []),
    }


def _serialize_thread_decision(decision: ThreadDecision) -> Dict[str, Any]:
    return {
        "final_thread": decision.final_thread,
        "anchor": decision.anchor,
        "anchor_class": decision.anchor_class,
        "aha_insight": decision.aha_insight,
        "support_evidence": list(decision.support_evidence or []),
        "score": float(decision.score or 0.0),
        "score_reasons": list(decision.score_reasons or []),
        "rejected_threads": [
            _serialize_thread_candidate(candidate)
            for candidate in list(decision.rejected_threads or [])
        ],
    }


def _serialize_instruction_check(check: InstructionCheck) -> Dict[str, Any]:
    return {
        "section_name": check.section_name,
        "check_type": check.check_type,
        "target": check.target,
        "guidance": check.guidance,
    }


def _serialize_section_plan(plan: SectionPlan) -> Dict[str, Any]:
    return {
        "section_name": plan.section_name,
        "job": plan.job,
        "question": plan.question,
        "owned_evidence": list(plan.owned_evidence or []),
        "callback_evidence": list(plan.callback_evidence or []),
        "forbidden_themes": list(plan.forbidden_themes or []),
        "forbidden_openings": list(plan.forbidden_openings or []),
        "tone_mode": plan.tone_mode,
        "readability_mode": plan.readability_mode,
        "instruction_checks": [
            _serialize_instruction_check(check)
            for check in list(plan.instruction_checks or [])
        ],
    }


def _build_thread_scorecard(decision: ThreadDecision) -> Dict[str, Any]:
    return {
        "selected": {
            "final_thread": decision.final_thread,
            "anchor": decision.anchor,
            "anchor_class": decision.anchor_class,
            "aha_insight": decision.aha_insight,
            "support_evidence": list(decision.support_evidence or []),
            "score": float(decision.score or 0.0),
            "score_reasons": list(decision.score_reasons or []),
        },
        "rejected": [
            _serialize_thread_candidate(candidate)
            for candidate in list(decision.rejected_threads or [])
        ],
    }


def _hydrate_thread_decision(payload: Optional[Dict[str, Any]]) -> Optional[ThreadDecision]:
    if not isinstance(payload, dict):
        return None
    rejected_threads = [
        ThreadCandidate(
            source=str(item.get("source") or ""),
            candidate_text=str(item.get("candidate_text") or ""),
            anchor=str(item.get("anchor") or ""),
            anchor_class=str(item.get("anchor_class") or ""),
            support_evidence=list(item.get("support_evidence") or []),
            accepted=bool(item.get("accepted")),
            rejection_reason=str(item.get("rejection_reason") or ""),
            score=float(item.get("score") or 0.0),
            score_reasons=list(item.get("score_reasons") or []),
        )
        for item in list(payload.get("rejected_threads") or [])
        if isinstance(item, dict)
    ]
    return ThreadDecision(
        final_thread=str(payload.get("final_thread") or ""),
        anchor=str(payload.get("anchor") or ""),
        anchor_class=str(payload.get("anchor_class") or ""),
        aha_insight=str(payload.get("aha_insight") or ""),
        support_evidence=list(payload.get("support_evidence") or []),
        score=float(payload.get("score") or 0.0),
        score_reasons=list(payload.get("score_reasons") or []),
        rejected_threads=rejected_threads,
    )


def _hydrate_section_plan(
    section_name: str,
    payload: Optional[Dict[str, Any]],
) -> Optional[SectionPlan]:
    if not isinstance(payload, dict):
        return None
    instruction_checks = [
        InstructionCheck(
            section_name=str(item.get("section_name") or section_name),
            check_type=str(item.get("check_type") or ""),
            target=str(item.get("target") or ""),
            guidance=str(item.get("guidance") or ""),
        )
        for item in list(payload.get("instruction_checks") or [])
        if isinstance(item, dict)
    ]
    return SectionPlan(
        section_name=str(payload.get("section_name") or section_name),
        job=str(payload.get("job") or ""),
        question=str(payload.get("question") or ""),
        owned_evidence=list(payload.get("owned_evidence") or []),
        callback_evidence=list(payload.get("callback_evidence") or []),
        forbidden_themes=list(payload.get("forbidden_themes") or []),
        forbidden_openings=list(payload.get("forbidden_openings") or []),
        tone_mode=str(payload.get("tone_mode") or ""),
        readability_mode=str(payload.get("readability_mode") or ""),
        instruction_checks=instruction_checks,
    )


def _build_instruction_compliance_results(
    *,
    section_bodies: Dict[str, str],
    section_plans: Dict[str, SectionPlan],
) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for section_name, plan in dict(section_plans or {}).items():
        checks = list((plan.instruction_checks if plan else []) or [])
        body = str(section_bodies.get(section_name) or "")
        failed_checks = [
            _serialize_instruction_check(check)
            for check in checks
            if _instruction_check_failed(check, body)
        ]
        results[section_name] = {
            "total_checks": int(len(checks)),
            "passed": not failed_checks,
            "failed_checks": failed_checks,
            "used_owned_evidence": bool(plan and _body_uses_owned_evidence(body, plan)),
        }
    return results


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 1 — Company Intelligence Agent
# ═══════════════════════════════════════════════════════════════════════════


AGENT_1_SYSTEM_PROMPT = (
    "You are a senior equity research analyst specializing in company-specific "
    "KPI identification. Your job is to determine what makes a company unique "
    "and which metrics investors MUST track to understand its business.\n\n"
    "You MUST output valid JSON matching the schema provided. No markdown, "
    "no commentary outside the JSON."
)

AGENT_1_USER_PROMPT_TEMPLATE = """\
Analyze {company_name} ({ticker}) in the {sector_industry} sector.

This company filed a {filing_type}. I need you to identify:

1. BUSINESS IDENTITY: What does {company_name} actually do? How do they make money?
   Be specific — not "technology company" but "subscription streaming service that
   monetizes through tiered monthly memberships across 190+ countries."

2. PRIMARY KPIs (CRITICAL — most important part):
   Identify exactly 3-5 Key Performance Indicators that are SPECIFIC to
   {company_name}'s business model. These should be the metrics that professional
   analysts covering this stock actually track.

   Rules for KPI selection:
   - NEVER include generic financial metrics (Revenue, Net Income, EPS, Operating
     Margin) — those apply to ALL companies. Only business-model-specific metrics.
   - For a streaming company: subscribers, ARM, churn, content spend
   - For a bank: NIM, loan growth, deposit growth, credit quality, CET1 ratio
   - For a SaaS company: ARR, NDR, CAC payback, rule of 40
   - For a retailer: same-store sales, store count, sales per sq ft
   - For an insurer: combined ratio, premium growth, loss ratio
   - For a pharma company: pipeline candidates, approval milestones, patent cliffs
   - For each KPI, explain WHY it matters and include search terms to find it in filings

3. COMPETITIVE MOAT: What is {company_name}'s actual competitive advantage?
   Not "strong brand" — something specific like "largest content library with $17B
   annual content spend creating a scale barrier."

4. KEY COMPETITORS: Name 2-4 actual competitors.

5. INVESTOR FOCUS AREAS: What should an investment memo emphasize? List 3-5 specific
   analytical questions.

6. BUSINESS ARCHETYPE: Classify the company into exactly one of these operating archetypes:
   - cloud_software
   - semicap_hardware
   - industrial_manufacturing
   - retail_consumer
   - payments_marketplaces
   - bank
   - insurance_asset_manager
   - pharma_biotech_medtech
   - energy_materials_utilities
   - telecom_media_ads
   - diversified_other

7. INDUSTRY KPI NORMS: What does "good" look like for the primary KPIs in this industry?

{existing_context}

Output your analysis as JSON matching this exact schema:
{{
  "business_identity": "string — 2-3 sentences",
  "competitive_moat": "string — 1-2 specific sentences",
  "primary_kpis": [
    {{
      "name": "string — metric name",
      "why_it_matters": "string — 1 sentence",
      "filing_search_terms": ["term1", "term2"],
      "metric_type": "count|currency|percentage|ratio"
    }}
  ],
  "key_competitors": ["competitor1", "competitor2"],
  "competitive_dynamics": "string — 1-2 sentences",
  "investor_focus_areas": ["area1", "area2", "area3"],
  "business_archetype": "one of: cloud_software|semicap_hardware|industrial_manufacturing|retail_consumer|payments_marketplaces|bank|insurance_asset_manager|pharma_biotech_medtech|energy_materials_utilities|telecom_media_ads|diversified_other",
  "industry_kpi_norms": "string — 2-3 sentences"
}}"""


def _intelligence_cache_key(
    company_name: str, ticker: str, filing_type: str
) -> str:
    """Cache key includes filing_type since 10-K vs 10-Q may surface different KPIs."""
    raw = (
        f"{(company_name or '').strip().lower()}"
        f"|{(ticker or '').strip().upper()}"
        f"|{(filing_type or '').strip().upper()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read_intelligence_cache(
    cache_key: str,
) -> Optional[CompanyIntelligenceProfile]:
    """Read cached intelligence profile from Supabase. Returns None on miss."""
    try:
        from app.models.database import get_supabase_client

        client = get_supabase_client()
    except Exception:
        return None

    try:
        response = (
            client.table(INTELLIGENCE_CACHE_TABLE)
            .select("intelligence_json, created_at")
            .eq("cache_key", cache_key)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None

        row = response.data[0]
        intelligence_json = row.get("intelligence_json")
        if not intelligence_json or not isinstance(intelligence_json, dict):
            return None

        # Check TTL
        created_at_str = row.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(
                    str(created_at_str).replace("Z", "+00:00")
                )
                age = (datetime.now(timezone.utc) - created_at).total_seconds()
                if age > INTELLIGENCE_CACHE_TTL_SECONDS:
                    return None
            except Exception:
                pass

        return _parse_intelligence_profile(intelligence_json, from_cache=True)

    except Exception as exc:
        logger.debug("Intelligence cache read error: %s", exc)
        return None


def _write_intelligence_cache(
    cache_key: str,
    company_name: str,
    ticker: str,
    filing_type: str,
    profile: CompanyIntelligenceProfile,
) -> None:
    """Write intelligence profile to Supabase cache (best-effort)."""
    try:
        from app.models.database import get_supabase_client

        client = get_supabase_client()
    except Exception:
        return

    try:
        intelligence_dict = _profile_to_dict(profile)
        row = {
            "cache_key": cache_key,
            "company_name": (company_name or "").strip(),
            "ticker": (ticker or "").strip().upper(),
            "dossier_text": profile.raw_brief,  # backward compat
            "intelligence_json": intelligence_dict,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(INTELLIGENCE_CACHE_TABLE).upsert(
            row, on_conflict="cache_key"
        ).execute()
    except Exception as exc:
        logger.debug("Intelligence cache write error: %s", exc)


def _parse_intelligence_profile(
    data: Dict[str, Any],
    *,
    from_cache: bool = False,
    sector: str = "",
    industry: str = "",
) -> CompanyIntelligenceProfile:
    """Parse a dict (from JSON) into a CompanyIntelligenceProfile."""
    kpis = []
    for kpi_data in data.get("primary_kpis") or []:
        if not isinstance(kpi_data, dict):
            continue
        kpis.append(
            KPIDescriptor(
                name=str(kpi_data.get("name") or ""),
                why_it_matters=str(kpi_data.get("why_it_matters") or ""),
                filing_search_terms=kpi_data.get("filing_search_terms") or [],
                metric_type=str(kpi_data.get("metric_type") or "currency"),
            )
        )

    business_identity = str(data.get("business_identity") or "")
    investor_focus_areas = [
        str(item) for item in (data.get("investor_focus_areas") or [])
    ]
    business_archetype = _normalize_business_archetype(
        str(data.get("business_archetype") or "")
        or _infer_business_archetype(
            sector=sector,
            industry=industry,
            business_identity=business_identity,
            primary_kpis=kpis,
            investor_focus_areas=investor_focus_areas,
        )
    )

    return CompanyIntelligenceProfile(
        business_identity=business_identity,
        competitive_moat=str(data.get("competitive_moat") or ""),
        primary_kpis=kpis,
        key_competitors=data.get("key_competitors") or [],
        competitive_dynamics=str(data.get("competitive_dynamics") or ""),
        investor_focus_areas=investor_focus_areas,
        industry_kpi_norms=str(data.get("industry_kpi_norms") or ""),
        raw_brief=str(data.get("raw_brief") or ""),
        business_archetype=business_archetype,
        from_cache=from_cache,
    )


def _profile_to_dict(profile: CompanyIntelligenceProfile) -> Dict[str, Any]:
    """Serialize a CompanyIntelligenceProfile to a JSON-safe dict."""
    return {
        "business_identity": profile.business_identity,
        "competitive_moat": profile.competitive_moat,
        "primary_kpis": [
            {
                "name": kpi.name,
                "why_it_matters": kpi.why_it_matters,
                "filing_search_terms": kpi.filing_search_terms,
                "metric_type": kpi.metric_type,
            }
            for kpi in profile.primary_kpis
        ],
        "key_competitors": profile.key_competitors,
        "competitive_dynamics": profile.competitive_dynamics,
        "investor_focus_areas": profile.investor_focus_areas,
        "business_archetype": _normalize_business_archetype(
            getattr(profile, "business_archetype", "") or "diversified_other"
        ),
        "industry_kpi_norms": profile.industry_kpi_norms,
        "raw_brief": profile.raw_brief,
    }


def _build_raw_brief(profile: CompanyIntelligenceProfile) -> str:
    """Build a flat-text brief from a profile for backward compatibility."""
    parts = []
    if profile.business_identity:
        parts.append(f"BUSINESS: {profile.business_identity}")
    if profile.business_archetype:
        parts.append(f"ARCHETYPE: {profile.business_archetype}")
    if profile.competitive_moat:
        parts.append(f"MOAT: {profile.competitive_moat}")
    if profile.primary_kpis:
        kpi_lines = ", ".join(k.name for k in profile.primary_kpis)
        parts.append(f"KEY KPIs: {kpi_lines}")
    if profile.key_competitors:
        parts.append(f"COMPETITORS: {', '.join(profile.key_competitors)}")
    if profile.competitive_dynamics:
        parts.append(f"DYNAMICS: {profile.competitive_dynamics}")
    if profile.investor_focus_areas:
        focus = "; ".join(profile.investor_focus_areas)
        parts.append(f"FOCUS: {focus}")
    if profile.industry_kpi_norms:
        parts.append(f"NORMS: {profile.industry_kpi_norms}")
    return "\n".join(parts)


def _run_agent_1(
    *,
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    filing_type: str,
    filing_date: str = "",
    openai_client: Any,
) -> CompanyIntelligenceProfile:
    """Run Agent 1 — Company Intelligence Agent.

    Checks cache first.  On miss, uses GPT-5.2 with web search to generate
    a structured intelligence profile.
    """
    # 1. Check cache
    cache_key = _intelligence_cache_key(company_name, ticker, filing_type)
    cached = _read_intelligence_cache(cache_key)
    if cached:
        logger.info(
            "Agent 1 cache HIT for %s (%s) — skipping LLM call",
            company_name,
            ticker,
        )
        return cached

    # 2. Generate via LLM
    logger.info(
        "Agent 1 cache MISS for %s (%s) — running Company Intelligence Agent",
        company_name,
        ticker,
    )

    sector_industry = f"{sector}/{industry}" if sector or industry else "Unknown"
    existing_context = ""

    prompt = AGENT_1_USER_PROMPT_TEMPLATE.format(
        company_name=company_name,
        ticker=ticker,
        sector_industry=sector_industry,
        filing_type=filing_type or "SEC filing",
        existing_context=existing_context,
    )

    try:
        # PRIMARY: Responses API with web search (time-aware)
        raw_json = openai_client.research_company_intelligence_with_web(
            prompt=prompt,
            system_message=AGENT_1_SYSTEM_PROMPT,
            filing_date=filing_date,
            timeout_seconds=30.0,
        )
        # FALLBACK: Chat Completions without web search
        if not raw_json or not isinstance(raw_json, dict):
            logger.info(
                "Agent 1 web search returned no valid JSON for %s, falling back to chat",
                company_name,
            )
            raw_json = openai_client.research_company_intelligence(
                prompt=prompt,
                system_message=AGENT_1_SYSTEM_PROMPT,
                timeout_seconds=25.0,
            )

        if not raw_json or not isinstance(raw_json, dict):
            logger.warning("Agent 1 returned invalid JSON for %s", company_name)
            return _build_fallback_profile(
                company_name, ticker, sector, industry, openai_client
            )

        profile = _parse_intelligence_profile(
            raw_json,
            from_cache=False,
            sector=sector,
            industry=industry,
        )

        # Build a human-readable brief for backward compat
        if not profile.raw_brief:
            profile.raw_brief = _build_raw_brief(profile)

        # 3. Cache the result
        _write_intelligence_cache(
            cache_key, company_name, ticker, filing_type, profile
        )

        return profile

    except Exception as exc:
        logger.warning(
            "Agent 1 failed for %s (%s): %s. Using fallback.",
            company_name,
            ticker,
            exc,
        )
        return _build_fallback_profile(
            company_name, ticker, sector, industry, openai_client
        )


def _build_fallback_profile(
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    openai_client: Any,
) -> CompanyIntelligenceProfile:
    """Build an archetype-aware fallback profile when research fails."""
    brief = ""
    try:
        brief = openai_client.research_company_background(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
        )
    except Exception:
        pass

    business_archetype = _infer_business_archetype(
        sector=sector,
        industry=industry,
        business_identity=brief,
        context_text=brief,
    )
    config = _archetype_config(business_archetype)
    sector_industry = "/".join(part for part in (sector, industry) if str(part or "").strip())
    primary_kpis = _fallback_kpis_for_archetype(business_archetype)
    kpi_names = ", ".join(kpi.name for kpi in primary_kpis[:3])
    profile = CompanyIntelligenceProfile(
        business_identity=(
            f"{company_name} is best understood as a {config.get('identity')}"
            + (f" in {sector_industry}." if sector_industry else ".")
        ),
        competitive_moat=(
            f"Durability in this archetype depends on execution around {', '.join(config.get('default_terms')[:3])}."
            if config.get("default_terms")
            else ""
        ),
        primary_kpis=primary_kpis,
        key_competitors=[],
        competitive_dynamics=(
            f"Investors usually focus on {', '.join(config.get('focus_areas')[:2])}."
            if config.get("focus_areas")
            else ""
        ),
        investor_focus_areas=[str(item) for item in list(config.get("focus_areas") or [])],
        industry_kpi_norms=(
            f"Relevant KPI discipline in this operating model usually centers on {kpi_names}."
            if kpi_names
            else ""
        ),
        raw_brief=brief or "",
        business_archetype=business_archetype,
        from_cache=False,
    )
    if not profile.raw_brief:
        profile.raw_brief = _build_raw_brief(profile)
    return profile


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 2 — Filing Analysis Agent
# ═══════════════════════════════════════════════════════════════════════════


AGENT_2_SYSTEM_PROMPT = (
    "You are a senior equity research analyst performing deep filing analysis. "
    "You have been given a company intelligence profile that identifies what "
    "metrics and dynamics matter most for this specific company.\n\n"
    "You also have real-time context about what was happening with the company "
    "around the filing date. Use this to ground your analysis in the correct "
    "time period.\n\n"
    "Your job is to read the filing and extract ONLY the information that is "
    "relevant to THIS company's specific business model and KPIs.\n\n"
    "DO NOT extract generic financial metrics (Revenue, Net Income, EPS) unless "
    "they are directly relevant to the company-specific thesis. Focus first on the "
    "PRIMARY KPIs identified in the intelligence profile and use generic financials "
    "only as support when they are the real driver.\n\n"
    "MANAGEMENT QUOTES ARE MANDATORY: Extract at least 3 verbatim quotes from "
    "the filing text. Each must be ≤25 words and reveal strategy, outlook, or "
    "risk acknowledgment.\n\n"
    "MANAGEMENT STRATEGIC BETS: Identify 2-3 specific strategic bets management "
    "is making — investments, market entries, product launches, cost programs. "
    "These are not generic statements like 'investing in growth' but specific "
    "commitments like 'investing $10B in data center capacity in Arizona.'\n\n"
    "FORWARD GUIDANCE: Summarize what management explicitly expects or guides "
    "for the next 1-2 periods. Use their own language and numbers.\n\n"
    "PROMISE SCORECARD: If the filing references prior guidance or commitments, "
    "note whether management delivered, is on track, or missed. If no prior "
    "guidance is referenced, note what management is now committing to.\n\n"
    "COMPANY TERMS: Extract 8-15 high-signal company nouns/phrases directly "
    "from the filing that make the memo unmistakably about this company. "
    "Prefer product names, segments, geographies, regulations, customer types, "
    "distribution channels, programs, and operating terms.\n\n"
    "MANAGEMENT EXPECTATIONS: Extract 2-4 concrete forward-looking statements "
    "from management with a topic, timeframe, and short evidence quote or "
    "paraphrase anchor.\n\n"
    "PROMISE SCORECARD ITEMS: Extract 2-4 explicit commitment assessments with "
    "status labels of delivered, on_track, missed, or new_commitment, plus the "
    "evidence supporting that assessment.\n\n"
    "RISKS MUST BE COMPANY-SPECIFIC. BANNED generic risks: 'macroeconomic "
    "uncertainty', 'competitive pressure', 'regulatory risk', 'margin "
    "compression' — name the SPECIFIC factor, competitor, product, segment, "
    "customer concentration, geography, funding structure, supply/input, or regulation.\n\n"
    "RISK ≠ METRIC: A risk is a BUSINESS EVENT (customer loss, regulation change, "
    "competitor launch, supply disruption, patent expiration, contract non-renewal). "
    "Financial figures (operating margin %, FCF, cash balance) are EVIDENCE you cite "
    "inside a risk body, NOT the risk itself. Start each risk with the business event, "
    "not a financial number.\n\n"
    "RISK MATERIALITY FILTER (CRITICAL): Most SEC filings list 20-50 risk factors as "
    "legal boilerplate. Your job is NOT to summarize those disclosures. Instead, identify "
    "the 1-2 risks that a portfolio manager would ACTUALLY worry about — risks where: "
    "(a) there is a credible, near-term triggering mechanism (not hypothetical), "
    "(b) the financial impact would be material (>5% revenue or >200bps margin), and "
    "(c) the risk is ASYMMETRIC — it could get much worse but the upside is already priced. "
    "Ignore boilerplate legal-cover risks like 'we may face competition' or 'regulations "
    "may change' UNLESS the filing identifies a SPECIFIC regulatory proceeding, competitor "
    "action, or market shift with a timeline. A risk that has been in every filing for 5 "
    "years without materializing is not a real risk — it is disclosure padding.\n\n"
    "You MUST output valid JSON matching the schema provided.\n\n"
    "RISK FACTOR QUOTES ARE MANDATORY: Extract at least 2 verbatim quotes from the "
    "RISK FACTORS section of the filing that identify specific exposures, products, "
    "customers, geographies, or regulations at risk. Tag these with "
    "suggested_section: 'Risk Factors', source_section, and source_quote. These must "
    "be the company's own words about their risks, not generic language.\n\n"
    "ZERO GENERIC TOLERANCE: Every extracted insight, quote, risk, and KPI finding "
    "must be specific to THIS company. If an extraction could apply to any large-cap "
    "company without changing any nouns, discard it and find something company-specific. "
    "Reject generic risk names, metric-led names, numeric-led openings, and boilerplate "
    "mechanism language."
)

AGENT_2_USER_PROMPT_TEMPLATE = """\
Analyze the {filing_type} filing for {company_name} ({filing_period}).

=== COMPANY INTELLIGENCE (from prior research) ===
Business Archetype: {business_archetype}
Business: {business_identity}
Competitive Moat: {competitive_moat}

PRIMARY KPIs TO FIND (these are the metrics that matter for {company_name}):
{formatted_kpi_list}

Investor Focus Areas:
{formatted_focus_areas}

USER STYLE / OUTPUT PREFERENCES:
{style_preferences}

PER-SECTION USER INSTRUCTION BRIEFS:
{section_instruction_briefs}

=== FILING TEXT ===
{context_excerpt}

Filing Date: {filing_date}

=== TIME-AWARE COMPANY CONTEXT (around {filing_date}) ===
{current_company_context}

CRITICAL: This filing is from {filing_date}. Your analysis MUST be grounded in
this time period. Do NOT reference events that occurred after this date.

=== MD&A EXCERPT ===
{mda_excerpt}

=== RISK FACTORS EXCERPT ===
{risk_factors_excerpt}

=== COMPANY-SPECIFIC KPI DATA (pre-extracted from filing) ===
{company_kpi_context}

=== FINANCIAL DATA ===
{financial_snapshot}
{metrics_lines}

=== PERIOD COMPARISON DATA ===
{prior_period_delta}

=== FILING LANGUAGE SNIPPETS (available for verbatim quotes) ===
{filing_language_snippets}

YOUR TASK:
1. CENTRAL TENSION — THE UNDERWRITING QUESTION:
   Identify the ONE strategic question that, if answered differently, would
   CHANGE THE INVESTMENT DECISION for {company_name}. This is not "Will revenue
   grow?" — that is obvious. This is the non-obvious question hiding in the data.

   Framework: Look for the CONTRADICTION in the filing — where two signals
   point in opposite directions. Examples:
   - "Revenue is accelerating but the growth is coming from lower-margin segments"
   - "Free cash flow is strong but only because capex was deferred"
   - "Customer count is growing but revenue per customer is declining"
   - "Management guides higher but the balance sheet suggests they'll need to raise capital"

   The tension must be specific to {company_name} and grounded in THIS filing's
   data. State it as a question the memo will answer.
   Ground it in the company intelligence above.

2. KPI FINDINGS: For each PRIMARY KPI listed above, find its current value, prior
   period value, and the change. If a KPI is not mentioned in the filing, skip it
   — do NOT invent data. PRIORITIZE company-model KPIs over generic Revenue/EPS
   whenever available. For each found KPI, write ONE sentence explaining why
   the change matters for the thesis and what it says about the business now.

3. PERIOD-SPECIFIC INSIGHTS — THE "MODEL-REVISION TEST":
   For each candidate insight, ask: "Would a sell-side analyst revise their
   financial model because of this?" If yes, it's material. If no, skip it.

   Prioritize in this order:
   (a) SURPRISES: What deviated from consensus or management's own prior guidance?
       (e.g., margin expanded when street expected contraction)
   (b) INFLECTION SIGNALS: What changed direction or accelerated/decelerated
       meaningfully? (e.g., backlog growth turned negative for the first time)
   (c) NEW INFORMATION: What was disclosed for the first time that wasn't in
       prior filings? (e.g., new segment reporting, first mention of a product)
   (d) MANAGEMENT TONE SHIFTS: Did management's language about a topic change
       notably from prior periods? (e.g., from "confident" to "cautious")

   List 3-5 insights. Each should start with the WHAT (the fact), followed by
   the SO-WHAT (why it matters for the investment thesis).

4. MANAGEMENT QUOTES: Find 3-6 high-signal verbatim quotes from the filing text.
   Each must be ≤25 words, reveal strategy/outlook/risk, and appear in the provided
   filing text. Tag each with which memo section it best fits.

5. COMPANY-SPECIFIC RISKS (apply the MATERIALITY FILTER):
   Step 1: Scan the filing's risk factors section AND the MD&A for risks.
   Step 2: For each candidate risk, ask: "Would this make a portfolio manager
   change their position size THIS quarter?" If no, discard it.
   Step 3: Rank by: (a) probability of triggering in next 4 quarters,
   (b) magnitude of P&L impact, (c) whether the market already knows.
   Step 4: Keep only the top 2-3. Each must name a BUSINESS EVENT, not a metric.

   DISCARD these common boilerplate patterns:
   - "We face competition from..." (unless a specific competitor launched something)
   - "Regulatory changes may..." (unless a specific regulation/proceeding is pending with a timeline)
   - "Our dependence on key personnel..." (almost never material)
   - "Cybersecurity threats..." (unless a specific incident occurred)
   - "Foreign currency fluctuations..." (boilerplate for multinationals unless a specific FX event is named)

   KEEP risks like:
   - A specific antitrust case with a ruling date
   - A customer concentration where top-3 customers are >30% revenue
   - A product transition where the new product isn't ramping fast enough
   - A capacity buildout that may overshoot demand
   - A pricing headwind from a specific competitive response

   Each risk must be tied to a real company exposure: a product, segment,
   geography, customer class, regulation, supply/input, funding structure, or
   operating model mechanism.
   For each, explain what could go wrong, why it matters for this company,
   and what investors should watch.
   For each risk, include a verbatim quote or close paraphrase from the filing's
   risk factors section in the "evidence_from_filing" field, plus source_section
   and source_quote fields. The reader should see the company's own language about
   this risk.
   Look for risks throughout the filing (management commentary, competitive
   discussion), not just the Risk Factors section.
   ANTI-PATTERN (will be REJECTED):
   - "Unit-Economics Reset Risk", "Infrastructure Utilization Risk", "Capital Allocation Constraint Risk"
   - "Operating Model Leverage Risk", "Revenue Concentration Risk", "Margin Durability Risk"
   - Any name that another company in a different industry could have.
   TEST: the risk name must contain a product, segment, customer, or technology noun from this filing.
   Do not rename generic risks into pseudo-specific ones. If the source risk is generic,
   discard it and mine a better source-backed candidate elsewhere in the filing.

6. MANAGEMENT STRATEGIC BETS: Identify 2-3 specific strategic bets management
   is making in this filing — concrete investments, market entries, product
   launches, cost restructuring programs. NOT generic statements ("investing in
   growth") but specific commitments ("investing $10B in Arizona data center
   capacity," "launching X product in European markets Q3").

7. FORWARD GUIDANCE: Summarize what management explicitly expects, guides, or
   targets for the next 1-2 periods. Use their own language and numbers.

8. PROMISE SCORECARD: If the filing references prior guidance, commitments, or
   stated targets, assess whether management delivered, is on track, or missed.
   If no prior guidance is available, note what management is now committing to.

9. COMPANY TERMS: Extract 8-15 high-signal company nouns/phrases from the filing.
   These should be the words that keep the final memo from sounding generic.

10. MANAGEMENT EXPECTATIONS: Extract 2-4 explicit forward-looking management
   expectations with:
   - a topic,
   - what management expects,
   - the timeframe,
   - a short evidence anchor from the filing.

11. PROMISE SCORECARD ITEMS: Extract 2-4 promise-vs-delivery items with:
   - the commitment,
   - status: delivered | on_track | missed | new_commitment,
   - assessment,
   - supporting evidence.

12. EVIDENCE MAP: For each memo section (Executive Summary, Financial Performance,
   MD&A, Risk Factors, Closing Takeaway), list the 2-3 most important CLAIMS to
   include, not just loose facts. Each item should already sound like a section-ready
   point with company-specific nouns and, when relevant, the quote/metric anchor.
   For Risk Factors specifically: list the 2-3 most PROBABLE and MATERIAL risk
   mechanisms (not boilerplate disclosures). Each should name the specific trigger,
   the affected business line, and why it is not already priced in.
   Respect the relevant per-section user instruction when choosing the claims.

13. DECISIVE WATCH METRICS:
   Name 2-3 metrics or operating checkpoints investors should watch next, and
   explain why each one would change the current view. Prefer company-model
   KPIs, guidance checkpoints, or management execution proof points over generic
   headline figures.

Output as JSON matching this schema:
{{
  "central_tension": "string",
  "tension_evidence": "string — 2-3 sentences",
  "kpi_findings": [
    {{
      "kpi_name": "string",
      "current_value": "string",
      "prior_value": "string or null",
      "change": "string or null",
      "change_direction": "improved|declined|stable",
      "insight": "string — 1 sentence",
      "source_quote": "string"
    }}
  ],
  "period_specific_insights": ["string", "string", "string"],
  "management_quotes": [
    {{
      "quote": "string — verbatim ≤25 words",
      "attribution": "string",
      "topic": "string",
      "suggested_section": "string"
    }}
  ],
  "management_strategy_summary": "string — 2-3 sentences",
  "company_specific_risks": [
    {{
      "risk_name": "string",
      "mechanism": "string",
      "early_warning": "string",
      "evidence_from_filing": "string",
      "source_section": "string",
      "source_quote": "string"
    }}
  ],
  "company_terms": ["string — company/product/segment/program term"],
  "management_expectations": [
    {{
      "topic": "string",
      "expectation": "string",
      "timeframe": "string",
      "evidence": "string"
    }}
  ],
  "promise_scorecard_items": [
    {{
      "commitment": "string",
      "status": "delivered|on_track|missed|new_commitment",
      "assessment": "string",
      "evidence": "string"
    }}
  ],
  "management_strategic_bets": ["string — specific bet 1", "specific bet 2"],
  "forward_guidance_summary": "string — 2-3 sentences of management's forward view using their own words",
  "promise_scorecard": "string — delivery assessment on prior commitments or new commitments",
  "decisive_watch_metrics": ["string — metric or operating checkpoint investors should watch next"],
  "evidence_map": {{
    "Executive Summary": ["string", "string"],
    "Financial Performance": ["string", "string"],
    "Management Discussion & Analysis": ["string", "string"],
    "Risk Factors": ["string", "string"],
    "Closing Takeaway": ["string", "string"]
  }}
}}"""


def _format_kpi_list(profile: CompanyIntelligenceProfile) -> str:
    """Format the KPI list for Agent 2's prompt."""
    if not profile.primary_kpis:
        return "(No company-specific KPIs identified — use standard financial metrics)"
    lines = []
    for i, kpi in enumerate(profile.primary_kpis, 1):
        search = ", ".join(kpi.filing_search_terms) if kpi.filing_search_terms else ""
        lines.append(
            f"{i}. {kpi.name} ({kpi.metric_type})\n"
            f"   Why: {kpi.why_it_matters}\n"
            f"   Search for: {search}"
        )
    return "\n".join(lines)


def _format_focus_areas(profile: CompanyIntelligenceProfile) -> str:
    """Format investor focus areas for Agent 2's prompt."""
    if not profile.investor_focus_areas:
        return "(Use standard financial analysis focus areas)"
    return "\n".join(f"- {area}" for area in profile.investor_focus_areas)


def _parse_filing_analysis(data: Dict[str, Any]) -> FilingAnalysis:
    """Parse a dict (from JSON) into a FilingAnalysis."""
    kpi_findings = []
    for kf in data.get("kpi_findings") or []:
        if not isinstance(kf, dict):
            continue
        kpi_findings.append(
            KPIFinding(
                kpi_name=str(kf.get("kpi_name") or ""),
                current_value=str(kf.get("current_value") or ""),
                prior_value=kf.get("prior_value"),
                change=kf.get("change"),
                change_direction=kf.get("change_direction"),
                insight=str(kf.get("insight") or ""),
                source_quote=str(kf.get("source_quote") or ""),
            )
        )

    quotes = []
    for q in data.get("management_quotes") or []:
        if not isinstance(q, dict):
            continue
        quotes.append(
            ManagementQuote(
                quote=str(q.get("quote") or ""),
                attribution=str(q.get("attribution") or ""),
                topic=str(q.get("topic") or ""),
                suggested_section=str(q.get("suggested_section") or ""),
            )
        )

    company_terms = [
        str(term).strip()
        for term in (data.get("company_terms") or [])
        if str(term or "").strip()
    ]

    risks = []
    for r in data.get("company_specific_risks") or []:
        if not isinstance(r, dict):
            continue
        risk_name = str(r.get("risk_name") or "").strip()
        mechanism = str(r.get("mechanism") or "")
        early_warning = str(r.get("early_warning") or "")
        source_section = str(r.get("source_section") or "").strip() or "Risk Factors"
        source_quote = str(
            r.get("source_quote")
            or r.get("evidence_from_filing")
            or ""
        ).strip()
        evidence_from_filing = source_quote or str(r.get("evidence_from_filing") or "")
        candidate = RiskEvidenceCandidate(
            risk_name=risk_name,
            source_section=source_section,
            source_quote=source_quote,
            source_anchor_terms=tuple(
                extract_anchor_terms(
                    " ".join(
                        part
                        for part in (
                            risk_name,
                            mechanism,
                            early_warning,
                            source_quote,
                            evidence_from_filing,
                        )
                        if part
                    ),
                    company_terms=company_terms,
                    limit=6,
                )
            ),
            mechanism_seed=mechanism,
            early_warning_seed=early_warning,
        )
        ok, _reason = candidate_is_strictly_acceptable(candidate, company_terms=company_terms)
        if not ok:
            continue
        risks.append(
            CompanyRisk(
                risk_name=risk_name,
                mechanism=mechanism,
                early_warning=early_warning,
                evidence_from_filing=(
                    candidate_to_evidence_line(candidate)
                    or evidence_from_filing
                    or source_quote
                ),
                source_section=source_section,
                source_quote=source_quote,
            )
        )

    expectations = []
    for item in data.get("management_expectations") or []:
        if not isinstance(item, dict):
            continue
        expectations.append(
            ManagementExpectation(
                topic=str(item.get("topic") or ""),
                expectation=str(item.get("expectation") or ""),
                timeframe=str(item.get("timeframe") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    promise_items = []
    for item in data.get("promise_scorecard_items") or []:
        if not isinstance(item, dict):
            continue
        promise_items.append(
            PromiseScorecardItem(
                commitment=str(item.get("commitment") or ""),
                status=str(item.get("status") or ""),
                assessment=str(item.get("assessment") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    evidence_map_raw = data.get("evidence_map") or {}
    evidence_map: Dict[str, List[str]] = {}
    for section, items in evidence_map_raw.items():
        if isinstance(items, list):
            evidence_map[str(section)] = [str(i) for i in items]
    if risks:
        evidence_map["Risk Factors"] = [
            _risk_source_evidence_line(risk) for risk in risks[:3]
        ]
    else:
        evidence_map.setdefault("Risk Factors", [])

    return FilingAnalysis(
        central_tension=str(data.get("central_tension") or ""),
        tension_evidence=str(data.get("tension_evidence") or ""),
        kpi_findings=kpi_findings,
        period_specific_insights=[
            str(s) for s in (data.get("period_specific_insights") or [])
        ],
        management_quotes=quotes,
        management_strategy_summary=str(
            data.get("management_strategy_summary") or ""
        ),
        company_specific_risks=risks,
        evidence_map=evidence_map,
        company_terms=company_terms,
        management_expectations=expectations,
        promise_scorecard_items=promise_items,
        management_strategic_bets=[
            str(s) for s in (data.get("management_strategic_bets") or [])
        ],
        forward_guidance_summary=str(data.get("forward_guidance_summary") or ""),
        promise_scorecard=str(data.get("promise_scorecard") or ""),
        decisive_watch_metrics=[
            str(item).strip()
            for item in (data.get("decisive_watch_metrics") or [])
            if str(item or "").strip()
        ],
    )


def _run_agent_2(
    *,
    company_intelligence: CompanyIntelligenceProfile,
    company_name: str,
    ticker: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    context_excerpt: str,
    mda_excerpt: str,
    risk_factors_excerpt: str,
    company_kpi_context: str,
    financial_snapshot: str,
    metrics_lines: str,
    prior_period_delta_block: str,
    filing_language_snippets: str,
    openai_client: Any,
    preferences: Any = None,
    section_instructions: Optional[Dict[str, str]] = None,
) -> FilingAnalysis:
    """Run Agent 2 — Filing Analysis Agent."""
    cleaned_risk_factors_excerpt = _clean_risk_excerpt_for_prompt(risk_factors_excerpt)
    # Fetch time-aware company context (non-blocking)
    current_context = ""
    if filing_date:
        try:
            current_context = openai_client.research_company_current_context(
                company_name=company_name,
                ticker=ticker,
                filing_date=filing_date,
                filing_type=filing_type,
                timeout_seconds=18.0,
            )
        except Exception:
            pass

    prompt = AGENT_2_USER_PROMPT_TEMPLATE.format(
        filing_type=filing_type or "SEC filing",
        company_name=company_name,
        filing_period=filing_period or "",
        business_archetype=(
            getattr(company_intelligence, "business_archetype", "")
            or "diversified_other"
        ),
        business_identity=company_intelligence.business_identity,
        competitive_moat=company_intelligence.competitive_moat,
        formatted_kpi_list=_format_kpi_list(company_intelligence),
        formatted_focus_areas=_format_focus_areas(company_intelligence),
        style_preferences=_format_style_preferences_for_agent_2(preferences),
        section_instruction_briefs=_format_section_instruction_briefs(
            section_instructions
        ),
        context_excerpt=context_excerpt or "(No filing text available)",
        filing_date=filing_date or "(Not available)",
        current_company_context=current_context or "(No additional context available)",
        mda_excerpt=mda_excerpt or "(No MD&A excerpt available)",
        risk_factors_excerpt=cleaned_risk_factors_excerpt or "(No risk factors available)",
        company_kpi_context=company_kpi_context or "(None)",
        financial_snapshot=financial_snapshot or "(None)",
        metrics_lines=metrics_lines or "(None)",
        prior_period_delta=prior_period_delta_block or "(None)",
        filing_language_snippets=filing_language_snippets or "(None)",
    )

    try:
        raw_json = openai_client.analyze_filing_with_context(
            prompt=prompt,
            system_message=AGENT_2_SYSTEM_PROMPT,
            timeout_seconds=40.0,
        )

        if not raw_json or not isinstance(raw_json, dict):
            logger.warning("Agent 2 returned invalid JSON for %s", company_name)
            return _build_fallback_analysis(
                company_name=company_name,
                company_intelligence=company_intelligence,
                context_excerpt=context_excerpt,
                mda_excerpt=mda_excerpt,
                risk_factors_excerpt=cleaned_risk_factors_excerpt,
                filing_language_snippets=filing_language_snippets,
            )

        return _parse_filing_analysis(raw_json)

    except Exception as exc:
        logger.warning("Agent 2 failed for %s: %s. Using fallback.", company_name, exc)
        return _build_fallback_analysis(
            company_name=company_name,
            company_intelligence=company_intelligence,
            context_excerpt=context_excerpt,
            mda_excerpt=mda_excerpt,
            risk_factors_excerpt=cleaned_risk_factors_excerpt,
            filing_language_snippets=filing_language_snippets,
        )


def _clean_risk_excerpt_for_prompt(text: str) -> str:
    """Strip obvious filing-structure debris before risk prompting/extraction."""
    if not text:
        return ""
    cleaned_lines: List[str] = []
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if is_filing_structure_line(stripped):
            continue
        if re.match(
            r"^(?:This\s+.{0,30}\s+contains\s+forward[- ]looking|"
            r"These\s+forward[- ]looking\s+statements|"
            r"Safe\s+Harbor)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines).strip()


def _split_fallback_context_sentences(text: str) -> List[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(text or "").strip())
        if sentence.strip()
    ]


def _fallback_anchor_matches(term: str, keywords: List[str]) -> bool:
    normalized = _normalize_phrase_key(term)
    if not normalized:
        return False
    return any(
        keyword and keyword in normalized for keyword in [_normalize_phrase_key(item) for item in keywords]
    )


def _select_company_anchor(
    company_terms: List[str],
    *,
    preferred_keywords: List[str],
    fallback_terms: Optional[List[str]] = None,
    fallback_index: int = 0,
) -> str:
    normalized_keywords = [
        _normalize_phrase_key(keyword)
        for keyword in list(preferred_keywords or [])
        if _normalize_phrase_key(keyword)
    ]
    ranked_terms = [str(term).strip() for term in list(company_terms or []) if str(term).strip()]
    fallback_pool = [str(term).strip() for term in list(fallback_terms or []) if str(term).strip()]

    def _score(term: str) -> Tuple[int, int, int]:
        normalized = _normalize_phrase_key(term)
        keyword_score = 0
        for idx, keyword in enumerate(normalized_keywords):
            if keyword and keyword in normalized:
                keyword_score = max(keyword_score, len(normalized_keywords) - idx)
        token_count = max(1, len(normalized.split()))
        if token_count == 1:
            specificity = 3
        elif token_count == 2:
            specificity = 4
        elif token_count == 3:
            specificity = 3
        else:
            specificity = 1
        upper_bonus = 1 if re.search(r"[A-Z0-9]", term) else 0
        return keyword_score, specificity, upper_bonus

    scored_matches = [
        (_score(term), term)
        for term in ranked_terms
        if any(keyword and keyword in _normalize_phrase_key(term) for keyword in normalized_keywords)
    ]
    if scored_matches:
        scored_matches.sort(key=lambda item: (-item[0][0], -item[0][1], -item[0][2], item[1].lower()))
        return str(scored_matches[0][1]).strip()

    if ranked_terms:
        safe_index = max(0, min(int(fallback_index or 0), len(ranked_terms) - 1))
        return ranked_terms[safe_index]

    if fallback_pool:
        safe_index = max(0, min(int(fallback_index or 0), len(fallback_pool) - 1))
        return fallback_pool[safe_index]

    return ""


def _fallback_expectation_template(
    *,
    archetype: str,
    primary: str,
    secondary: str,
) -> str:
    if archetype == "cloud_software":
        return f"Management expects {primary} to keep deepening while execution around {secondary} stays disciplined over the next 1-2 periods."
    if archetype == "semicap_hardware":
        return f"Management expects {primary} conversion to improve as {secondary} stays aligned with customer ramps over the next several quarters."
    if archetype == "industrial_manufacturing":
        return f"Management expects {primary} and {secondary} to stay constructive over the next year."
    if archetype == "retail_consumer":
        return f"Management expects {primary} to stay healthy while {secondary} remains disciplined this year."
    if archetype == "payments_marketplaces":
        return f"Management expects {primary} to stay healthy while pressure around {secondary} remains manageable this year."
    if archetype == "bank":
        return f"Management expects {primary} to stay supportive while pressure around {secondary} remains contained over the next 1-2 periods."
    if archetype == "insurance_asset_manager":
        return f"Management expects {primary} to remain manageable while {secondary} stabilizes over the next year."
    if archetype == "pharma_biotech_medtech":
        return f"Management expects {primary} to improve while {secondary} stays on schedule over the next several quarters."
    if archetype == "energy_materials_utilities":
        return f"Management expects {primary} to remain supportive while {secondary} stays on plan over the next year."
    if archetype == "telecom_media_ads":
        return f"Management expects {primary} to hold up while {secondary} remains disciplined over the next 1-2 periods."
    return f"Management expects {primary} to keep supporting the operating case while {secondary} stays disciplined over the next 1-2 periods."


def _clean_fallback_term_candidate(
    candidate: str,
    *,
    company_name: str,
) -> str:
    value = " ".join(str(candidate or "").split()).strip(" ,.;:()[]{}\"'“”")
    if not value:
        return ""
    while True:
        updated = _FALLBACK_TERM_LEADING_NOISE_RE.sub("", value).strip(" ,.;:()[]{}\"'“”")
        if updated == value:
            break
        value = updated
    while True:
        updated = _FALLBACK_TERM_TRAILING_STATUS_RE.sub("", value).strip(" ,.;:()[]{}\"'“”")
        if updated == value:
            break
        value = updated
    value = re.sub(r"\s*/\s*", " / ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    normalized = _normalize_phrase_key(value)
    if not normalized:
        return ""
    if normalized == _normalize_phrase_key(company_name):
        return ""
    if normalized in _GENERIC_COMPANY_TERM_STOPWORDS:
        return ""
    tokens = normalized.split()
    if any(token in {"and", "or", "versus", "vs", "while"} for token in tokens):
        return ""
    if len(tokens) == 1 and len(tokens[0]) < 4 and not re.search(r"[A-Z0-9]", value):
        return ""
    if (
        len(tokens) == 1
        and tokens[0] in _FALLBACK_GENERIC_SINGLE_TOKENS
        and not re.fullmatch(r"[A-Z]{2,6}", value)
    ):
        return ""
    if len(tokens) > 5:
        return ""
    if all(token in _GENERIC_COMPANY_TERM_STOPWORDS for token in tokens):
        return ""
    return value


def _extract_fallback_company_terms(
    *,
    company_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    context_text: str,
) -> List[str]:
    scores: Dict[str, float] = {}
    pretty: Dict[str, str] = {}

    def _add(term: str, weight: float) -> None:
        cleaned = _clean_fallback_term_candidate(term, company_name=company_name)
        if not cleaned:
            return
        key = _normalize_phrase_key(cleaned)
        if not key:
            return
        scores[key] = float(scores.get(key, 0.0) + weight)
        pretty.setdefault(key, cleaned)

    archetype = _normalize_business_archetype(
        getattr(company_intelligence, "business_archetype", "") or "diversified_other"
    )
    config = _archetype_config(archetype)
    combined_context = " ".join((context_text or "").split())
    lowered = combined_context.lower()

    for kpi in list(company_intelligence.primary_kpis or []):
        _add(kpi.name, 4.5)
        for search_term in list(kpi.filing_search_terms or []):
            _add(str(search_term), 3.0)
    for part in (
        company_intelligence.business_identity,
        company_intelligence.competitive_moat,
        company_intelligence.competitive_dynamics,
    ):
        for token in re.findall(r"\b[A-Z]{2,6}(?:/[A-Z]{2,6})?\b", part or ""):
            _add(token, 2.5)
    for focus_area in list(company_intelligence.investor_focus_areas or []):
        for phrase in re.split(r"[,;/]", str(focus_area or "")):
            _add(phrase, 1.5)
    for default_term in tuple(config.get("default_terms") or ()):
        if re.search(rf"\b{re.escape(str(default_term).lower())}\b", lowered):
            _add(str(default_term), 3.5)
    for sentence in _split_fallback_context_sentences(combined_context):
        for quoted in re.findall(r"[“\"]([^“”\"\n]{6,160})[”\"]", sentence):
            _add(quoted, 1.8)
    for match in re.finditer(
        r"\b(?:[A-Z][A-Za-z0-9&/-]+(?:\s+[A-Z][A-Za-z0-9&/-]+){0,3}|[A-Z]{2,6}(?:/[A-Z]{2,6})?)\b",
        combined_context,
    ):
        _add(match.group(0), 3.0)
    for match in re.finditer(
        r"\b[a-z][a-z0-9&/-]{2,}(?:\s+[a-z][a-z0-9&/-]{2,}){0,2}\b",
        lowered,
    ):
        phrase = match.group(0)
        if phrase in _GENERIC_COMPANY_TERM_STOPWORDS:
            continue
        if any(
            str(default_term).lower() in phrase
            for default_term in tuple(config.get("default_terms") or ())
        ):
            _add(phrase, 2.0)

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], -len(item[0]), pretty.get(item[0], item[0]).lower()),
    )
    terms = [pretty[key] for key, _score in ranked]
    if len(terms) < 6:
        for fallback_term in tuple(config.get("default_terms") or ()):
            _add(str(fallback_term), 1.0)
        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], -len(item[0]), pretty.get(item[0], item[0]).lower()),
        )
        terms = [pretty[key] for key, _score in ranked]
    cleaned_terms = []
    for term in terms:
        cleaned = _clean_fallback_term_candidate(term, company_name=company_name)
        if cleaned:
            cleaned_terms.append(cleaned)
    return _dedupe_ordered_strings(cleaned_terms, limit=12)


def _fallback_topic_from_sentence(
    sentence: str,
    *,
    company_terms: List[str],
    archetype: str,
) -> str:
    lowered = str(sentence or "").lower()
    for term in company_terms:
        if str(term).lower() in lowered:
            return str(term)
    for term in tuple(_archetype_config(archetype).get("default_terms") or ()):
        if str(term).lower() in lowered:
            return str(term)
    return "management outlook"


def _fallback_timeframe_from_sentence(sentence: str) -> str:
    match = re.search(
        r"\b(next\s+(?:quarter|two quarters|year|half|period)|this\s+(?:year|quarter)|over\s+the\s+next\s+\d+\s+\w+)\b",
        str(sentence or ""),
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return "next 1-2 periods"


def _fallback_quote_surrounding_context(
    text: str, *, start: int, end: int, radius: int = 160
) -> str:
    source = str(text or "")
    left = max(0, int(start) - int(radius))
    right = min(len(source), int(end) + int(radius))
    return source[left:right]


def _fallback_quote_source_line(text: str, *, start: int, end: int) -> str:
    source = str(text or "")
    line_start = source.rfind("\n", 0, int(start))
    line_start = 0 if line_start < 0 else line_start + 1
    line_end = source.find("\n", int(end))
    line_end = len(source) if line_end < 0 else line_end
    return source[line_start:line_end]


def _looks_high_signal_fallback_management_quote(
    quote: str, *, surrounding_context: str, source_line: str
) -> bool:
    cleaned_quote = " ".join(str(quote or "").split()).strip()
    if not cleaned_quote:
        return False
    if _FALLBACK_ACCOUNTING_DISCLOSURE_RE.search(cleaned_quote):
        return False
    if _FALLBACK_RISK_LEGAL_QUOTE_RE.search(cleaned_quote) and not (
        _FALLBACK_MANAGEMENT_ATTRIBUTION_RE.search(surrounding_context)
        or _FALLBACK_MANAGEMENT_ATTRIBUTION_RE.search(source_line)
    ):
        return False
    if not _FALLBACK_MANAGEMENT_SIGNAL_RE.search(cleaned_quote):
        return False
    if re.search(r"\b(?:we|our)\b", cleaned_quote, re.IGNORECASE):
        return True
    if _FALLBACK_MANAGEMENT_ATTRIBUTION_RE.search(surrounding_context):
        return True
    if _FALLBACK_MANAGEMENT_ATTRIBUTION_RE.search(source_line):
        return True
    return bool(re.match(r"\s*[-*•]\s*[\"“]", source_line))


def _extract_fallback_management_quotes(
    *,
    context_text: str,
    company_terms: List[str],
    archetype: str,
) -> List[ManagementQuote]:
    quotes: List[ManagementQuote] = []
    seen_quotes: Set[str] = set()
    for match in re.finditer(r"[“\"]([^“”\"\n]{8,160})[”\"]", context_text or ""):
        quote = " ".join((match.group(1) or "").split()).strip()
        if not quote or len(quote.split()) > 25:
            continue
        surrounding_context = _fallback_quote_surrounding_context(
            context_text,
            start=match.start(),
            end=match.end(),
        )
        source_line = _fallback_quote_source_line(
            context_text,
            start=match.start(),
            end=match.end(),
        )
        if not _looks_high_signal_fallback_management_quote(
            quote,
            surrounding_context=surrounding_context,
            source_line=source_line,
        ):
            continue
        normalized_quote = quote.rstrip(".!?").lower()
        if normalized_quote in seen_quotes:
            continue
        seen_quotes.add(normalized_quote)
        topic = _fallback_topic_from_sentence(
            quote, company_terms=company_terms, archetype=archetype
        )
        suggested = "Management Discussion & Analysis"
        lowered = quote.lower()
        if any(term in lowered for term in ("expect", "plan", "priorit", "will", "next")):
            suggested = "Management Discussion & Analysis"
        elif any(term in lowered for term in ("demand", "orders", "ship", "traffic", "volume", "renewal")):
            suggested = "Financial Performance"
        quotes.append(
            ManagementQuote(
                quote=quote,
                attribution="Management",
                topic=topic,
                suggested_section=suggested,
            )
        )
    return quotes[:4]


def _extract_fallback_management_expectations(
    *,
    context_text: str,
    company_terms: List[str],
    archetype: str,
) -> List[ManagementExpectation]:
    expectations: List[ManagementExpectation] = []
    for sentence in _split_fallback_context_sentences(context_text):
        if not re.search(
            r"\b(expect|expects|expected|plan|plans|planned|target|targets|guidance|outlook|will|next|ahead|continue|priorit)\b",
            sentence,
            re.IGNORECASE,
        ):
            continue
        expectations.append(
            ManagementExpectation(
                topic=_fallback_topic_from_sentence(
                    sentence, company_terms=company_terms, archetype=archetype
                ),
                expectation=sentence,
                timeframe=_fallback_timeframe_from_sentence(sentence),
                evidence=sentence,
            )
        )
        if len(expectations) >= 3:
            break
    if expectations:
        return expectations

    config = _archetype_config(archetype)
    primary = _select_company_anchor(
        company_terms,
        preferred_keywords=[str(term) for term in tuple(config.get("default_terms") or ())[:3]],
        fallback_terms=[str(term) for term in tuple(config.get("default_terms") or ())],
        fallback_index=0,
    ) or "the operating thread"
    secondary = _select_company_anchor(
        company_terms,
        preferred_keywords=[str(term) for term in tuple(config.get("default_terms") or ())[1:4]],
        fallback_terms=[str(term) for term in tuple(config.get("default_terms") or ())],
        fallback_index=1,
    ) or primary
    evidence_sentence = ""
    for sentence in _split_fallback_context_sentences(context_text):
        lowered = sentence.lower()
        if "management" in lowered or re.search(r"[“\"]", sentence):
            evidence_sentence = sentence
            break
    if not evidence_sentence:
        evidence_sentence = next(
            iter(_split_fallback_context_sentences(context_text)),
            "",
        )
    if evidence_sentence:
        expectations.append(
            ManagementExpectation(
                topic=primary,
                expectation=_fallback_expectation_template(
                    archetype=archetype,
                    primary=primary,
                    secondary=secondary,
                ),
                timeframe="next 1-2 periods",
                evidence=evidence_sentence,
            )
        )
    return expectations


def _extract_fallback_promise_scorecard_items(
    *,
    context_text: str,
    company_terms: List[str],
    archetype: str,
    expectations: List[ManagementExpectation],
) -> List[PromiseScorecardItem]:
    items: List[PromiseScorecardItem] = []
    for sentence in _split_fallback_context_sentences(context_text):
        lowered = sentence.lower()
        status = ""
        if re.search(r"\b(delivered|improved|ahead of|stronger|accelerated|expanded)\b", lowered):
            status = "delivered"
        elif re.search(
            r"\b(on track|continue|continuing|remain focused|remains focused|maintain|held|holding|remained stable|stayed contained|stayed healthy|remained healthy)\b",
            lowered,
        ):
            status = "on_track"
        elif re.search(r"\b(missed|delay|slower|weaker|pressure)\b", lowered):
            status = "missed"
        elif re.search(r"\b(expect|plan|target|will|next)\b", lowered):
            status = "new_commitment"
        if not status:
            continue
        topic = _fallback_topic_from_sentence(
            sentence, company_terms=company_terms, archetype=archetype
        )
        items.append(
            PromiseScorecardItem(
                commitment=topic,
                status=status,
                assessment=sentence,
                evidence=sentence,
            )
        )
        if len(items) >= 3:
            break
    if not items and expectations:
        first = expectations[0]
        items.append(
            PromiseScorecardItem(
                commitment=first.topic or "management commitment",
                status="new_commitment",
                assessment=first.expectation,
                evidence=first.evidence,
            )
        )
    return items


def _extract_fallback_period_specific_insights(
    *,
    context_text: str,
    company_terms: List[str],
    archetype: str,
) -> List[str]:
    insights: List[str] = []
    default_terms = [str(term) for term in tuple(_archetype_config(archetype).get("default_terms") or ())]
    for sentence in _split_fallback_context_sentences(context_text):
        lowered = sentence.lower()
        if any(term.lower() in lowered for term in company_terms[:6]) or any(
            term.lower() in lowered for term in default_terms[:4]
        ):
            insights.append(sentence)
        if len(insights) >= 4:
            break
    return _dedupe_ordered_strings(insights, limit=4)


def _build_fallback_management_strategy_summary(
    *,
    company_intelligence: CompanyIntelligenceProfile,
    company_terms: List[str],
    expectations: List[ManagementExpectation],
    promise_items: List[PromiseScorecardItem],
) -> str:
    clauses: List[str] = []
    if company_intelligence.business_identity:
        clauses.append(company_intelligence.business_identity)
    if expectations:
        clauses.append(expectations[0].expectation)
    if promise_items:
        clauses.append(promise_items[0].assessment)
    if not clauses and company_terms:
        clauses.append(
            f"Management is still trying to convert {company_terms[0]} into a durable source of earnings and cash."
        )
    return " ".join(clauses[:2]).strip()


def _build_fallback_company_risks(
    *,
    company_terms: List[str],
    source_texts: Dict[str, str],
) -> List[CompanyRisk]:
    candidates = build_risk_evidence_candidates(
        source_texts,
        company_terms=company_terms,
        limit=3,
    )
    risks: List[CompanyRisk] = []
    for candidate in candidates:
        risks.append(
            CompanyRisk(
                risk_name=candidate.risk_name,
                mechanism=candidate.mechanism_seed or candidate.source_quote,
                early_warning=candidate.early_warning_seed or candidate.source_quote,
                evidence_from_filing=candidate_to_evidence_line(candidate),
                source_section=candidate.source_section,
                source_quote=candidate.source_quote,
            )
        )
    return risks


def _build_fallback_central_tension(
    *,
    company_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    company_terms: List[str],
) -> str:
    archetype = _normalize_business_archetype(
        getattr(company_intelligence, "business_archetype", "") or "diversified_other"
    )
    config = _archetype_config(archetype)
    fallback_terms = [str(term) for term in tuple(config.get("default_terms") or ())[:4]]
    anchors = list(company_terms[:4]) or fallback_terms
    if archetype == "cloud_software":
        first = _select_company_anchor(
            company_terms,
            preferred_keywords=["renewal", "ai", "rpo", "attach", "enterprise"],
            fallback_terms=fallback_terms,
            fallback_index=0,
        ) or (anchors[0] if anchors else "recurring demand")
        second = _select_company_anchor(
            company_terms,
            preferred_keywords=["pricing", "attach", "seat expansion", "largest account"],
            fallback_terms=fallback_terms,
            fallback_index=1,
        ) or (anchors[1] if len(anchors) > 1 else "monetization")
    elif archetype == "semicap_hardware":
        first = _select_company_anchor(
            company_terms,
            preferred_keywords=["euv", "backlog", "shipment"],
            fallback_terms=fallback_terms,
            fallback_index=0,
        ) or (anchors[0] if anchors else "backlog")
        second = _select_company_anchor(
            company_terms,
            preferred_keywords=["installed base", "node transition", "fab"],
            fallback_terms=fallback_terms,
            fallback_index=1,
        ) or (anchors[1] if len(anchors) > 1 else "customer timing")
    elif archetype == "bank":
        first = _select_company_anchor(
            company_terms,
            preferred_keywords=["deposits", "deposit mix", "net interest margin"],
            fallback_terms=fallback_terms,
            fallback_index=0,
        ) or (anchors[0] if anchors else "deposits")
        second = _select_company_anchor(
            company_terms,
            preferred_keywords=["credit quality", "charge-offs", "cet1"],
            fallback_terms=fallback_terms,
            fallback_index=1,
        ) or (anchors[1] if len(anchors) > 1 else "credit discipline")
    elif archetype == "payments_marketplaces":
        first = _select_company_anchor(
            company_terms,
            preferred_keywords=["payment volume", "take rate", "merchant mix"],
            fallback_terms=fallback_terms,
            fallback_index=0,
        ) or (anchors[0] if anchors else "payment volume")
        second = _select_company_anchor(
            company_terms,
            preferred_keywords=["chargebacks", "funding costs", "merchant mix"],
            fallback_terms=fallback_terms,
            fallback_index=1,
        ) or (anchors[1] if len(anchors) > 1 else "merchant quality")
    else:
        first = anchors[0] if anchors else "demand"
        second = anchors[1] if len(anchors) > 1 else "execution"
    if archetype == "cloud_software":
        return f"The key question is whether {company_name} can turn {first} and {second} into durable recurring economics without overspending ahead of monetization."
    if archetype == "semicap_hardware":
        return f"The key question is whether {company_name} can convert {first} into shipments and installed-base economics without getting caught by customer timing or fab-spend volatility."
    if archetype == "industrial_manufacturing":
        return f"The key question is whether {company_name} can convert {first} into margin and cash while keeping execution around {second} on schedule."
    if archetype == "retail_consumer":
        return f"The key question is whether {company_name} can keep {first} healthy without giving up merchandise economics through {second} or heavier promotions."
    if archetype == "payments_marketplaces":
        return f"The key question is whether {company_name} can scale {first} without weakening take rate, loss discipline, or funding quality through {second}."
    if archetype == "bank":
        return f"The key question is whether {company_name} can keep {first} supporting earnings while credit and funding discipline around {second} remain intact."
    if archetype == "insurance_asset_manager":
        return f"The key question is whether {company_name} can hold underwriting and fee economics together while {first} and {second} stay supportive."
    if archetype == "pharma_biotech_medtech":
        return f"The key question is whether {company_name} can turn {first} into durable growth before reimbursement, regulatory timing, or pipeline execution around {second} gets in the way."
    if archetype == "energy_materials_utilities":
        return f"The key question is whether {company_name} can keep {first} translating into cash returns while execution around {second} stays disciplined."
    if archetype == "telecom_media_ads":
        return f"The key question is whether {company_name} can keep monetizing {first} without letting churn, content spend, or network intensity around {second} erode returns."
    return f"The key question is whether {company_name} can convert {first} into durable economics while management keeps execution around {second} disciplined."


def _build_fallback_evidence_map(
    *,
    central_tension: str,
    strategy_summary: str,
    company_terms: List[str],
    expectations: List[ManagementExpectation],
    promise_items: List[PromiseScorecardItem],
    risks: List[CompanyRisk],
    period_specific_insights: List[str],
) -> Dict[str, List[str]]:
    exec_claims = [central_tension]
    if strategy_summary:
        exec_claims.append(strategy_summary)
    if expectations:
        exec_claims.append(expectations[0].expectation)

    perf_claims = [
        f"{company_terms[0]} is the clearest operating lens on whether the period improved through real economics rather than timing."
        if company_terms
        else "The operating model still has to prove the period's improvement is durable."
    ]
    perf_claims.extend(period_specific_insights[:2])

    mdna_claims = [strategy_summary] if strategy_summary else []
    mdna_claims.extend(item.expectation for item in expectations[:2])
    mdna_claims.extend(item.assessment for item in promise_items[:2])

    risk_claims = [
        _risk_source_evidence_line(item)
        for item in risks[:3]
        if _risk_source_evidence_line(item)
    ]
    closing_claims = []
    if promise_items:
        closing_claims.append(promise_items[0].assessment)
    if expectations:
        closing_claims.append(expectations[0].expectation)
    if central_tension:
        closing_claims.append(central_tension)

    return {
        "Executive Summary": _dedupe_ordered_strings(exec_claims, limit=3),
        "Financial Performance": _dedupe_ordered_strings(perf_claims, limit=3),
        "Management Discussion & Analysis": _dedupe_ordered_strings(mdna_claims, limit=4),
        "Risk Factors": _dedupe_ordered_strings(risk_claims, limit=3),
        "Closing Takeaway": _dedupe_ordered_strings(closing_claims, limit=3),
    }


def _build_fallback_analysis(
    company_name: str,
    *,
    company_intelligence: CompanyIntelligenceProfile,
    context_excerpt: str = "",
    mda_excerpt: str = "",
    risk_factors_excerpt: str = "",
    filing_language_snippets: str = "",
) -> FilingAnalysis:
    """Build a filing-aware cross-company fallback analysis when Agent 2 fails."""
    combined_context = "\n".join(
        part
        for part in (
            filing_language_snippets,
            mda_excerpt,
            risk_factors_excerpt,
            context_excerpt,
        )
        if str(part or "").strip()
    )
    business_archetype = _normalize_business_archetype(
        getattr(company_intelligence, "business_archetype", "") or "diversified_other"
    )
    company_terms = _extract_fallback_company_terms(
        company_name=company_name,
        company_intelligence=company_intelligence,
        context_text=combined_context,
    )
    management_quotes = _extract_fallback_management_quotes(
        context_text=combined_context,
        company_terms=company_terms,
        archetype=business_archetype,
    )
    expectations = _extract_fallback_management_expectations(
        context_text=combined_context,
        company_terms=company_terms,
        archetype=business_archetype,
    )
    promise_items = _extract_fallback_promise_scorecard_items(
        context_text=combined_context,
        company_terms=company_terms,
        archetype=business_archetype,
        expectations=expectations,
    )
    period_specific_insights = _extract_fallback_period_specific_insights(
        context_text=combined_context,
        company_terms=company_terms,
        archetype=business_archetype,
    )
    strategy_summary = _build_fallback_management_strategy_summary(
        company_intelligence=company_intelligence,
        company_terms=company_terms,
        expectations=expectations,
        promise_items=promise_items,
    )
    central_tension = _build_fallback_central_tension(
        company_name=company_name,
        company_intelligence=company_intelligence,
        company_terms=company_terms,
    )
    fallback_risks = _build_fallback_company_risks(
        company_terms=company_terms,
        source_texts={
            "Risk Factors": risk_factors_excerpt,
            "Management Discussion & Analysis": mda_excerpt,
            "Filing Language Snippets": filing_language_snippets,
            "Context Excerpt": context_excerpt,
        },
    )
    evidence_map = _build_fallback_evidence_map(
        central_tension=central_tension,
        strategy_summary=strategy_summary,
        company_terms=company_terms,
        expectations=expectations,
        promise_items=promise_items,
        risks=fallback_risks,
        period_specific_insights=period_specific_insights,
    )
    strategic_bets = [item.topic for item in expectations[:2] if item.topic]
    return FilingAnalysis(
        central_tension=central_tension,
        tension_evidence=" ".join(period_specific_insights[:2]).strip(),
        kpi_findings=[],
        period_specific_insights=period_specific_insights,
        management_quotes=management_quotes,
        management_strategy_summary=strategy_summary or company_intelligence.business_identity or "",
        company_specific_risks=fallback_risks,
        evidence_map=evidence_map,
        company_terms=company_terms,
        management_expectations=expectations,
        promise_scorecard_items=promise_items,
        management_strategic_bets=_dedupe_ordered_strings(strategic_bets, limit=3),
        forward_guidance_summary=" ".join(item.expectation for item in expectations[:2]).strip(),
        promise_scorecard=" ".join(item.assessment for item in promise_items[:2]).strip(),
        decisive_watch_metrics=_dedupe_ordered_strings(
            [
                item.topic
                for item in expectations[:2]
                if str(getattr(item, "topic", "") or "").strip()
            ]
            + list(period_specific_insights[:1] or []),
            limit=3,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 3 — Summary Composition Agent
# ═══════════════════════════════════════════════════════════════════════════


def _build_agent_3_system_prompt(
    *,
    company_name: str,
    target_length: Optional[int],
    persona_name: Optional[str],
    persona_requested: bool,
) -> str:
    """Build the system prompt for Agent 3."""
    identity = (
        f"You are a senior equity research analyst writing an institutional "
        f"investment memo for portfolio managers."
    )
    if persona_name:
        identity += (
            f" You are filtering the analysis through the priorities of "
            f"{persona_name}, but you must NOT mimic catchphrases or produce "
            f"self-referential manifesto language."
        )

    quote_policy = _quote_policy_for_target_length(target_length)
    word_count_block = ""
    if target_length:
        tolerance = total_word_tolerance_words(target_length)
        lower = max(1, int(target_length) - int(tolerance))
        upper = int(target_length) + int(tolerance)
        word_count_block = (
            f"\n\nCRITICAL WORD COUNT RULES:\n"
            f"- Total word count MUST be between {lower} and {upper} words.\n"
            f"- Section budgets are BODY-word budgets only; headings do not count.\n"
            f"- Each section has a specific budget. Follow it without front-loading early sections.\n"
            f"- If exact target match would force repetition or filler, stay inside the allowed band and preserve quality.\n"
            f"- Never pad. Under-target output must add new analysis or compress expectations."
        )

    return (
        f"{identity}\n\n"
        f"You are writing the final investment memo. You have been given:\n"
        f"1. A company intelligence profile (what makes this company unique)\n"
        f"2. A filing analysis (the specific insights from this filing)\n"
        f"3. Exact section-by-section word budgets\n"
        f"{word_count_block}\n\n"
        f"CRITICAL CONTENT RULES:\n"
        f"- Use the COMPANY-SPECIFIC KPIs as your PRIMARY metrics, not generic financials.\n"
        f"- If the filing analysis provides a KPI finding, USE IT instead of generic Revenue/EPS.\n"
        f"- The central tension from the filing analysis IS your thesis — do not invent a different one.\n"
        f"- Management quotes are pre-verified from the filing — use them verbatim.\n"
        f"- Period-specific insights explain where the business stands now, what changed this period, and what management thinks happens next. Use them.\n"
        f"- Follow the evidence map: put the right data in the right section.\n"
        f"- Direct quotes are optional and quality-gated. Use at most {quote_policy['max_total']} total direct quotes, "
        f"and only when they materially sharpen strategy, outlook, or the next operating checkpoint.\n"
        f"- If a direct quote feels legal, tax, accounting, governance, or otherwise low-signal, skip it and use attributed paraphrase instead.\n"
        f"- RISK FACTORS must quote or clearly attribute the company's own risk disclosure language. Integrate it naturally, for example: "
        f"'As the filing notes, \"[quote from risk factors],\" which highlights [mechanism].' "
        f"BANNED: 'macroeconomic uncertainty', 'competitive pressure' without naming the SPECIFIC factor.\n"
        f"- NUMBERS support the argument, not replace it. Lead every paragraph with a business insight.\n"
        f"- COMPANY UNIQUENESS TEST: Every sentence must pass this test — if you could swap "
        f"in any other company's name and the sentence would still make sense, it is too "
        f"generic. Name the specific product, segment, geography, customer, or competitive "
        f"dynamic that makes each claim unique to {company_name}.\n"
        f"- MD&A must lead with management's stated strategy BEFORE any metrics, and include "
        f"management's voice (direct quotes or clear attribution) throughout."
    )


def _format_kpi_findings(analysis: FilingAnalysis) -> str:
    """Format KPI findings for Agent 3's prompt."""
    if not analysis.kpi_findings:
        return "(No company-specific KPI findings — use supplementary financial data)"
    lines = []
    for kf in analysis.kpi_findings:
        line = f"- {kf.kpi_name}: {kf.current_value}"
        if kf.prior_value:
            line += f" (prior: {kf.prior_value}"
            if kf.change:
                line += f", change: {kf.change}"
            line += ")"
        if kf.insight:
            line += f"\n  Insight: {kf.insight}"
        lines.append(line)
    return "\n".join(lines)


def _format_management_quotes(analysis: FilingAnalysis) -> str:
    """Format management quotes for Agent 3's prompt."""
    if not analysis.management_quotes:
        return "(No pre-verified quotes — paraphrase management with attribution)"
    lines = []
    for q in analysis.management_quotes:
        lines.append(
            f'- "{q.quote}" ({q.attribution}, re: {q.topic}) '
            f"→ best for: {q.suggested_section}"
        )
    return "\n".join(lines)


def _format_risks(analysis: FilingAnalysis) -> str:
    """Format company-specific risks for Agent 3's prompt."""
    if not analysis.company_specific_risks:
        return "(Use standard risk analysis from filing)"
    lines = []
    for r in analysis.company_specific_risks:
        lines.append(
            f"- {r.risk_name} [{r.source_section or 'Risk Factors'}]\n"
            f"  Source quote: {r.source_quote or r.evidence_from_filing}\n"
            f"  Core exposure: {r.mechanism}\n"
            f"  Investor watchpoint: {r.early_warning}"
        )
    return "\n".join(lines)


def _build_source_backed_risk_section_body(
    analysis: FilingAnalysis,
    *,
    limit: int = 3,
) -> str:
    accepted_risks = _accepted_company_risks(analysis)
    if not accepted_risks:
        return ""
    lines: List[str] = []
    for risk in accepted_risks[: max(0, int(limit))]:
        source_quote = " ".join(
            str(risk.source_quote or risk.evidence_from_filing or "").split()
        ).strip()
        if not source_quote or is_fragment_quote(source_quote):
            continue
        if is_filing_fragment_risk_name(str(risk.risk_name or "")):
            continue
        mechanism = " ".join(str(risk.mechanism or "").split()).strip()
        watch_text = " ".join(str(risk.early_warning or "").split()).strip()
        watch_text = re.sub(
            r"^(?:an?\s+)?early[- ]warning signal\s+(?:is|would be)\s+",
            "Investors should watch ",
            watch_text,
            flags=re.IGNORECASE,
        )
        watch_text = re.sub(
            r"^early[- ]warning signal:\s*",
            "Investors should watch ",
            watch_text,
            flags=re.IGNORECASE,
        )
        if watch_text and not re.match(r"^(investors should watch|watch)\b", watch_text, re.IGNORECASE):
            watch_text = f"Investors should watch {watch_text[0].lower()}{watch_text[1:]}"

        body = f'As the filing notes, "{source_quote},"'
        if mechanism:
            lowered_mechanism = mechanism[0].lower() + mechanism[1:] if len(mechanism) > 1 else mechanism.lower()
            connector = " and " if re.match(r"^(if|when|unless)\b", lowered_mechanism, re.IGNORECASE) else " which underscores how "
            body += f"{connector}{lowered_mechanism.rstrip('.')}"
        if not body.endswith("."):
            body += "."
        if watch_text:
            body += f" {watch_text.rstrip('.') }."
        lines.append(f"{risk.risk_name}: {body}")
    return "\n\n".join(lines)


def _format_company_terms(analysis: FilingAnalysis, *, limit: int = 12) -> str:
    """Format filing-specific company nouns/phrases for prompt grounding."""
    terms = []
    seen: set[str] = set()
    for raw in analysis.company_terms or []:
        term = str(raw or "").strip()
        if not term:
            continue
        canon = re.sub(r"\s+", " ", term.lower())
        if canon in seen:
            continue
        seen.add(canon)
        terms.append(term)
        if len(terms) >= max(1, int(limit)):
            break
    if not terms:
        return "(No company-specific filing terms extracted.)"
    return "\n".join(f"- {term}" for term in terms)


def _format_management_expectations(
    analysis: FilingAnalysis,
    *,
    limit: int = 4,
) -> str:
    """Format forward-looking management expectations for section prompts."""
    if not analysis.management_expectations:
        return "(No explicit management expectations extracted.)"
    lines = []
    for item in analysis.management_expectations[: max(1, int(limit))]:
        line = f"- {item.topic}: {item.expectation}"
        if item.timeframe:
            line += f" (timeframe: {item.timeframe})"
        if item.evidence:
            line += f"\n  Evidence: {item.evidence}"
        lines.append(line)
    return "\n".join(lines)


def _format_promise_scorecard_items(
    analysis: FilingAnalysis,
    *,
    limit: int = 4,
) -> str:
    """Format promise-vs-delivery items for MD&A and Closing Takeaway."""
    if not analysis.promise_scorecard_items:
        return "(No promise-vs-delivery items extracted.)"
    lines = []
    for item in analysis.promise_scorecard_items[: max(1, int(limit))]:
        status = str(item.status or "").strip() or "unknown"
        line = f"- {item.commitment} [{status}]"
        if item.assessment:
            line += f": {item.assessment}"
        if item.evidence:
            line += f"\n  Evidence: {item.evidence}"
        lines.append(line)
    return "\n".join(lines)


def _format_evidence_map(analysis: FilingAnalysis) -> str:
    """Format the evidence map for Agent 3's prompt."""
    if not analysis.evidence_map:
        return "(No evidence map — use filing data for each section)"
    lines = []
    for section, items in analysis.evidence_map.items():
        items_text = "\n".join(f"  - {item}" for item in items)
        lines.append(f"{section}:\n{items_text}")
    return "\n".join(lines)


def _format_section_memory(memory: SectionMemory) -> str:
    blocks: List[str] = []
    if memory.used_claims:
        blocks.append(
            "Claims already used:\n"
            + "\n".join(f"- {claim}" for claim in memory.used_claims[:5])
        )
    if memory.used_theme_keys:
        blocks.append(
            "Themes already explained earlier:\n"
            + "\n".join(f"- {item}" for item in memory.used_theme_keys[:6])
        )
    if memory.used_anchor_metrics:
        blocks.append(
            "Anchor metrics already used as primary proof:\n"
            + "\n".join(f"- {item}" for item in memory.used_anchor_metrics[:6])
        )
    if memory.used_company_terms:
        blocks.append(
            "Company terms already foregrounded:\n"
            + "\n".join(f"- {item}" for item in memory.used_company_terms[:6])
        )
    if memory.used_management_topics:
        blocks.append(
            "Management topics already covered:\n"
            + "\n".join(f"- {item}" for item in memory.used_management_topics[:5])
        )
    if memory.used_promise_items:
        blocks.append(
            "Promise / credibility items already used:\n"
            + "\n".join(f"- {item}" for item in memory.used_promise_items[:4])
        )
    if not blocks:
        return "(No prior section memory yet.)"
    return "\n\n".join(blocks)


def _format_section_blueprint(
    blueprint: Optional[SectionBlueprint],
) -> Dict[str, str]:
    if blueprint is None:
        return {
            "job": "(No explicit section job.)",
            "question": "(No explicit section question.)",
            "primary_evidence": "(No explicit primary evidence.)",
            "secondary_evidence": "(No explicit secondary evidence.)",
            "banned_overlap": "(No explicit overlap guardrails.)",
            "subtle_handoff": "(No explicit handoff guidance.)",
        }

    return {
        "job": blueprint.section_job or "(No explicit section job.)",
        "question": blueprint.section_question or "(No explicit section question.)",
        "primary_evidence": "\n".join(
            f"- {item}" for item in (blueprint.primary_evidence or [])
        )
        or "(No explicit primary evidence.)",
        "secondary_evidence": "\n".join(
            f"- {item}" for item in (blueprint.secondary_evidence or [])
        )
        or "(No explicit secondary evidence.)",
        "banned_overlap": "\n".join(
            f"- {item}" for item in (blueprint.banned_overlap or [])
        )
        or "(No explicit overlap guardrails.)",
        "subtle_handoff": blueprint.subtle_handoff
        or "(No explicit handoff guidance.)",
    }


def _format_period_insights(analysis: FilingAnalysis) -> str:
    """Format period-specific insights for Agent 3's prompt."""
    if not analysis.period_specific_insights:
        return "(No period-specific insights identified)"
    return "\n".join(
        f"- {insight}" for insight in analysis.period_specific_insights
    )


def _quote_policy_for_target_length(target_length: Optional[int]) -> Dict[str, int]:
    """Budget-aware quote contract for sectioned outputs."""
    target = int(target_length or 0)
    if target <= 0:
        return {"min_total": 0, "max_total": 3, "exec_min": 0, "mdna_min": 0}
    if target < 400:
        return {"min_total": 0, "max_total": 1, "exec_min": 0, "mdna_min": 0}
    if target < 1200:
        return {"min_total": 0, "max_total": 2, "exec_min": 0, "mdna_min": 0}
    return {"min_total": 0, "max_total": 3, "exec_min": 0, "mdna_min": 0}


def _section_kpi_limit(section_name: str) -> int:
    if section_name == "Key Metrics":
        return 4
    if section_name in {
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Financial Health Rating",
    }:
        return 3
    if section_name == "Closing Takeaway":
        return 2
    return 0


def _section_kpi_findings(
    analysis: FilingAnalysis,
    section_name: str,
) -> List[KPIFinding]:
    limit = _section_kpi_limit(section_name)
    if limit <= 0:
        return []
    findings = list(analysis.kpi_findings or [])
    if section_name == "Financial Performance":
        findings.sort(
            key=lambda item: (
                0 if item.change else 1,
                0 if item.prior_value else 1,
                0 if item.insight else 1,
            )
        )
    return findings[:limit]


def _format_section_kpi_findings(
    analysis: FilingAnalysis,
    section_name: str,
) -> str:
    findings = _section_kpi_findings(analysis, section_name)
    if not findings:
        return "(No company-specific KPI findings selected for this section.)"
    lines: List[str] = []
    for finding in findings:
        line = f"- {finding.kpi_name}: {finding.current_value}"
        if finding.prior_value:
            line += f" (prior: {finding.prior_value}"
            if finding.change:
                line += f", change: {finding.change}"
            line += ")"
        if finding.insight:
            line += f"\n  Why it matters now: {finding.insight}"
        lines.append(line)
    return "\n".join(lines)


def _format_section_period_insights(
    analysis: FilingAnalysis,
    section_name: str,
) -> str:
    insights = list(analysis.period_specific_insights or [])
    if not insights:
        return "(No filing-period-specific insights identified.)"
    if section_name in {"Executive Summary", "Management Discussion & Analysis"}:
        return "\n".join(f"- {item}" for item in insights[:4])
    if section_name in {"Financial Performance", "Closing Takeaway"}:
        return "\n".join(f"- {item}" for item in insights[:3])
    return "\n".join(f"- {item}" for item in insights[:2])


def _quotes_for_section_with_fallback(
    analysis: FilingAnalysis, section_name: str
) -> List[ManagementQuote]:
    assigned = _quotes_for_section(analysis, section_name)
    if assigned:
        return assigned
    if section_name == "Risk Factors":
        risk_quotes: List[ManagementQuote] = []
        for risk in _accepted_company_risks(analysis):
            source_quote = " ".join(
                str(risk.source_quote or risk.evidence_from_filing or "").split()
            ).strip()
            if not source_quote:
                continue
            risk_quotes.append(
                ManagementQuote(
                    quote=source_quote,
                    attribution=str(risk.source_section or "Risk Factors"),
                    topic=str(risk.risk_name or "Risk Factors"),
                    suggested_section="Risk Factors",
                )
            )
        return risk_quotes
    fallback_sections = {
        "Executive Summary": ("Executive Summary", "Management Discussion & Analysis"),
        "Management Discussion & Analysis": (
            "Management Discussion & Analysis",
            "Executive Summary",
        ),
        "Closing Takeaway": (
            "Management Discussion & Analysis",
            "Executive Summary",
            "Closing Takeaway",
        ),
    }
    preferred = fallback_sections.get(section_name, ())
    if not preferred:
        return []
    for preferred_section in preferred:
        matches = [
            quote
            for quote in analysis.management_quotes
            if (quote.suggested_section or "").strip() == preferred_section
        ]
        if matches:
            return matches
    return list(analysis.management_quotes or [])


def _sanitize_metric_label(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(label or "").strip())
    cleaned = cleaned.strip(":|-")
    return cleaned


def _looks_numeric_metric_value(value: str) -> bool:
    cleaned = str(value or "").strip()
    if not cleaned:
        return False
    return bool(re.search(r"\d", cleaned))


def _company_kpi_metric_lines(
    analysis: FilingAnalysis,
    *,
    limit: int = 4,
) -> List[str]:
    lines: List[str] = []
    seen_labels: set[str] = set()
    for finding in analysis.kpi_findings[: max(0, int(limit))]:
        label = _sanitize_metric_label(finding.kpi_name)
        current_value = str(finding.current_value or "").strip()
        if not label or not _looks_numeric_metric_value(current_value):
            continue
        canon_label = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        if canon_label in seen_labels:
            continue
        seen_labels.add(canon_label)
        line = f"→ {label}: {current_value}"
        if finding.change:
            line += f" | {finding.change}"
        lines.append(line)
    return lines


def _metric_line_key(line: str) -> str:
    stripped = re.sub(r"^[^A-Za-z0-9]+", "", str(line or "").strip())
    label, sep, _rest = stripped.partition(":")
    candidate = label if sep else stripped
    return re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()


def _trim_key_metrics_lines_to_budget(
    lines: List[str],
    *,
    max_words: Optional[int],
) -> List[str]:
    budget = int(max_words or 0)
    if budget <= 0 or not lines:
        return lines
    if count_words("\n".join(lines)) <= budget:
        return lines

    kept: List[str] = []
    for line in lines:
        candidate = kept + [line]
        if kept and count_words("\n".join(candidate)) > budget:
            break
        kept = candidate

    while kept and count_words("\n".join(kept)) > budget:
        kept.pop()
    return kept or lines[:1]


def _build_metrics_highlights(
    analysis: FilingAnalysis,
    *,
    limit: int = 4,
) -> List[str]:
    highlights: List[str] = []
    seen: Set[str] = set()

    def _add(text: str) -> None:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        cleaned = cleaned.rstrip(".")
        if not cleaned:
            return
        key = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
        if not key or key in seen:
            return
        seen.add(key)
        highlights.append(cleaned + ".")

    contrastive_insight = next(
        (
            str(item or "").strip()
            for item in list(analysis.period_specific_insights or [])
            if re.search(
                r"\b(real shift|not just|rather than|instead of|no longer|self[- ]fund(?:ed|ing)|from\b.+\bto)\b",
                str(item or ""),
                re.IGNORECASE,
            )
        ),
        "",
    )
    if contrastive_insight:
        _add(contrastive_insight)
        if len(highlights) >= int(limit):
            return highlights[:limit]

    watch_metrics = list(analysis.decisive_watch_metrics or [])
    for watch_metric in watch_metrics:
        match = next(
            (
                finding
                for finding in list(analysis.kpi_findings or [])
                if str(finding.kpi_name or "").strip()
                and (
                    str(watch_metric or "").lower() in str(finding.kpi_name or "").lower()
                    or str(finding.kpi_name or "").lower() in str(watch_metric or "").lower()
                )
            ),
            None,
        )
        if match is not None:
            insight_text = str(match.insight or "").strip()
            change_text = str(match.change or "").strip()
            value_text = str(match.current_value or "").strip()
            if insight_text and change_text:
                sentence = f"{match.kpi_name} ({change_text}): {insight_text}"
            elif insight_text:
                sentence = f"{match.kpi_name}: {insight_text}"
            elif change_text and value_text:
                sentence = f"{match.kpi_name} at {value_text} ({change_text}) — watch for continuation"
            else:
                sentence = f"Watch {match.kpi_name}: {change_text or value_text or 'monitor for this filing period'}"
        else:
            expectation = next(
                (
                    item
                    for item in list(analysis.management_expectations or [])
                    if str(item.topic or "").strip()
                    and (
                        str(watch_metric or "").lower() in str(item.topic or "").lower()
                        or str(item.topic or "").lower() in str(watch_metric or "").lower()
                    )
                ),
                None,
            )
            if expectation is not None:
                sentence = f"Watch {expectation.topic}: {expectation.expectation}"
            else:
                sentence = f"Watch {watch_metric}: this is the clearest operating line to watch in the filing."
        _add(sentence)
        if len(highlights) >= int(limit):
            return highlights[:limit]

    for finding in list(analysis.kpi_findings or []):
        if len(highlights) >= int(limit):
            break
        insight = str(finding.insight or finding.change or finding.current_value or "").strip()
        if not insight:
            continue
        change_context = f" ({finding.change})" if finding.change else ""
        _add(f"{finding.kpi_name}{change_context}: {insight}")

    for expectation in list(analysis.management_expectations or []):
        if len(highlights) >= int(limit):
            break
        if not str(expectation.topic or "").strip() or not str(expectation.expectation or "").strip():
            continue
        _add(f"Management is explicitly watching {expectation.topic}: {expectation.expectation}")

    for insight in list(analysis.period_specific_insights or []):
        if len(highlights) >= int(limit):
            break
        _add(insight)

    if len(highlights) < int(limit):
        fallback_candidates = [
            str(analysis.central_tension or "").strip(),
            str(analysis.management_strategy_summary or "").strip(),
        ]
        for candidate in fallback_candidates:
            if len(highlights) >= int(limit):
                break
            if candidate:
                _add(candidate)

    return highlights[:limit]


def _fallback_metrics_intro_from_rows(
    metric_lines: Sequence[str],
    *,
    limit: int = 3,
) -> List[str]:
    labels: List[str] = []
    seen: Set[str] = set()
    for raw_line in metric_lines:
        stripped = str(raw_line or "").strip()
        if not stripped or stripped in {"DATA_GRID_START", "DATA_GRID_END"}:
            continue
        label = ""
        if "|" in stripped:
            label = str(stripped.split("|", 1)[0] or "").strip()
        elif stripped.startswith("→") and ":" in stripped:
            label = str(stripped.lstrip("→").split(":", 1)[0] or "").strip()
        label = label.strip(":- ")
        if not label:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        labels.append(label)

    if not labels:
        return []

    label_keys = {re.sub(r"[^a-z0-9]+", " ", label.lower()).strip() for label in labels}
    company_specific = [
        label
        for label in labels
        if re.search(
            r"\b(arr|arpu|attach|backlog|bookings|capacity|churn|conversion|cohort|deployments?|"
            r"inference|monetized usage|nrr|nr[rt]|paid[- ]seat|pipeline|renewals?|retention|rpo|"
            r"seat expansion|shipments?|take rate|traffic acquisition|utilization)\b",
            label,
            re.IGNORECASE,
        )
    ]
    bullets: List[str] = []
    if company_specific:
        bullets.append(
            f"Watch {company_specific[0]} first; it tells you fastest whether the filing's main claim is turning into operating results."
        )
    if {"operating margin", "free cash flow"} <= label_keys:
        bullets.append(
            "Read operating margin with free cash flow to see whether the business is staying self-funded through its next move."
        )
    if (
        {"cash", "current ratio"} & label_keys
        or {"net debt", "total debt"} & label_keys
    ):
        bullets.append(
            "Treat the liquidity and leverage rows as the downside buffer, not the main thesis."
        )
    if len(bullets) < 2:
        bullets.append(
            f"Watch {labels[0]} first; it is the clearest early sign that the filing story is turning into operating results."
        )
    if len(bullets) < 2 and len(labels) > 1:
        bullets.append(
            f"Use {labels[1]} as the next confirmation that the current improvement is becoming durable, not just quarter-end timing."
        )
    return bullets[: max(1, int(limit))]


def _build_key_metrics_body(
    *,
    metrics_lines: str,
    analysis: FilingAnalysis,
    max_words: Optional[int] = None,
) -> str:
    base_lines = [line.strip() for line in str(metrics_lines or "").splitlines() if line.strip()]
    company_lines = _company_kpi_metric_lines(analysis)
    seen: set[str] = set()
    ordered_lines: List[str] = []
    for line in company_lines + base_lines:
        canon = _metric_line_key(line)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        ordered_lines.append(line)
    ordered_lines = _trim_key_metrics_lines_to_budget(
        ordered_lines,
        max_words=max_words,
    )
    if not ordered_lines:
        return ""

    highlight_lines = _build_metrics_highlights(analysis, limit=4)
    if not highlight_lines:
        highlight_lines = _fallback_metrics_intro_from_rows(ordered_lines, limit=3)
    if highlight_lines:
        while highlight_lines:
            intro_block = "What Matters:\n" + "\n".join(
                f"- {item}" for item in highlight_lines
            )
            candidate = f"{intro_block}\n\n" + "\n".join(ordered_lines)
            if not max_words or count_words(candidate) <= int(max_words):
                return candidate.strip()
            highlight_lines.pop()

    return "\n".join(ordered_lines).strip()


def _normalize_section_body(section_name: str, raw_text: str) -> str:
    """Normalize a section body returned by the model."""
    text = str(raw_text or "").strip()
    if not text:
        return ""
    fenced = re.search(r"```(?:markdown)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    heading_pattern = re.compile(
        rf"^\s*#{{1,6}}\s*{re.escape(section_name)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    duplicate_title_pattern = re.compile(
        rf"^\s*{re.escape(section_name)}\s*:?\s*",
        re.IGNORECASE,
    )
    while True:
        prior = text
        text = heading_pattern.sub("", text).strip()
        text = duplicate_title_pattern.sub("", text, count=1).strip()
        if section_name != "Key Metrics":
            text = re.sub(r"^\s*#.+$", "", text, flags=re.MULTILINE).strip()
        if text == prior:
            break
    # Strip obvious instruction leakage headings that should never appear in output
    for leak_heading in (
        "BODY WORD BUDGET:", "SECTION WORD BUDGET:", "MANDATORY:",
        "ANTI-BOREDOM CONSTRAINTS", "STYLE CONTRACT:", "SECTION FOCUS:",
        "AHA INSIGHT TO PROTECT:", "AHA INSIGHT (MUST SURFACE",
        "CENTRAL TENSION:", "VALIDATED MEMO THREAD:",
        "QUOTE POLICY:", "QUOTE MANDATE:", "EDITORIAL ANCHOR RULES:",
        "DEPTH MOVES:", "REPAIR INSTRUCTION:", "BANNED OVERLAP:",
        "FORBIDDEN OPENINGS:", "SECTION INSTRUCTION CHECKS:",
    ):
        text = re.sub(
            rf"^\s*{re.escape(leak_heading)}[^\n]*$",
            "",
            text,
            flags=re.MULTILINE | re.IGNORECASE,
        ).strip()
    return text.strip()


def _depth_moves_for_section(section_name: str, depth_plan: Any) -> List[str]:
    moves: List[str] = []
    if section_name == "Financial Performance":
        if depth_plan.yoy_score >= 0.35:
            moves.append("Add a year-over-year comparison only if the filing evidence supports it.")
        if depth_plan.sequential_score >= 0.35:
            moves.append("Add a sequential comparison if it sharpens the current-period read.")
        if depth_plan.leverage_score >= 0.4:
            moves.append("Explain operating leverage through price, volume, mix, or cost absorption.")
        if depth_plan.example_score >= 0.45:
            moves.append("Use one concrete example or evidence anchor to prove the main driver.")
    elif section_name == "Management Discussion & Analysis":
        if depth_plan.leverage_score >= 0.4:
            moves.append("Connect management actions to operating leverage or margin durability.")
        if depth_plan.capital_allocation_score >= 0.4:
            moves.append("Add capital allocation implications only if filing evidence supports them.")
        if depth_plan.balance_sheet_score >= 0.4:
            moves.append("Mention balance-sheet flexibility only if it changes execution capacity.")
    elif section_name == "Risk Factors":
        if depth_plan.scenario_score >= 0.35:
            moves.append("Frame downside in scenario terms with a measurable trigger.")
        if depth_plan.cash_conversion_score >= 0.35:
            moves.append("Tie at least one risk to cash conversion, liquidity, or working-capital strain if supported.")
    elif section_name == "Closing Takeaway":
        if depth_plan.scenario_score >= 0.35:
            moves.append("Include one measurable trigger that would change the stance.")
        if depth_plan.capital_allocation_score >= 0.4:
            moves.append("Mention capital allocation only if it changes the underwriting conclusion.")
    elif section_name == "Financial Health Rating":
        if depth_plan.cash_conversion_score >= 0.35:
            moves.append("Explain how cash conversion influences the health verdict.")
        if depth_plan.balance_sheet_score >= 0.35:
            moves.append("Explain how balance-sheet flexibility caps downside.")
    else:
        if depth_plan.example_score >= 0.45:
            moves.append("Use one evidence-backed example if it adds new insight.")
    if not moves:
        moves.append("Focus on signal only. Compress secondary angles rather than adding filler.")
    return moves


def _section_budget_range(section_name: str, budget: int) -> tuple[int, int]:
    tolerance = section_budget_tolerance_words(section_name, int(budget or 0))
    return max(1, int(budget) - int(tolerance)), int(budget) + int(tolerance)


def _quotes_for_section(analysis: FilingAnalysis, section_name: str) -> List[ManagementQuote]:
    return [
        quote
        for quote in analysis.management_quotes
        if (quote.suggested_section or "").strip() == section_name
    ]


def _fallback_key_metrics_from_kpis(analysis: FilingAnalysis) -> str:
    return "\n".join(_company_kpi_metric_lines(analysis, limit=5))


_END_PUNCT_RE = re.compile(r'[.!?](?:["\')\]]+)?$')
_RISK_LABEL_PATTERN = r"(?:\[[^\]\n]{2,120}\]|[A-Z0-9][^:\n*]{1,120})"
_RISK_HEADER_RE = re.compile(
    rf"^(?:\*\*)?(?P<name>{_RISK_LABEL_PATTERN})(?:\*\*\s*:|:\s*\*\*|:)\s*(?P<body>.*)$",
    re.DOTALL,
)
_LEGACY_BOLD_RISK_HEADER_RE = re.compile(
    r"^\*\*(?P<name>[^*\n]{2,140}?)\*\*\s*(?P<body>.+)$",
    re.DOTALL,
)
_RISK_INLINE_HEADER_RE = re.compile(
    r"\s+(?=\*\*(?:\[[^\]\n]{2,120}\]|[^:\n*]{2,120})(?:\*\*\s*:|:\s*\*\*)\s+)",
)
_RISK_SENTENCE_BOUNDARY_HEADER_RE = re.compile(
    rf"(?<=[.!?])\s+(?=(?:\*\*)?(?:\[[^\]\n]{{2,120}}\]|[A-Z0-9][^:\n]{{1,120}}?)(?:\*\*\s*:|:\s*\*\*|:)\s+)",
)
_RISK_EMBEDDED_HEADER_RE = re.compile(
    rf"(?<=[.!?])\s+(?=(?:\*\*)?(?:\[[^\]\n]{{2,120}}\]|[A-Z0-9][^:\n]{{1,120}})(?:\*\*\s*:|:\s*\*\*|:)\s+)",
)
_MECHANISM_RE = re.compile(
    r"\b(because|driven by|if|unless|leads to|results in|pressure|compress|dilute|erode|funding|liquidity|working capital|pricing|churn|renewal|mix shift|substitution|execution slip)\b",
    re.IGNORECASE,
)
_TRANSMISSION_RE = re.compile(
    r"\b(revenue|pricing|volume|mix|margin|gross margin|operating margin|cash flow|free cash flow|liquidity|refinancing|debt|balance sheet|working capital|capex|opex|demand|backlog|bookings)\b",
    re.IGNORECASE,
)
_EARLY_WARNING_RE = re.compile(
    r"\b(early[- ]warning|watch|signal|trigger|threshold|leading indicator|renewal|bookings|backlog|churn|pipeline|pricing|utilization|adoption|attrition|downtime|default|refinancing)\b",
    re.IGNORECASE,
)
_HEALTH_PROFITABILITY_RE = re.compile(
    r"\b(margin|profitability|cash conversion|free cash flow|operating cash flow|fcf|earnings quality|return on capital)\b",
    re.IGNORECASE,
)
_HEALTH_BALANCE_SHEET_RE = re.compile(
    r"\b(balance sheet|liquidity|cash|debt|funding|flexibility|leverage|coverage|refinancing|optionalit)\b",
    re.IGNORECASE,
)
_DANGLING_PATTERNS = (
    r",\s*$",
    r"\bbut\s*$",
    r"\band\s*$",
    r"\bor\s*$",
    r"\bthat\s*$",
    r"\bwith\s*$",
    r"\bwhich\s*$",
    r"\bwhere\s*$",
    r"\bif\s*$",
)
_STANCE_RE = re.compile(
    r"\b(buy|hold|sell|overweight|underweight|neutral|constructive|cautious|positive|negative|attractive|unattractive)\b",
    re.IGNORECASE,
)
_TRIGGER_RE = re.compile(
    r"\b(trigger|watch|must stay true|breaks the thesis|would change|needs to|if\b|unless\b)\b",
    re.IGNORECASE,
)


def _extract_structured_risk_items(text: str) -> List[Tuple[str, str]]:
    def _is_non_risk_label(name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
        return normalized in {
            "early warning",
            "early warning signal",
            "early warning signals",
            "indicator",
            "indicators",
            "signal",
            "signals",
            "trigger",
            "triggers",
            "watch",
            "watch for",
        }

    normalized = str(text or "").replace("\u00a0", " ").strip()
    if not normalized:
        return []
    items: List[Tuple[str, str]] = []
    def _append_item(name: str, body: str) -> None:
        if not name or not body:
            return
        if _is_non_risk_label(name):
            if items:
                prior_name, prior_body = items[-1]
                merged_body = f"{prior_body} {name}: {body}".strip()
                items[-1] = (prior_name, merged_body)
            return
        items.append((name, body))

    for paragraph in re.split(r"\n\s*\n+", normalized):
        match = _RISK_HEADER_RE.match(str(paragraph or "").strip()) or _LEGACY_BOLD_RISK_HEADER_RE.match(
            str(paragraph or "").strip()
        )
        if not match:
            continue
        name = str(match.group("name") or "").strip().strip("[]")
        body = " ".join(str(match.group("body") or "").split()).strip()
        _append_item(name, body)
    if items:
        if len(items) > 1 or not _RISK_EMBEDDED_HEADER_RE.search(items[0][1]):
            return items
        items = []

    normalized = _RISK_SENTENCE_BOUNDARY_HEADER_RE.sub("\n\n", normalized)
    normalized = _RISK_INLINE_HEADER_RE.sub("\n\n", normalized)
    for paragraph in re.split(r"\n\s*\n+", normalized):
        match = _RISK_HEADER_RE.match(str(paragraph or "").strip()) or _LEGACY_BOLD_RISK_HEADER_RE.match(
            str(paragraph or "").strip()
        )
        if not match:
            continue
        name = str(match.group("name") or "").strip().strip("[]")
        body = " ".join(str(match.group("body") or "").split()).strip()
        _append_item(name, body)
    return items


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()])


def _has_terminal_punctuation(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped) and bool(_END_PUNCT_RE.search(stripped))


def _has_dangling_ending(text: str) -> bool:
    stripped = str(text or "").rstrip()
    for pattern in _DANGLING_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    return False


def _section_word_distance(section_name: str, text: str, budget: int) -> int:
    actual_words = count_words(text)
    lower, upper = _section_budget_range(section_name, budget)
    if lower <= actual_words <= upper:
        return 0
    if actual_words < lower:
        return lower - actual_words
    return actual_words - upper


def _validate_health_local_contract(
    text: str,
    *,
    budget: int,
    health_score_data: Optional[Dict[str, Any]],
) -> List[str]:
    failures: List[str] = []
    shape = get_financial_health_shape(budget)
    score = health_score_data.get("overall_score") if health_score_data else None
    score_line_present = "/100" in str(text or "")
    if score is not None and not score_line_present:
        failures.append("Open with the exact pre-calculated health score line.")
    if not _HEALTH_PROFITABILITY_RE.search(text or ""):
        failures.append("Explain profitability quality or cash conversion.")
    if not _HEALTH_BALANCE_SHEET_RE.search(text or ""):
        failures.append("Explain balance-sheet flexibility or liquidity.")
    sentence_count = _sentence_count(text)
    if not (shape.min_sentences <= sentence_count <= shape.max_sentences):
        failures.append(
            "Use "
            + describe_sentence_range(shape.min_sentences, shape.max_sentences)
            + " for this Financial Health Rating budget."
        )
    return failures


def _validate_risk_local_contract(
    text: str,
    *,
    budget: int,
    analysis: Optional[FilingAnalysis] = None,
) -> List[str]:
    failures: List[str] = []
    shape = get_risk_factors_shape(budget)
    items = _extract_structured_risk_items(text or "")
    expected_count = int(shape.risk_count or 0)
    accepted_risks = _accepted_company_risks(analysis) if analysis else []
    required_count = int(expected_count)
    if accepted_risks:
        required_count = min(int(expected_count), len(accepted_risks))
    accepted_names = {
        str(risk.risk_name or "").strip().lower()
        for risk in accepted_risks
        if str(risk.risk_name or "").strip()
    }
    if len(items) != required_count:
        failures.append(
            f"Write {required_count} structured risks from the accepted source-backed set (target budget assumes up to {expected_count})."
        )
        return failures
    per_risk_target = max(18, budget // max(1, expected_count or required_count))
    per_risk_tolerance = max(8, int(round(per_risk_target * 0.12)))
    min_body_words = max(18, min(80, per_risk_target - per_risk_tolerance))
    if required_count < expected_count:
        min_body_words = max(18, int(round(min_body_words * 0.7)))
    prior_risks: List[Tuple[str, str]] = []
    for risk_name, body in items:
        if accepted_names and risk_name.lower() not in accepted_names:
            failures.append(
                f"Risk '{risk_name}' is not one of the accepted source-backed risk names."
            )
        sentence_count = _sentence_count(body)
        if not (
            int(shape.per_risk_min_sentences or 2)
            <= sentence_count
            <= int(shape.per_risk_max_sentences or 3)
        ):
            failures.append(
                f"Risk '{risk_name}' must contain "
                + describe_sentence_range(
                    int(shape.per_risk_min_sentences or 2),
                    int(shape.per_risk_max_sentences or 3),
                )
                + "."
            )
        body_words = count_words(body)
        if body_words < min_body_words:
            failures.append(
                f"Risk '{risk_name}' is too short for this section budget; expand it with company-specific analysis."
            )
        if looks_numeric_led(body):
            failures.append(
                f"Risk '{risk_name}' opens with a numeric-led fragment; start with the business event."
            )
        if is_metric_only_risk_name(risk_name):
            failures.append(
                f"Risk '{risk_name}' is metric-led; use a company-specific exposure."
            )
        if is_generic_risk_name(risk_name):
            failures.append(
                f"Risk '{risk_name}' is too generic; name the company-specific exposure."
            )
        if is_filing_fragment_risk_name(risk_name):
            failures.append(
                f"Risk '{risk_name}' looks like a filing structure fragment; name the company-specific exposure."
            )
        if looks_boilerplate_risk_body(body):
            failures.append(
                f"Risk '{risk_name}' contains boilerplate risk language."
            )
        if not _MECHANISM_RE.search(body):
            failures.append(f"Risk '{risk_name}' needs a concrete mechanism.")
        if not _TRANSMISSION_RE.search(body):
            failures.append(f"Risk '{risk_name}' must explain the financial impact path.")
        for existing_name, existing_body in prior_risks:
            overlap = assess_risk_overlap(
                risk_name=risk_name,
                risk_body=body,
                other_risk_name=existing_name,
                other_risk_body=existing_body,
            )
            if overlap.exact_name_match or overlap.names_overlap:
                failures.append(
                    f"Risk '{risk_name}' overlaps too much with '{existing_name}'. Use distinct mechanisms and business areas."
                )
                break
            if overlap.bodies_overlap:
                failures.append(
                    f"Risk '{risk_name}' reuses the same mechanism as '{existing_name}'. Rewrite it around a different impact path."
                )
                break
        prior_risks.append((risk_name, body))
    return failures


def _validate_closing_local_contract(text: str, *, budget: int) -> List[str]:
    failures: List[str] = []
    shape = get_closing_takeaway_shape(budget)
    sentence_count = _sentence_count(text)
    if not (shape.min_sentences <= sentence_count <= shape.max_sentences):
        failures.append(
            "Use "
            + describe_sentence_range(shape.min_sentences, shape.max_sentences)
            + " for this Closing Takeaway budget."
        )
    if len(_STANCE_RE.findall(text or "")) != 1:
        failures.append("State exactly one clear stance in the Closing Takeaway.")
    if budget < 120 and not _TRIGGER_RE.search(text or ""):
        failures.append("Include one measurable trigger or watch item.")
    if budget >= 120:
        lowered = str(text or "")
        if not re.search(
            r"\b(what must stay true|must stay true|as long as|while|so long as|holds?|stays?)\b",
            lowered,
            re.IGNORECASE,
        ):
            failures.append("Include a measurable 'what must stay true' trigger.")
        if not re.search(
            r"\b(what breaks the thesis|breaks the thesis|downgrade|downside|if .*below|if .*compress|deteriorat(?:e|es|ion))\b",
            lowered,
            re.IGNORECASE,
        ):
            failures.append("Include a measurable 'what breaks the thesis' trigger.")
    if _has_dangling_ending(text):
        failures.append("Close on a complete sentence with no dangling clause.")
    return failures


def _validate_section_local_contract(
    *,
    section_name: str,
    text: str,
    budget: int,
    analysis: Optional[FilingAnalysis] = None,
    health_score_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    failures: List[str] = []
    body = str(text or "").strip()
    if not body:
        return ["Section body is empty."]
    if section_name == "Key Metrics":
        if "\n" not in body and "→" not in body and "->" not in body:
            return ["Return only deterministic metric lines for Key Metrics."]
        return []

    lower, upper = _section_budget_range(section_name, budget)
    actual_words = count_words(body)
    if actual_words < lower:
        failures.append(
            f"Section is underweight at {actual_words} words; required range is {lower}-{upper}."
        )
    elif actual_words > upper:
        failures.append(
            f"Section is overweight at {actual_words} words; required range is {lower}-{upper}."
        )
    if not _has_terminal_punctuation(body):
        failures.append("End the section with terminal punctuation.")
    if _has_dangling_ending(body):
        failures.append("End the section on a complete thought without a dangling clause.")
    if _sentence_count(body) < 2:
        failures.append("Write at least two full sentences.")

    if section_name == "Financial Health Rating":
        failures.extend(
            _validate_health_local_contract(
                body, budget=budget, health_score_data=health_score_data
            )
        )
    elif section_name == "Risk Factors":
        failures.extend(
            _validate_risk_local_contract(
                body,
                budget=budget,
                analysis=analysis,
            )
        )
    elif section_name == "Closing Takeaway":
        failures.extend(_validate_closing_local_contract(body, budget=budget))
    return failures


def generate_section_body_to_budget(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    budget: int,
    depth_plan: Any,
    prior_section_text: str,
    used_claims: Optional[List[str]],
    section_memory: Optional[Any],
    narrative_blueprint: Optional[NarrativeBlueprint],
    openai_client: Any,
    failure_reason: str = "",
    section_instructions: Optional[Dict[str, str]] = None,
    thread_decision: Optional[ThreadDecision] = None,
    section_plan: Optional[SectionPlan] = None,
    tone: str = "objective",
    detail_level: str = "balanced",
    output_style: str = "narrative",
    focus_areas: Optional[Sequence[str]] = None,
    investor_focus: Optional[str] = None,
) -> str:
    if section_name == "Key Metrics":
        return _generate_section_body(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            section_memory=section_memory,
            narrative_blueprint=narrative_blueprint,
            openai_client=openai_client,
            failure_reason=failure_reason,
            section_instructions=section_instructions,
            thread_decision=thread_decision,
            section_plan=section_plan,
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
        )

    if section_name == "Risk Factors":
        shape = get_risk_factors_shape(budget)
        required_risks = int(shape.risk_count or risk_budget_target_count(budget) or 0)
        accepted_risks = _accepted_company_risks(filing_analysis)
        if required_risks > 0 and len(accepted_risks) == 0:
            safe_body = _build_source_backed_risk_section_body(
                filing_analysis,
                limit=max(1, len(accepted_risks)),
            )
            return _normalize_section_body(section_name, safe_body)

    max_attempts = 6 if section_name == "Risk Factors" else 3
    best_body = ""
    best_rank: Optional[tuple[int, int, int, int]] = None
    next_failure_reason = failure_reason

    for attempt in range(1, max_attempts + 1):
        draft = _generate_section_body(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            section_memory=section_memory,
            narrative_blueprint=narrative_blueprint,
            openai_client=openai_client,
            failure_reason=next_failure_reason,
            section_instructions=section_instructions,
            thread_decision=thread_decision,
            section_plan=section_plan,
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
        )
        local_failures = _validate_section_local_contract(
            section_name=section_name,
            text=draft,
            budget=budget,
            analysis=filing_analysis,
            health_score_data=health_score_data,
        )
        rank = (
            0 if not local_failures else 1,
            _section_word_distance(section_name, draft, budget),
            0 if _sentence_count(draft) >= 2 else 1,
            0 if _has_terminal_punctuation(draft) else 1,
        )
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_body = draft
        if not local_failures:
            return draft

        actual_words = count_words(draft)
        lower, upper = _section_budget_range(section_name, budget)
        shortfall = max(0, lower - actual_words)
        overage = max(0, actual_words - upper)
        next_failure_reason = (
            f"Attempt {attempt} failed local section validation.\n"
            f"- Actual word count: {actual_words}\n"
            f"- Required range: {lower}-{upper}\n"
            f"- Shortfall: {shortfall}\n"
            f"- Overage: {overage}\n"
            f"- Failures:\n"
            + "\n".join(f"  - {item}" for item in local_failures[:8])
        )

    return best_body


def regenerate_pipeline_section_body(
    *,
    pipeline_result: PipelineResult,
    section_name: str,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    budget: int,
    prior_section_text: str,
    used_claims: Optional[List[str]],
    section_memory: Optional[Any] = None,
    openai_client: Any,
    failure_reason: str = "",
    section_instructions: Optional[Dict[str, str]] = None,
) -> str:
    """Regenerate one section from an existing pipeline result's evidence context."""
    if budget <= 0:
        return ""

    depth_plan = compute_depth_plan(compute_scale_factor(target_length or 300))
    metadata = dict(getattr(pipeline_result, "metadata", {}) or {})
    thread_decision = _hydrate_thread_decision(
        metadata.get("thread_decision")
    )
    section_plan = _hydrate_section_plan(
        section_name,
        (metadata.get("section_plans") or {}).get(section_name),
    )
    return generate_section_body_to_budget(
        section_name=section_name,
        company_intelligence=pipeline_result.company_intelligence,
        filing_analysis=pipeline_result.filing_analysis,
        company_name=company_name,
        target_length=target_length,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        health_score_data=health_score_data,
        budget=budget,
        depth_plan=depth_plan,
        prior_section_text=prior_section_text,
        used_claims=used_claims,
        section_memory=section_memory,
        narrative_blueprint=_build_narrative_blueprint(
            company_name=company_name,
            company_intelligence=pipeline_result.company_intelligence,
            filing_analysis=pipeline_result.filing_analysis,
        ),
        openai_client=openai_client,
        failure_reason=failure_reason,
        section_instructions=section_instructions or dict(metadata.get("section_instructions") or {}),
        thread_decision=thread_decision,
        section_plan=section_plan,
        tone=str(metadata.get("tone") or "objective"),
        detail_level=str(metadata.get("detail_level") or "balanced"),
        output_style=str(metadata.get("output_style") or "narrative"),
        focus_areas=list(metadata.get("focus_areas") or []),
        investor_focus=str(metadata.get("investor_focus") or "") or None,
    )


def _assemble_summary_from_sections(
    section_bodies: Dict[str, str],
    *,
    include_health_rating: bool,
) -> str:
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    parts: List[str] = []
    for section_name in ordered_sections:
        body = (section_bodies.get(section_name) or "").strip()
        if not body:
            continue
        parts.append(f"## {section_name}\n{body}")
    return "\n\n".join(parts).strip()


def _collect_section_word_counts(
    text: str,
    *,
    include_health_rating: bool,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    for section_name in ordered_sections:
        match = re.search(
            rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
            text or "",
            re.DOTALL,
        )
        counts[section_name] = count_words((match.group(1) if match else "").strip())
    return counts


def _extract_section_bodies_from_summary(
    text: str,
    *,
    include_health_rating: bool,
) -> Dict[str, str]:
    bodies: Dict[str, str] = {}
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    for section_name in ordered_sections:
        match = re.search(
            rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
            text or "",
            re.DOTALL,
        )
        bodies[section_name] = (match.group(1) if match else "").strip()
    return bodies


def _thread_anchor_terms(decision: ThreadDecision) -> List[str]:
    terms = [str(decision.anchor or "").strip()]
    anchor_key = _normalized_thread_key(decision.anchor)
    if anchor_key:
        terms.extend(
            part
            for part in anchor_key.split()
            if len(part) >= 4 and part not in {"that", "with", "into", "while"}
        )
    return _dedupe_ordered_strings([term for term in terms if term], limit=6)


def _body_mentions_thread_anchor(body: str, decision: ThreadDecision) -> bool:
    lowered = str(body or "").lower()
    if not lowered:
        return False
    for term in _thread_anchor_terms(decision):
        if term.lower() in lowered:
            return True
    return False


def _body_uses_owned_evidence(body: str, plan: SectionPlan) -> bool:
    lowered = str(body or "").lower()
    for item in list(plan.owned_evidence or [])[:4]:
        words = [word for word in re.findall(r"[a-z0-9]+", item.lower()) if len(word) >= 5]
        if words and sum(1 for word in words[:3] if word in lowered) >= 1:
            return True
    return False


def _section_sentences(text: str, *, limit: Optional[int] = None) -> List[str]:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        if sentence.strip()
    ]
    if limit is not None:
        return sentences[: max(0, int(limit))]
    return sentences


def _meaningful_token_overlap(left: str, right: str) -> int:
    stopwords = {
        "that",
        "this",
        "with",
        "from",
        "into",
        "over",
        "under",
        "while",
        "where",
        "which",
        "their",
        "there",
        "management",
        "company",
        "because",
        "through",
    }
    left_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{3,}", str(left or "").lower())
        if token not in stopwords
    }
    right_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{3,}", str(right or "").lower())
        if token not in stopwords
    }
    return len(left_tokens & right_tokens)


def _opening_surfaces_aha(body: str, decision: ThreadDecision) -> bool:
    opening = " ".join(_section_sentences(body, limit=2))
    aha_text = str(decision.aha_insight or "").strip()
    if not opening or not aha_text:
        return True
    if _AHA_SIGNAL_RE.search(opening):
        return True
    overlap = _meaningful_token_overlap(opening, aha_text)
    if re.search(
        r"\b(real shift|not just|rather than|instead of|no longer|self[- ]fund(?:ed|ing)|from\b.+\bto)\b",
        aha_text,
        re.IGNORECASE,
    ):
        return overlap >= 3 and bool(
            re.search(
                r"\b(real shift|not just|rather than|instead of|no longer|self[- ]fund(?:ed|ing)|from\b.+\bto)\b",
                opening,
                re.IGNORECASE,
            )
        )
    return overlap >= 2


def _opening_has_decision_block(body: str) -> bool:
    opening = " ".join(_section_sentences(body, limit=2))
    if not opening:
        return False
    has_decision_language = bool(_EXEC_DECISION_OPENING_RE.search(opening))
    has_implication = bool(_AHA_SIGNAL_RE.search(opening)) or "means" in opening.lower()
    return has_decision_language or has_implication


def _opening_names_next_proof_point(body: str) -> bool:
    opening = " ".join(_section_sentences(body, limit=2))
    if not opening:
        return False
    return bool(_WATCHPOINT_RE.search(opening))


def _final_sentence_has_concrete_trigger(
    body: str,
    *,
    require_numeric: bool = False,
) -> bool:
    sentences = _section_sentences(body)
    if not sentences:
        return False
    final_sentence = sentences[-1]
    has_trigger_language = bool(_WATCHPOINT_RE.search(final_sentence)) or bool(
        _TRIGGER_RE.search(final_sentence)
    )
    has_timing = bool(_TIMELINE_RE.search(final_sentence))
    has_threshold = bool(re.search(r"\d|above|below|at least|less than|more than", final_sentence, re.IGNORECASE))
    if require_numeric:
        return has_trigger_language and (has_threshold or has_timing)
    return (has_trigger_language and (has_threshold or has_timing)) or (has_timing and has_threshold)


def _financial_performance_metric_classes(body: str) -> Set[str]:
    return {
        name
        for name, pattern in _FINANCIAL_PERFORMANCE_METRIC_PATTERNS.items()
        if pattern.search(body or "")
    }


def _financial_performance_has_repeated_interpretation(body: str) -> bool:
    matches = _FINANCIAL_PERFORMANCE_REINVESTMENT_ECHO_RE.findall(body or "")
    return len(matches) > 1


def _instruction_check_failed(check: InstructionCheck, body: str) -> bool:
    lowered = str(body or "").lower()
    target = str(check.target or "").lower()
    if not lowered:
        return True
    if check.check_type == "must_be_forward_looking":
        return not _FORWARD_LOOKING_RE.search(body or "")
    if check.check_type == "must_use_management_view":
        has_management_actor = bool(
            re.search(r"\b(management|leadership|ceo|cfo|company)\b", body or "", re.IGNORECASE)
        )
        has_management_view = bool(
            re.search(
                r"\b(expects?|guides?|plans?|targets?|priorit(?:y|ies|ize|izing)|outlook|positioning|positioned)\b",
                body or "",
                re.IGNORECASE,
            )
        )
        has_attribution = bool(
            re.search(
                r"\b(?:management|CEO|CFO|company)\b.{0,40}\b(?:noted|said|stated|indicated|emphasized|highlighted|cautioned|expects?|guides?)\b",
                body or "",
                re.IGNORECASE,
            )
        )
        return not ((has_management_actor and has_management_view) or has_attribution)
    if check.check_type == "must_include_watch_metric":
        has_watch_language = bool(
            re.search(
                r"\b(watch|watchpoint|checkpoint|proof point|metric|trigger|leading indicator|should watch)\b",
                body or "",
                re.IGNORECASE,
            )
        )
        has_numeric_anchor = bool(re.search(r"\d", body or ""))
        return not (has_watch_language and has_numeric_anchor)
    if check.check_type in {"must_emphasize_theme", "must_prioritize_angle"}:
        target_terms = [term for term in re.findall(r"[a-z0-9]+", target) if len(term) >= 4]
        if not target_terms:
            return target not in lowered
        return not any(term in lowered for term in target_terms[:4])
    if check.check_type == "must_avoid_angle":
        target_terms = [term for term in re.findall(r"[a-z0-9]+", target) if len(term) >= 4]
        return any(term in lowered for term in target_terms[:4]) if target_terms else target in lowered
    return target not in lowered


def _average_sentence_length(text: str) -> float:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        if sentence.strip()
    ]
    if not sentences:
        return 0.0
    return float(sum(count_words(sentence) for sentence in sentences)) / float(len(sentences))


def _judge_sectioned_summary(
    *,
    section_bodies: Dict[str, str],
    include_health_rating: bool,
    thread_decision: ThreadDecision,
    section_plans: Dict[str, SectionPlan],
) -> List[EditorialFailure]:
    failures: List[EditorialFailure] = []
    if _is_invalid_thread_anchor(thread_decision.anchor):
        failures.append(
            EditorialFailure(
                section_name="Executive Summary",
                code="thread_anchor_invalid",
                message=(
                    f"The validated memo thread is anchored to an invalid concept ('{thread_decision.anchor}')."
                ),
                severity=3.0,
            )
        )
    summary_text = _assemble_summary_from_sections(
        section_bodies,
        include_health_rating=include_health_rating,
    )
    repetition_report = check_repetition(summary_text)
    for section_name in getattr(repetition_report, "affected_sections", []) or []:
        if getattr(repetition_report, "repeated_leadins", None):
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="repeated_leadin",
                    message=f"{section_name} repeats the same lead-in stem as another section.",
                    severity=2.7,
                )
            )
        if repetition_report.repeated_trailing_phrases or repetition_report.repeated_ngrams:
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="repeated_clause_family",
                    message=f"{section_name} repeats clause shapes or phrase families already used elsewhere.",
                    severity=2.5,
                )
            )

    similar_pairs = detect_similar_paragraphs(summary_text, threshold=0.84)
    for pair in similar_pairs:
        failures.append(
            EditorialFailure(
                section_name=pair.section_b,
                code="section_overlap",
                message=(
                    f"{pair.section_b} substantially re-explains logic already used in {pair.section_a}."
                ),
                severity=2.6,
            )
        )

    for section_name, plan in section_plans.items():
        body = str(section_bodies.get(section_name) or "").strip()
        if not body:
            continue
        if section_name == "Executive Summary":
            if not _opening_has_decision_block(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="exec_opening_soft",
                        message="Executive Summary should use the first 2 sentences as a decision block, not a soft scene-setting opening.",
                        severity=2.8,
                    )
                )
            if not _opening_surfaces_aha(body, thread_decision):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="aha_not_surfaced",
                        message="Executive Summary does not surface the memo's non-obvious insight early enough.",
                        severity=2.9,
                    )
                )
            if not _opening_names_next_proof_point(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="exec_missing_proof_point",
                        message="Executive Summary should name the single proof point investors should watch next within the opening block.",
                        severity=2.5,
                    )
                )
        if section_name in {"Management Discussion & Analysis", "Closing Takeaway"}:
            if not _body_mentions_thread_anchor(body, thread_decision):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="thread_not_resolved",
                        message=f"{section_name} does not resolve the validated memo thread '{thread_decision.anchor}'.",
                        severity=2.6,
                    )
                )
        if not _body_uses_owned_evidence(body, plan):
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="section_overlap",
                    message=f"{section_name} is missing its owned evidence and is drifting into another section's territory.",
                    severity=1.8,
                )
            )
        for check in list(plan.instruction_checks or []):
            if _instruction_check_failed(check, body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="instruction_miss",
                        message=f"{section_name} missed a section instruction: {check.guidance}",
                        severity=2.8,
                    )
                )
        if section_name == "Financial Performance":
            metric_classes = _financial_performance_metric_classes(body)
            if len(metric_classes) > 3:
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="financial_performance_metric_drift",
                        message="Financial Performance is trying to carry too many primary metrics. Keep it to 2-3 interpreted drivers.",
                        severity=2.4,
                    )
                )
            if _financial_performance_has_repeated_interpretation(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="financial_performance_redundancy",
                        message="Financial Performance repeats the same reinvestment-or-funding idea in different words instead of advancing the analysis.",
                        severity=2.6,
                    )
                )
            if not _final_sentence_has_concrete_trigger(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="soft_section_ending",
                        message="Financial Performance should end on the specific metric, threshold, or timeline that hands the question into management execution.",
                        severity=1.9,
                    )
                )
        if section_name == "Management Discussion & Analysis" and not _final_sentence_has_concrete_trigger(body):
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="soft_section_ending",
                    message="Management Discussion & Analysis should end on the checkpoint, trigger, or timeline that would show management's plan is slipping.",
                    severity=2.0,
                )
            )
        if section_name == "Risk Factors":
            if not _EARLY_WARNING_RE.search(body) or not re.search(
                r"\b(now|currently|over the next|within the next|near[- ]term|this filing|position size|priced in)\b",
                body,
                re.IGNORECASE,
            ):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="risk_not_actionable",
                        message="Risk Factors need clearer 'why this matters now' and investor-actionable trigger language.",
                        severity=2.4,
                    )
                )
            if not _final_sentence_has_concrete_trigger(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="soft_section_ending",
                        message="Risk Factors should end on the first metric, checkpoint, or dated catalyst that would show the downside is forming.",
                        severity=2.1,
                    )
                )
        if section_name == "Closing Takeaway":
            if not _AHA_SIGNAL_RE.search(body) or not _TRIGGER_RE.search(body):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="closing_soft",
                        message="Closing Takeaway needs a sharper implication sentence and a measurable thesis-break or must-hold trigger.",
                        severity=2.7,
                    )
                )
            if not _final_sentence_has_concrete_trigger(body, require_numeric=True):
                failures.append(
                    EditorialFailure(
                        section_name=section_name,
                        code="soft_section_ending",
                        message="Closing Takeaway should end on a measurable trigger or dated threshold, not a generic cliffhanger.",
                        severity=2.7,
                    )
                )
        avg_sentence_length = _average_sentence_length(body)
        hedge_count = len(_HEDGE_RE.findall(body))
        awkward_count = len(_AWKWARD_LINKER_RE.findall(body))
        analyst_fog_count = len(_ANALYST_FOG_RE.findall(body))
        if avg_sentence_length > 30 or awkward_count > 1:
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="readability_drift",
                    message="The prose is too clause-heavy or overly formal for the premium analyst-note standard.",
                    severity=1.7,
                )
            )
        if hedge_count > 3:
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="tone_drift",
                    message="The prose is over-hedged and loses the intended premium analyst-note confidence.",
                    severity=1.6,
                )
            )
        if analyst_fog_count > 0:
            failures.append(
                EditorialFailure(
                    section_name=section_name,
                    code="tone_drift",
                    message="The prose uses analyst-fog phrases instead of plain investor-facing language.",
                    severity=1.8,
                )
            )

    deduped: List[EditorialFailure] = []
    seen: Set[Tuple[str, str, str]] = set()
    for failure in sorted(failures, key=lambda item: (-float(item.severity or 0.0), item.section_name, item.code)):
        key = (failure.section_name, failure.code, failure.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(failure)
    return deduped


def _format_editorial_failure_reason(
    section_name: str,
    failures: Sequence[EditorialFailure],
    plan: Optional[SectionPlan],
    thread_decision: ThreadDecision,
) -> str:
    lines = [
        f"Editorial rewrite required for {section_name}.",
        f"- Keep the validated memo thread centered on: {thread_decision.final_thread}",
    ]
    if plan is not None:
        lines.append(f"- Keep the section job: {plan.job}")
        if plan.owned_evidence:
            lines.append("- Re-anchor on owned evidence:")
            lines.extend(f"  - {item}" for item in list(plan.owned_evidence or [])[:4])
        if plan.instruction_checks:
            lines.append("- Section instructions that must remain satisfied:")
            lines.extend(
                f"  - {check.guidance}" for check in list(plan.instruction_checks or [])[:3]
            )
    lines.append("- Fix these failures without restating other sections:")
    lines.extend(
        f"  - [{failure.code}] {failure.message}"
        for failure in list(failures or [])[:6]
    )
    lines.append("- Use cleaner sentence openings, fewer hedges, and no repeated clause stems.")
    return "\n".join(lines)


def _run_editorial_section_rewrite_loop(
    *,
    section_bodies: Dict[str, str],
    include_health_rating: bool,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    section_budgets: Dict[str, int],
    depth_plan: Any,
    openai_client: Any,
    narrative_blueprint: NarrativeBlueprint,
    thread_decision: ThreadDecision,
    section_plans: Dict[str, SectionPlan],
    tone: str,
    detail_level: str,
    output_style: str,
    focus_areas: Sequence[str],
    investor_focus: Optional[str],
    section_instructions: Optional[Dict[str, str]] = None,
    max_rounds: int = 8,
) -> Tuple[str, List[Dict[str, Any]]]:
    working_bodies = dict(section_bodies or {})
    history: List[Dict[str, Any]] = []
    rewrite_counts: Dict[str, int] = {}
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    for _round in range(max(1, int(max_rounds or 1))):
        failures = _judge_sectioned_summary(
            section_bodies=working_bodies,
            include_health_rating=include_health_rating,
            thread_decision=thread_decision,
            section_plans=section_plans,
        )
        if not failures:
            break
        grouped: Dict[str, List[EditorialFailure]] = {}
        for failure in failures:
            if failure.section_name == "Key Metrics":
                continue
            grouped.setdefault(failure.section_name, []).append(failure)
        if not grouped:
            break
        target_section = max(
            grouped,
            key=lambda key: (
                max(float(item.severity or 0.0) for item in grouped[key]),
                -ordered_sections.index(key) if key in ordered_sections else 0,
            ),
        )
        if rewrite_counts.get(target_section, 0) >= 3:
            break
        rewrite_counts[target_section] = int(rewrite_counts.get(target_section, 0) or 0) + 1
        budget = int(section_budgets.get(target_section, 0) or 0)
        if budget <= 0:
            break
        prior_index = ordered_sections.index(target_section)
        prior_section_text = (
            working_bodies.get(ordered_sections[prior_index - 1], "")
            if prior_index > 0
            else ""
        )
        section_memory = _build_section_memory_from_bodies(
            working_bodies,
            filing_analysis,
        )
        failure_reason = _format_editorial_failure_reason(
            target_section,
            grouped[target_section],
            section_plans.get(target_section),
            thread_decision,
        )
        new_body = generate_section_body_to_budget(
            section_name=target_section,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=section_memory.used_claims,
            section_memory=section_memory,
            narrative_blueprint=narrative_blueprint,
            openai_client=openai_client,
            failure_reason=failure_reason,
            section_instructions=section_instructions,
            thread_decision=thread_decision,
            section_plan=section_plans.get(target_section),
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
        )
        if not new_body or new_body.strip() == str(working_bodies.get(target_section) or "").strip():
            break
        working_bodies[target_section] = new_body
        history.append(
            {
                "section_name": target_section,
                "failure_codes": [item.code for item in grouped[target_section]],
                "failure_messages": [item.message for item in grouped[target_section]],
            }
        )
    return (
        _assemble_summary_from_sections(
            working_bodies,
            include_health_rating=include_health_rating,
        ),
        history,
    )


def _build_section_prompt(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    budget: int,
    prior_section_text: str,
    used_claims: Optional[List[str]],
    section_memory: Optional[Any],
    narrative_blueprint: Optional[NarrativeBlueprint],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    depth_plan: Any,
    failure_reason: str = "",
    section_instructions: Optional[Dict[str, str]] = None,
    thread_decision: Optional[ThreadDecision] = None,
    section_plan: Optional[SectionPlan] = None,
    tone: str = "objective",
    detail_level: str = "balanced",
    output_style: str = "narrative",
    focus_areas: Optional[Sequence[str]] = None,
    investor_focus: Optional[str] = None,
) -> str:
    lower, upper = _section_budget_range(section_name, budget)
    narrative_blueprint = narrative_blueprint or _build_narrative_blueprint(
        company_name=company_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
    )
    section_blueprint = narrative_blueprint.section_blueprints.get(section_name)
    blueprint_parts = _format_section_blueprint(section_blueprint)
    thread_decision = thread_decision or ThreadDecision(
        final_thread=narrative_blueprint.memo_thread,
        anchor=narrative_blueprint.memo_thread,
        anchor_class="operating_driver",
        aha_insight=str((filing_analysis.period_specific_insights or [""])[0] or narrative_blueprint.memo_thread),
        support_evidence=[],
        rejected_threads=[],
    )
    if section_plan is None:
        section_plan = SectionPlan(
            section_name=section_name,
            job=blueprint_parts["job"],
            question=blueprint_parts["question"],
            owned_evidence=list(section_blueprint.primary_evidence or []) if section_blueprint else [],
            callback_evidence=list(section_blueprint.secondary_evidence or []) if section_blueprint else [],
            forbidden_themes=list(section_blueprint.banned_overlap or []) if section_blueprint else [],
            forbidden_openings=list(_REPEATED_LEADIN_STEMS),
            tone_mode=_tone_mode_for_preferences(tone, section_name),
            readability_mode=_readability_mode_for_preferences(detail_level, output_style),
            instruction_checks=_build_instruction_checks(
                section_name=section_name,
                instruction_text=str((section_instructions or {}).get(section_name) or ""),
            ),
        )
    memory = _coerce_section_memory(
        section_memory=section_memory,
        analysis=filing_analysis,
        used_claims=used_claims,
    )
    evidence_points = filing_analysis.evidence_map.get(section_name) or []
    quote_policy = _quote_policy_for_target_length(target_length)
    quotes = _quotes_for_section_with_fallback(filing_analysis, section_name)
    quote_lines = "\n".join(
        f'- "{quote.quote}" ({quote.attribution}, re: {quote.topic})'
        for quote in quotes[: quote_policy["max_total"]]
    ) or "(No pre-assigned quotes. Paraphrase management only if grounded in the filing.)"
    evidence_block = "\n".join(f"- {item}" for item in evidence_points[:5]) or "- Use the strongest filing-grounded evidence available for this section."
    prior_block = prior_section_text.strip() or "(This is the first section or there is no prior section text.)"
    used_claims_block = "\n".join(f"- {claim}" for claim in memory.used_claims[:5]) or "- No prior claims yet."
    section_memory_block = _format_section_memory(memory)
    depth_moves = "\n".join(f"- {move}" for move in _depth_moves_for_section(section_name, depth_plan))
    kpi_block = _format_section_kpi_findings(filing_analysis, section_name)
    period_insights_block = _format_section_period_insights(filing_analysis, section_name)
    company_terms_block = _format_company_terms(filing_analysis)
    expectations_block = _format_management_expectations(filing_analysis)
    promise_items_block = _format_promise_scorecard_items(filing_analysis)
    accepted_risks = _accepted_company_risks(filing_analysis) if section_name == "Risk Factors" else []
    risk_target = risk_budget_target_count(budget) if section_name == "Risk Factors" else 0
    plan_owned_evidence = "\n".join(
        f"- {item}" for item in list(section_plan.owned_evidence or [])[:5]
    ) or blueprint_parts["primary_evidence"]
    plan_callback_evidence = "\n".join(
        f"- {item}" for item in list(section_plan.callback_evidence or [])[:4]
    ) or blueprint_parts["secondary_evidence"]
    plan_forbidden_themes = "\n".join(
        f"- {item}" for item in list(section_plan.forbidden_themes or [])[:6]
    ) or blueprint_parts["banned_overlap"]
    instruction_checks_block = "\n".join(
        f"- {check.check_type}: {check.guidance}"
        for check in list(section_plan.instruction_checks or [])
    ) or "- No extra section instruction checks."
    forbidden_openings_block = "\n".join(
        f"- {item}" for item in list(section_plan.forbidden_openings or [])[:8]
    ) or "- Avoid repeated rhetorical stems from earlier sections."
    focus_areas_block = "\n".join(
        f"- {item}" for item in list(focus_areas or [])[:4]
    ) or "- No special focus areas supplied."
    decisive_watch_metrics_block = "\n".join(
        f"- {item}" for item in list(filing_analysis.decisive_watch_metrics or [])[:3]
    ) or "- No explicit decisive watch metrics extracted."
    style_contract = (
        "STYLE CONTRACT:\n"
        f"- Tone mode: {section_plan.tone_mode}\n"
        f"- Readability mode: {section_plan.readability_mode}\n"
        "- Keep prose clear, direct, and conversational — like a sharp analyst explaining over coffee. Evidence-first, occasionally blunt, never stiff or hedging.\n"
        "- Do not convert the section into bullets even if the global output style is mixed.\n\n"
    )
    if section_name != "Closing Takeaway":
        style_contract += (
            "- Do not use explicit BUY, HOLD, or SELL language outside Closing Takeaway.\n\n"
        )
    risk_lines = (
        "\n".join(
            f"- {risk.risk_name} [{risk.source_section or 'Risk Factors'}]: {risk.source_quote or risk.evidence_from_filing}"
            for risk in accepted_risks[:3]
        )
        if accepted_risks
        else "- No accepted source-backed risks are available. Do not invent generic or archetype-template risks."
    )
    health_block = ""
    if section_name == "Financial Health Rating" and health_score_data:
        score = health_score_data.get("overall_score")
        band = health_score_data.get("score_band")
        if score is not None:
            health_block = (
                f"\nPRE-CALCULATED HEALTH SCORE:\n"
                f"- Use this exact opening: {float(score):.0f}/100 - {band or ''}.\n"
                f"- Do not compute a different score.\n"
            )

    key_metrics_instruction = ""
    if section_name == "Key Metrics":
        key_metrics_instruction = (
            "\nReturn only arrow-format metric lines. No prose paragraphs. "
            "Prepend 2-4 company-specific operating KPI rows when they are available, then include the core financial rows. "
            "Do not add analysis, bullets, or a heading."
        )
    section_contract = ""
    if section_name == "Financial Health Rating":
        shape = get_financial_health_shape(budget)
        section_contract = (
            "FINANCIAL HEALTH RATING CONTRACT:\n"
            f"- Write {describe_sentence_range(shape.min_sentences, shape.max_sentences)} across "
            f"{describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs)}.\n"
            "- Start with the exact pre-calculated score line.\n"
            "- Explain the score through business-model-specific cash conversion, capital intensity, funding, reserve, or working-capital dynamics.\n"
            "- Avoid generic margin/liquidity boilerplate that could apply to any company.\n"
            "- End by bridging into Executive Summary.\n"
            "- Do not stop after the opening score line.\n\n"
        )
    elif section_name == "Risk Factors":
        shape = get_risk_factors_shape(budget)
        required_risks = int(shape.risk_count or risk_target or len(accepted_risks) or 1)
        available_risks = min(required_risks, len(accepted_risks)) if accepted_risks else required_risks
        per_risk_target = max(18, budget // max(1, available_risks))
        per_risk_tolerance = max(8, int(round(per_risk_target * 0.12)))
        section_contract = (
            f"RISK FACTORS CONTRACT:\n"
            f"- Write up to {required_risks} risks from the accepted source-backed set below.\n"
            f"- Each risk should land around {per_risk_target} words (allowed variance ±{per_risk_tolerance}).\n"
            f"- Format each as Risk Name: followed by "
            f"{describe_sentence_range(int(shape.per_risk_min_sentences or 2), int(shape.per_risk_max_sentences or 3))}.\n"
            f"- Rank risks by probability first, then magnitude.\n"
            f"- Each risk must explain what could go wrong, why it matters for this company, and what investors should watch.\n"
            f"- Write natural prose; do not force separate mechanism/impact/signal slots.\n"
            f"- Every risk name must identify a real company-specific exposure rather than a symptom like margin pressure or liquidity risk.\n"
            f"- Risk names must come from the accepted source-backed risk list below. Do not invent replacements or archetype-template labels.\n"
            f"- {available_risks} accepted source-backed risk(s) are currently available for this section budget.\n"
            f"- If fewer than {required_risks} accepted source-backed risks are available, do not synthesize new ones or pad with weaker placeholders.\n"
            f"- If the budget is large, expand each risk with company-specific narrative mass instead of compressing it into one mini-paragraph.\n"
            f"- No merged risks. No generic macro filler.\n"
            f"- Accepted source-backed risks:\n{risk_lines}\n\n"
        )
    elif section_name == "Closing Takeaway":
        shape = get_closing_takeaway_shape(budget)
        section_contract = (
            "CLOSING TAKEAWAY CONTRACT:\n"
            f"- Write {describe_sentence_range(shape.min_sentences, shape.max_sentences)} across "
            f"{describe_paragraph_range(shape.min_paragraphs, shape.max_paragraphs, short=True)}.\n"
            "- State exactly one stance.\n"
        )
        if budget < 120:
            section_contract += "- Include one measurable trigger.\n\n"
        else:
            section_contract += (
                "- Include the verdict, one 'what must stay true' trigger, one 'what breaks the thesis' trigger, "
                "and one implication for capital allocation, cash generation, or valuation support.\n\n"
            )

    section_focus_instruction = ""
    if section_name == "Executive Summary":
        section_focus_instruction = (
            "SECTION FOCUS:\n"
            "- Open with management's actual message for this filing period, then anchor what the company does, how it makes money, and why its moat matters now.\n"
            "- State what changed in this filing before discussing numbers.\n"
            "- Use management tone, expectations, or guidance to frame what happens next.\n"
            "- Prefer company-specific KPIs over generic Revenue/EPS when choosing evidence.\n"
            "- State the memo thread once here; later sections should answer it, not restate it.\n"
            "- Do not do a full capex, cash-conversion, or risk walkthrough in this section.\n"
            "- Reuse the company terms below so the section reads like it came from this filing, not a template.\n"
            "- Use no more than one anchor figure if target length is under 800 words; otherwise cap at two.\n"
            "- Never open with a templated metric sentence like 'Revenue of X produced Y'. Start with the thesis or the real surprise.\n"
            "- End with a subtle handoff to the operating proof the quarter must show; do not say 'the next section'.\n"
            "- QUOTE RULE: Use one verbatim management quote within the first 3 sentences only if it directly supports strategy, outlook, or what happens next; otherwise use attributed paraphrase.\n"
            "- OPENING CONTRACT: Within the first 2 sentences, state the main takeaway, the non-obvious report insight, and the single proof point investors should watch next.\n"
            "- SURPRISE LEAD: If the filing contains a genuine surprise (deviation from "
            "guidance, inflection point, new disclosure), lead with it. Do not bury the "
            "lede under a generic company description.\n"
            "- ANALYTICAL STANCE: State what the filing MEANS for the investment case, "
            "not just what it SAYS. A paraphrase is not analysis.\n"
            "- DECISION FRAMING: Open the very first sentence with the single most important "
            "takeaway. The reader should know the verdict within 2 sentences.\n"
            "- AHA INSIGHT: The filing's non-obvious insight (from AHA INSIGHT above) "
            "must appear explicitly in the first 2 paragraphs, not buried at the end.\n\n"
        )
    elif section_name == "Financial Performance":
        section_focus_instruction = (
            "SECTION FOCUS:\n"
            "- Use the 1-2 metrics that best test the thesis, prioritizing company-specific KPI findings.\n"
            "- Use the decisive watch metrics block below to choose the 1-2 metrics that matter most.\n"
            "- Use generic financial metrics only when they are the true driver of the current view.\n"
            "- Explicitly say whether the numbers confirm or challenge management's expectations or commitments.\n"
            "- Tell the reader what changed in those metrics and why that changes the current read.\n"
            "- Prefer metrics tied to specific products, segments, customer cohorts, geographies, or programs named in the filing.\n"
            "- HARD CAP: Keep this section to 2-3 interpreted metrics. If you need a fourth figure, move it to Key Metrics.\n"
            "- REDUNDANCY CUT: Never restate the same point in different words. If margin strength "
            "is funding investment, say it once — do not rephrase as 'cash generation supporting buildout' "
            "or 'operating leverage enabling reinvestment.' Each metric gets ONE interpretation.\n"
            "- METRICS CAP: If you have written interpretations for 3 metrics, stop. Additional metrics "
            "belong in Key Metrics, not here.\n"
            "- No strategy recap and no long management monologue here.\n"
            "- End with a subtle shift toward the management decision or execution question this performance now raises.\n"
            "- FINAL SENTENCE CONTRACT: Name the metric, threshold, checkpoint, or timeline that will answer that question first.\n\n"
        )
    elif section_name == "Management Discussion & Analysis":
        bets_block = ""
        if filing_analysis.management_strategic_bets:
            bets = "\n".join(f"  - {bet}" for bet in filing_analysis.management_strategic_bets[:3])
            bets_block = f"- MANAGEMENT'S KEY STRATEGIC BETS (use these):\n{bets}\n"
        guidance_block = ""
        if filing_analysis.forward_guidance_summary:
            guidance_block = f"- FORWARD GUIDANCE: {filing_analysis.forward_guidance_summary}\n"
        promise_block = ""
        if filing_analysis.promise_scorecard:
            promise_block = f"- PROMISE SCORECARD: {filing_analysis.promise_scorecard}\n"
        section_focus_instruction = (
            "SECTION FOCUS:\n"
            "- LEAD with management's stated strategy or priorities BEFORE any metrics.\n"
            "- Center the section on management choices, stated priorities, pricing, investment, product, segment, or capital-allocation actions.\n"
            "- Use period-specific insights plus the management strategy summary, not just the raw MD&A excerpt.\n"
            "- Explicitly state what management expects, what management is prioritizing, and what management is trying to achieve next.\n"
            "- Say what management thinks is likely to happen next, using the expectations block below.\n"
            "- Assess whether management delivered on prior promises or guidance, using the promise scorecard items below.\n"
            "- Every strategy claim must be supported by a direct quote or clear management attribution.\n"
            "- Use direct quotes only when they add strategic or forward-looking context and fit the quality-gated quote allowance; otherwise use clear management attribution.\n"
            "- Do not open with revenue, operating income, or a cash-flow recap.\n"
            "- This section owns mechanism, intent, and credibility; it is not a second Financial Performance section.\n"
            "- CREDIBILITY JUDGMENT: Explicitly assess whether management's strategy "
            "is working or not, based on the numbers. Do not just report what management "
            "says — judge whether the data supports it.\n"
            "- FORWARD-LOOKING EDGE: Identify what the MARKET might be missing about "
            "management's strategy. What would change if the market fully understood "
            "this filing?\n"
            "- CITATION MANDATE: Every claim about management's strategy must include a direct quote or attributed paraphrase: 'Management noted that \"[quote]\"' or 'Management characterized [topic] as [paraphrase].'\n"
            "- FINAL SENTENCE CONTRACT: End on the trigger, metric, or dated checkpoint that would first show the strategy is slipping.\n"
            f"{bets_block}"
            f"{guidance_block}"
            f"{promise_block}\n"
        )
    elif section_name == "Risk Factors":
        section_focus_instruction = (
            "SECTION FOCUS:\n"
            "- Build risks from the candidate company-specific risk list below before inventing any new risk framing.\n"
            "- RISK ≠ METRIC: A risk is a BUSINESS EVENT (customer loss, regulation change, competitor "
            "launch, supply disruption, patent expiration, contract non-renewal). Financial figures "
            "(operating margin %, FCF, cash balance) are evidence you cite INSIDE a risk body, NOT the risk itself.\n"
            "- Do NOT start any risk body with a financial number or metric recap. Start with the business event.\n"
            "- REJECTED NAMES: 'Cost-to-Serve Risk', 'Asset Deployment Risk', 'Pricing Pressure Risk', "
            "'Conversion Timing Risk', 'Cybersecurity Risk', or ANY name built from financial metrics. "
            "These WILL fail validation and waste a retry.\n"
            "- Reuse the company terms below inside risk names and mechanisms so the risks are unmistakably filing-specific.\n"
            "- Tie each risk to a concrete product, segment, geography, customer class, regulation, supply/input, or funding mechanism.\n"
            "- Do not re-summarize the quarter or reuse the same explanatory paragraph from earlier sections.\n"
            "- CITATION MANDATE: Each risk body must include close filing attribution and may use a direct quote only if it sharpens a concrete business exposure. Paraphrase is preferred to legal boilerplate.\n"
            "- MATERIALITY FILTER: Write ONLY risks that would make a portfolio manager "
            "change their position size. If a risk has appeared in every filing for years "
            "without triggering, it is not a real risk.\n"
            "- PROBABILITY ANCHOR: Favor risks with a concrete trigger mechanism "
            "and plausible timeline. 'If regulatory environment changes' is too vague. "
            "'The DOJ antitrust trial ruling expected Q2 2026 could mandate structural remedies' is specific.\n"
            "- ASYMMETRY TEST: Prioritize risks where the downside is larger than what is "
            "currently priced in. Skip symmetric or already-known risks.\n"
            "- DE-PRIORITIZE GENERIC COMPLIANCE: Supplier-code, anti-corruption, code-of-conduct, transfer-restriction, foreign-registry, or anti-takeover risks should stay below real operating risks unless the filing ties them to a near-term investigation, ruling, deadline, transaction, or enforcement path.\n"
            "- TIMELINE REQUIREMENT: Each risk must state a specific timeline or catalyst — "
            "'within the next 2 quarters,' 'if the Q3 contract renewal fails,' 'before the "
            "fiscal year-end pricing reset.' Risks without timelines are too abstract.\n"
            "- P&L IMPACT: Each risk must name the specific P&L line item affected and an "
            "approximate magnitude or direction. 'Could pressure margins' is too vague. "
            "'Could compress gross margin by 100-200bp if [trigger]' is specific.\n\n"
        )
    elif section_name == "Closing Takeaway":
        ct_promise_block = ""
        if filing_analysis.promise_scorecard:
            ct_promise_block = f"- MANAGEMENT CREDIBILITY: {filing_analysis.promise_scorecard}\n"
        section_focus_instruction = (
            "SECTION FOCUS:\n"
            "- Synthesize current state, management credibility, and the forward setup.\n"
            "- Connect verdict to whether management has earned trust through execution on prior commitments.\n"
            "- Use at least one management expectation or promise-scorecard item so the verdict is tied to this filing's commitments.\n"
            "- Do not recap the same numbers already used above.\n"
            "- Resolve the memo with one sharp implication the reader would not get from a generic recap.\n"
            "- Use one decisive watch metric or operating checkpoint in the must-stay-true / thesis-break framing.\n"
            "- Make the must-hold and thesis-break triggers specific to this company's operating model.\n"
            "- Resolve the memo thread with credibility and proof points, not another performance recap.\n"
            "- Do not justify the verdict with a generic cash-versus-liabilities line unless that balance-sheet tension was a real driver of the memo above.\n"
            "- FINAL SENTENCE CONTRACT: End with the single measurable trigger that changes the stance first.\n"
            f"{ct_promise_block}\n"
        )

    return (
        f"Write ONLY the body of the '{section_name}' section for {company_name}.\n\n"
        f"BODY WORD BUDGET:\n"
        f"- Target {budget} body words.\n"
        f"- Allowed range {lower}-{upper} body words.\n"
        f"- Count body words only; do not include a heading.\n"
        f"- Never pad with filler or repetition.\n"
        f"- Never repeat the section title inside the body.\n"
        f"- Never add extra sections or inline headers.\n"
        f"- End on complete sentences only; no dangling clauses or mid-thought cutoffs.\n"
        f"{key_metrics_instruction}\n\n"
        f"CENTRAL TENSION:\n{filing_analysis.central_tension}\n\n"
        f"VALIDATED MEMO THREAD:\n{thread_decision.final_thread}\n\n"
        f"THREAD ANCHOR CLASS:\n{thread_decision.anchor_class} — anchor: {thread_decision.anchor}\n\n"
        f"AHA INSIGHT (MUST SURFACE IN OUTPUT):\n"
        f"{thread_decision.aha_insight}\n"
        f"This is the non-obvious thing the filing reveals. Weave it into the most "
        f"relevant section — it must appear as an explicit claim in the output prose, "
        f"not just implied. The reader should think 'I would not have known that.'\n\n"
        f"SECTION JOB:\n{section_plan.job}\n\n"
        f"SECTION QUESTION TO ANSWER:\n{section_plan.question}\n\n"
        f"QUOTE POLICY:\n"
        f"- Direct quotes are optional and quality-gated for this memo. Use at most {quote_policy['max_total']} total direct quote(s).\n"
        f"- Only keep a direct quote if it materially sharpens strategy, outlook, or the next operating checkpoint.\n"
        f"- If a quote feels legal, tax, accounting, governance, or otherwise low-signal, delete it and use management attribution instead.\n\n"
        f"PRIOR SECTION BRIDGE CONTEXT:\n{prior_block}\n\n"
        f"USED CLAIMS TO AVOID RESTATING:\n{used_claims_block}\n\n"
        f"EARLIER SECTION MEMORY:\n{section_memory_block}\n\n"
        f"PRIMARY EVIDENCE THIS SECTION OWNS:\n{plan_owned_evidence}\n\n"
        f"SECONDARY CALLBACK EVIDENCE ONLY:\n{plan_callback_evidence}\n\n"
        f"BANNED OVERLAP FOR THIS SECTION:\n{plan_forbidden_themes}\n\n"
        f"FORBIDDEN OPENINGS:\n{forbidden_openings_block}\n\n"
        f"SECTION INSTRUCTION CHECKS:\n{instruction_checks_block}\n\n"
        f"SUBTLE HANDOFF INSTRUCTION:\n{blueprint_parts['subtle_handoff']}\n\n"
        f"SECTION EVIDENCE TO PRIORITIZE:\n{evidence_block}\n\n"
        f"KPI FINDINGS TO PRIORITIZE:\n{kpi_block}\n\n"
        f"FILING-PERIOD INSIGHTS TO USE:\n{period_insights_block}\n\n"
        f"FOCUS AREAS TO RESPECT:\n{focus_areas_block}\n\n"
        f"DECISIVE WATCH METRICS:\n{decisive_watch_metrics_block}\n\n"
        f"GLOBAL INVESTOR FOCUS:\n{investor_focus or '(None supplied)'}\n\n"
        f"COMPANY TERMS TO REUSE:\n{company_terms_block}\n\n"
        f"MANAGEMENT EXPECTATIONS TO USE:\n{expectations_block}\n\n"
        f"PROMISE SCORECARD ITEMS TO USE:\n{promise_items_block}\n\n"
        f"AVAILABLE QUOTES:\n{quote_lines}\n\n"
        f"DEPTH MOVES:\n{depth_moves}\n"
        f"{style_contract}"
        f"{health_block}\n"
        f"COMPANY CONTEXT:\n"
        f"- Business archetype: {getattr(company_intelligence, 'business_archetype', '') or 'diversified_other'}\n"
        f"- Business: {company_intelligence.business_identity or '(Not available)'}\n"
        f"- Moat: {company_intelligence.competitive_moat or '(Not available)'}\n"
        f"- Competitive dynamics: {company_intelligence.competitive_dynamics or '(Not available)'}\n"
        f"- Management strategy summary: {filing_analysis.management_strategy_summary or '(Not available)'}\n"
        f"- Financial snapshot: {financial_snapshot or '(Not available)'}\n"
        f"- Metrics block available elsewhere: {'yes' if metrics_lines.strip() else 'no'}\n"
        f"- Filing target length: {target_length or 0}\n\n"
        + section_focus_instruction
        + (
            (
                f"USER INSTRUCTION FOR THIS SECTION (absolute priority — follow this "
                f"before any other guidance):\n"
                f"{section_instructions[section_name].strip()}\n\n"
            )
            if section_instructions and section_name in section_instructions
            and section_instructions[section_name].strip()
            else ""
        )
        + (
            "EDITORIAL ANCHOR RULES:\n"
            "- Every narrative section must use concrete company nouns or phrases from COMPANY TERMS TO REUSE.\n"
            "- If a sentence could apply to another large-cap company without changing any nouns, rewrite it.\n"
            "- Use management expectations and promise scorecard items before falling back to abstract balance-sheet or margin commentary.\n"
            "- Financial Performance should test management's claims; MD&A should explain management's plan; Closing should judge management's credibility.\n"
            "- The same anchor or theme can appear in at most two narrative sections, and only one section may fully explain it.\n"
            "- If EARLIER SECTION MEMORY already used a theme, mention it only as a brief callback unless this section clearly owns it.\n"
            "- Favor subtle handoffs over explicit 'the next section' phrasing.\n\n"
            "- Do not use repeated lead-ins such as 'That leaves', 'This leaves', 'What matters now', or 'The next question is'.\n\n"
        )
        + section_contract
        + (
            f"KEY METRICS BLOCK TO COPY FROM:\n{_build_key_metrics_body(metrics_lines=metrics_lines, analysis=filing_analysis, max_words=budget) or _fallback_key_metrics_from_kpis(filing_analysis) or '(No deterministic metrics available)'}\n\n"
            if section_name == "Key Metrics"
            else ""
        )
        + (
            f"REPAIR INSTRUCTION:\n{failure_reason}\n"
            f"Introduce new analytical content or compress; do not pad and do not restate earlier sections.\n"
            f"If this section is underweight, first expand with unused company terms, management expectations, promise scorecard items, strategic bets, or filing-grounded evidence.\n"
            f"If you reuse a previously mentioned theme, do it as a short callback or trigger rather than a second full explanation.\n"
            f"Do not add generic finance axioms, textbook liquidity commentary, or reusable big-tech prose.\n\n"
            if failure_reason
            else ""
        )
        + "Return only the section body."
    )


def _generate_section_body(
    *,
    section_name: str,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    budget: int,
    depth_plan: Any,
    prior_section_text: str,
    used_claims: Optional[List[str]],
    section_memory: Optional[Any],
    narrative_blueprint: Optional[NarrativeBlueprint],
    openai_client: Any,
    failure_reason: str = "",
    section_instructions: Optional[Dict[str, str]] = None,
    thread_decision: Optional[ThreadDecision] = None,
    section_plan: Optional[SectionPlan] = None,
    tone: str = "objective",
    detail_level: str = "balanced",
    output_style: str = "narrative",
    focus_areas: Optional[Sequence[str]] = None,
    investor_focus: Optional[str] = None,
) -> str:
    if section_name == "Key Metrics":
        body = _build_key_metrics_body(
            metrics_lines=metrics_lines,
            analysis=filing_analysis,
            max_words=budget,
        ) or _fallback_key_metrics_from_kpis(filing_analysis)
        return _normalize_section_body(section_name, body)

    prompt = _build_section_prompt(
        section_name=section_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        company_name=company_name,
        target_length=target_length,
        budget=budget,
        prior_section_text=prior_section_text,
        used_claims=used_claims,
        section_memory=section_memory,
        narrative_blueprint=narrative_blueprint,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        health_score_data=health_score_data,
        depth_plan=depth_plan,
        failure_reason=failure_reason,
        section_instructions=section_instructions,
        thread_decision=thread_decision,
        section_plan=section_plan,
        tone=tone,
        detail_level=detail_level,
        output_style=output_style,
        focus_areas=focus_areas,
        investor_focus=investor_focus,
    )
    raw = openai_client.compose_summary(
        prompt=prompt,
        system_message=(
            f"Write the {section_name} section body only. "
            "Use complete sentences, no headings, no bullets in narrative sections, and no padding."
        ),
        max_output_tokens=min(2500, max(300, int(budget * 3))),
        temperature=0.35,
        timeout_seconds=45.0,
    )
    return _normalize_section_body(section_name, raw)


def _build_per_section_instructions(
    *,
    analysis: FilingAnalysis,
    intelligence: CompanyIntelligenceProfile,
    section_budgets: Dict[str, int],
    include_health_rating: bool,
    health_score_data: Optional[Dict[str, Any]],
    persona_name: Optional[str],
    persona_requested: bool,
    company_name: str,
    industry: str,
) -> str:
    """Build section-by-section instructions with company-specific evidence."""
    sections_list = list(SECTION_ORDER)
    if not include_health_rating:
        sections_list = [s for s in sections_list if s != "Financial Health Rating"]

    instructions = []
    for section_name in sections_list:
        template = SECTION_TEMPLATES.get(section_name)
        if not template:
            continue

        budget = section_budgets.get(section_name, 0)
        budget_line = f"Target: ~{budget} words." if budget > 0 else ""

        # Evidence from Agent 2's evidence map
        evidence = analysis.evidence_map.get(section_name, [])
        evidence_block = (
            "\n".join(f"  - {e}" for e in evidence) if evidence else "  (use filing data)"
        )

        # Quotes assigned to this section
        section_quotes = [
            q
            for q in analysis.management_quotes
            if q.suggested_section == section_name
        ]
        quotes_block = (
            "\n".join(
                f'  - "{q.quote}" ({q.attribution}, re: {q.topic})'
                for q in section_quotes
            )
            if section_quotes
            else "  (no pre-assigned quotes)"
        )

        # Health-specific instructions
        health_block = ""
        if section_name == "Financial Health Rating" and health_score_data:
            score = health_score_data.get("overall_score")
            band = health_score_data.get("score_band")
            if score is not None:
                health_block = (
                    f"\nPRE-CALCULATED SCORE: {score}/100 — {band or ''}\n"
                    f"Use this exact score. Do NOT compute your own.\n"
                )

        # Persona instruction for Closing Takeaway
        persona_block = ""
        if section_name == "Closing Takeaway":
            if persona_name:
                persona_block = (
                    f"\nWrite with subtle {persona_name}-aligned framing for "
                    f"{company_name}. Avoid imitation catchphrases."
                )
            elif persona_requested:
                persona_block = (
                    "\nWrite in first person using the selected persona lens."
                )

        do_rules = ", ".join(template.do_rules[:3])
        dont_rules = ", ".join(template.dont_rules[:3])

        instructions.append(
            f"## {section_name} ({budget_line})\n"
            f"{template.system_guidance}\n"
            f"{health_block}"
            f"\nKey evidence for this section:\n{evidence_block}\n"
            f"\nAvailable quotes:\n{quotes_block}\n"
            f"{persona_block}\n"
            f"DO: {do_rules}\n"
            f"DON'T: {dont_rules}\n"
            f"Max numeric density: {template.max_numeric_density} per 100 words.\n"
            f"Transition OUT: {template.transition_out}"
        )

    return "\n\n".join(instructions)


AGENT_3_USER_PROMPT_TEMPLATE = """\
Write the investment memo for {company_name} ({filing_type}, {filing_period}).

=== COMPANY INTELLIGENCE ===
Business: {business_identity}
Competitive Moat: {competitive_moat}
Industry: {industry}

PRIMARY KPIs FOR {company_name} (USE THESE, not generic metrics):
{formatted_kpi_findings}

=== FILING ANALYSIS ===
Central Tension: {central_tension}
Supporting Evidence: {tension_evidence}

Period-Specific Insights (unique to this filing):
{formatted_period_insights}

Management Strategy: {management_strategy_summary}

Management Quotes (use the budget-aware quote allowance for this memo):
{formatted_management_quotes}

Company-Specific Risks:
{formatted_risks}

=== EVIDENCE MAP (which data goes where) ===
{formatted_evidence_map}

=== SUPPLEMENTARY FINANCIAL DATA ===
{financial_snapshot}

=== PRE-FORMATTED KEY METRICS (copy these EXACT lines into Key Metrics section) ===
{metrics_lines}
NOTE: These are ready to use. Copy them directly as → MetricName: Value lines.
Do NOT rewrite them as prose paragraphs.

{health_score_block}

=== SECTION STRUCTURE AND BUDGETS ===
Write these sections in this exact order using ## headers:

{per_section_instructions}

=== WORD COUNT CONTRACT ===
{word_count_contract}

=== FORMAT REQUIREMENTS ===
- Use ## headers for each section in the specified order
- Use billions as "$X.XB", millions as "$X.XM"
- Specify fiscal period with figures (FY24, Q3 FY25)
- Every sentence must end with a complete thought
- Do not echo these instructions

{persona_instruction}

{anti_boredom_rules}
{quote_behavior_spec}

FINAL QUALITY CONTRACT:
- Every section must advance the central tension: "{central_tension}"
- If a paragraph does not move the argument forward, cut it.
- Closing Takeaway must include a clear BUY/HOLD/SELL stance and ONE measurable trigger.
- The memo should read like a single coherent argument, not independent section reports.
"""


def _build_word_count_contract(
    target_length: Optional[int], section_budgets: Dict[str, int]
) -> str:
    """Build the word count contract block for Agent 3."""
    if not target_length:
        return "Keep concise; prioritize substance over length."

    tolerance = total_word_tolerance_words(target_length)
    lower = max(1, int(target_length) - int(tolerance))
    upper = int(target_length) + int(tolerance)

    budget_lines = "\n".join(
        (
            f"- {section}: target {words} body words "
            f"(allowed range {_section_budget_range(section, words)[0]}-{_section_budget_range(section, words)[1]})"
        )
        for section, words in section_budgets.items()
        if words > 0
    )

    return (
        f"Total: approximately {target_length} words (allowed range {lower}-{upper}).\n"
        f"Section budgets:\n{budget_lines}\n\n"
        f"Count section BODY words only; markdown headings do not count. "
        f"Stay within the band without padding or repetition."
    )


def _build_persona_instruction(
    persona_name: Optional[str], persona_requested: bool, company_name: str
) -> str:
    """Build persona instruction for Agent 3."""
    if persona_requested and persona_name:
        return (
            f"Write with subtle {persona_name}-aligned framing for "
            f"{company_name}. Avoid imitation catchphrases, role-play "
            f"theatrics, and investor name-dropping."
        )
    if persona_requested:
        return "Write in first person using the selected persona lens, but keep the voice institutional."
    return f"Write in neutral third-person analyst voice focused on {company_name}."


def _run_agent_3(
    *,
    company_intelligence: CompanyIntelligenceProfile,
    filing_analysis: FilingAnalysis,
    company_name: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    target_length: Optional[int],
    financial_snapshot: str,
    metrics_lines: str,
    health_score_data: Optional[Dict[str, Any]],
    include_health_rating: bool,
    section_budgets: Dict[str, int],
    persona_name: Optional[str],
    persona_requested: bool,
    investor_focus: Optional[str],
    industry: str,
    openai_client: Any,
    preferences: Any = None,
    section_instructions: Optional[Dict[str, str]] = None,
) -> Tuple[str, int, PostProcessResult, Dict[str, str], ThreadDecision, Dict[str, SectionPlan], List[Dict[str, Any]]]:
    """Run Agent 3 — section-by-section summary composition."""
    ordered_sections = [
        section_name
        for section_name in SECTION_ORDER
        if include_health_rating or section_name != "Financial Health Rating"
    ]
    tone, detail_level, output_style, focus_areas = _normalize_style_preferences(
        preferences
    )
    depth_plan = compute_depth_plan(compute_scale_factor(target_length or 300))
    narrative_blueprint = _build_narrative_blueprint(
        company_name=company_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
    )
    thread_decision = _arbitrate_thread(
        company_name=company_name,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        focus_areas=focus_areas,
        investor_focus=investor_focus,
        section_instructions=section_instructions,
    )
    section_plans = _build_section_plans(
        narrative_blueprint=narrative_blueprint,
        thread_decision=thread_decision,
        tone=tone,
        detail_level=detail_level,
        output_style=output_style,
        section_instructions=section_instructions,
    )
    section_bodies: Dict[str, str] = {}
    section_prompts: Dict[str, str] = {}
    editorial_failure_history: List[Dict[str, Any]] = []

    try:
        for section_name in ordered_sections:
            budget = int(section_budgets.get(section_name, 0) or 0)
            if budget <= 0:
                continue
            section_memory = _build_section_memory_from_bodies(
                section_bodies,
                filing_analysis,
            )
            section_prompts[section_name] = _build_section_prompt(
                section_name=section_name,
                company_intelligence=company_intelligence,
                filing_analysis=filing_analysis,
                company_name=company_name,
                target_length=target_length,
                budget=budget,
                prior_section_text=(
                    section_bodies.get(ordered_sections[ordered_sections.index(section_name) - 1], "")
                    if ordered_sections.index(section_name) > 0
                    else ""
                ),
                used_claims=section_memory.used_claims,
                section_memory=section_memory,
                narrative_blueprint=narrative_blueprint,
                financial_snapshot=financial_snapshot,
                metrics_lines=metrics_lines,
                health_score_data=health_score_data,
                depth_plan=depth_plan,
                failure_reason="",
                section_instructions=section_instructions,
                thread_decision=thread_decision,
                section_plan=section_plans.get(section_name),
                tone=tone,
                detail_level=detail_level,
                output_style=output_style,
                focus_areas=focus_areas,
                investor_focus=investor_focus,
            )
            section_bodies[section_name] = generate_section_body_to_budget(
                section_name=section_name,
                company_intelligence=company_intelligence,
                filing_analysis=filing_analysis,
                company_name=company_name,
                target_length=target_length,
                financial_snapshot=financial_snapshot,
                metrics_lines=metrics_lines,
                health_score_data=health_score_data,
                budget=budget,
                depth_plan=depth_plan,
                prior_section_text=(
                    section_bodies.get(ordered_sections[ordered_sections.index(section_name) - 1], "")
                    if ordered_sections.index(section_name) > 0
                    else ""
                ),
                used_claims=section_memory.used_claims,
                section_memory=section_memory,
                narrative_blueprint=narrative_blueprint,
                openai_client=openai_client,
                section_instructions=section_instructions,
                thread_decision=thread_decision,
                section_plan=section_plans.get(section_name),
                tone=tone,
                detail_level=detail_level,
                output_style=output_style,
                focus_areas=focus_areas,
                investor_focus=investor_focus,
            )
    except Exception as exc:
        logger.error("Agent 3 failed for %s: %s", company_name, exc)
        raise

    summary_text = _assemble_summary_from_sections(
        section_bodies,
        include_health_rating=include_health_rating,
    )

    section_retry_counts: Dict[str, int] = {}

    def _regenerate_section(
        *,
        section_name: str,
        budget: int,
        failure_reason: str,
        prior_section_text: str,
        existing_section_text: str,
        used_claims: List[str],
        section_memory: Optional[Any] = None,
    ) -> str:
        retry_count = int(section_retry_counts.get(section_name, 0) or 0)
        if retry_count >= 3:
            return existing_section_text
        section_retry_counts[section_name] = retry_count + 1
        return generate_section_body_to_budget(
            section_name=section_name,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            budget=budget,
            depth_plan=depth_plan,
            prior_section_text=prior_section_text,
            used_claims=used_claims,
            section_memory=section_memory,
            narrative_blueprint=narrative_blueprint,
            openai_client=openai_client,
            failure_reason=failure_reason,
            section_instructions=section_instructions,
            thread_decision=thread_decision,
            section_plan=section_plans.get(section_name),
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
        )

    if not target_length:
        empty_report = PostProcessResult(text=summary_text, passed=True, retries=0)
        summary_text, editorial_failure_history = _run_editorial_section_rewrite_loop(
            section_bodies=section_bodies,
            include_health_rating=include_health_rating,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            section_budgets=section_budgets,
            depth_plan=depth_plan,
            openai_client=openai_client,
            narrative_blueprint=narrative_blueprint,
            thread_decision=thread_decision,
            section_plans=section_plans,
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
            section_instructions=section_instructions,
        )
        empty_report.text = summary_text
        return summary_text, 0, empty_report, section_prompts, thread_decision, section_plans, editorial_failure_history

    processed = post_process_summary(
        summary_text,
        target_words=int(target_length),
        section_budgets=section_budgets,
        include_health_rating=include_health_rating,
        risk_factors_excerpt=" ".join(
            _risk_source_evidence_line(risk)
            for risk in filing_analysis.company_specific_risks
            if _risk_source_evidence_line(risk)
        ),
        max_retries=12,
        max_retries_per_section=3,
        regenerate_section_fn=_regenerate_section,
    )
    final_text = processed.text or summary_text
    if final_text:
        final_text, editorial_failure_history = _run_editorial_section_rewrite_loop(
            section_bodies=_extract_section_bodies_from_summary(
                final_text,
                include_health_rating=include_health_rating,
            ),
            include_health_rating=include_health_rating,
            company_intelligence=company_intelligence,
            filing_analysis=filing_analysis,
            company_name=company_name,
            target_length=target_length,
            financial_snapshot=financial_snapshot,
            metrics_lines=metrics_lines,
            health_score_data=health_score_data,
            section_budgets=section_budgets,
            depth_plan=depth_plan,
            openai_client=openai_client,
            narrative_blueprint=narrative_blueprint,
            thread_decision=thread_decision,
            section_plans=section_plans,
            tone=tone,
            detail_level=detail_level,
            output_style=output_style,
            focus_areas=focus_areas,
            investor_focus=investor_focus,
            section_instructions=section_instructions,
        )
        processed.text = final_text
        processed.validation_report = validate_summary(
            final_text,
            target_words=int(target_length),
            section_budgets=section_budgets,
            include_health_rating=include_health_rating,
            risk_factors_excerpt=" ".join(
                _risk_source_evidence_line(risk)
                for risk in filing_analysis.company_specific_risks
                if _risk_source_evidence_line(risk)
            ),
        )
        processed.passed = bool(processed.validation_report.passed)
    if not processed.passed:
        validation_report = processed.validation_report
        logger.warning(
            "Agent 3 post-process ended with unresolved validation drift (retries=%s, section_failures=%s, global_failures=%s); deferring final enforcement to route-level repairs.",
            int(processed.retries or 0),
            len(list((validation_report.section_failures if validation_report else []) or [])),
            len(list((validation_report.global_failures if validation_report else []) or [])),
        )
        return final_text, int(processed.retries or 0), processed, section_prompts, thread_decision, section_plans, editorial_failure_history
    return final_text, int(processed.retries or 0), processed, section_prompts, thread_decision, section_plans, editorial_failure_history


# ═══════════════════════════════════════════════════════════════════════════
# Word Count Compliance — surgical rewrite loop
# ═══════════════════════════════════════════════════════════════════════════


def _count_words_simple(text: str) -> int:
    """Simple word counter: split on whitespace and count."""
    if not text:
        return 0
    return len(text.split())


def _surgical_word_count_rewrite(
    *,
    summary_text: str,
    target_length: int,
    openai_client: Any,
    max_attempts: int = 2,
) -> str:
    """Surgically adjust word count to be within ±10 of target.

    If the summary is already within tolerance, returns it unchanged.
    Makes up to ``max_attempts`` tries, keeping the best result.
    On any exception, returns the original text unchanged.
    """
    import re as _re

    best_text = summary_text
    best_distance = abs(_count_words_simple(summary_text) - target_length)

    for attempt in range(max_attempts):
        current_wc = _count_words_simple(best_text)
        delta = current_wc - target_length

        # Already within tolerance
        if abs(delta) <= 10:
            return best_text

        try:
            if delta > 0:
                # OVER target — need to cut words
                rewrite_prompt = (
                    f"The following investment memo is {current_wc} words but must be "
                    f"EXACTLY {target_length} words (±10). It is OVER by {delta} words.\n\n"
                    f"CUT exactly {abs(delta)} words by:\n"
                    f"- Removing redundant adjectives and filler phrases\n"
                    f"- Tightening verbose sentences\n"
                    f"- Removing low-value qualifiers\n\n"
                    f"PRESERVE EXACTLY:\n"
                    f"- All ## section headers\n"
                    f"- All quoted text (in quotation marks)\n"
                    f"- All numeric data and dollar figures\n"
                    f"- All → arrow-format lines in Key Metrics\n"
                    f"- The BUY/HOLD/SELL verdict\n\n"
                    f"Return ONLY the revised memo with no commentary.\n\n"
                    f"---\n{best_text}"
                )
            else:
                # UNDER target — need to add words
                rewrite_prompt = (
                    f"The following investment memo is {current_wc} words but must be "
                    f"EXACTLY {target_length} words (±10). It is UNDER by {abs(delta)} words.\n\n"
                    f"ADD exactly {abs(delta)} words by:\n"
                    f"- Adding 'so what' interpretation sentences after key data points\n"
                    f"- Extending causal reasoning chains\n"
                    f"- Adding forward-looking implications\n"
                    f"- Distributing additions across multiple sections\n\n"
                    f"PRESERVE EXACTLY:\n"
                    f"- All ## section headers\n"
                    f"- All quoted text (in quotation marks)\n"
                    f"- All numeric data and dollar figures\n"
                    f"- All → arrow-format lines in Key Metrics\n"
                    f"- The BUY/HOLD/SELL verdict\n\n"
                    f"Do NOT add generic filler or repeated framework sentences.\n"
                    f"Return ONLY the revised memo with no commentary.\n\n"
                    f"---\n{best_text}"
                )

            system_msg = (
                "You are a precision editor. Your ONLY job is to adjust the word count "
                "of an investment memo to hit an exact target. Make minimal changes. "
                "Preserve all structure, data, quotes, and analytical substance."
            )

            result = openai_client.compose_summary(
                prompt=rewrite_prompt,
                system_message=system_msg,
                temperature=0.2,
                timeout_seconds=45.0,
            )

            if not result or not result.strip():
                continue

            new_wc = _count_words_simple(result)
            new_distance = abs(new_wc - target_length)

            # Accept if closer to target
            if new_distance < best_distance:
                best_text = result
                best_distance = new_distance

        except Exception as exc:
            logger.warning(
                "Surgical word count rewrite attempt %d failed: %s", attempt + 1, exc
            )
            continue

    return best_text


# ═══════════════════════════════════════════════════════════════════════════
# Key Metrics Validation
# ═══════════════════════════════════════════════════════════════════════════


def _validate_key_metrics_section(summary_text: str, metrics_lines: str) -> str:
    """Validate and fix the Key Metrics section to ensure deterministic numeric rows.

    If >50% of content lines are prose (not matching the accepted numeric row formats), replaces
    the entire section body with the raw metrics_lines data.
    Otherwise, keeps valid numeric rows and drops prose lines.
    """
    import re as _re

    if not summary_text:
        return summary_text

    # Find the Key Metrics section
    pattern = _re.compile(
        r'(##\s*Key\s*Metrics[^\n]*\n)(.*?)(?=\n##\s|\Z)',
        _re.DOTALL | _re.IGNORECASE,
    )
    match = pattern.search(summary_text)
    if not match:
        return summary_text

    header = match.group(1)
    body = match.group(2).strip()

    if not body:
        return summary_text

    intro_lines: List[str] = []
    intro_consumed = 0
    raw_lines = [line for line in body.split("\n")]
    non_empty = [line.strip() for line in raw_lines if line.strip()]
    if non_empty and re.match(
        r"^what matters(?:\s+this filing period)?:?\s*$",
        non_empty[0],
        re.IGNORECASE,
    ):
        intro_lines.append("What Matters:")
        consumed_non_empty = 1
        bullet_count = 0
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                intro_consumed += 1
                continue
            if consumed_non_empty == 1 and re.match(
                r"^what matters(?:\s+this filing period)?:?\s*$",
                stripped,
                re.IGNORECASE,
            ):
                intro_consumed += 1
                continue
            bullet_match = re.match(r"^(?:[-*•])\s+(.+)$", stripped)
            if bullet_match and bullet_count < 4:
                intro_lines.append(f"- {bullet_match.group(1).strip()}")
                intro_consumed += 1
                consumed_non_empty += 1
                bullet_count += 1
                continue
            break
        if bullet_count == 0:
            intro_lines = []
            intro_consumed = 0

    if intro_consumed > 0:
        body = "\n".join(raw_lines[intro_consumed:]).strip()

    # Parse lines and validate
    arrow_pattern = _re.compile(r'^→\s*[^:]+:\s*[\$\d\-\+\(]')
    grid_pattern = _re.compile(r"^[^|]{2,80}\|\s*[^|]*\d")
    lines = [line for line in body.split('\n') if line.strip()]

    if not lines and intro_lines:
        replacement_body = "\n".join(intro_lines)
        new_section = f"{header}\n{replacement_body}\n"
        return summary_text[:match.start()] + new_section + summary_text[match.end():]

    if not lines:
        return summary_text

    valid_lines = []
    prose_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper() in {"DATA_GRID_START", "DATA_GRID_END"}:
            valid_lines.append(stripped)
        elif arrow_pattern.match(stripped) or grid_pattern.match(stripped):
            valid_lines.append(stripped)
        else:
            prose_count += 1

    if not intro_lines and prose_count > 0:
        prose_lines = [
            line.strip()
            for line in lines
            if line.strip()
            and line.strip().upper() not in {"DATA_GRID_START", "DATA_GRID_END"}
            and not arrow_pattern.match(line.strip())
            and not grid_pattern.match(line.strip())
        ]
        synthesized: List[str] = []
        for raw in prose_lines[:4]:
            cleaned = re.sub(r"\s+", " ", raw).strip().rstrip(".")
            if not cleaned or len(cleaned.split()) < 5:
                continue
            synthesized.append(f"- {cleaned}.")
        if synthesized:
            intro_lines = ["What Matters:", *synthesized[:4]]
            prose_count = max(0, prose_count - len(synthesized))

    total_content_lines = len(valid_lines) + prose_count

    if total_content_lines == 0:
        return summary_text

    # If >50% prose, replace entire section with raw metrics
    if prose_count > total_content_lines * 0.5:
        if metrics_lines and metrics_lines.strip() and metrics_lines.strip() != "(None)":
            replacement_body = metrics_lines.strip()
            if not intro_lines:
                fallback_intro = _fallback_metrics_intro_from_rows(
                    [line.strip() for line in replacement_body.splitlines() if line.strip()],
                    limit=3,
                )
                if fallback_intro:
                    intro_lines = ["What Matters:", *[f"- {item}" for item in fallback_intro]]
        elif valid_lines:
            replacement_body = '\n'.join(valid_lines)
        else:
            return summary_text
    else:
        # Keep only valid arrow lines
        if valid_lines:
            replacement_body = '\n'.join(valid_lines)
            if not intro_lines:
                fallback_intro = _fallback_metrics_intro_from_rows(valid_lines, limit=3)
                if fallback_intro:
                    intro_lines = ["What Matters:", *[f"- {item}" for item in fallback_intro]]
        else:
            return summary_text

    if intro_lines:
        replacement_body = "\n\n".join(
            ["\n".join(intro_lines), replacement_body]
        ).strip()

    # Replace in summary
    new_section = f"{header}\n{replacement_body}\n"
    return summary_text[:match.start()] + new_section + summary_text[match.end():]


def run_summary_agent_pipeline(
    *,
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    filing_type: str,
    filing_period: str,
    filing_date: str,
    target_length: Optional[int],
    context_excerpt: str,
    mda_excerpt: str,
    risk_factors_excerpt: str,
    company_kpi_context: str,
    financial_snapshot: str,
    metrics_lines: str,
    prior_period_delta_block: str,
    filing_language_snippets: str,
    calculated_metrics: Dict[str, Any],
    health_score_data: Optional[Dict[str, Any]],
    include_health_rating: bool,
    section_budgets: Dict[str, int],
    preferences: Any,
    persona_name: Optional[str],
    persona_requested: bool,
    investor_focus: Optional[str],
    openai_client: Any,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    section_instructions: Optional[Dict[str, str]] = None,
) -> PipelineResult:
    """Orchestrate the 3-agent pipeline for filing summary generation.

    This is the main entry point called by ``generate_filing_summary()`` in
    filings.py when the agent pipeline feature flag is enabled.

    Parameters match the data already prepared by filings.py's generation flow.
    """
    timings: Dict[str, float] = {}
    total_calls = 0

    # ─── Agent 1: Company Intelligence ────────────────────────
    if progress_callback:
        progress_callback("Researching company intelligence...", 50)

    t0 = time.monotonic()
    company_intelligence = _run_agent_1(
        company_name=company_name,
        ticker=ticker,
        sector=sector,
        industry=industry,
        filing_type=filing_type,
        filing_date=filing_date,
        openai_client=openai_client,
    )
    timings["agent_1"] = time.monotonic() - t0
    if not company_intelligence.from_cache:
        total_calls += 1

    # ─── Agent 2: Filing Analysis ─────────────────────────────
    if progress_callback:
        progress_callback("Analyzing filing with company context...", 65)

    t0 = time.monotonic()
    filing_analysis = _run_agent_2(
        company_intelligence=company_intelligence,
        company_name=company_name,
        ticker=ticker,
        filing_type=filing_type,
        filing_period=filing_period,
        filing_date=filing_date,
        context_excerpt=context_excerpt,
        mda_excerpt=mda_excerpt,
        risk_factors_excerpt=risk_factors_excerpt,
        company_kpi_context=company_kpi_context,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        prior_period_delta_block=prior_period_delta_block,
        filing_language_snippets=filing_language_snippets,
        openai_client=openai_client,
        preferences=preferences,
        section_instructions=section_instructions,
    )
    timings["agent_2"] = time.monotonic() - t0
    total_calls += 1

    # ─── Agent 3: Summary Composition ─────────────────────────
    if progress_callback:
        progress_callback("Composing investment memo...", 80)

    t0 = time.monotonic()
    summary_text, repair_attempts, agent_3_post_process, section_prompts, thread_decision, section_plans, section_failure_history = _run_agent_3(
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        company_name=company_name,
        filing_type=filing_type,
        filing_period=filing_period,
        filing_date=filing_date,
        target_length=target_length,
        financial_snapshot=financial_snapshot,
        metrics_lines=metrics_lines,
        health_score_data=health_score_data,
        include_health_rating=include_health_rating,
        section_budgets=section_budgets,
        persona_name=persona_name,
        persona_requested=persona_requested,
        investor_focus=investor_focus,
        industry=industry,
        openai_client=openai_client,
        preferences=preferences,
        section_instructions=section_instructions,
    )
    timings["agent_3"] = time.monotonic() - t0
    total_calls += len(
        [
            section_name
            for section_name in SECTION_ORDER
            if section_name != "Key Metrics"
            and (include_health_rating or section_name != "Financial Health Rating")
        ]
    )

    # ─── Post-processing: Key Metrics Validation ──────────────
    if summary_text:
        summary_text = _validate_key_metrics_section(summary_text, metrics_lines)

    logger.info(
        "Agent pipeline complete for %s: calls=%d, timings=%s",
        company_name,
        total_calls,
        {k: f"{v:.1f}s" for k, v in timings.items()},
    )

    section_word_counts = _collect_section_word_counts(
        summary_text,
        include_health_rating=include_health_rating,
    )
    final_section_bodies = _extract_section_bodies_from_summary(
        summary_text,
        include_health_rating=include_health_rating,
    )
    fatal_editorial_failures = _judge_sectioned_summary(
        section_bodies=final_section_bodies,
        include_health_rating=include_health_rating,
        thread_decision=thread_decision,
        section_plans=section_plans,
    )
    instruction_compliance_results = _build_instruction_compliance_results(
        section_bodies=final_section_bodies,
        section_plans=section_plans,
    )
    metrics_highlights = _build_metrics_highlights(filing_analysis, limit=4)

    return PipelineResult(
        summary_text=summary_text,
        company_intelligence=company_intelligence,
        filing_analysis=filing_analysis,
        agent_timings=timings,
        total_llm_calls=total_calls,
        metadata={
            "scale_factor": compute_scale_factor(target_length or 300),
            "depth_plan": (
                compute_depth_plan(compute_scale_factor(target_length or 300)).__dict__
            ),
            "tone": str(_normalize_style_preferences(preferences)[0]),
            "detail_level": str(_normalize_style_preferences(preferences)[1]),
            "output_style": str(_normalize_style_preferences(preferences)[2]),
            "focus_areas": list(_normalize_style_preferences(preferences)[3]),
            "investor_focus": str(investor_focus or ""),
            "key_metrics_budget": int(section_budgets.get("Key Metrics", 0) or 0),
            "narrative_budgets": {
                key: value
                for key, value in section_budgets.items()
                if key != "Key Metrics"
            },
            "repair_attempts": int(repair_attempts or 0),
            "section_prompts": dict(section_prompts or {}),
            "section_instructions": dict(section_instructions or {}),
            "thread_decision": _serialize_thread_decision(thread_decision),
            "thread_scorecard": _build_thread_scorecard(thread_decision),
            "section_plans": {
                key: _serialize_section_plan(value)
                for key, value in dict(section_plans or {}).items()
            },
            "instruction_checks": {
                key: [
                    _serialize_instruction_check(check)
                    for check in list((value.instruction_checks if value else []) or [])
                ]
                for key, value in dict(section_plans or {}).items()
            },
            "rejected_thread_candidates": [
                _serialize_thread_candidate(candidate)
                for candidate in list(thread_decision.rejected_threads or [])
            ],
            "section_failure_history": list(section_failure_history or []),
            "fatal_editorial_failures": [
                {
                    "section_name": failure.section_name,
                    "code": failure.code,
                    "message": failure.message,
                    "severity": float(failure.severity or 0.0),
                }
                for failure in list(fatal_editorial_failures or [])
            ],
            "metrics_highlights": list(metrics_highlights or []),
            "instruction_compliance_results": instruction_compliance_results,
            "risk_count": int(risk_budget_target_count(section_budgets.get("Risk Factors", 0) or 0)),
            "section_word_counts": section_word_counts,
            "section_word_ranges": {
                key: {
                    "lower": _section_budget_range(key, value)[0],
                    "upper": _section_budget_range(key, value)[1],
                }
                for key, value in (section_budgets or {}).items()
                if int(value or 0) > 0
            },
            "section_validation_passed": bool(
                agent_3_post_process.validation_report.passed
                if agent_3_post_process.validation_report
                else True
            ),
            "section_validation_failures": [
                {
                    "section_name": failure.section_name,
                    "code": failure.code,
                    "message": failure.message,
                    "severity": float(failure.severity or 0.0),
                }
                for failure in list(
                    (agent_3_post_process.validation_report.section_failures if agent_3_post_process.validation_report else [])
                    or []
                )
            ],
            "pipeline_mode": "continuous_v2_sectioned",
            "used_padding": False,
        },
    )
