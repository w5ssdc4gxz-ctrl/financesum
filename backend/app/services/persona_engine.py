"""Premium Investor Persona Engine - Radically Distinctive Voice Implementation."""
from typing import Dict, List, Optional, Any, Tuple
from app.services.gemini_client import GeminiClient
from app.services.summary_length import (
    clamp_summary_target_length,
    enforce_summary_target_length,
)
import re
from uuid import uuid4


# =============================================================================
# PERSONA ID MAPPING (Frontend uses full names, backend uses short IDs)
# =============================================================================

PERSONA_ID_MAP = {
    "warren_buffett": "buffett",
    "charlie_munger": "munger",
    "benjamin_graham": "graham",
    "peter_lynch": "lynch",
    "ray_dalio": "dalio",
    "cathie_wood": "wood",
    "joel_greenblatt": "greenblatt",
    "john_bogle": "bogle",
    "howard_marks": "marks",
    "bill_ackman": "ackman",
    # Also allow direct backend IDs
    "buffett": "buffett",
    "munger": "munger",
    "graham": "graham",
    "lynch": "lynch",
    "dalio": "dalio",
    "wood": "wood",
    "greenblatt": "greenblatt",
    "bogle": "bogle",
    "marks": "marks",
    "ackman": "ackman",
}


def normalize_persona_id(persona_id: str) -> str:
    """Normalize persona ID from frontend format to backend format."""
    return PERSONA_ID_MAP.get(persona_id.lower(), persona_id.lower())


# =============================================================================
# BANNED PHRASES - Generic financial language that breaks persona authenticity
# =============================================================================

BANNED_GENERIC_PHRASES = [
    "robust financial", "strong fundamentals", "poised for growth",
    "driving shareholder value", "showcases its dominance", "incredibly encouraging",
    "fueling future growth", "welcome addition", "testament to",
    "remains to be seen", "clear indication", "well-positioned",
    "solid execution", "attractive opportunity", "compelling valuation",
    "favorable outlook", "strategic initiatives", "operational excellence",
    "market leadership", "competitive positioning", "growth trajectory",
    "value creation", "shareholder returns", "industry tailwinds",
    "macro headwinds", "regulatory uncertainty", "data privacy concerns",
    "cloud infrastructure", "digital transformation",  # unless actually relevant
]

# Corporate analyst phrases that instantly break persona immersion
# These NEVER belong in persona output - they sound like institutional research
CORPORATE_ANALYST_PHRASES = [
    "margin trajectory",
    "capital allocation optimization",
    "inventory build-up is a yellow flag",
    "valuation is pricing in",
    "monitor margin trajectory",
    "refinancing timelines",
    "operating leverage dynamics",
    "inflection point in margins",
    "accretive to earnings",
    "multiple expansion potential",
    "normalized earnings power",
    "free cash flow conversion",
    "working capital efficiency",
    "ROIC vs WACC spread",
    "incremental ROIC",
    "unit economics inflection",
    "customer acquisition cost optimization",
    "addressable market expansion",
    "secular growth tailwinds",
    "cyclical headwinds",
    "margin inflection",
    "cash conversion cycle",
]

# MD&A speculation phrases - things companies don't actually disclose
# These make the analysis unrealistic and non-compliant
MDA_SPECULATION_PHRASES = [
    "management expects",
    "management projects",
    "management guidance suggests",
    "management's roadmap",
    "management's strategic plan indicates",
    "management has outlined",
    "according to management's forecast",
    "management believes revenue will",
    "management targets",
    "regulatory precursors suggest",
    "based on management's commentary",
    "management disclosed that",
    "management anticipates",
    "management's outlook",
    "as management has stated",
    "management's stated priorities",
    "management's vision for",
    "detailed KPI milestones",
    "management's revenue projections",
    "projected revenue growth of",
    "expected margin expansion of",
    # SMART goals and unrealistic disclosure requests (companies don't publish these)
    "smart goal",
    "smart goals",
    "kpi targets",
    "kpi milestones",
    "quarterly targets",
    "monthly targets",
    "detailed breakdown of",
    "segment-level projections",
    "unit economics breakdown",
    "customer acquisition cost breakdown",
    "ltv/cac ratio disclosure",
    "roi on each initiative",
    "specific margin guidance",
    "detailed cost reduction targets",
    "headcount targets",
    "market share targets",
    "specific revenue by segment",
    "management should provide",
    "management should disclose",
    "we need management to",
    "investors require more transparency",
    "lack of disclosure on",
]

# Generic risk phrases that apply to any company - must be avoided
GENERIC_RISK_PHRASES = [
    "macroeconomic volatility",
    "regulatory pressure",
    "regulatory uncertainty",
    "competitive landscape",
    "general economic downturn",
    "geopolitical risks",
    "supply chain disruptions",
    "cyber security risks",
    "talent retention",
    "management execution risk",
    "market conditions",
    "advertising budgets",  # Only relevant for ad-dependent companies
    "ad spend volatility",  # Only relevant for ad-dependent companies
    "consumer discretionary spending",  # Only relevant for retail/consumer
]

# =============================================================================
# INCOMPLETE SENTENCE PATTERNS - Detect truncated or cut-off output
# =============================================================================

INCOMPLETE_SENTENCE_PATTERNS = [
    r'\.\s*The\s+cost\s+of\s+revenue\s+at\s+\$\d+\.?\s*$',  # "The cost of revenue at $17."
    r'\.\s*\w+\s+at\s+\$[\d,]+\.?\s*$',  # Ends with "X at $Y."
    r'\.\s*\w+\s+of\s+\$[\d,]+\.?\s*$',  # Ends with "X of $Y."
    r'\.\s*\w+\s+is\s+\$[\d,]+\.?\s*$',  # Ends with "X is $Y."
    r'\.\s*\w+\s+was\s+\$[\d,]+\.?\s*$',  # Ends with "X was $Y."
    r'(?:,|;)\s*(?:and|or|but|while|as|with)\s*$',  # Ends with conjunction
    r'\.\s*(?:However|Moreover|Furthermore|Additionally|Meanwhile),?\s*$',  # Transition word at end
    r':\s*$',  # Ends with colon
    r'\.\s*\d+\.?\d*%?\s*$',  # Ends with just a number/percentage
    r'\.\s*(?:The|A|An|This|That|These|Those|It|Its)\s+\w+\s*$',  # Incomplete "The X" fragment
    # New patterns for common truncation issues
    r'repurchases\s+\(\$\d+\.?\s*$',  # "share repurchases ($14." - cuts off mid-number
    r'\(\$[\d.,]+\.?\s*$',  # Ends with incomplete dollar amount in parens
    r'alignment\s+with\s*\.?\s*$',  # "strategic alignment with..." trails off
    r'with\s+secular\s*\.?\s*$',  # "with secular..." trails off
    r'FCF/Net\s+Income\s*\.?\s*$',  # "FCF/Net Income..." cuts off
    r'/Net\s+Income\s*\.?\s*$',  # "FCF/Net Income" incomplete
    r'positions\s+the\s+company\s+for\s*\.?\s*$',  # "positions the company for..." trails off
    r'continued\s+long-term\s*\.?\s*$',  # "continued long-term..." trails off
    r'(?:strategic|long-term|secular)\s+(?:alignment|growth|trends?)\s*\.?\s*$',  # Common trailing phrases
    r'\d+/\d+\s*\([^)]*\s*$',  # "25/25 (FCF/Net Income..." incomplete parens
    r'\d+/\d+\s*$',  # Ends with just a score like "25/25"
    # New patterns from user feedback - mid-thought truncation
    r'establishes\s+a\s+strong\s*\.?\s*$',  # "establishes a strong..."
    r'indicator\s+of\s+(?:overall\s+)?(?:financial|operational)\s*\.?\s*$',  # "indicator of overall financial..."
    r'essential\s+for\s+(?:sustaining|maintaining)\s+(?:its|the)?\s*\.?\s*$',  # "essential for sustaining its..."
    r'resources\s+(?:to|and)\s*\.?\s*$',  # "resources to..." or "resources and..."
    r'sign\s+of\s+(?:management|strong)\s*\.?\s*$',  # "sign of management..."
    r'(?:a|the)\s+key\s+(?:indicator|driver|factor)\s+of\s*\.?\s*$',  # "a key indicator of..."
    r'which\s+is\s+(?:essential|critical|important)\s+for\s*\.?\s*$',  # "which is essential for..."
    r'as\s+(?:existing|current)\s+players\s+have\s+the\s*\.?\s*$',  # "as existing players have the..."
    r'have\s+the\s+resources\s*\.?\s*$',  # "have the resources..."
    # Generic mid-thought patterns
    r'\ba\s+strong\s*\.?\s*$',  # Ends with "a strong..."
    r'\bthe\s+(?:overall|key|main|primary)\s*\.?\s*$',  # "the overall..."
    r'\bfor\s+(?:sustaining|maintaining|ensuring)\s*\.?\s*$',  # "for sustaining..."
    r'\bof\s+(?:management|overall|strong)\s*\.?\s*$',  # "of management..."
    # CRITICAL: Ackman/activist voice trailing patterns
    r'but\s+I\s+acknowledge\s+the\s*\.?\s*$',  # "but I acknowledge the..."
    r'which\s+I\s+(?:want|need)\s+to\s+see\s*\.?\s*$',  # "which I want to see..."
    r'I\s+need\s+to\s+see\s+(?:evidence\s+of\s+)?[^.]*\s*$',  # "I need to see evidence of..." incomplete
    r'and\s+I\s+need\s+to\s+see\s*\.?\s*$',  # "and I need to see..."
    r'address\s+this\s+risk\s*\.?\s*$',  # "address this risk..." incomplete
    r'mitigation\s+strategies\s*\.?\s*$',  # "mitigation strategies..." incomplete
    r'contingency\s+planning\s+to\s*\.?\s*$',  # "contingency planning to..."
    r',\s*but\s*\.?\s*$',  # Just ", but..." trailing
    r';\s*but\s*\.?\s*$',  # "; but..." trailing
    r'I\s+am\s+concerned\s*\.?\s*$',  # "I am concerned..." incomplete
    r'I\s+remain\s+(?:cautious|concerned|skeptical)\s*\.?\s*$',  # "I remain cautious..." incomplete
    r'requiring\s+proactive\s*\.?\s*$',  # "requiring proactive..." incomplete
    # GREENBLATT-specific truncation patterns
    r'operating\s+in\s+a\s+high\s*\.?\s*$',  # "operating in a high..." incomplete
    r'operating\s+in\s+a\s+high[^.]*\.\s*$',  # "operating in a high..." ends mid-thought
    r'(?:present|presents?)\s+a\s+major\s+risk\s*\.?\s*$',  # "presents a major risk." without explanation
    r'this\s+reliance\s+presents?\s+a\s+major\s+risk\s*\.?\s*$',  # incomplete risk statement
    r'major\s+risk\.\s*$',  # ends with "major risk." without probability/severity
    r'and\s+this\s+earnings\s+power\s+is\s+precisely\s+what\s+I\s+seek\s*\.?\s*$',  # conversational ending
    r'precisely\s+what\s+I\s+seek\s*\.?\s*$',  # "precisely what I seek..." incomplete
    r'which\s+I\s+seek\s*\.?\s*$',  # trailing "which I seek"
    r'what\s+I\s+(?:seek|want|need)\s*\.?\s*$',  # "what I seek..." incomplete
    # Generic truncation with ellipsis or incomplete thoughts
    r'in\s+a\s+(?:high|low|strong|weak)\s*…\s*$',  # "in a high…" with ellipsis
    r'[^.!?]\s*…\s*$',  # ends with ellipsis mid-sentence
    # BOGLE-specific truncation patterns
    r'(?:Given|With)\s+my\s+emphasis\s+on\s+(?:diversification|risk)[^.]*\.{3}\s*$',  # "Given my emphasis on diversification..."
    r'the\s+missing\s+(?:growth\s+)?data[^.]*\.{3}\s*$',  # "the missing growth data..."
    r'moderate\s+liquidity\s*\.{3}\s*$',  # "moderate liquidity..."
    r',\s*the\s+missing\s+[^,]+,\s*(?:moderate|low|high)\s+\w+\s*\.{3}\s*$',  # list trails off
    r'compared\s+to\s+\$\d+\.?\s*$',  # "compared to $22." incomplete comparison
    r'compared\s+to\s+\$\d+\.\s*$',  # "compared to $22." with period but no context
    r'versus\s+\$\d+\.?\s*$',  # "versus $22." incomplete
    r'from\s+\$\d+\.?\s*$',  # "from $22." incomplete
    r'at\s+\$\d+\.?\s*$',  # "at $22." incomplete without context
    r'risk\s+minimization[^.]*\.{3}\s*$',  # "risk minimization..." trails off
    r'concentration[^.]*\.{3}\s*$',  # "concentration..." trails off
    r'diversification[^.]*\.{3}\s*$',  # "diversification..." trails off
    # Incomplete lists (Bogle often lists concerns)
    r',\s*(?:and|or)\s*\.{3}\s*$',  # ", and..." or ", or..." trailing
    r'(?:first|second|third|finally)[^.]*\.{3}\s*$',  # enumeration trails off
    # NEW: "which is..." trailing patterns (common mid-thought truncation)
    r'which\s+is\s*\.?\s*$',  # "which is..." incomplete
    r'which\s+is\s+\w+\s*\.?\s*$',  # "which is essential..." incomplete (one word after)
    r',\s*which\s+is\s*\.?\s*$',  # ", which is..." trailing
    r';\s*which\s+is\s*\.?\s*$',  # "; which is..." trailing
    r'which\s+is\s+(?:essential|critical|important|key|vital)\s*\.?\s*$',  # "which is essential..." incomplete
    r'underscores\s+the\s+company\'?s?\s+commitment\s+to[^.]*which\s+is\s*\.?\s*$',  # specific pattern from user feedback
    # Score truncation patterns (e.g., "Liquidity: 6/15 (Current Ratio is 4.")
    r':\s*\d+/\d+\s*\([^)]*\s+is\s+\d+\.?\s*$',  # "Liquidity: 6/15 (Current Ratio is 4." - incomplete
    r'\(Current\s+Ratio\s+is\s+\d+\.?\s*$',  # "(Current Ratio is 4." - incomplete
    r'Ratio\s+is\s+\d+\.?\s*$',  # "Ratio is 4." without context
    r'\([A-Za-z\s]+is\s+\d+\.?\s*$',  # "(X is 4." incomplete parenthetical
    # Additional "which is" patterns seen in production
    r'technological\s+edge,?\s*which\s+is\s*\.?\s*$',  # "technological edge, which is..." incomplete
    r'commitment\s+to\s+\w+,?\s*which\s+is\s*\.?\s*$',  # "commitment to X, which is..." incomplete
    # Mid-number truncation patterns (e.g., "0..." or "$1..." or "24..." cutting off)
    r'\b\d+\s*\.{2,}\s*$',  # "0..." or "24..." - number followed by ellipsis
    r'\$\d+\.{2,}\s*$',  # "$1..." - dollar amount with ellipsis
    r'\d+\.\d*\.{2,}\s*$',  # "1.2..." - decimal with ellipsis
    r'[\d.]+%\.{2,}\s*$',  # "25%..." - percentage with ellipsis
    r'\(\d+\.{2,}\s*$',  # "(0..." - parenthetical number cut off
    r'of\s+\d+\.{2,}\s*$',  # "of 0..." - "of X..." truncation
    r'at\s+\d+\.{2,}\s*$',  # "at 0..." - "at X..." truncation
    r'is\s+\d+\.{2,}\s*$',  # "is 0..." - "is X..." truncation
    r'to\s+\d+\.{2,}\s*$',  # "to 0..." - "to X..." truncation
    r'\d+\s+(?:to|and|or)\s*\.{2,}\s*$',  # "24 to..." or "24 and..." - incomplete range
    r'\$[\d.,]+\s+(?:to|and|or)\s*\.{2,}\s*$',  # "$1.2B to..." - incomplete dollar range
    r'[\d.]+%\s+(?:to|and|or)\s*\.{2,}\s*$',  # "25% to..." - incomplete percentage range
    # Truncation with trailing periods only (no ellipsis)
    r'\b\d+\.\s*$',  # Just "0." at end (not in context of complete sentence)
    r'\$\d+\.\s*$',  # Just "$1." at end without context
    r'ratio\s+of\s+\d+\.\s*$',  # "ratio of 4." incomplete
    r'is\s+\d+x\s*\.?\s*$',  # "is 4x." incomplete multiple
    r'at\s+\d+x\s*\.?\s*$',  # "at 4x." incomplete multiple
]

# Additional patterns for section-specific truncation
SECTION_TRUNCATION_PATTERNS = [
    # Financial Health Rating truncation
    r'(?:Cash\s+Flow\s+Quality|Profitability|Leverage|Liquidity):\s*\d+/\d+\s*(?:\([^)]*)?$',
    # Executive Summary trailing off
    r'strategic\s+alignment\s+with[^.]*$',
    r'positions?\s+(?:the\s+)?company\s+for[^.]*$',
    r'establishes\s+a\s+strong[^.]*$',
    # Capital Allocation truncation
    r'share\s+repurchases?\s+\([^)]*$',
    r'dividends?\s+\([^)]*$',
    r'buybacks?\s+\([^)]*$',
    # Financial Performance trailing off
    r'key\s+indicator\s+of\s+overall\s+financial[^.]*$',
    r'indicator\s+of\s+(?:financial|operational)[^.]*$',
    # MD&A trailing off
    r'essential\s+for\s+sustaining\s+its[^.]*$',
    r'which\s+is\s+essential\s+for[^.]*$',
    # Competitive Landscape trailing off
    (r'existing\s+players\s+have\s+the\s+resources[^.]*$'),
    (r'as\s+existing\s+players\s+have[^.]*$'),
    # Strategic Initiatives trailing off
    (r'a\s+sign\s+of\s+management[^.]*$'),
]

# Phrases that indicate uncontextualized numbers
UNCONTEXTUALIZED_NUMBER_PATTERNS = [
    r'revenue\s+(?:of\s+)?\$[\d.,]+[BM]?\s*(?:\.|$)',  # "revenue of $X." without context
    r'margin\s+(?:of\s+)?[\d.]+%\s*(?:\.|$)',  # "margin of X%." without context
    r'growth\s+(?:of\s+)?[\d.]+%\s*(?:\.|$)',  # "growth of X%." without context
]

# Strong sentence completion patterns - output MUST end with one of these
VALID_ENDING_PATTERNS = [
    r'\.\s*$',  # Ends with period
    r'\!\s*$',  # Ends with exclamation
    r'\?\s*$',  # Ends with question mark
    r'"\s*$',   # Ends with closing quote
    r"'\s*$",   # Ends with single quote
]

# Lynch-specific ending requirements - must have a verdict
LYNCH_VERDICT_PATTERNS = [
    r"i'?d buy",
    r"i'?d pass",
    r"i'?d wait",
    r"i'?m buying",
    r"i'?m passing",
    r"buy it",
    r"pass on",
    r"verdict:",
    r"my verdict",
    r"the verdict",
]

# Generic risk factors that should be avoided unless company-specific
GENERIC_RISK_FACTORS = [
    "macroeconomic volatility", "regulatory scrutiny", "competitive pressures",
    "interest rate sensitivity", "currency fluctuations", "supply chain disruptions",
    "cybersecurity risks", "talent retention", "geopolitical tensions",
]

# =============================================================================
# INDUSTRY-MISMATCHED RISK PHRASES - Filter out risks that don't apply
# =============================================================================
# Maps industry to phrases that should NEVER appear in that industry's analysis
RISK_INDUSTRY_MISMATCHES = {
    "semiconductors": [
        "advertising budget", "ad spend", "social media engagement", "content moderation",
        "subscription churn", "app store", "streaming", "subscriber growth",
        "same-store sales", "retail footprint", "inventory shrinkage",
    ],
    "semiconductor_equipment": [
        "advertising budget", "ad spend", "social media engagement", "content moderation",
        "subscription churn", "app store", "streaming", "subscriber growth",
        "same-store sales", "retail footprint", "inventory shrinkage",
    ],
    "software": [
        "inventory shrinkage", "retail footprint", "same-store sales",
        "drilling costs", "exploration risk", "commodity prices",
        "OPEC", "refinery margins",
    ],
    "pharma": [
        "advertising budget", "social media", "content creation",
        "subscriber churn", "streaming", "app downloads",
        "same-store sales", "retail footprint",
    ],
    "biotech": [
        "advertising budget", "social media", "content creation",
        "subscriber churn", "streaming", "app downloads",
        "same-store sales", "retail footprint",
    ],
    "retail": [
        "clinical trial", "FDA approval", "drug pipeline",
        "GPU demand", "chip shortage", "wafer capacity",
    ],
    "banking": [
        "subscriber churn", "content costs", "streaming wars",
        "chip shortage", "GPU demand", "clinical trials",
    ],
    "advertising": [
        "clinical trials", "FDA approval", "drug pipeline",
        "chip fabrication", "wafer capacity", "EUV lithography",
    ],
    "energy": [
        "subscriber churn", "content costs", "streaming",
        "app downloads", "clinical trials", "FDA approval",
    ],
    "automotive": [
        "advertising budget cuts", "content moderation",
        "subscriber churn", "streaming wars", "clinical trials",
    ],
    "healthcare": [
        "advertising budget", "content creation",
        "GPU demand", "chip shortage", "streaming",
    ],
    "payments": [
        "clinical trials", "drug pipeline", "FDA approval",
        "chip fabrication", "content moderation",
    ],
}


# =============================================================================
# COMPANY-SPECIFIC CONTEXT EXTRACTION
# =============================================================================

# =============================================================================
# KNOWN COMPANY DATABASE - For accurate industry classification
# =============================================================================

KNOWN_COMPANIES = {
    # Semiconductors & Equipment
    "asml": {"industry": "semiconductor_equipment", "sub": "lithography", "moat": "monopoly on EUV"},
    "nvidia": {"industry": "semiconductors", "sub": "GPUs/AI chips", "moat": "CUDA ecosystem"},
    "amd": {"industry": "semiconductors", "sub": "CPUs/GPUs", "moat": "x86 license"},
    "intel": {"industry": "semiconductors", "sub": "CPUs/foundry", "moat": "manufacturing scale"},
    "tsmc": {"industry": "semiconductors", "sub": "foundry", "moat": "process leadership"},
    "qualcomm": {"industry": "semiconductors", "sub": "mobile chips", "moat": "patent portfolio"},
    "broadcom": {"industry": "semiconductors", "sub": "networking chips", "moat": "design expertise"},
    "applied materials": {"industry": "semiconductor_equipment", "sub": "deposition", "moat": "installed base"},
    "lam research": {"industry": "semiconductor_equipment", "sub": "etch", "moat": "process expertise"},
    "klac": {"industry": "semiconductor_equipment", "sub": "inspection", "moat": "precision optics"},
    
    # Big Tech
    "apple": {"industry": "consumer_tech", "sub": "devices/services", "moat": "ecosystem lock-in"},
    "microsoft": {"industry": "software", "sub": "enterprise/cloud", "moat": "enterprise relationships"},
    "google": {"industry": "advertising", "sub": "search/cloud", "moat": "search dominance"},
    "alphabet": {"industry": "advertising", "sub": "search/cloud", "moat": "search dominance"},
    "amazon": {"industry": "retail_tech", "sub": "ecommerce/cloud", "moat": "logistics/AWS"},
    "meta": {"industry": "advertising", "sub": "social media", "moat": "network effects"},
    "facebook": {"industry": "advertising", "sub": "social media", "moat": "network effects"},
    
    # Financials
    "jpmorgan": {"industry": "banking", "sub": "universal bank", "moat": "scale/relationships"},
    "berkshire": {"industry": "conglomerate", "sub": "insurance/investments", "moat": "float/capital allocation"},
    "visa": {"industry": "payments", "sub": "card networks", "moat": "network effects"},
    "mastercard": {"industry": "payments", "sub": "card networks", "moat": "network effects"},
    
    # Healthcare
    "unitedhealth": {"industry": "healthcare", "sub": "insurance/PBM", "moat": "vertical integration"},
    "eli lilly": {"industry": "pharma", "sub": "diabetes/obesity", "moat": "GLP-1 franchise"},
    "novo nordisk": {"industry": "pharma", "sub": "diabetes/obesity", "moat": "GLP-1 franchise"},
    "pfizer": {"industry": "pharma", "sub": "diversified", "moat": "scale/distribution"},
    "johnson": {"industry": "healthcare", "sub": "diversified", "moat": "brand/distribution"},
    
    # Consumer
    "costco": {"industry": "retail", "sub": "warehouse clubs", "moat": "membership model"},
    "walmart": {"industry": "retail", "sub": "discount retail", "moat": "scale/logistics"},
    "coca-cola": {"industry": "beverages", "sub": "soft drinks", "moat": "brand/distribution"},
    "pepsi": {"industry": "beverages", "sub": "beverages/snacks", "moat": "brand/distribution"},
    "mcdonalds": {"industry": "restaurants", "sub": "QSR/real estate", "moat": "franchise model"},
    
    # Industrial
    "caterpillar": {"industry": "industrial", "sub": "heavy equipment", "moat": "dealer network"},
    "deere": {"industry": "industrial", "sub": "agricultural equipment", "moat": "dealer network/precision ag"},
    "boeing": {"industry": "aerospace", "sub": "aircraft", "moat": "duopoly position"},
    "lockheed": {"industry": "defense", "sub": "defense contractor", "moat": "classified programs"},
    "general electric": {"industry": "industrial", "sub": "aerospace/energy", "moat": "installed base/services"},
    "honeywell": {"industry": "industrial", "sub": "diversified", "moat": "aerospace/automation"},
    "3m": {"industry": "industrial", "sub": "diversified", "moat": "innovation/distribution"},
    "raytheon": {"industry": "defense", "sub": "missiles/defense systems", "moat": "classified programs"},
    
    # Software/SaaS
    "salesforce": {"industry": "software", "sub": "CRM/enterprise", "moat": "ecosystem/switching costs"},
    "adobe": {"industry": "software", "sub": "creative/marketing", "moat": "creative suite dominance"},
    "oracle": {"industry": "software", "sub": "database/cloud", "moat": "enterprise lock-in"},
    "sap": {"industry": "software", "sub": "ERP", "moat": "enterprise mission-critical"},
    "servicenow": {"industry": "software", "sub": "IT workflows", "moat": "enterprise automation"},
    "snowflake": {"industry": "software", "sub": "data cloud", "moat": "data sharing network"},
    "palantir": {"industry": "software", "sub": "data analytics", "moat": "government relationships"},
    "crowdstrike": {"industry": "software", "sub": "cybersecurity", "moat": "cloud-native architecture"},
    
    # E-commerce/Internet
    "shopify": {"industry": "software", "sub": "e-commerce platform", "moat": "merchant ecosystem"},
    "spotify": {"industry": "advertising", "sub": "audio streaming", "moat": "user base/playlists"},
    "netflix": {"industry": "entertainment", "sub": "streaming", "moat": "content/scale"},
    "uber": {"industry": "mobility", "sub": "rideshare/delivery", "moat": "network effects"},
    "airbnb": {"industry": "travel", "sub": "accommodations", "moat": "host/guest network"},
    "doordash": {"industry": "delivery", "sub": "food delivery", "moat": "logistics network"},
    
    # Electric Vehicles / Clean Energy
    "tesla": {"industry": "automotive", "sub": "EVs/energy", "moat": "brand/manufacturing"},
    "rivian": {"industry": "automotive", "sub": "EVs", "moat": "adventure brand"},
    "lucid": {"industry": "automotive", "sub": "luxury EVs", "moat": "powertrain technology"},
    "enphase": {"industry": "clean_energy", "sub": "microinverters", "moat": "residential solar"},
    "first solar": {"industry": "clean_energy", "sub": "solar panels", "moat": "thin-film technology"},
    
    # Biotech
    "moderna": {"industry": "biotech", "sub": "mRNA therapeutics", "moat": "mRNA platform"},
    "regeneron": {"industry": "biotech", "sub": "antibodies", "moat": "VelociSuite platform"},
    "vertex": {"industry": "biotech", "sub": "rare disease", "moat": "CF franchise"},
    "illumina": {"industry": "biotech", "sub": "gene sequencing", "moat": "sequencing dominance"},
    "dexcom": {"industry": "medical_devices", "sub": "CGM", "moat": "diabetes management"},
    "intuitive": {"industry": "medical_devices", "sub": "surgical robots", "moat": "da Vinci platform"},
    
    # Payments/Fintech
    "paypal": {"industry": "payments", "sub": "digital payments", "moat": "network effects"},
    "square": {"industry": "payments", "sub": "SMB payments", "moat": "ecosystem"},
    "block": {"industry": "payments", "sub": "SMB payments", "moat": "ecosystem"},
    "adyen": {"industry": "payments", "sub": "payment processing", "moat": "unified platform"},
    "coinbase": {"industry": "crypto", "sub": "crypto exchange", "moat": "regulatory compliance"},
    
    # REITs / Real Estate
    "prologis": {"industry": "real_estate", "sub": "industrial logistics", "moat": "location/scale"},
    "american tower": {"industry": "real_estate", "sub": "cell towers", "moat": "tower portfolio"},
    "equinix": {"industry": "real_estate", "sub": "data centers", "moat": "interconnection"},
    "realty income": {"industry": "real_estate", "sub": "retail REIT", "moat": "triple-net leases"},
    
    # Energy
    "exxon": {"industry": "energy", "sub": "oil/gas integrated", "moat": "scale/reserves"},
    "chevron": {"industry": "energy", "sub": "oil/gas integrated", "moat": "scale/reserves"},
    "conocophillips": {"industry": "energy", "sub": "E&P", "moat": "low-cost reserves"},
    "schlumberger": {"industry": "energy", "sub": "oilfield services", "moat": "technology/scale"},
    
    # Telecom
    "verizon": {"industry": "telecom", "sub": "wireless", "moat": "network quality"},
    "at&t": {"industry": "telecom", "sub": "wireless/fiber", "moat": "spectrum/network"},
    "t-mobile": {"industry": "telecom", "sub": "wireless", "moat": "5G spectrum"},
    
    # Entertainment/Media
    "disney": {"industry": "entertainment", "sub": "media/parks", "moat": "IP/brand"},
    "warner": {"industry": "entertainment", "sub": "media/streaming", "moat": "content library"},
    "comcast": {"industry": "entertainment", "sub": "cable/media", "moat": "broadband/content"},
}

# Industry-specific risk templates
INDUSTRY_RISKS = {
    "semiconductor_equipment": [
        "customer concentration (top 3 customers = most revenue)",
        "cyclical capex spending by chipmakers",
        "geopolitical restrictions on China sales",
        "technology transitions requiring R&D pivots",
    ],
    "semiconductors": [
        "cyclical demand patterns",
        "inventory corrections in the channel",
        "technology node transitions",
        "geopolitical supply chain fragmentation",
    ],
    "software": [
        "customer churn and retention",
        "sales cycle elongation",
        "competition from AI-native alternatives",
        "pricing pressure from procurement teams",
    ],
    "advertising": [
        "ad spend cyclicality tied to economy",
        "privacy regulation impact on targeting",
        "platform competition for user attention",
        "brand safety concerns",
    ],
    "banking": [
        "credit cycle deterioration",
        "net interest margin compression",
        "regulatory capital requirements",
        "deposit flight to higher-yielding alternatives",
    ],
    "pharma": [
        "patent cliffs and generic competition",
        "clinical trial failures",
        "pricing pressure from PBMs and governments",
        "pipeline concentration risk",
    ],
    "retail": [
        "consumer spending sensitivity",
        "inventory shrinkage and theft",
        "labor cost inflation",
        "e-commerce margin pressure",
    ],
    "consumer_tech": [
        "product cycle dependence",
        "China manufacturing concentration",
        "services growth deceleration",
        "regulatory antitrust scrutiny",
    ],
    "healthcare": [
        "regulatory changes (Medicare/Medicaid)",
        "medical loss ratio volatility",
        "PBM reform legislation risk",
        "provider contract negotiations",
    ],
    "payments": [
        "regulatory interchange fee caps",
        "competition from alternative payment rails",
        "cross-border volume fluctuations",
        "merchant fee pressure",
    ],
    "biotech": [
        "binary clinical trial outcomes",
        "regulatory approval uncertainty",
        "competitive pipeline threats",
        "manufacturing scale-up challenges",
    ],
    "medical_devices": [
        "reimbursement rate changes",
        "competitive product launches",
        "clinical evidence requirements",
        "hospital capital budget cycles",
    ],
    "automotive": [
        "EV transition execution risk",
        "battery cost and supply chain",
        "autonomous driving liability",
        "legacy ICE asset stranding",
    ],
    "clean_energy": [
        "interest rate sensitivity (project financing)",
        "policy/subsidy dependence",
        "supply chain constraints (rare earths)",
        "utility interconnection backlogs",
    ],
    "entertainment": [
        "content cost inflation",
        "subscriber churn in streaming",
        "theatrical window disruption",
        "cord-cutting acceleration",
    ],
    "telecom": [
        "spectrum auction costs",
        "infrastructure capex requirements",
        "price competition intensity",
        "regulatory net neutrality changes",
    ],
    "energy": [
        "commodity price volatility",
        "energy transition stranded assets",
        "regulatory/environmental liability",
        "OPEC+ supply decisions",
    ],
    "real_estate": [
        "interest rate sensitivity",
        "tenant credit quality",
        "work-from-home structural shift",
        "cap rate expansion risk",
    ],
    "industrial": [
        "cyclical capital goods demand",
        "supply chain cost inflation",
        "trade tariff exposure",
        "labor availability constraints",
    ],
    "defense": [
        "government budget dependence",
        "program cancellation risk",
        "cost overrun penalties",
        "classified program execution",
    ],
    "conglomerate": [
        "complexity discount",
        "capital allocation discipline",
        "segment underperformance",
        "activist pressure for breakup",
    ],
    "travel": [
        "macro sensitivity to discretionary spend",
        "geopolitical event disruption",
        "host/supply growth constraints",
        "regulatory compliance (short-term rentals)",
    ],
    "delivery": [
        "driver cost inflation",
        "unit economics at scale",
        "competitive intensity",
        "regulatory gig worker classification",
    ],
    "mobility": [
        "driver supply/demand balance",
        "insurance cost volatility",
        "autonomous vehicle disruption timeline",
        "regulatory licensing requirements",
    ],
    "crypto": [
        "regulatory uncertainty",
        "trading volume volatility",
        "security/hack risk",
        "competitive exchange landscape",
    ],
}


def extract_company_specific_context(company_name: str, financial_data: Dict, ratios: Dict) -> Dict[str, Any]:
    """
    Extract company-specific context for generating authentic, relevant analysis.
    Uses financial data signals + known company database for accurate classification.
    """
    context = {
        "company_name": company_name,
        "business_model_signals": [],
        "sector_risks": [],
        "financial_character": [],
        "scale_descriptor": "",
        "known_moat": "",
        "industry": "",
    }
    
    # =========================================================================
    # STEP 1: Check known company database first
    # =========================================================================
    name_lower = company_name.lower()
    matched_company = None
    
    for company_key, company_info in KNOWN_COMPANIES.items():
        if company_key in name_lower:
            matched_company = company_info
            context["industry"] = company_info.get("industry", "")
            context["business_model_signals"].append(f"{company_info.get('sub', '')} business")
            context["known_moat"] = company_info.get("moat", "")
            
            # Get industry-specific risks
            industry_risks = INDUSTRY_RISKS.get(company_info.get("industry", ""), [])
            context["sector_risks"] = industry_risks[:4]  # Top 4 risks
            break
    
    # =========================================================================
    # STEP 2: Determine scale from revenue
    # =========================================================================
    revenue = None
    if financial_data.get("income_statement"):
        rev_data = financial_data["income_statement"].get("revenue", {})
        if rev_data:
            revenue = list(rev_data.values())[0] if isinstance(rev_data, dict) else rev_data
    
    if revenue:
        try:
            rev_val = float(revenue)
            if rev_val > 100e9:
                context["scale_descriptor"] = "mega-cap titan ($100B+ revenue)"
            elif rev_val > 50e9:
                context["scale_descriptor"] = "mega-cap giant ($50B+ revenue)"
            elif rev_val > 10e9:
                context["scale_descriptor"] = "large-cap company ($10B+ revenue)"
            elif rev_val > 1e9:
                context["scale_descriptor"] = "mid-cap business ($1B+ revenue)"
            elif rev_val > 100e6:
                context["scale_descriptor"] = "small-cap company ($100M+ revenue)"
            else:
                context["scale_descriptor"] = "micro-cap enterprise"
        except (ValueError, TypeError):
            pass
    
    # =========================================================================
    # STEP 3: Analyze financial character from ratios (data-driven)
    # =========================================================================
    gross_margin = ratios.get("gross_margin")
    operating_margin = ratios.get("operating_margin")
    net_margin = ratios.get("net_margin")
    fcf = ratios.get("fcf")
    fcf_margin = ratios.get("fcf_margin")
    debt_to_equity = ratios.get("debt_to_equity")
    roe = ratios.get("roe")
    current_ratio = ratios.get("current_ratio")
    revenue_growth = ratios.get("revenue_growth_yoy")
    
    # Margin profile analysis
    if gross_margin is not None:
        if gross_margin > 0.70:
            context["business_model_signals"].append("ultra-high gross margins (70%+) - likely software, IP, or luxury")
            if not context["sector_risks"]:
                context["sector_risks"].append("margin sustainability as competition increases")
        elif gross_margin > 0.50:
            context["business_model_signals"].append("high gross margins (50%+) - differentiated product/service")
        elif gross_margin < 0.25:
            context["business_model_signals"].append("low gross margins (<25%) - commodity/volume business")
            if not context["sector_risks"]:
                context["sector_risks"].append("razor-thin margins leave no room for error")
    
    # Operating leverage analysis
    if operating_margin is not None and gross_margin is not None:
        opex_burden = gross_margin - operating_margin
        if opex_burden > 0.35:
            context["business_model_signals"].append("heavy opex burden (35%+ of revenue on SG&A/R&D)")
            context["financial_character"].append("investing heavily in growth")
        elif opex_burden < 0.15 and gross_margin > 0.40:
            context["business_model_signals"].append("efficient cost structure")
            context["financial_character"].append("operating leverage potential")
    
    # Cash flow character
    if fcf is not None:
        try:
            fcf_val = float(fcf)
            if fcf_val > 0:
                context["financial_character"].append("cash generative")
                if fcf_margin and fcf_margin > 0.20:
                    context["financial_character"].append("exceptional cash conversion (20%+ FCF margin)")
            else:
                context["financial_character"].append("cash burning")
                context["sector_risks"].append("dilution risk from future capital raises")
                context["sector_risks"].append("runway concerns if growth doesn't materialize")
        except (ValueError, TypeError):
            pass
    
    # Balance sheet health
    if debt_to_equity is not None:
        if debt_to_equity > 3.0:
            context["financial_character"].append("heavily leveraged (D/E > 3x)")
            context["sector_risks"].append("refinancing risk in higher rate environment")
            context["sector_risks"].append("covenant pressure if earnings decline")
        elif debt_to_equity > 1.5:
            context["financial_character"].append("moderately leveraged")
        elif debt_to_equity < 0.3:
            context["financial_character"].append("fortress balance sheet (minimal debt)")
    
    if current_ratio is not None:
        if current_ratio < 1.0:
            context["financial_character"].append("liquidity concerns (current ratio < 1)")
            context["sector_risks"].append("short-term funding risk")
        elif current_ratio > 3.0:
            context["financial_character"].append("excess liquidity (potentially inefficient capital)")
    
    # Returns analysis
    if roe is not None:
        if roe > 0.30:
            context["financial_character"].append("exceptional returns on equity (30%+)")
        elif roe > 0.15:
            context["financial_character"].append("solid returns on equity (15%+)")
        elif roe < 0.05:
            context["financial_character"].append("subpar returns on equity (<5%)")
        elif roe < 0:
            context["financial_character"].append("negative ROE (destroying equity value)")
    
    # Growth character
    if revenue_growth is not None:
        if revenue_growth > 0.30:
            context["financial_character"].append("hypergrowth (30%+ revenue growth)")
        elif revenue_growth > 0.15:
            context["financial_character"].append("strong growth (15%+ revenue growth)")
        elif revenue_growth < 0:
            context["sector_risks"].append("market share loss or industry headwinds")
            context["financial_character"].append("revenue declining")
    
    # =========================================================================
    # STEP 3.5: Additional data-driven risk inference (NEW)
    # =========================================================================
    
    # R&D intensity check
    r_and_d_ratio = ratios.get("r_and_d_ratio") or ratios.get("rd_ratio")
    if r_and_d_ratio is not None:
        try:
            rd_val = float(r_and_d_ratio)
            if rd_val > 0.15:
                context["business_model_signals"].append("R&D intensive (15%+ of revenue)")
                context["sector_risks"].append("R&D productivity and pipeline execution risk")
            elif rd_val > 0.08:
                context["business_model_signals"].append("moderate R&D investment")
        except (ValueError, TypeError):
            pass
    
    # Capex intensity check
    capex_ratio = ratios.get("capex_to_revenue") or ratios.get("capex_ratio")
    if capex_ratio is not None:
        try:
            capex_val = float(capex_ratio)
            if capex_val > 0.15:
                context["business_model_signals"].append("capital intensive (15%+ capex/revenue)")
                context["sector_risks"].append("ROIC vs WACC spread sustainability")
            elif capex_val > 0.08:
                context["business_model_signals"].append("moderate capital requirements")
        except (ValueError, TypeError):
            pass
    
    # Distressed/declining business detection
    if gross_margin is not None and operating_margin is not None:
        if gross_margin < 0.30 and operating_margin < 0.05:
            context["sector_risks"].append("commodity economics - limited pricing power")
    
    if revenue_growth is not None and net_margin is not None:
        if revenue_growth < 0 and net_margin < 0:
            context["sector_risks"].append("declining business with no profitability - turnaround required")
    
    # High growth but low/negative margins
    if revenue_growth is not None and operating_margin is not None:
        if revenue_growth > 0.20 and operating_margin < 0:
            context["sector_risks"].append("growth-at-all-costs model - path to profitability unclear")
    
    # Working capital intensity
    if current_ratio is not None and revenue is not None:
        try:
            if current_ratio > 2.5 and float(revenue) > 1e9:
                context["business_model_signals"].append("capital-light with excess working capital")
        except (ValueError, TypeError):
            pass
    
    # =========================================================================
    # STEP 4: Fallback industry inference if no known company match
    # =========================================================================
    if not matched_company and not context["sector_risks"]:
        # Use keyword matching as fallback
        industry_keywords = {
            "semiconductors": ["semiconductor", "chip", "silicon", "foundry", "wafer"],
            "software": ["software", "saas", "cloud", "platform"],
            "pharma": ["pharma", "therapeutics", "biotech", "drug", "medical"],
            "banking": ["bank", "financial", "credit", "lending"],
            "retail": ["retail", "store", "shop", "mart"],
            "advertising": ["advertising", "media", "social"],
        }
        
        for industry, keywords in industry_keywords.items():
            if any(kw in name_lower for kw in keywords):
                context["industry"] = industry
                context["sector_risks"] = INDUSTRY_RISKS.get(industry, [])[:4]
                break
        
        # If still no match, derive risks from financial character
        if not context["sector_risks"]:
            if "cash burning" in context["financial_character"]:
                context["sector_risks"] = [
                    "capital raise dilution",
                    "path to profitability uncertainty",
                    "customer acquisition cost sustainability",
                ]
            elif "hypergrowth" in str(context["financial_character"]):
                context["sector_risks"] = [
                    "growth deceleration as base effect kicks in",
                    "competition attracted by high growth",
                    "execution risk at scale",
                ]
            else:
                context["sector_risks"] = [
                    "competitive pressure on margins",
                    "market share dynamics",
                    "management execution",
                ]
    
    return context


def format_company_context_for_prompt(context: Dict[str, Any]) -> str:
    """Format company context as a prompt section with rich detail."""
    lines = []
    
    # Industry & Moat (if known)
    if context.get("industry"):
        lines.append(f"Industry: {context['industry']}")
    
    if context.get("known_moat"):
        lines.append(f"Competitive Moat: {context['known_moat']}")
    
    # Scale
    if context.get("scale_descriptor"):
        lines.append(f"Scale: {context['scale_descriptor']}")
    
    # Business model signals
    if context.get("business_model_signals"):
        signals = context['business_model_signals']
        lines.append(f"Business Model: {', '.join(signals[:3])}")
    
    # Financial character (what the numbers tell us)
    if context.get("financial_character"):
        chars = context['financial_character']
        lines.append(f"Financial Character: {', '.join(chars[:4])}")
    
    # Sector-specific risks (NOT generic macro risks)
    if context.get("sector_risks"):
        risks = context['sector_risks'][:4]
        lines.append(f"\nCompany-Specific Risks (use these, NOT generic macro risks):")
        for risk in risks:
            lines.append(f"  • {risk}")
    
    return "\n".join(lines) if lines else "No specific context available."


# =============================================================================
# OUTPUT SANITIZATION - Strip ratings, generic phrases, and section headers
# =============================================================================

def filter_placeholders_and_irrelevant_risks(output: str, company_context: Dict) -> str:
    """
    Remove placeholder text and industry-mismatched risk factors.
    This ensures no 'data unavailable' or wrong-industry risks leak through.
    """
    if not output:
        return output

    lines = output.split('\n')
    industry = company_context.get("industry", "")

    # Placeholder patterns to COMPLETELY remove lines containing them
    placeholder_patterns = [
        r'data\s+(?:is\s+)?unavailable',
        r'data\s+(?:is\s+)?not\s+(?:available|disclosed)',
        r'information\s+(?:is\s+)?(?:not\s+)?(?:available|disclosed)',
        r'not\s+disclosed',
        r'metrics?\s+(?:is|are)\s+unavailable',
        r'figures?\s+(?:is|are)\s+unavailable',
        r'numbers?\s+(?:is|are)\s+unavailable',
        r'\bN/?A\b(?!\s*\w)',  # N/A but not "N/A" followed by word (like N/America)
        r'\[.*?unavailable.*?\]',  # [data unavailable] style brackets
        r'\[.*?not\s+disclosed.*?\]',
    ]

    # Get industry-mismatched phrases
    mismatched_phrases = RISK_INDUSTRY_MISMATCHES.get(industry, [])

    filtered_lines = []
    for line in lines:
        line_lower = line.lower()

        # Skip lines with placeholder patterns
        skip_line = False
        for pattern in placeholder_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                skip_line = True
                break

        if skip_line:
            continue

        # Skip lines with industry-mismatched risks
        if mismatched_phrases:
            for phrase in mismatched_phrases:
                if phrase.lower() in line_lower:
                    skip_line = True
                    break

        if skip_line:
            continue

        # Skip lines that ONLY contain generic risk phrases without company-specific context
        # A line is "purely generic" if it contains a generic phrase AND doesn't have company-specific detail
        for generic_phrase in GENERIC_RISK_PHRASES:
            if generic_phrase.lower() in line_lower:
                # Check if the line has company-specific context (dollar amounts, percentages, company names)
                has_specifics = bool(
                    re.search(r'\$[\d.,]+', line) or  # Dollar amounts
                    re.search(r'\d+\.?\d*%', line) or  # Percentages
                    re.search(r'\b(?:revenue|margin|growth|profit|loss)\b', line_lower)  # Financial terms with numbers nearby
                )
                if not has_specifics:
                    skip_line = True
                    break

        if skip_line:
            continue

        filtered_lines.append(line)

    return '\n'.join(filtered_lines)


def detect_incomplete_sentences(output: str, persona_id: str = "") -> List[str]:
    """
    Detect incomplete or truncated sentences in the output.
    Returns a list of issues found.
    """
    issues = []
    output_stripped = output.strip()

    # Check for incomplete sentence patterns at the end
    for pattern in INCOMPLETE_SENTENCE_PATTERNS:
        if re.search(pattern, output_stripped, re.IGNORECASE):
            issues.append("TRUNCATION: Output appears to be cut off mid-sentence or mid-number")
            break

    # Check for section-specific truncation patterns
    for pattern in SECTION_TRUNCATION_PATTERNS:
        if re.search(pattern, output_stripped, re.IGNORECASE):
            issues.append("SECTION TRUNCATION: A section appears incomplete or cut off")
            break

    # Check for unclosed parentheses (common in financial data)
    open_parens = output_stripped.count('(')
    close_parens = output_stripped.count(')')
    if open_parens > close_parens:
        issues.append("TRUNCATION: Unclosed parenthesis detected - output may be cut off")

    # Check for sentences that end without proper punctuation
    if output_stripped and not any(re.search(p, output_stripped) for p in VALID_ENDING_PATTERNS):
        issues.append("INCOMPLETE: Output doesn't end with proper punctuation (., !, ?, or closing quote)")

    # Check for dangling modifiers/incomplete clauses
    dangling_patterns = [
        r'\bincluding\s*$',
        r'\bsuch\s+as\s*$',
        r'\bespecially\s*$',
        r'\bparticularly\s*$',
        r'\bnamely\s*$',
        r'\bfor example\s*$',
        r'\bwhich\s*$',
        r'\bthat\s*$',
        r'\bwhere\s*$',
        r'\bwhen\s*$',
        r'\bbecause\s*$',
        r'\balthough\s*$',
        r'\bwhile\s*$',
        r'\bif\s*$',
        r'\bwhether\s*$',
        r'\bto determine\s*$',
        r'\bI need to\s*$',
        r'\bmy take is\s*$',
        r'\bcautiously optimistic;\s*I need to\s*$',
    ]
    for pattern in dangling_patterns:
        if re.search(pattern, output_stripped, re.IGNORECASE | re.MULTILINE):
            issues.append(f"DANGLING: Output ends with incomplete clause")
            break

    # Check for mid-sentence endings like "I need to determine..."
    mid_sentence_patterns = [
        r'\bneed to determine[^.!?]*$',
        r'\bneed to assess[^.!?]*$',
        r'\bneed to evaluate[^.!?]*$',
        r'\bwill be watching[^.!?]*$',
        r'\bremains to be[^.!?]*$',
        r'\bI\'m looking at[^.!?]*$',
    ]
    for pattern in mid_sentence_patterns:
        if re.search(pattern, output_stripped, re.IGNORECASE):
            issues.append("INCOMPLETE: Output ends with an unfinished thought")
            break

    # =========================================================================
    # NEW: Check for paragraphs that end mid-argument (setup without payoff)
    # =========================================================================
    paragraphs = output_stripped.split('\n\n')
    setup_without_payoff_patterns = [
        # Patterns that indicate setup for content that never comes
        (r'is critical[^.]*\.\s*$', "ends with 'is critical' but no explanation follows"),
        (r'looking for[^.]*\.\s*$', "ends with 'looking for' but doesn't deliver what was sought"),
        (r'must be addressed[^.]*\.\s*$', "mentions something must be addressed but doesn't address it"),
        (r'requires? (?:further |careful |detailed )(?:analysis|examination|review)[^.]*\.\s*$', "defers to future analysis instead of providing it"),
        (r'will be important[^.]*\.\s*$', "says something will be important but doesn't explain why"),
        (r'we need to (?:see|understand|monitor)[^.]*\.\s*$', "ends with need statement without delivery"),
    ]

    for para in paragraphs:
        para_stripped = para.strip()
        if len(para_stripped) < 50:  # Skip very short paragraphs
            continue
        for pattern, description in setup_without_payoff_patterns:
            if re.search(pattern, para_stripped, re.IGNORECASE):
                # Check if the next paragraph follows up (if there is one)
                para_idx = paragraphs.index(para)
                has_followup = para_idx < len(paragraphs) - 1 and len(paragraphs[para_idx + 1].strip()) > 50
                if not has_followup:
                    issues.append(f"INCOMPLETE ARGUMENT: Paragraph {description}")
                    break

    # Lynch-specific: must have a verdict
    if persona_id == "lynch":
        output_lower = output_stripped.lower()
        has_verdict = any(re.search(p, output_lower) for p in LYNCH_VERDICT_PATTERNS)
        if not has_verdict:
            issues.append("LYNCH VERDICT MISSING: Peter Lynch analysis must end with a clear verdict (buy/pass/wait)")

    # Check minimum word count
    word_count = len(output_stripped.split())
    if word_count < 150 and persona_id not in ["greenblatt", "munger"]:
        issues.append(f"TOO SHORT: Only {word_count} words, minimum 150 for substantive analysis")

    # Check for overly long sentences (45+ words) - weakens authority
    sentences = re.split(r'[.!?]+', output_stripped)
    long_sentences = []
    for sentence in sentences:
        word_count_sentence = len(sentence.split())
        if word_count_sentence > 45:
            # Get first 10 words as preview
            preview = ' '.join(sentence.split()[:10]) + '...'
            long_sentences.append(f"({word_count_sentence} words): '{preview}'")
    if long_sentences:
        issues.append(f"VERBOSE SENTENCES: {len(long_sentences)} sentence(s) exceed 45 words - trim for clarity: {long_sentences[0]}")

    return issues


def detect_unsupported_valuation_claims(output: str) -> List[str]:
    """
    Detect valuation claims that aren't backed by metrics.
    """
    issues = []
    output_lower = output.lower()

    # Valuation claims that need supporting metrics
    valuation_claims = [
        ("undervalued", ["p/e", "multiple", "earnings yield", "fcf yield", "price-to", "discount"]),
        ("overvalued", ["p/e", "multiple", "earnings yield", "fcf yield", "price-to", "premium"]),
        ("cheap", ["p/e", "multiple", "yield", "price-to", "trades at"]),
        ("expensive", ["p/e", "multiple", "yield", "price-to", "trades at"]),
        ("fair value", ["dcf", "intrinsic", "worth", "p/e", "multiple"]),
        ("attractive valuation", ["p/e", "multiple", "yield", "price-to"]),
    ]

    for claim, required_support in valuation_claims:
        if claim in output_lower:
            # Check if any supporting metric is mentioned within 200 chars of the claim
            claim_pos = output_lower.find(claim)
            context_window = output_lower[max(0, claim_pos-200):claim_pos+200]

            has_support = any(support in context_window for support in required_support)
            if not has_support:
                issues.append(f"UNSUPPORTED VALUATION: Claim '{claim}' made without supporting valuation metric")

    return issues


def check_financial_contextualization(output: str) -> List[str]:
    """
    Check that financial figures ($X.XXB, $X.XXM) have proper context.
    Every dollar figure should be followed within 50 characters by an interpretation
    (comparison, benchmark, or meaning).

    Returns list of issues found.
    """
    issues = []

    # Pattern to find dollar figures with B/M suffix
    dollar_pattern = r'\$\d+\.?\d*[BM]'

    # Context indicators that show the number has been interpreted
    context_indicators = [
        # Comparisons
        'vs', 'versus', 'compared to', 'above', 'below', 'higher than', 'lower than',
        'up from', 'down from', 'increase', 'decrease', 'growth', 'decline',
        # Interpretations
        'indicating', 'suggesting', 'which means', 'demonstrating', 'showing',
        'reflects', 'represents', 'translating to',
        # Benchmarks
        'average', 'benchmark', 'peer', 'industry', 'S&P', 'market',
        # Ratios/calculations
        '÷', 'divided by', 'ratio', '%', 'yield', 'margin',
        # Quality indicators
        'strong', 'weak', 'healthy', 'concerning', 'impressive', 'solid',
        # Specific context words
        'annual', 'quarterly', 'fiscal', 'YoY', 'year-over-year',
    ]

    matches = list(re.finditer(dollar_pattern, output))

    # Only check if we have multiple figures - single figures are usually fine
    if len(matches) <= 2:
        return issues

    uncontextualized = []

    for match in matches:
        start_pos = match.start()
        end_pos = match.end()

        # Look at the 80 characters after the figure (wider window)
        context_after = output[end_pos:end_pos + 80].lower()

        # Check if any context indicator appears
        has_context = any(indicator in context_after for indicator in context_indicators)

        if not has_context:
            # Also check if it's part of a calculation (X ÷ Y = Z)
            calc_context = output[max(0, start_pos-20):end_pos + 50].lower()
            is_calculation = '÷' in calc_context or '=' in calc_context or '/' in calc_context

            if not is_calculation:
                figure = match.group()
                uncontextualized.append(figure)

    # Only report if we have significant issues (more than 2 uncontextualized figures)
    if len(uncontextualized) > 2:
        issues.append(f"Uncontextualized figures: {uncontextualized[:3]} - consider adding interpretation (what does this number mean?)")

    return issues


def detect_numerical_contradictions(output: str) -> List[str]:
    """
    Detect mathematical/numerical contradictions in the output.
    For example: "FCF/NI of 0.51 falls within the 0.7-1.0 range" is a contradiction.

    Returns list of issues found.
    """
    issues = []

    # Pattern 1: Ratio claimed to be in a range when it's not
    # Match patterns like "of 0.51 falls within the 0.7-1.0 range"
    ratio_range_pattern = r'(?:of|is|at)\s+(\d+\.?\d*)\s+(?:falls within|is within|in|within)\s+(?:the\s+)?(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)\s*(?:range|band|zone)?'

    for match in re.finditer(ratio_range_pattern, output, re.IGNORECASE):
        try:
            value = float(match.group(1))
            lower_bound = float(match.group(2))
            upper_bound = float(match.group(3))

            if value < lower_bound or value > upper_bound:
                issues.append(
                    f"NUMERICAL CONTRADICTION: Value {value} claimed to be within {lower_bound}-{upper_bound} range, but it is not. "
                    f"Actual assessment: {'below' if value < lower_bound else 'above'} the range."
                )
        except (ValueError, TypeError):
            pass

    # Pattern 2: Contradictory quality assessments for the same metric
    # E.g., "solid" and "concerning" for the same metric within 100 chars
    positive_terms = ['solid', 'strong', 'healthy', 'excellent', 'impressive', 'robust']
    negative_terms = ['concerning', 'weak', 'poor', 'disappointing', 'troubling', 'worrying']

    # Find sentences that contain both positive and negative terms for same concept
    sentences = output.split('.')
    for sentence in sentences:
        sentence_lower = sentence.lower()
        has_positive = any(term in sentence_lower for term in positive_terms)
        has_negative = any(term in sentence_lower for term in negative_terms)

        if has_positive and has_negative:
            # Check if it's a legitimate contrast (e.g., "strong revenue but weak margins")
            if ' but ' not in sentence_lower and ' however ' not in sentence_lower and ' while ' not in sentence_lower:
                issues.append(f"CONTRADICTORY ASSESSMENT: Mixed positive/negative terms without contrast word: '{sentence[:80]}...'")

    # Pattern 3: FCF/NI specific validation
    fcf_ni_pattern = r'FCF[/\\](?:Net\s+)?(?:Income|NI)\s+(?:of\s+)?(\d+\.?\d*)'
    for match in re.finditer(fcf_ni_pattern, output, re.IGNORECASE):
        try:
            ratio = float(match.group(1))
            context_start = max(0, match.start() - 50)
            context_end = min(len(output), match.end() + 150)
            context = output[context_start:context_end].lower()

            # Check for incorrect range claims
            if ratio < 0.7 and ('0.7-1' in context or '0.7 to 1' in context or 'healthy range' in context):
                if 'below' not in context and 'outside' not in context and 'not' not in context:
                    issues.append(
                        f"FCF/NI CONTRADICTION: Ratio of {ratio} is described as within 0.7-1.0 healthy range, "
                        f"but {ratio} < 0.7. Should note: 'below the healthy range' or 'cash conversion needs improvement'."
                    )
            elif ratio > 1.0 and ('0.7-1' in context or '0.7 to 1' in context):
                if 'above' not in context and 'exceeds' not in context:
                    issues.append(
                        f"FCF/NI CONTRADICTION: Ratio of {ratio} exceeds 1.0, which may indicate accounting adjustments. "
                        f"Context should note this is above the typical range."
                    )
        except (ValueError, TypeError):
            pass

    return issues


def detect_internal_data_inconsistency(output: str) -> List[str]:
    """
    Detect when the same metric is cited with different values in the same output.
    For example: Revenue mentioned as both "$130.5B" and "$125.1B" in the same report.

    Returns list of issues found.
    """
    issues = []
    output_lower = output.lower()

    # Pattern to find dollar amounts with context (e.g., "revenue of $130.5B")
    # Group metrics by their type and check for inconsistency

    metric_patterns = {
        "revenue": r'(?:revenue|sales|top[- ]line)\s+(?:reached\s+|of\s+|was\s+|totaled\s+|hit\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)?',
        "net_income": r'(?:net income|earnings|profit|bottom[- ]line)\s+(?:of\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)?',
        "fcf": r'(?:free cash flow|fcf|operating cash)\s+(?:of\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)?',
        "assets": r'(?:total assets|assets)\s+(?:of\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)?',
        "debt": r'(?:total debt|debt|liabilities)\s+(?:of\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)?',
    }

    # Also match patterns like "reported $60.9B in revenue" or "$60.9B revenue"
    alternate_patterns = {
        "revenue": r'(?:reported\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)\s+(?:in\s+)?(?:revenue|sales)',
        "net_income": r'(?:reported\s+)?\$?([\d,.]+)\s*([BMTbmt](?:illion)?)\s+(?:in\s+)?(?:net income|earnings|profit)',
    }

    def normalize_value(num_str: str, scale: str) -> float:
        """Convert number string with scale indicator to float."""
        try:
            num = float(num_str.replace(',', ''))
            scale_upper = (scale or '').upper()
            if scale_upper.startswith('T'):
                num *= 1e12
            elif scale_upper.startswith('B'):
                num *= 1e9
            elif scale_upper.startswith('M'):
                num *= 1e6
            return num
        except (ValueError, TypeError):
            return 0.0

    for metric_name, pattern in metric_patterns.items():
        matches = list(re.finditer(pattern, output, re.IGNORECASE))

        # Also check alternate patterns for this metric if they exist
        if metric_name in alternate_patterns:
            alt_matches = list(re.finditer(alternate_patterns[metric_name], output, re.IGNORECASE))
            matches.extend(alt_matches)

        if len(matches) >= 2:
            # Extract and normalize all values for this metric
            values = []
            for match in matches:
                num_str = match.group(1)
                scale = match.group(2) if len(match.groups()) > 1 else ''
                normalized = normalize_value(num_str, scale)
                if normalized > 0:
                    values.append((normalized, match.group(0)[:50]))

            if len(values) >= 2:
                # Check if values differ significantly (more than 10%)
                values.sort()
                min_val, min_context = values[0]
                max_val, max_context = values[-1]

                if min_val > 0 and (max_val - min_val) / min_val > 0.10:
                    issues.append(
                        f"DATA INCONSISTENCY: {metric_name.replace('_', ' ').title()} cited with different values - "
                        f"'{min_context}...' vs '{max_context}...'. "
                        f"Use ONE consistent figure throughout."
                    )

    # Also check conflicting fiscal periods
    fy_pattern = r'(?:FY|fiscal year)\s*\'?(\d{2,4})'
    fy_matches = list(re.finditer(fy_pattern, output, re.IGNORECASE))
    if len(fy_matches) >= 2:
        years = set()
        for match in fy_matches:
            year = match.group(1)
            if len(year) == 2:
                year = '20' + year
            years.add(year)

        if len(years) > 1:
            issues.append(
                f"DATA INCONSISTENCY: Multiple fiscal years referenced ({', '.join(sorted(years))}). "
                f"State the fiscal period ONCE at the start and use consistent data."
            )

    return issues


def validate_financial_data_sanity(financial_data: Dict, output: str) -> List[str]:
    """
    Validate that financial data makes sense and flag potential issues.
    For example: Total Assets < Revenue is highly unusual for capital-intensive businesses.

    This function checks the SOURCE DATA, not just what's written in the output.
    Returns list of issues found.
    """
    issues = []

    if not financial_data:
        return issues

    # Helper to safely get nested values
    def get_val(data, *keys):
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key, {})
            else:
                return None
        if isinstance(current, dict) and current:
            return list(current.values())[0] if current else None
        return current if current else None

    # Extract key metrics
    revenue = get_val(financial_data, "income_statement", "revenue")
    total_assets = get_val(financial_data, "balance_sheet", "total_assets")
    total_equity = get_val(financial_data, "balance_sheet", "total_equity")
    total_liabilities = get_val(financial_data, "balance_sheet", "total_liabilities")
    net_income = get_val(financial_data, "income_statement", "net_income")
    cash = get_val(financial_data, "balance_sheet", "cash")

    # =========================================================================
    # SANITY CHECK 1: Total Assets vs Revenue
    # For most companies, especially capital-intensive ones, Assets > Revenue
    # Exception: High-turnover retail or service businesses
    # =========================================================================
    if revenue and total_assets:
        try:
            rev = float(revenue)
            assets = float(total_assets)
            if assets > 0 and rev > 0:
                asset_turnover = rev / assets
                # For capital-intensive businesses (like NVIDIA with data centers),
                # asset turnover > 1.2 is unusual - revenue usually < assets
                if asset_turnover > 1.5:  # Revenue is more than 1.5x assets
                    issues.append(
                        f"DATA SANITY WARNING: Revenue (${rev/1e9:.2f}B) is {asset_turnover:.2f}x Total Assets (${assets/1e9:.2f}B). "
                        f"For capital-intensive businesses, revenue typically < assets. "
                        f"Verify: Is this comparing quarterly revenue to annual balance sheet? Or mixing fiscal periods?"
                    )
                elif asset_turnover > 1.15 and rev > 50e9:  # Large companies with rev > assets
                    # For large companies ($50B+ revenue), revenue > assets is notable
                    issues.append(
                        f"DATA NOTE: Revenue (${rev/1e9:.2f}B) exceeds Total Assets (${assets/1e9:.2f}B). "
                        f"Asset turnover of {asset_turnover:.2f}x is high for a company this size. "
                        f"This could indicate: capital-light business model, or verify data extraction is correct."
                    )
        except (ValueError, TypeError):
            pass

    # =========================================================================
    # SANITY CHECK 2: Balance Sheet Identity (Assets = Liabilities + Equity)
    # =========================================================================
    if total_assets and total_liabilities and total_equity:
        try:
            assets = float(total_assets)
            liabilities = float(total_liabilities)
            equity = float(total_equity)
            expected_assets = liabilities + equity
            if assets > 0 and abs(assets - expected_assets) / assets > 0.05:  # More than 5% difference
                issues.append(
                    f"DATA SANITY WARNING: Balance sheet doesn't balance. "
                    f"Assets: ${assets/1e9:.2f}B, Liabilities + Equity: ${expected_assets/1e9:.2f}B. "
                    f"Difference: ${(assets - expected_assets)/1e9:.2f}B. Verify data extraction."
                )
        except (ValueError, TypeError):
            pass

    # =========================================================================
    # SANITY CHECK 3: Net Income vs Revenue (margin sanity)
    # Net margin > 100% is impossible; > 60% is rare
    # =========================================================================
    if revenue and net_income:
        try:
            rev = float(revenue)
            ni = float(net_income)
            if rev > 0:
                net_margin = ni / rev
                if net_margin > 1.0:
                    issues.append(
                        f"DATA SANITY ERROR: Net Income (${ni/1e9:.2f}B) > Revenue (${rev/1e9:.2f}B). "
                        f"Net margin of {net_margin*100:.1f}% is impossible. Check data extraction."
                    )
                elif net_margin > 0.6 and net_margin <= 1.0:
                    # Very high but possible - just flag for awareness
                    issues.append(
                        f"DATA NOTE: Exceptionally high net margin of {net_margin*100:.1f}%. "
                        f"This is rare - verify it's accurate (could be one-time gains or software/IP business)."
                    )
        except (ValueError, TypeError):
            pass

    # =========================================================================
    # SANITY CHECK 4: Cash vs Total Assets
    # Cash > Total Assets is impossible
    # =========================================================================
    if cash and total_assets:
        try:
            c = float(cash)
            a = float(total_assets)
            if c > a and a > 0:
                issues.append(
                    f"DATA SANITY ERROR: Cash (${c/1e9:.2f}B) > Total Assets (${a/1e9:.2f}B). "
                    f"This is impossible - cash is a subset of assets. Check data extraction."
                )
        except (ValueError, TypeError):
            pass

    return issues


def fix_incomplete_output(output: str, persona_id: str = "") -> str:
    """
    Attempt to fix common incomplete output issues.
    This is a safety net - truncates to the last complete sentence if needed.
    Includes financial-specific pattern completion.
    """
    if not output:
        return output

    output = output.strip()

    # =========================================================================
    # FINANCIAL-SPECIFIC COMPLETIONS (fix truncated metrics)
    # =========================================================================

    # Fix incomplete ratio statements like "falls within the 0.7-1." → complete the range
    output = re.sub(
        r'falls within the (\d+\.?\d*)-(\d+\.?\d*)\.\s*$',
        r'falls within the \1-\2 healthy range.',
        output
    )

    # Fix trailing incomplete numbers with dollar signs (e.g., "$31." → needs B/M)
    # This catches truncated currency amounts at end of text
    output = re.sub(
        r'\$(\d+)\.\s*$',
        r'$\1 billion.',  # Default to billion as most common in SEC filings
        output
    )

    # Fix incomplete parenthetical scores like "(30/30 (" → complete it
    output = re.sub(r'\((\d+/\d+)\s*\(\s*$', r'(\1).', output)

    # Fix incomplete threshold comparisons like "15% threshold) +" → clean up
    output = re.sub(r'threshold\)\s*\+\s*$', 'threshold).', output)

    # Fix "Cash Flow: 18/25 (FCF/Net Income of 0.69 falls within the 0.7-1."
    output = re.sub(
        r'FCF/Net Income of (\d+\.?\d*) falls within the (\d+\.?\d*)-(\d+\.?\d*)\.\s*$',
        r'FCF/Net Income of \1 falls within the \2-\3 healthy range, indicating quality earnings.',
        output
    )

    # Fix sentences ending with just a number and period like "reached $31."
    # Pattern: any amount that looks truncated (just whole number with .)
    output = re.sub(
        r'reached \$(\d{1,3})\.(?!\d)',  # Matches "$31." but not "$31.91"
        r'reached $\1 billion.',
        output
    )

    # Fix incomplete percentage comparisons
    output = re.sub(
        r'(\d+\.?\d*%)\s+(?:threshold|benchmarks|average)\s*$',
        r'\1 threshold).',
        output
    )

    # Fix leverage typo: "below 0" should be "below 0.5" or similar threshold
    # "Debt/Equity ratio of 0.13 is comfortably below 0." is mathematically impossible
    output = re.sub(
        r'(?:Debt[/\\]Equity|D/E|debt-to-equity|leverage)\s+(?:ratio\s+)?(?:of\s+)?(\d+\.?\d*)\s+is\s+comfortably\s+below\s+0\.',
        r'Debt/Equity ratio of \1 is comfortably below 0.5, indicating a conservative capital structure.',
        output,
        flags=re.IGNORECASE
    )
    # Also catch "below 0," or "below 0 " mid-sentence
    output = re.sub(
        r'(?:Debt[/\\]Equity|D/E|debt-to-equity|leverage)\s+(?:ratio\s+)?(?:of\s+)?(\d+\.?\d*)\s+is\s+comfortably\s+below\s+0(?=[,\s])',
        r'Debt/Equity ratio of \1 is comfortably below typical risk thresholds',
        output,
        flags=re.IGNORECASE
    )

    # Fix trailing "creating barriers to..." patterns
    output = re.sub(
        r'creating barriers to\.\.\.\s*$',
        'creating barriers to entry that protect margins.',
        output
    )

    # Fix trailing ellipsis patterns in various contexts
    output = re.sub(
        r'(?:products and services|future growth|innovation),?\s*which is a positive sign for future growth\.\.\.\s*$',
        'which supports the long-term thesis.',
        output
    )

    # =========================================================================
    # NEW: Fix specific mid-sentence truncation patterns from user feedback
    # =========================================================================

    # Fix "but..." patterns that trail off (CRITICAL - user reported)
    output = re.sub(
        r',?\s*but\s+the\s+figure\s+is\s+less\s+than\s+net\s*\.?\.\.\.\s*$',
        ', indicating cash flow lags net income slightly but remains healthy.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r',?\s*but\s+the\s+figure\s+is\s+less\s+than\s*\.?\.\.\.\s*$',
        ', though conversion could improve.',
        output,
        flags=re.IGNORECASE
    )

    # Fix "although I want to assess if this is sustainable in the face of increasing..."
    output = re.sub(
        r',?\s*although\s+I\s+want\s+to\s+assess\s+if\s+this\s+is\s+sustainable\s+in\s+the\s+face\s+of\s+increasing\s*\.?\.\.\.\s*$',
        ', though sustainability requires monitoring as competitive pressures mount.',
        output,
        flags=re.IGNORECASE
    )

    # Fix "driven by the AI..." trailing off
    output = re.sub(
        r',?\s*driven\s+by\s+the\s+AI\s*\.?\.\.\.\s*$',
        ', driven by the AI infrastructure buildout that remains a key growth driver.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r',?\s*driven\s+by\s+the\s+AI\s*\.?\s*$',
        ', driven by the AI infrastructure buildout.',
        output,
        flags=re.IGNORECASE
    )

    # Fix "which is..." trailing patterns
    output = re.sub(
        r',?\s*which\s+is\s*\.?\.\.\.\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r',?\s*which\s+is\s*\.?\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )

    # Fix "but I acknowledge the..." trailing
    output = re.sub(
        r',?\s*but\s+I\s+acknowledge\s+the\s*\.?\.\.\.\s*$',
        ', though risks remain that require monitoring.',
        output,
        flags=re.IGNORECASE
    )

    # Fix generic trailing "although..." patterns
    output = re.sub(
        r',?\s*although\s+[^.!?]{0,50}\s*\.?\.\.\.\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )

    # Fix generic trailing "however..." patterns
    output = re.sub(
        r',?\s*however\s+[^.!?]{0,50}\s*\.?\.\.\.\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )

    # Fix generic trailing "while..." patterns
    output = re.sub(
        r',?\s*while\s+[^.!?]{0,50}\s*\.?\.\.\.\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )

    # Remove trailing incomplete fragments
    # Pattern: ends with "The X at $Y" without completion
    output = re.sub(r'\.\s*(?:The|A|An)\s+\w+\s+(?:at|of|is|was)\s+\$[\d,]+\.?\s*$', '.', output)

    # Remove trailing conjunctions
    output = re.sub(r'(?:,|;)\s*(?:and|or|but|while|as|with)\s*$', '.', output)

    # Remove trailing transition words
    output = re.sub(r'\.\s*(?:However|Moreover|Furthermore|Additionally|Meanwhile),?\s*$', '.', output)

    # Remove trailing colons
    output = re.sub(r':\s*$', '.', output)

    # Remove incomplete "I need to..." endings
    output = re.sub(r'[.;,]\s*(?:I need to|my take is|cautiously optimistic;\s*I need to)[^.!?]*$', '.', output, flags=re.IGNORECASE)

    # =========================================================================
    # FIX EXECUTIVE SUMMARY TRUNCATION
    # =========================================================================
    # Pattern: "introduces a level of risk that I believe is unnecessary and can..."
    # Completes mid-sentence executive summary endings
    output = re.sub(
        r'(?:introduces?|presents?|creates?)\s+(?:a\s+)?level\s+of\s+risk\s+that\s+I\s+believe\s+is\s+unnecessary\s+and\s+can\s*\.?\.{0,3}\s*$',
        'introduces a level of risk that I believe is unnecessary and can be avoided through broad market indexing.',
        output,
        flags=re.IGNORECASE
    )
    # More generic version: "...and can..." trailing off at end
    output = re.sub(
        r'and\s+can\s*\.?\.{0,}\s*$',
        'and can be mitigated with proper diversification.',
        output,
        flags=re.IGNORECASE
    )
    # "...that I believe is..." trailing
    output = re.sub(
        r'that\s+I\s+believe\s+is\s*\.?\.{0,}\s*$',
        'that I believe warrants caution.',
        output,
        flags=re.IGNORECASE
    )
    # "...is unnecessary and..." trailing
    output = re.sub(
        r'is\s+unnecessary\s+and\s*\.?\.{0,}\s*$',
        'is unnecessary and avoidable.',
        output,
        flags=re.IGNORECASE
    )

    # =========================================================================
    # FIX ELLIPSIS AND TRAILING INCOMPLETE PATTERNS (COMPREHENSIVE)
    # =========================================================================
    # These patterns catch sentences that trail off with "..." or incomplete thoughts

    # Pattern: "Cash Flow Quality: 10/25..." - incomplete score with ellipsis
    output = re.sub(
        r'(Cash\s+Flow\s+Quality:\s*\d+/\d+)\s*\.{2,}\s*$',
        r'\1, indicating room for improvement in cash conversion.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: Executive summary trailing with "; I..." or ", I..."
    output = re.sub(
        r'[;,]\s*I\s*\.{2,}\s*$',
        '. This requires further monitoring.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r'[;,]\s*I\s*$',
        '. This warrants attention.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "I need to see..." trailing incomplete
    output = re.sub(
        r'I\s+need\s+to\s+see\s+[^.!?]*\.{2,}\s*$',
        'I need to see concrete evidence before committing.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r'I\s+need\s+to\s+see\s+[^.!?]{0,50}\s*$',
        'I need to see more clarity on execution.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "concrete steps..." or "specific timelines..." trailing
    output = re.sub(
        r'(?:concrete\s+steps|s\u00edmilar\s+timelines|d\u00e9tails\s+plans?|clear\s+plan)\s*\.{2,}\s*$',
        'concrete steps with measurable milestones.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "ROI expectations for these R&D investments..."
    output = re.sub(
        r'ROI\s+expectations\s+for\s+(?:these\s+)?(?:R&D\s+)?investments\s*\.{2,}\s*$',
        'ROI expectations for these investments, with specific return targets.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "until I..." at the end (Closing Takeaway)
    output = re.sub(
        r'until\s+I\s*\.{2,}\s*$',
        'until I see clearer catalysts for value creation.',
        output,
        flags=re.IGNORECASE
    )
    output = re.sub(
        r'until\s+I\s*$',
        'until conditions improve.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "defend their market share..." or similar trailing
    output = re.sub(
        r'(?:defend|protect|maintain)\s+(?:their|its)\s+market\s+share\s*\.{2,}\s*$',
        'defend their market share against emerging competitors.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: Generic ellipsis at end of any sentence (aggressive cleanup)
    output = re.sub(
        r'(\w+)\s*\.{3,}\s*$',
        r'\1.',
        output
    )
    output = re.sub(
        r'(\w+)\s*\.{2}\s*$',
        r'\1.',
        output
    )

    # Pattern: Semicolon followed by incomplete thought at end
    output = re.sub(
        r';\s+[^.!?]{0,40}\s*\.{2,}\s*$',
        '.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "but I am staying on the sidelines until I..."
    output = re.sub(
        r'(?:but\s+)?I\s+am\s+staying\s+on\s+the\s+sidelines\s+until\s+I\s*\.{0,3}\s*$',
        'I am staying on the sidelines until clearer catalysts emerge.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: Ackman-specific "I demand..." or "I need..." trailing
    output = re.sub(
        r'I\s+demand\s+[^.!?]*\.{2,}\s*$',
        'I demand accountability and concrete action from management.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "which is crucial..." or "which is essential..." trailing
    output = re.sub(
        r'which\s+is\s+(?:crucial|essential|important|critical)\s+[^.!?]*\.{2,}\s*$',
        'which is crucial for long-term success.',
        output,
        flags=re.IGNORECASE
    )

    # Pattern: "but I need to see better..." trailing
    output = re.sub(
        r'but\s+I\s+need\s+to\s+see\s+(?:better|more|clearer)\s+[^.!?]*\.{2,}\s*$',
        'but I need to see better execution before risking capital.',
        output,
        flags=re.IGNORECASE
    )

    # Check if output ends with proper punctuation
    if output and not output[-1] in '.!?"\'':
        # Find the last sentence
        last_period = output.rfind('.')
        last_exclaim = output.rfind('!')
        last_question = output.rfind('?')
        last_punct = max(last_period, last_exclaim, last_question)
        if last_punct > 0 and last_punct > len(output) - 100:
            output = output[:last_punct + 1]
        else:
            output += '.'

    # Additional cleanup: remove any trailing "I need to determine..." type phrases
    # that might have survived (even with punctuation)
    trailing_incomplete = [
        r'\.\s*I need to determine[^.!?]*[.!?]?\s*$',
        r'\.\s*I need to assess[^.!?]*[.!?]?\s*$',
        r'\.\s*I need to evaluate[^.!?]*[.!?]?\s*$',
        r'\.\s*My take is[^.!?]*;\s*I need to\s*.',
    ]
    for pattern in trailing_incomplete:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            output = output[:match.start()] + '.'
            break

    # =========================================================================
    # FINAL SAFETY NET: Aggressive truncation cleanup
    # =========================================================================
    # If output still ends mid-sentence after all fixes, find last complete sentence

    # Check for common truncation endings that slipped through
    final_truncation_patterns = [
        r',?\s*but\s+[^.!?]{0,30}\s*$',  # ", but X..." where X is incomplete
        r',?\s*although\s+[^.!?]{0,30}\s*$',  # ", although X..."
        r',?\s*however\s+[^.!?]{0,30}\s*$',  # ", however X..."
        r',?\s*while\s+[^.!?]{0,30}\s*$',  # ", while X..."
        r',?\s*which\s+is\s+[^.!?]{0,20}\s*$',  # ", which is X..."
        r',?\s*driven\s+by\s+[^.!?]{0,20}\s*$',  # ", driven by X..."
        r'\s+in\s+the\s+face\s+of\s+[^.!?]{0,20}\s*$',  # "in the face of X..."
    ]

    for pattern in final_truncation_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            # Find and remove the trailing incomplete clause
            output = re.sub(pattern, '.', output, flags=re.IGNORECASE)
            break

    return output.strip()


def fix_mid_text_ellipsis(output: str) -> str:
    """
    Fix ellipsis (...) that appear in the middle of text, not just at the end.
    This catches patterns like "Cash Flow Quality: 10/25..." in the middle of output.
    Also handles incomplete trailing phrases without explicit ellipsis.
    """
    if not output:
        return output

    lines = output.split('\n')
    fixed_lines = []

    for line in lines:
        original_line = line

        # =================================================================
        # PERSONA-SPECIFIC TRAILING PATTERNS (Howard Marks, etc.)
        # =================================================================
        
        # "I would..." patterns (common for investment personas)
        line = re.sub(
            r'I\s+would\s*\.{2,}\s*$',
            'I would proceed with caution given current valuations.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'I\s+would\s*$',
            'I would proceed with caution given current valuations.',
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # EXECUTIVE SUMMARY / CLOSING PATTERNS
        # =================================================================
        
        # "sustainability and the..." patterns
        line = re.sub(
            r'sustainability\s+and\s+the\s*\.{2,}\s*$',
            'sustainability and the long-term durability of these exceptional margins.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'sustainability\s+and\s+the\s*$',
            'sustainability and the long-term durability of these exceptional margins.',
            line,
            flags=re.IGNORECASE
        )
        
        # "and the..." at end
        line = re.sub(
            r'\s+and\s+the\s*\.{2,}\s*$',
            ' and the implications for long-term value creation.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'\s+and\s+the\s*$',
            ' and the implications for long-term value creation.',
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # MD&A / MANAGEMENT PATTERNS
        # =================================================================
        
        # "uncertainties in global..." patterns
        line = re.sub(
            r'uncertainties\s+in\s+global\s*\.{2,}\s*$',
            'uncertainties in the global supply chain and macroeconomic environment.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'uncertainties\s+in\s+global\s*$',
            'uncertainties in the global supply chain and macroeconomic environment.',
            line,
            flags=re.IGNORECASE
        )
        
        # "in global..." at end
        line = re.sub(
            r'\s+in\s+global\s*\.{2,}\s*$',
            ' in the global market.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'\s+in\s+global\s*$',
            ' in the global market.',
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # RISK FACTOR PATTERNS
        # =================================================================
        
        # "a geopolitical..." patterns
        line = re.sub(
            r',?\s*a\s+geopolitical\s*\.{2,}\s*$',
            ', a geopolitical risk that warrants close monitoring.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r',?\s*a\s+geopolitical\s*$',
            ', a geopolitical risk that warrants close monitoring.',
            line,
            flags=re.IGNORECASE
        )
        
        # "in a key market..." patterns
        line = re.sub(
            r'in\s+a\s+key\s+market\s*\.{2,}\s*$',
            'in a key market that could materially impact results.',
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r'in\s+a\s+key\s+market\s*$',
            'in a key market that could materially impact results.',
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # COMPETITIVE LANDSCAPE PATTERNS
        # =================================================================
        
        # "NVIDIA's..." patterns (company possessive without noun)
        line = re.sub(
            r"NVIDIA['']s\s*\.{2,}\s*$",
            "NVIDIA's competitive positioning and pricing power.",
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r"NVIDIA['']s\s*$",
            "NVIDIA's competitive positioning and pricing power.",
            line,
            flags=re.IGNORECASE
        )
        
        # "reliance on NVIDIA's..." patterns
        line = re.sub(
            r"reliance\s+on\s+NVIDIA['']s\s*\.{2,}\s*$",
            "reliance on NVIDIA's chips and potentially developing alternatives.",
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r"reliance\s+on\s+NVIDIA['']s\s*$",
            "reliance on NVIDIA's chips and potentially developing alternatives.",
            line,
            flags=re.IGNORECASE
        )
        
        # "potentially reducing their..." patterns
        line = re.sub(
            r"potentially\s+reducing\s+their\s*\.{2,}\s*$",
            "potentially reducing their dependency on external suppliers.",
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r"potentially\s+reducing\s+their\s*$",
            "potentially reducing their dependency on external suppliers.",
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # STRATEGIC INITIATIVES PATTERNS
        # =================================================================
        
        # "technological advancements..." patterns
        line = re.sub(
            r"technological\s+advancements\s*\.{2,}\s*$",
            "technological advancements and market adoption milestones.",
            line,
            flags=re.IGNORECASE
        )
        line = re.sub(
            r"technological\s+advancements\s*$",
            "technological advancements and market adoption milestones.",
            line,
            flags=re.IGNORECASE
        )
        
        # "along with milestones..." patterns
        line = re.sub(
            r"along\s+with\s+milestones\s*\.{2,}\s*$",
            "along with milestones for key product launches and technological innovations.",
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # ORIGINAL PATTERNS (preserved)
        # =================================================================

        # Pattern: "Category: X/Y..." at end of line → complete it
        line = re.sub(
            r'((?:Cash\s*Flow\s*Quality|Profitability|Leverage|Liquidity):\s*\d+/\d+)\s*\.{2,}\s*$',
            r'\1, which warrants attention.',
            line,
            flags=re.IGNORECASE
        )

        # Pattern: Line ending with "; I..." → complete it
        line = re.sub(
            r';\s*I\s*\.{2,}\s*$',
            '; this requires further analysis.',
            line,
            flags=re.IGNORECASE
        )

        # Pattern: Line ending with ", I..." → complete it
        line = re.sub(
            r',\s*I\s*\.{2,}\s*$',
            ', which I will monitor closely.',
            line,
            flags=re.IGNORECASE
        )

        # Pattern: Any line ending with word + "..." → truncate to word + "."
        line = re.sub(
            r'(\w+)\s*\.{3,}\s*$',
            r'\1.',
            line
        )
        line = re.sub(
            r'(\w+)\s*\.{2}\s*$',
            r'\1.',
            line
        )

        # Pattern: "I need to see..." mid-line without completion
        line = re.sub(
            r'I\s+need\s+to\s+see\s+[^.!?\n]{0,30}\.{2,}',
            'I need to see concrete evidence.',
            line,
            flags=re.IGNORECASE
        )

        # Pattern: "until I..." mid-line
        line = re.sub(
            r'until\s+I\s+\.{2,}',
            'until I see clearer execution.',
            line,
            flags=re.IGNORECASE
        )

        # =================================================================
        # GENERIC TRAILING ARTICLE/PREPOSITION PATTERNS
        # =================================================================
        
        # Ends with articles
        line = re.sub(r'\s+the\s*\.{2,}\s*$', ' the implications for investors.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+a\s*\.{2,}\s*$', ' a key consideration.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+an\s*\.{2,}\s*$', ' an important factor.', line, flags=re.IGNORECASE)
        
        # Ends with conjunctions
        line = re.sub(r'\s+and\s*\.{2,}\s*$', ' and other relevant factors.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+but\s*\.{2,}\s*$', ' but caution is warranted.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+or\s*\.{2,}\s*$', ' or alternative approaches.', line, flags=re.IGNORECASE)
        
        # Ends with prepositions
        line = re.sub(r'\s+to\s*\.{2,}\s*$', ' to monitor closely.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+of\s*\.{2,}\s*$', ' of significance.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+for\s*\.{2,}\s*$', ' for consideration.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+with\s*\.{2,}\s*$', ' with appropriate risk management.', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+in\s*\.{2,}\s*$', ' in the current environment.', line, flags=re.IGNORECASE)

        fixed_lines.append(line)

    return '\n'.join(fixed_lines)


def reorder_persona_sections(output: str) -> str:
    """
    Reorder persona output to canonical 7-section structure.
    Ensures sections appear in the correct order regardless of how the model generated them.
    Also removes non-canonical sections.
    """
    if not output:
        return output

    # Define canonical section order with detection patterns
    section_defs = [
        ("health", r'^##?\s*\d*\.?\s*financial\s+health\s+rating'),
        ("exec", r'^##?\s*\d*\.?\s*executive\s+summary'),
        ("perf", r'^##?\s*\d*\.?\s*financial\s+performance'),
        ("mda", r'^##?\s*\d*\.?\s*management\s+discussion'),
        ("risks", r'^##?\s*\d*\.?\s*risk\s+factors?'),
        ("metrics", r'^##?\s*\d*\.?\s*key\s+metrics'),
        ("closing", r'^##?\s*\d*\.?\s*closing\s+takeaway'),
    ]
    
    # Non-canonical sections to skip/remove
    non_canonical_patterns = [
        r'^##?\s*\d*\.?\s*strategic\s+initiatives',
        r'^##?\s*\d*\.?\s*capital\s+allocation',
        r'^##?\s*\d*\.?\s*competitive\s+landscape',
        r'^##?\s*\d*\.?\s*catalysts?',
        r'^##?\s*\d*\.?\s*investment\s+recommendation',
        r'^##?\s*\d*\.?\s*investment\s+thesis',
        r'^##?\s*\d*\.?\s*top\s+\d+\s+risks',
        r'^##?\s*\d*\.?\s*key\s+kpis',
        r'^##?\s*\d*\.?\s*cash\s+flow\s+analysis',
        r'^##?\s*\d*\.?\s*key\s+data\s+appendix',
        r'^##?\s*\d*\.?\s*health\s+score\s+drivers',
        r'^##?\s*\d*\.?\s*tl;?dr',
        r'^##?\s*\d*\.?\s*valuation',
    ]

    correct_order = ["health", "exec", "perf", "mda", "risks", "metrics", "closing"]

    lines = output.strip().split('\n')
    sections_found = {}
    current_section = None
    current_content = []
    preamble = []  # Content before first section
    in_non_canonical = False  # Flag to skip non-canonical section content

    for line in lines:
        line_lower = line.lower().strip()

        # Check if this line starts a canonical section
        found_section = None
        for section_key, pattern in section_defs:
            if re.match(pattern, line_lower):
                found_section = section_key
                break
        
        # Check if this is a non-canonical section header
        is_non_canonical = any(re.match(p, line_lower) for p in non_canonical_patterns)

        if found_section:
            # Save previous section if any
            if current_section:
                sections_found[current_section] = '\n'.join(current_content)
            elif current_content and not preamble:
                preamble = current_content

            current_section = found_section
            current_content = [line]
            in_non_canonical = False
        elif is_non_canonical:
            # Save previous section and mark we're in non-canonical territory
            if current_section:
                sections_found[current_section] = '\n'.join(current_content)
                current_content = []
            current_section = None
            in_non_canonical = True  # Skip content until next canonical section
        elif in_non_canonical:
            # Skip this content - it belongs to a non-canonical section
            continue
        else:
            current_content.append(line)

    # Save last section
    if current_section:
        sections_found[current_section] = '\n'.join(current_content)
    elif current_content and not preamble:
        preamble = current_content

    # If no sections were found, return original
    if not sections_found:
        return output

    # Rebuild in correct order
    rebuilt = []

    # Don't add preamble - we want clean section-based output
    # (Any important content should be in Executive Summary)

    # Add sections in correct order
    for key in correct_order:
        if key in sections_found:
            rebuilt.append(sections_found[key])

    if not rebuilt:
        return output

    result = '\n\n'.join(rebuilt)

    # Clean up multiple newlines
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def sanitize_persona_output(output: str, company_context: Optional[Dict] = None) -> str:
    """
    Aggressively post-process persona output to remove any leaked generic elements.
    This is a safety net, not a substitute for good prompts.
    """
    if not output:
        return output
    
    # =========================================================================
    # PHASE 0: AGGRESSIVE CLEANUP OF HEALTH SCORE / KEY DATA APPENDIX SECTIONS
    # =========================================================================
    
    # Remove "Health Score Drivers:" sections entirely (with all sub-bullets)
    output = re.sub(
        r'(?i)(?:^|\n)\s*(?:##?\s*)?Health\s+Score\s+Drivers?\*:?\s*\n(?:[^\n]*\n)*?(?=\n\s*(?:##|\Z|STANCE:|VERDICT:))',
        '\n',
        output,
        flags=re.MULTILINE
    )
    
    # Remove any "Key Data Appendix" section and everything after it until STANCE/VERDICT
    output = re.sub(
        r'(?i)(?:^|\n)\s*(?:##?\s*)?Key\s+Data\s+Appendix\s*\n(?:[^\n]*\n)*?(?=\n\s*(?:STANCE:|VERDICT:|\Z))',
        '\n',
        output,
        flags=re.MULTILINE
    )
    
    # Remove ALL lines that start with → (arrow notation) - COMPREHENSIVE
    output = re.sub(r'(?m)^[→\->]\s*[^\n]*\n?', '', output)
    output = re.sub(r'(?m)^→[^\n]*\n?', '', output)
    
    # Remove lines that contain ONLY metrics in arrow format
    output = re.sub(r'(?m)^.*(?:Revenue|Operating|Net\s+Income|Capital|Assets|Margin|Cash\s+Flow)\s*:\s*\$?[\d.,]+[BMK]?\s*\|.*$\n?', '', output)
    
    # Remove "Financial Health Rating: X, Health Score: X, Overall Rating: X"
    output = re.sub(r'(?i)Financial\s+Health\s+Rating[^\n]*(?:\n|$)', '', output)
    
    # =========================================================================
    # PHASE 0.5: REMOVE ALL REPETITIVE MONITORING SUGGESTIONS
    # =========================================================================
    
    # Pattern: Lines that are just "Monitor X.", "Track Y.", "Watch Z.", "Additionally, evaluate..."
    monitoring_patterns = [
        r'(?m)^(?:Monitor|Track|Watch|Evaluate|Assess|Review|Consider|Additionally,?\s*(?:monitor|track|watch|evaluate|assess|review))[^\n]*\.?\s*\n?',
        r'(?m)^Additionally,?\s+(?:monitor|track|watch|evaluate|assess|review|compare|test|benchmark)[^\n]*\.?\s*\n?',
        r'(?m)^(?:Test\s+sensitivity|Benchmark|Compare\s+cash)[^\n]*\.?\s*\n?',
    ]
    
    for pattern in monitoring_patterns:
        # Only remove if it's near the end of the document (last 500 chars)
        if len(output) > 500:
            end_section = output[-500:]
            cleaned_end = re.sub(pattern, '', end_section, flags=re.IGNORECASE)
            if cleaned_end != end_section:
                output = output[:-500] + cleaned_end
    
    # Also remove trailing monitoring lines regardless of position if they're standalone
    output = re.sub(r'\n(?:Monitor|Track|Watch)\s+[^\n]{10,80}\.?\s*$', '', output, flags=re.IGNORECASE | re.MULTILINE)
    
    # =========================================================================
    # PHASE 1: Remove ALL rating patterns (very aggressive)
    # =========================================================================
    
    # X/100, X/10, X out of 10, etc.
    output = re.sub(r'\b\d{1,3}\s*/\s*100\b', '', output)
    output = re.sub(r'\b\d{1,2}\s*/\s*10\b', '', output)
    output = re.sub(r'\b\d+\s*out of\s*\d+\b', '', output, flags=re.IGNORECASE)
    
    # Score: 85, Rating: 72, Grade: B+, etc.
    output = re.sub(r'(?i)\b(?:score|rating|grade|rank)\s*:?\s*\d+', '', output)
    output = re.sub(r'(?i)\b(?:score|rating|grade|rank)\s*:?\s*[A-F][+-]?', '', output)
    
    # Financial Health Rating: X, Health Score: X, Overall Rating: X
    output = re.sub(r'(?i)(?:financial\s+)?health\s+(?:rating|score)[^.]*\.?', '', output)
    output = re.sub(r'(?i)overall\s+(?:rating|score)[^.]*\.?', '', output)
    output = re.sub(r'(?i)investment\s+(?:rating|grade)[^.]*\.?', '', output)

    # Remove entire "Financial Health Rating" sections with category breakdowns
    output = re.sub(
        r'(?i)(?:financial\s+)?health\s+(?:rating|score)[^.]*(?:\n[^\n]*(?:profitability|leverage|liquidity|cash\s*flow)[^.]*)+',
        '',
        output
    )

    # Remove standalone "Financial Health Rating" sections entirely
    output = re.sub(
        r'(?i)(?:^|\n)(?:##?\s*)?Financial\s+Health\s+Rating\s*\n(?:[^\n]*(?:Profitability|Cash\s*Flow\s*Quality|Leverage|Liquidity)[^\n]*\n?)+',
        '\n',
        output
    )

    # Remove category scoring lines like "Profitability: 25/30" or "Cash Flow Quality: 18/25"
    output = re.sub(
        r'(?i)(?:^|\n)(?:Profitability|Cash\s*Flow\s*Quality|Leverage|Liquidity):\s*\d+\s*/\s*\d+[^\n]*',
        '',
        output
    )

    # Remove category scoring lines with explanations
    output = re.sub(r'(?i)\n[^\n]*(?:profitability|leverage|liquidity|cash\s*flow\s*quality)[^:]*:\s*\d+\s*/\d+[^\n]*', '', output)

    # Remove "Total: X/100" lines
    output = re.sub(r'(?i)\n[^\n]*total[^:]*:\s*\d+\s*/\s*100[^\n]*', '', output)

    # Parenthetical ratings
    output = re.sub(r'\(\s*\d+\s*(?:out of|/)\s*\d+\s*\)', '', output)
    output = re.sub(r'\(\s*[A-F][+-]?\s*\)', '', output)
    
    # Remove "(W) - Watch" type annotations
    output = re.sub(r'\([A-Z]\)\s*-\s*\w+', '', output)
    
    # =========================================================================
    # PHASE 1.5: FIX CAPITALIZATION - Convert all-caps sentences to sentence case
    # =========================================================================
    
    # Find sentences that are ALL CAPS (excluding headers which start with ##)
    def fix_all_caps_sentence(match):
        text = match.group(0)
        # Don't touch markdown headers
        if text.strip().startswith('#'):
            return text
        # Don't touch STANCE: or VERDICT: lines
        if text.strip().startswith(('STANCE:', 'VERDICT:')):
            return text
        # Convert to sentence case
        return text.capitalize()
    
    # Fix all-caps words in the middle of sentences (more than 3 consecutive caps words)
    def fix_caps_run(match):
        text = match.group(0)
        words = text.split()
        # Convert to title case for runs of caps words
        return ' '.join(word.capitalize() for word in words)
    
    # Pattern: 3+ consecutive ALL-CAPS words (not at start of line)
    output = re.sub(r'(?<![#\n])\b([A-Z]{2,}\s+){2,}[A-Z]{2,}\b', fix_caps_run, output)
    
    # =========================================================================
    # PHASE 2: Preserve canonical section headers, remove others
    # =========================================================================

    # Canonical 7-section headers to preserve
    canonical_headers = [
        'financial health rating', 'executive summary', 'financial performance',
        'management discussion', 'risk factors', 'key metrics', 'closing takeaway'
    ]

    # Headers to explicitly remove
    banned_headers = [
        'health score drivers', 'key data appendix', 'strategic initiatives',
        'capital allocation', 'key data'
    ]

    lines = output.split('\n')
    cleaned_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Handle markdown headers
        if stripped.startswith('#'):
            header_text = stripped.lstrip('#').strip().lower()
            # Remove number prefixes like "1. " or "2. "
            header_text = re.sub(r'^\d+\.\s*', '', header_text)

            # Check if it's a banned header - skip entirely
            if any(banned in header_text for banned in banned_headers):
                continue

            # Check if it's a canonical header - keep it
            if any(ch in header_text for ch in canonical_headers):
                cleaned_lines.append(line)
                continue

            # Other headers - skip them (e.g., random headers not in our structure)
            continue

        # Remove standalone "Key Risks:", "Investment Thesis:", etc. without ## prefix
        generic_labels = [
            'key risks:', 'investment thesis:',
            'financial health:', 'key data:', 'catalysts:',
            'conclusion:', 'summary:', 'overview:', 'analysis:',
            'key points:', 'recommendations:', 'verdict:',
            'key data appendix', 'health score drivers'
        ]
        if any(stripped.lower().startswith(label) for label in generic_labels):
            # Keep the content after the colon if any, otherwise skip
            if ':' in stripped:
                content_after = stripped.split(':', 1)[1].strip()
                if content_after:
                    cleaned_lines.append(content_after)
            continue

        cleaned_lines.append(line)

    output = '\n'.join(cleaned_lines)
    
    # =========================================================================
    # PHASE 3: Convert bullet points to prose (optional, less aggressive)
    # =========================================================================
    
    # Count bullet points - if too many, we have a structural problem
    bullet_count = output.count('\n- ') + output.count('• ') + output.count('* ')
    
    # If more than 3 bullet points, try to convert to prose
    if bullet_count > 3:
        # Simple conversion: replace bullet markers with sentence starters
        output = re.sub(r'\n[-•*]\s+', '\n', output)
    
    # =========================================================================
    # PHASE 4: Remove banned generic phrases
    # =========================================================================

    banned_phrases_to_remove = [
        'robust financial', 'strong fundamentals', 'poised for growth',
        'driving shareholder value', 'showcases its dominance', 'incredibly encouraging',
        'fueling future growth', 'welcome addition', 'testament to',
        'well-positioned', 'solid execution', 'attractive opportunity',
    ]

    for phrase in banned_phrases_to_remove:
        output = re.sub(rf'(?i)\b{re.escape(phrase)}\b', '', output)

    # =========================================================================
    # PHASE 4.2: Remove "Key Data Appendix" sections entirely
    # =========================================================================
    # Pattern: "Key Data Appendix" followed by arrow lines with metrics
    output = re.sub(
        r'(?i)(?:^|\n)(?:##?\s*)?Key\s+Data\s+Appendix\s*\n(?:[^\n]*(?:→|Revenue|Operating|Net\s+Income|Capital|Assets|Margin|Dividends)[^\n]*\n?)+',
        '\n',
        output
    )
    # Also remove individual lines that start with → (arrow notation)
    output = re.sub(r'(?m)^→\s*[^\n]*\n?', '', output)

    # =========================================================================
    # PHASE 4.5: Reduce repetitive phrases that make output feel robotic
    # =========================================================================

    # Map of overused phrases to their alternatives (keeps first occurrence, varies subsequent)
    repetitive_patterns = [
        # "as I always look for" variations
        (r'\bas I always look for\b', [
            'a key factor',
            'which I prioritize',
            'critical to my analysis',
            'worth noting',
            'an important consideration',
        ]),
        # "which is always encouraging" variations
        (r'\bwhich is always encouraging\b', [
            'a positive signal',
            'worth noting',
            'reassuring',
            'a good sign',
        ]),
        # "I need to see" variations
        (r'\bI need to see\b', [
            "it's important to monitor",
            'worth watching',
            'requires attention',
            'merits tracking',
        ]),
        # "which is a key consideration" variations
        (r'\bwhich is a key consideration\b', [
            'an important factor',
            'worth weighing',
            'notable',
            'relevant here',
        ]),
        # "as I always consider" variations
        (r'\bas I always consider\b', [
            'factoring in',
            'accounting for',
            'weighing',
            'considering',
        ]),
        # "making it more challenging" variations
        (r'\bmaking it more challenging\b', [
            'adding complexity',
            'complicating',
            'creating uncertainty',
        ]),
        # "requiring a robust" / "requiring careful" variations
        (r'\brequiring (?:a )?(?:robust|careful) (?:contingency plan|consideration|mitigation)\b', [
            'demanding attention',
            'warranting caution',
            'needing oversight',
        ]),
        # "I always question" / "I question whether" variations
        (r'\bI (?:always )?question (?:the sustainability|whether)\b', [
            'the sustainability merits scrutiny',
            'one must evaluate whether',
            'it bears asking whether',
            'sustainability is in question',
        ]),
        # "I am vigilant about" variations
        (r'\bI am vigilant about\b', [
            'we must watch',
            'monitoring',
            'staying alert to',
            'keeping an eye on',
        ]),
        # "what's priced in" repetition (for Marks/Dalio)
        (r"\bwhat's priced in\b", [
            'current expectations',
            'embedded assumptions',
            'the market believes',
            'consensus view',
        ]),
        # "sustainability of these margins" / "sustainability of margins" variations
        (r'\bsustainability of (?:these |the )?margins?\b', [
            'margin durability',
            'whether margins persist',
            'margin resilience',
            'ongoing margin health',
        ]),
        # "long-term reliability" variations
        (r'\blong-term reliability\b', [
            'durability',
            'staying power',
            'persistence',
            'ongoing stability',
        ]),
        # "I am always" / "I always" + verb variations (repetitive first-person patterns)
        (r'\bI (?:am )?always (?:question|monitor|watch|consider|evaluate)\b', [
            'it merits scrutiny',
            'worth monitoring',
            'deserves attention',
            'requires evaluation',
        ]),
        # "lack of explicit MD&A" / "limited MD&A" / "MD&A is limited" variations
        (r'\b(?:lack of|limited|absence of)(?:\s+explicit)?\s+(?:MD&A|management(?:\s+discussion)?(?:\s+and\s+analysis)?|management commentary)\b', [
            'based on available disclosures',
            'from the filing data',
            'interpreting the financial statements',
            'drawing from segment results',
        ]),
        # "sustainability of these margins" / "sustainability" repeated
        (r'\bsustainability\b', [
            'durability',
            'persistence',
            'ongoing health',
            'resilience',
        ]),
        # "the lack of" variations (repetitive negativity)
        (r'\bthe lack of\b', [
            'limited',
            'absent',
            'the missing',
            'without',
        ]),
        # "I am concerned about" / "my concern is" variations
        (r'\b(?:I am concerned about|my concern is|I worry about)\b', [
            'worth watching:',
            'a key risk:',
            'notable risk:',
            'attention needed on',
        ]),
    ]

    for pattern, alternatives in repetitive_patterns:
        matches = list(re.finditer(pattern, output, re.IGNORECASE))
        if len(matches) > 1:
            # Keep first occurrence, replace subsequent with alternatives
            offset_adjustment = 0
            for i, match in enumerate(matches[1:], 1):
                replacement = alternatives[i % len(alternatives)]
                start = match.start() + offset_adjustment
                end = match.end() + offset_adjustment
                original_len = end - start
                output = output[:start] + replacement + output[end:]
                offset_adjustment += len(replacement) - original_len

    # =========================================================================
    # PHASE 5: Clean up artifacts and ensure consistent formatting
    # =========================================================================

    # Remove empty parentheses left behind
    output = re.sub(r'\(\s*\)', '', output)

    # Remove double spaces
    output = re.sub(r'  +', ' ', output)

    # Clean up multiple blank lines (standardize to max 2 newlines between paragraphs)
    output = re.sub(r'\n{3,}', '\n\n', output)

    # Clean up lines that are just whitespace
    lines = [line for line in output.split('\n') if line.strip()]
    output = '\n'.join(lines)

    # Normalize quotation marks (curly to straight)
    output = output.replace('"', '"').replace('"', '"')
    output = output.replace(''', "'").replace(''', "'")

    # Remove trailing whitespace from each line
    output = '\n'.join(line.rstrip() for line in output.split('\n'))

    # Ensure proper spacing after punctuation
    output = re.sub(r'\.([A-Z])', r'. \1', output)  # Period followed by capital without space
    output = re.sub(r'\?([A-Z])', r'? \1', output)
    output = re.sub(r'!([A-Z])', r'! \1', output)

    # =========================================================================
    # PHASE 6: Filter placeholders and industry-mismatched risks
    # =========================================================================
    if company_context:
        output = filter_placeholders_and_irrelevant_risks(output, company_context)

    # =========================================================================
    # PHASE 7: Fix incomplete sentences and truncated output
    # =========================================================================
    output = fix_incomplete_output(output)

    # =========================================================================
    # PHASE 8: Fix mid-text ellipsis patterns
    # =========================================================================
    output = fix_mid_text_ellipsis(output)

    # =========================================================================
    # PHASE 9: Reorder sections to canonical order
    # =========================================================================
    output = reorder_persona_sections(output)

    return output.strip()


# =============================================================================
# PERSONA VOICE ANCHORS - Signature phrases and patterns for each persona
# =============================================================================

PERSONA_VOICE_ANCHORS = {
    "buffett": {
        "must_use_phrases": ["moat", "owner earnings", "circle of competence"],
        "encouraged_phrases": ["Mr. Market", "toll bridge", "wonderful company", "fair price", "durable", "simple"],
        "opening_patterns": ["I'll be honest", "Here's what I understand", "Let me tell you"],
        "emotional_register": "folksy, patient, humble",
        "structural_pattern": "conversational narrative with analogies",
        "never_says": ["EBITDA", "comps", "multiple expansion", "DCF", "target price"],
        "forbidden_concepts": ["Magic Formula", "TAM", "S-curve", "disruption", "exponential growth", "Wright's Law", "activist catalyst", "index fund"],
    },
    "munger": {
        "must_use_phrases": ["invert", "incentives", "stupid"],
        "encouraged_phrases": ["mental models", "lollapalooza", "nothing to add", "obviously", "asinine"],
        "opening_patterns": ["That's easy", "Let me explain", "The problem is"],
        "emotional_register": "blunt, pithy, sardonic",
        "structural_pattern": "short declarative sentences, minimal hedging",
        "never_says": ["I believe", "in my opinion", "potentially", "perhaps"],
        "forbidden_concepts": ["Magic Formula", "PEG ratio", "TAM", "S-curve", "exponential", "activist catalyst", "stay the course"],
    },
    "graham": {
        "must_use_phrases": ["margin of safety", "intrinsic value"],
        "encouraged_phrases": ["net current asset", "intelligent investor", "speculator", "Mr. Market"],
        "opening_patterns": ["We begin, as we must", "The balance sheet reveals", "The investor is offered"],
        "emotional_register": "academic, measured, quantitative",
        "structural_pattern": "formal prose with specific numbers, no adjectives",
        "never_says": ["exciting", "impressive", "robust", "strong"],
        "forbidden_concepts": ["TAM", "S-curve", "disruption", "exponential", "Wright's Law", "paradigm shift", "tenbagger", "catalyst"],
    },
    "lynch": {
        "must_use_phrases": ["PEG ratio"],
        "encouraged_phrases": ["story", "boring", "Fast Grower", "Stalwart", "tenbagger", "Wall Street is missing", "know the company", "kick the tires"],
        "opening_patterns": ["I love this", "Here's the story", "Nobody's looking at", "Let me tell you about", "This is a simple business"],
        "emotional_register": "enthusiastic, practical, accessible, explain-like-I'm-five",
        "structural_pattern": "excited narrative about real products and customers with stock classification",
        "never_says": ["macro", "Fed policy", "interest rates", "geopolitical", "factor-based", "scoring model"],
        "forbidden_concepts": ["Magic Formula", "margin of safety", "debt cycle", "paradigm shift", "index fund", "activist catalyst", "health rating", "scoring formula"],
        # Lynch-specific requirements
        "required_lynch_elements": [
            "STOCK CLASSIFICATION: Fast Grower, Stalwart, Slow Grower, Cyclical, Turnaround, or Asset Play",
            "PEG RATIO: Calculate and interpret (PEG < 1 = undervalued, 1-2 = fair, > 2 = expensive)",
            "THE STORY: What does the company actually do? Explain it simply.",
            "CUSTOMERS: Who buys from them? Why? Can you see it in your daily life?",
            "GROWTH RATE: Earnings growth rate and sustainability",
            "VALUATION: P/E ratio and how it compares to growth rate",
            "INNING: What inning of growth is this company in?",
        ],
        "anti_rating_guidance": "NEVER use factor-based scoring (72/100). Lynch uses intuition, stories, and simple math (PEG). No health ratings.",
    },
    "dalio": {
        "must_use_phrases": ["cycle", "machine", "paradigm"],
        "encouraged_phrases": ["deleveraging", "correlation", "debt cycle", "mechanism", "risk parity", "credit", "liquidity"],
        "opening_patterns": ["To understand this", "We are in", "The machine"],
        "emotional_register": "systematic, mechanical, dispassionate",
        "structural_pattern": "macro-first analysis connecting company to economic cycles",
        "never_says": ["exciting", "love", "hate", "feel"],
        "forbidden_concepts": ["tenbagger", "PEG ratio", "Magic Formula", "moat", "owner earnings", "S-curve", "index fund", "activist catalyst", "forward guidance"],
        "required_macro_factors": [
            "Where we are in the debt cycle (short-term and long-term)",
            "Interest rate environment and cost of capital implications",
            "Credit conditions and liquidity dynamics",
            "Currency/FX exposure if international",
            "Geopolitical risk factors (Taiwan/China for semiconductors)",
            "Correlation to macro factors (rates, credit spreads, risk assets)",
            "Supply chain concentration risk (TSMC dependency for chips)",
        ],
    },
    "wood": {
        "must_use_phrases": ["disruption", "exponential"],
        "encouraged_phrases": ["Wright's Law", "S-curve", "TAM", "convergence", "2030"],
        "opening_patterns": ["Traditional analysts", "Where are we on the S-curve", "By 2030"],
        "emotional_register": "visionary, optimistic, long-horizon",
        "structural_pattern": "future-focused thesis with exponential thinking",
        "never_says": ["P/E ratio", "current profitability", "near-term", "skeptical"],
        "forbidden_concepts": ["margin of safety", "net current asset", "debt cycle", "Magic Formula", "index fund", "stay the course"],
    },
    "greenblatt": {
        "must_use_phrases": ["return on capital", "earnings yield"],
        "encouraged_phrases": ["Magic Formula", "good company", "cheap price", "mean reversion", "ROIC", "EV/EBIT"],
        "opening_patterns": ["Return on Capital:", "The formula says", "Simple:", "Two numbers matter:"],
        "emotional_register": "minimal, formula-driven, matter-of-fact, no-nonsense",
        "structural_pattern": "extremely brief with ROIC calculation, earnings yield, and binary verdict",
        "never_says": ["story", "narrative", "management quality", "moat", "rating", "score", "guidance"],
        "forbidden_concepts": ["tenbagger", "PEG ratio", "TAM", "S-curve", "debt cycle", "paradigm", "index fund", "activist catalyst", "forward guidance"],
        # Greenblatt-specific requirements - Magic Formula
        "required_greenblatt_elements": [
            "ROIC or ROC calculation (EBIT / Net Working Capital + Net PPE)",
            "Earnings Yield (EBIT / Enterprise Value)",
            "Comparison to cost of capital (is ROIC > WACC?)",
            "Binary conclusion: Good AND Cheap, Good but Expensive, or Not Good",
        ],
        "magic_formula_framework": """
GREENBLATT'S MAGIC FORMULA:
1. Return on Capital (ROC) = EBIT / (Net Working Capital + Net PPE)
   - High ROC = efficient use of capital = GOOD company
   - Compare to industry average and cost of capital

2. Earnings Yield = EBIT / Enterprise Value
   - High earnings yield = CHEAP stock
   - Compare to risk-free rate and market average

3. VERDICT: Must answer "Is it GOOD and CHEAP?"
   - Good AND Cheap = Buy
   - Good but Expensive = Pass (wait for better price)
   - Not Good = Pass (regardless of price)
""",
        "anti_rating_guidance": "NEVER give numeric ratings (72/100, 8/10). Greenblatt uses ROC rank + Earnings Yield rank, not arbitrary scores.",
    },
    "bogle": {
        "must_use_phrases": ["costs", "index"],
        "encouraged_phrases": ["haystack", "stay the course", "90%", "speculation", "compounding", "simplicity", "diversification", "fees", "long-term"],
        "opening_patterns": ["Let me tell you a secret", "Here's what Wall Street won't", "The math is simple", "I've spent my career"],
        "emotional_register": "wise, humble, grandfatherly, skeptical of stock-picking, patient",
        "structural_pattern": "gentle but firm argument for indexing over individual stocks, with honest assessment of company fundamentals",
        "never_says": ["buy this stock", "outperform", "beat the market", "alpha", "upside potential", "price target", "I'm bullish"],
        "forbidden_concepts": ["tenbagger", "catalyst", "target price", "Magic Formula", "TAM", "disruption", "activist", "debt cycle", "guidance", "next quarter"],
        # Bogle-specific requirements
        "valuation_requirement": "You MUST discuss valuation - P/E ratio, earnings yield, or price-to-sales. Bogle believed in buying at reasonable prices, not at any price.",
        "anti_rating_guidance": "NEVER give this stock a rating or score. Bogle would find that absurd. Instead, assess whether owning this single stock makes sense vs. owning the entire market.",
    },
}


# =============================================================================
# METRIC EXTRACTION - COMPANY-SPECIFIC, NOT GENERIC
# =============================================================================

def extract_persona_relevant_metrics(persona_id: str, ratios: Dict, financial_data: Dict, company_name: str = "") -> str:
    """Extract metrics formatted for each persona's analytical style."""

    def fmt_currency(val):
        if val is None:
            return None  # Return None instead of placeholder - let caller decide whether to include
        try:
            val = float(val)
            if abs(val) >= 1e9:
                return f"${val/1e9:.2f}B"  # Always 2 decimals for clarity (e.g., $31.91B not $31.9B or $31.)
            if abs(val) >= 1e6:
                return f"${val/1e6:.2f}M"  # Always 2 decimals
            return f"${val:,.2f}"  # 2 decimals for smaller amounts
        except (ValueError, TypeError):
            return None

    def fmt_pct(val):
        if val is None:
            return None  # Return None instead of "N/A"
        try:
            return f"{float(val)*100:.1f}%"
        except (ValueError, TypeError):
            return None

    def fmt_ratio(val):
        if val is None:
            return None  # Return None instead of "N/A"
        try:
            return f"{float(val):.2f}x"
        except (ValueError, TypeError):
            return None

    def add_metric(lines: list, label: str, value) -> None:
        """Only add metric to lines if value is not None."""
        if value is not None:
            lines.append(f"- {label}: {value}")
    
    def get_val(data, *keys):
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key, {})
            else:
                return None
        if isinstance(current, dict) and current:
            return list(current.values())[0]
        return current if current else None
    
    # Extract common metrics
    revenue = get_val(financial_data, "income_statement", "revenue")
    net_income = get_val(financial_data, "income_statement", "net_income")
    operating_income = get_val(financial_data, "income_statement", "operating_income")
    cash = get_val(financial_data, "balance_sheet", "cash")
    total_equity = get_val(financial_data, "balance_sheet", "total_equity")
    current_assets = get_val(financial_data, "balance_sheet", "current_assets")
    total_liabilities = get_val(financial_data, "balance_sheet", "total_liabilities")
    
    fcf = ratios.get("fcf")
    gross_margin = ratios.get("gross_margin")
    operating_margin = ratios.get("operating_margin")
    net_margin = ratios.get("net_margin")
    roe = ratios.get("roe")
    current_ratio = ratios.get("current_ratio")
    debt_to_equity = ratios.get("debt_to_equity")
    revenue_growth = ratios.get("revenue_growth_yoy")
    
    is_profitable = net_income is not None and float(net_income) > 0
    is_fcf_positive = fcf is not None and float(fcf) > 0

    # Build metrics block - only include metrics that have values
    lines = []

    # Core financials
    rev_fmt = fmt_currency(revenue)
    if rev_fmt:
        lines.append(f"Revenue: {rev_fmt}")

    ni_fmt = fmt_currency(net_income)
    if ni_fmt:
        lines.append(f"Net Income: {ni_fmt} {'(profitable)' if is_profitable else '(loss)'}")

    fcf_fmt = fmt_currency(fcf)
    if fcf_fmt:
        lines.append(f"Free Cash Flow: {fcf_fmt} {'(positive)' if is_fcf_positive else '(negative)'}")

    cash_fmt = fmt_currency(cash)
    if cash_fmt:
        lines.append(f"Cash: {cash_fmt}")

    # Margins
    gm_fmt = fmt_pct(gross_margin)
    if gm_fmt:
        lines.append(f"Gross Margin: {gm_fmt}")

    om_fmt = fmt_pct(operating_margin)
    if om_fmt:
        lines.append(f"Operating Margin: {om_fmt}")

    roe_fmt = fmt_pct(roe)
    if roe_fmt:
        lines.append(f"Return on Equity: {roe_fmt}")

    rg_fmt = fmt_pct(revenue_growth)
    if rg_fmt:
        lines.append(f"Revenue Growth: {rg_fmt}")

    de_fmt = fmt_ratio(debt_to_equity)
    if de_fmt:
        lines.append(f"Debt/Equity: {de_fmt}")

    cr_fmt = fmt_ratio(current_ratio)
    if cr_fmt:
        lines.append(f"Current Ratio: {cr_fmt}")

    # Balance sheet section
    bs_lines = []
    ca_fmt = fmt_currency(current_assets)
    if ca_fmt:
        bs_lines.append(f"- Current Assets: {ca_fmt}")

    tl_fmt = fmt_currency(total_liabilities)
    if tl_fmt:
        bs_lines.append(f"- Total Liabilities: {tl_fmt}")

    eq_fmt = fmt_currency(total_equity)
    if eq_fmt:
        bs_lines.append(f"- Shareholders Equity: {eq_fmt}")

    if bs_lines:
        lines.append("\nBalance Sheet:")
        lines.extend(bs_lines)

    # ==========================================================================
    # GREENBLATT-SPECIFIC METRICS: Calculate ROC and Earnings Yield
    # ==========================================================================
    if persona_id == "greenblatt":
        greenblatt_lines = []

        # EBIT (Operating Income)
        ebit = operating_income
        ebit_fmt = fmt_currency(ebit)

        # For ROC: Invested Capital = Net Working Capital + Net Fixed Assets
        # Simplified: (Current Assets - Current Liabilities) + (Total Assets - Current Assets)
        # Or use Total Assets - Current Assets if available
        # Approximation: Total Equity + Total Debt (or Total Assets - Cash for simplicity)

        # Get additional balance sheet items
        total_assets = get_val(financial_data, "balance_sheet", "total_assets")
        current_liabilities = get_val(financial_data, "balance_sheet", "current_liabilities")
        long_term_debt = get_val(financial_data, "balance_sheet", "long_term_debt")
        total_debt = get_val(financial_data, "balance_sheet", "total_debt")

        # Calculate Net Working Capital
        nwc = None
        if current_assets is not None and current_liabilities is not None:
            try:
                nwc = float(current_assets) - float(current_liabilities)
            except (ValueError, TypeError):
                pass

        # Calculate Net Fixed Assets (Total Assets - Current Assets)
        net_fixed_assets = None
        if total_assets is not None and current_assets is not None:
            try:
                net_fixed_assets = float(total_assets) - float(current_assets)
            except (ValueError, TypeError):
                pass

        # Invested Capital = NWC + Net Fixed Assets
        invested_capital = None
        if nwc is not None and net_fixed_assets is not None:
            invested_capital = nwc + net_fixed_assets
        elif total_equity is not None:
            # Fallback: use Total Equity + Total Debt
            try:
                equity_val = float(total_equity)
                debt_val = float(total_debt) if total_debt else (float(total_liabilities) * 0.4 if total_liabilities else 0)
                invested_capital = equity_val + debt_val
            except (ValueError, TypeError):
                pass

        # Enterprise Value = Market Cap + Total Debt - Cash
        # If we don't have market cap, provide what we can
        market_cap = ratios.get("market_cap")
        enterprise_value = None
        if market_cap is not None:
            try:
                mc = float(market_cap)
                debt = float(total_debt) if total_debt else (float(total_liabilities) * 0.4 if total_liabilities else 0)
                cash_val = float(cash) if cash else 0
                enterprise_value = mc + debt - cash_val
            except (ValueError, TypeError):
                pass

        # Calculate ROC
        roc_pct = None
        if ebit is not None and invested_capital is not None and invested_capital > 0:
            try:
                roc_pct = (float(ebit) / invested_capital) * 100
            except (ValueError, TypeError):
                pass

        # Calculate Earnings Yield
        earnings_yield_pct = None
        if ebit is not None and enterprise_value is not None and enterprise_value > 0:
            try:
                earnings_yield_pct = (float(ebit) / enterprise_value) * 100
            except (ValueError, TypeError):
                pass

        # Build Greenblatt-specific ratios block
        greenblatt_lines.append("\n--- MAGIC FORMULA INPUTS ---")

        if ebit_fmt:
            greenblatt_lines.append(f"EBIT (Operating Income): {ebit_fmt}")
        else:
            greenblatt_lines.append("EBIT: Cannot be calculated - operating income not available in this filing")

        if invested_capital is not None:
            greenblatt_lines.append(f"Invested Capital (NWC + Net Fixed Assets): {fmt_currency(invested_capital)}")
        else:
            greenblatt_lines.append("Invested Capital: Cannot be calculated - need current assets, current liabilities, and net fixed assets from balance sheet")

        if enterprise_value is not None:
            greenblatt_lines.append(f"Enterprise Value: {fmt_currency(enterprise_value)}")
        else:
            greenblatt_lines.append("Enterprise Value: Cannot be calculated - requires market cap (current share price × shares outstanding), which is not in SEC filings")

        greenblatt_lines.append("\n--- CALCULATED RATIOS ---")

        if roc_pct is not None:
            roc_assessment = "Excellent (>25%)" if roc_pct > 25 else "Good (15-25%)" if roc_pct >= 15 else "Average (10-15%)" if roc_pct >= 10 else "Poor (<10%)"
            greenblatt_lines.append(f"Return on Capital (ROC): {roc_pct:.1f}% - {roc_assessment}")
        else:
            greenblatt_lines.append("Return on Capital (ROC): Cannot be calculated - need both EBIT and Invested Capital")

        if earnings_yield_pct is not None:
            ey_assessment = "Cheap (>10%)" if earnings_yield_pct > 10 else "Fair (5-10%)" if earnings_yield_pct >= 5 else "Expensive (<5%)"
            greenblatt_lines.append(f"Earnings Yield: {earnings_yield_pct:.1f}% - {ey_assessment}")
        else:
            greenblatt_lines.append("Earnings Yield: Cannot be calculated - requires Enterprise Value (market cap + debt - cash)")

        if market_cap is None:
            greenblatt_lines.append("\nNOTE: P/E and FCF Yield cannot be calculated without current market price data, which is outside SEC filing scope.")

        lines.extend(greenblatt_lines)

    pe_ratio = ratios.get("pe_ratio")
    if pe_ratio is None and persona_id != "greenblatt":
        lines.append("\nValuation Note: P/E ratio and market-based metrics require current share price data, which is not included in SEC filings. To calculate: obtain current market cap, then divide by net income.")

    return "\n".join(lines) if lines else "Limited financial data available."


def calculate_authenticity_score(persona_id: str, output: str, persona: Dict) -> Tuple[int, List[str]]:
    """
    Calculate an authenticity score (0-100) for how well the output embodies the persona.

    Returns:
        Tuple of (score, list of issues/feedback)
    """
    score = 100
    feedback = []
    output_lower = output.lower()

    voice_anchors = PERSONA_VOICE_ANCHORS.get(persona_id, {})
    must_use = voice_anchors.get("must_use_phrases", [])
    encouraged = voice_anchors.get("encouraged_phrases", [])
    never_says = voice_anchors.get("never_says", [])
    forbidden_concepts = voice_anchors.get("forbidden_concepts", [])

    persona_markers = {
        "buffett": ["moat", "owner earnings", "wonderful company", "circle of competence", "durable competitive advantage", "margin of safety", "long-term value", "Mr. Market", "economic moat"],
        "munger": ["invert", "lollapalooza", "mental models", "incentives", "stupid", "obviously", "asinine", "worldly wisdom"],
        "graham": ["margin of safety", "intrinsic value", "net current asset", "intelligent investor", "speculator", "Mr. Market", "financial metrics"],
        "lynch": ["tenbagger", "peg ratio", "fast grower", "stalwart", "story", "boring", "Wall Street is missing", "know the company", "kick the tires", "what inning"],
        "dalio": ["debt cycle", "economic machine", "paradigm", "deleveraging", "correlation", "debt cycle", "mechanism", "risk parity", "credit", "liquidity"],
        "wood": ["disruption", "exponential", "wright's law", "s-curve", "2030", "TAM", "convergence", "market potential"],
        "greenblatt": ["return on capital", "earnings yield", "magic formula", "good company", "cheap price", "mean reversion", "ROIC", "EV/EBIT"],
        "bogle": ["index", "costs", "haystack", "stay the course", "90%", "speculation", "compounding", "simplicity", "diversification", "fees", "long-term"],
        "marks": ["second-level thinking", "cycle", "risk control", "durable competitive advantage", "long-term value", "competitive landscape"],
        "ackman": ["activist", "catalyst", "simple business", "durable competitive advantage", "long-term value", "competitive landscape"],
    }

    for phrase in must_use:
        if phrase.lower() not in output_lower:
            score -= 10
            feedback.append(f"Missing required phrase: '{phrase}'")

    encouraged_count = sum(1 for phrase in encouraged if phrase.lower() in output_lower)
    if encouraged_count == 0:
        score -= 5
        feedback.append("No encouraged vocabulary used")

    for phrase in never_says:
        if phrase.lower() in output_lower:
            score -= 15
            feedback.append(f"Used forbidden phrase: '{phrase}'")

    for concept in forbidden_concepts:
        if concept.lower() in output_lower:
            score -= 10
            feedback.append(f"Used forbidden concept: '{concept}'")

    for other_persona, markers in persona_markers.items():
        if other_persona != persona_id:
            for marker in markers:
                if marker.lower() in output_lower and marker.lower() not in [p.lower() for p in must_use + encouraged]:
                    score -= 5
                    feedback.append(f"Voice contamination: '{marker}' belongs to {other_persona}")

    return max(0, score), feedback


# =============================================================================
# PERSONAS - Mapping of persona ID to persona information
# =============================================================================

PERSONAS = {
    "buffett": {
        "name": "Warren Buffett",
        "philosophy": "Invest in businesses with durable competitive advantages and long-term value creation. Focus on companies with strong moats, predictable earnings, and shareholder-friendly management. Buy when the market is irrational and sell when it's overvalued. The key is patience and understanding the business model.",
        "voice_style": "Folksy, patient, humble, conversational. Uses analogies like 'Mr. Market' and 'toll bridge'. Speaks in simple terms about business fundamentals and long-term value.",
        "framework": "Circle of Competence: Invest only in businesses you understand deeply. Focus on companies with durable competitive advantages (moats) that protect profits from competition. Value is in the business, not the stock price.",
        "key_metrics": ["moat", "owner earnings", "circle of competence", "durable competitive advantage", "margin of safety", "long-term value"],
    },
    "munger": {
        "name": "Charlie Munger",
        "philosophy": "Invest in businesses that solve real problems and create lasting value. Focus on companies with powerful mental models and simple business models. The key is understanding the business and avoiding complex financial engineering.",
        "voice_style": "Blunt, pithy, sardonic. Uses mental models like 'lollapalooza' and 'incentives matter'. Speaks in short, declarative sentences with minimal hedging.",
        "framework": "Mental Models: Use a diverse set of mental models (e.g., psychology, business, physics) to understand companies. Focus on businesses that solve real problems and create lasting value.",
        "key_metrics": ["mental models", "lollapalooza", "incentives", "stupid", "obviously", "asinine", "simple business"],
    },
    "graham": {
        "name": "Benjamin Graham",
        "philosophy": "Invest in companies with strong intrinsic value and a margin of safety. Focus on companies with predictable earnings, strong balance sheets, and low debt. The key is buying at a significant discount to intrinsic value.",
        "voice_style": "Academic, measured, quantitative. Uses formal prose with specific numbers and avoids adjectives. Focuses on financial metrics and risk management.",
        "framework": "Intrinsic Value: The true value of a company based on its financials. Margin of Safety: The buffer between intrinsic value and market price. Focus on companies with strong balance sheets and predictable earnings.",
        "key_metrics": ["margin of safety", "intrinsic value", "net current asset", "intelligent investor", "speculator", "Mr. Market", "financial metrics"],
    },
    "lynch": {
        "name": "Peter Lynch",
        "philosophy": "Invest in companies with strong growth potential and simple business models. Focus on companies with clear stories and real products. The key is understanding the business and its customers.",
        "voice_style": "Enthusiastic, practical, accessible. Uses stories about real products and customers. Focuses on growth, valuation, and business classification.",
        "framework": "Story-Based Analysis: Understand the business story, customers, and growth potential. Focus on companies with clear stories and real products.",
        "key_metrics": ["PEG ratio", "tenbagger", "fast grower", "stalwart", "story", "boring", "Wall Street is missing", "know the company", "kick the tires"],
    },
    "dalio": {
        "name": "Ray Dalio",
        "philosophy": "Invest in businesses that align with macroeconomic cycles. Focus on companies that benefit from economic expansion and are resilient during downturns. The key is understanding the economic cycle and timing.",
        "voice_style": "Systematic, mechanical, dispassionate. Uses macroeconomic analysis and economic cycles. Focuses on the broader economic environment.",
        "framework": "Macro-Cycle Investing: Understand the current phase of the economic cycle (expansion, peak, contraction, trough). Invest in businesses that benefit from expansion and are resilient during downturns.",
        "key_metrics": ["debt cycle", "economic machine", "paradigm", "deleveraging", "correlation", "debt cycle", "mechanism", "risk parity", "credit", "liquidity"],
    },
    "wood": {
        "name": "Cathie Wood",
        "philosophy": "Invest in companies with disruptive technologies and exponential growth potential. Focus on companies that are creating new markets and solving real problems. The key is understanding the technology and market potential.",
        "voice_style": "Visionary, optimistic, long-horizon. Uses future-focused analysis and exponential thinking. Focuses on disruptive technologies and market potential.",
        "framework": "Exponential Growth: Focus on companies with disruptive technologies and exponential growth potential. The key is understanding the technology and market potential.",
        "key_metrics": ["disruption", "exponential", "Wright's Law", "S-curve", "TAM", "convergence", "2030", "market potential"],
    },
    "greenblatt": {
        "name": "Joel Greenblatt",
        "philosophy": "Invest in companies with high return on capital and high earnings yield. Focus on companies with strong financials and low valuation. The key is using the Magic Formula to identify good companies.",
        "voice_style": "Minimal, formula-driven, matter-of-fact. Uses the Magic Formula to identify good companies. Focuses on financial metrics and simple calculations.",
        "framework": "Magic Formula: Use the Magic Formula to identify good companies. The key is high return on capital and high earnings yield.",
        "key_metrics": ["return on capital", "earnings yield", "Magic Formula", "good company", "cheap price", "mean reversion", "ROIC", "EV/EBIT"],
    },
    "bogle": {
        "name": "John Bogle",
        "philosophy": "Invest in index funds and diversified portfolios. Focus on low-cost, diversified investments. The key is long-term compounding and avoiding active management.",
        "voice_style": "Wise, humble, grandfatherly. Uses long-term perspective and diversified portfolios. Focuses on low-cost, diversified investments.",
        "framework": "Index Investing: Invest in low-cost index funds and diversified portfolios. The key is long-term compounding and avoiding active management.",
        "key_metrics": ["costs", "index", "haystack", "stay the course", "90%", "speculation", "compounding", "simplicity", "diversification", "fees", "long-term"],
    },
    "marks": {
        "name": "Howard Marks",
        "philosophy": "Invest in businesses with strong competitive advantages and long-term value. Focus on companies with durable competitive advantages and strong balance sheets. The key is understanding the business and its competitive landscape.",
        "voice_style": "Thoughtful, cyclical, risk-focused. Uses second-level thinking and cycle awareness. Focuses on risk control and contrarian positioning.",
        "framework": "Second-Level Thinking: Understand the business and its competitive landscape. Focus on companies with strong competitive advantages and long-term value.",
        "key_metrics": ["second-level thinking", "cycle", "risk control", "durable competitive advantage", "long-term value", "competitive landscape"],
    },
    "ackman": {
        "name": "Bill Ackman",
        "philosophy": "Invest in companies with strong competitive advantages and long-term value. Focus on companies with durable competitive advantages and strong balance sheets. The key is understanding the business and its competitive landscape.",
        "voice_style": "Bold, activist, conviction-driven. Uses activist lens and catalyst focus. Focuses on simple businesses with clear improvement paths.",
        "framework": "Activist Value: Understand the business and its competitive landscape. Focus on companies with strong competitive advantages and long-term value.",
        "key_metrics": ["activist", "catalyst", "simple business", "durable competitive advantage", "long-term value", "competitive landscape"],
    },
}


class PersonaEngine:
    """Engine for generating persona-specific investment analyses."""
    
    def __init__(self):
        self.gemini_client = GeminiClient()

    # Compatibility shim for older tests/utilities.
    def _build_prompt(
        self,
        persona_id: str,
        company_name: str,
        metrics_block: str,
        general_summary: str,
        company_context: Dict[str, Any],
    ) -> str:
        normalized_id = normalize_persona_id(persona_id)
        template = PERSONA_PROMPT_TEMPLATES.get(
            normalized_id,
            "Company: {company_name}\n\nMetrics:\n{metrics_block}\n",
        )
        prompt = template.format(company_name=company_name, metrics_block=metrics_block)

        sector_risks = company_context.get("sector_risks") if isinstance(company_context, dict) else None
        risks_block = ""
        if isinstance(sector_risks, list) and sector_risks:
            risks_block = "\n\nSector/Company-specific risks to consider:\n" + "\n".join(
                f"- {risk}" for risk in sector_risks[:6] if risk
            )

        prompt += (
            f"\n\nGeneral context:\n{general_summary}\n"
            f"{risks_block}\n\n"
            "## Final Recommendation Summary\n"
            "MANDATORY FINAL SECTION\n"
            "Write a 2-3 sentence summary with your final verdict and the single biggest driver behind it."
        )
        return prompt
    
    def generate_persona_analysis(
        self,
        persona_id: str,
        company_name: str,
        general_summary: str,
        ratios: Dict,
        financial_data: Dict,
        target_length: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate a persona-specific analysis for a company.
        
        Args:
            persona_id: ID of the persona (e.g., 'buffett', 'bogle')
            company_name: Name of the company being analyzed
            general_summary: Brief context about the company
            ratios: Financial ratios dictionary
            financial_data: Raw financial data dictionary
            target_length: Optional target word count
        
        Returns:
            Dictionary with persona analysis including summary, stance, reasoning, key_points
        """
        target_length = clamp_summary_target_length(target_length)

        # Normalize persona ID
        normalized_id = normalize_persona_id(persona_id)
        
        # Get persona info
        persona_info = PERSONAS.get(normalized_id)
        if not persona_info:
            return {
                "persona_name": persona_id,
                "summary": f"Unknown persona: {persona_id}",
                "stance": "Hold",
                "reasoning": "Persona not found",
                "key_points": []
            }
        
        # Extract company-specific context
        company_context = extract_company_specific_context(company_name, financial_data, ratios)
        
        # Get persona-relevant metrics
        metrics_context = extract_persona_relevant_metrics(
            persona_id=normalized_id,
            ratios=ratios,
            financial_data=financial_data,
            company_name=company_name
        )
        
        # Build the persona prompt
        prompt = self._build_persona_prompt(
            persona_id=normalized_id,
            persona_info=persona_info,
            company_name=company_name,
            general_summary=general_summary,
            metrics_context=metrics_context,
            company_context=company_context,
            target_length=target_length
        )
        
        # Generate using Gemini
        result = self.gemini_client.generate_premium_persona_view(
            prompt=prompt,
            persona_name=persona_info.get("name", persona_id)
        )

        # Post-process: sanitize output
        if result.get("summary"):
            result["summary"] = sanitize_persona_output(result["summary"], company_context)
            if target_length:
                result["summary"] = enforce_summary_target_length(
                    result["summary"], target_length
                )
        
        return result
    
    def _build_persona_prompt(
        self,
        persona_id: str,
        persona_info: Dict,
        company_name: str,
        general_summary: str,
        metrics_context: str,
        company_context: Dict,
        target_length: Optional[int] = None
    ) -> str:
        """Build the complete persona prompt."""
        
        persona_name = persona_info.get("name", persona_id.title())
        philosophy = persona_info.get("philosophy", "")
        voice_style = persona_info.get("voice_style", "")
        framework = persona_info.get("framework", "")
        key_metrics = persona_info.get("key_metrics", [])
        
        # Get voice anchors for authenticity
        voice_anchors = PERSONA_VOICE_ANCHORS.get(persona_id, {})
        must_use_phrases = voice_anchors.get("must_use_phrases", [])
        encouraged_phrases = voice_anchors.get("encouraged_phrases", [])
        never_says = voice_anchors.get("never_says", [])
        
        # Default target length
        word_target = target_length if target_length else 750
        
        # Format company context
        company_context_str = format_company_context_for_prompt(company_context) if company_context else ""
        
        prompt = f"""You ARE {persona_name}. Write EXACTLY as {persona_name} would speak - with their exact vocabulary, cadence, and philosophical lens.

PERSONA PHILOSOPHY:
{philosophy}

VOICE STYLE:
{voice_style}

ANALYTICAL FRAMEWORK:
{framework}

KEY METRICS TO ANALYZE:
{', '.join(key_metrics)}

AUTHENTICITY REQUIREMENTS:
- You MUST naturally use these phrases: {', '.join(must_use_phrases[:3])}
- You are encouraged to use: {', '.join(encouraged_phrases[:5])}
- You MUST NEVER say: {', '.join(never_says[:5])}

COMPANY: {company_name}

COMPANY CONTEXT:
{general_summary}

{company_context_str}

FINANCIAL METRICS:
{metrics_context}

LENGTH REQUIREMENT: Write approximately {word_target} words. This is a HARD requirement.

MANDATORY 7-SECTION STRUCTURE (OUTPUT IN EXACT ORDER - CRITICAL):
You MUST output these 7 sections in EXACTLY this order. Do not skip, reorder, combine, or add extra sections.

## 1. Financial Health Rating
[ONE LINE ONLY in YOUR voice: State your overall assessment with stance (bullish/bearish/neutral) and conviction (high/medium/low).
Example: "From my perspective, this is a HOLD with medium conviction - strong business but rich valuation."]

## 2. Executive Summary
[2-3 paragraphs in YOUR unique voice:
- Your conviction and why
- State ONE clear spine for the memo: operating strength (pricing/margins) versus cash conversion durability (OCF→FCF, capex, working capital), and keep the thesis aligned to it
- Core investment thesis through YOUR analytical lens
- What matters most to YOU as {persona_name}
- Conviction tone MUST match the action: if your stance is PASS/WATCH/HOLD, explicitly explain the restraint (what blocks action now) and temper language accordingly
- Do NOT describe your process ("here is how I think...", "my framework is..."). State conclusions; let structure imply process
- Include ONE explicit "what changed vs prior comparable period" sentence (QoQ for quarterly, YoY for annual) and do not repeat that same change later
Write in flowing prose. NO bullet lists of "Monitor X".]

## 3. Financial Performance
[Analyze through YOUR lens - what metrics matter to {persona_name}?
- Cite specific figures with $ and %
- Explain what the numbers MEAN, not just what they ARE
- If operating margin and net margin diverge significantly, explain WHY (one-time items, non-operating income, etc.)
- Explicitly compare this period vs the prior comparable period (QoQ for quarterly, YoY for annual): what improved, what deteriorated, and why
Every metric must have interpretation, not just data.]

## 4. Management Discussion & Analysis
[Evaluate through YOUR framework:
- Capital allocation - does it align with {persona_name}'s principles?
- Earnings quality concerns
- What would YOU want management to do differently?
- Explicitly call out what changed versus the prior comparable period in posture (capex pacing, cost discipline, capital return) and whether numbers corroborate it
Do NOT speculate about "management commentary" not in filings.]

## 5. Risk Factors
[3-5 SPECIFIC risks that concern YOU as {persona_name}:
- Each risk must have a clear name and 2-4 sentence explanation
- Be company-specific, not generic
- Quantify impact where possible
- Include explicit weighting: severity/likelihood (High/Med/Low) and one sentence on why it does NOT dominate your thesis yet (and what would make it dominate)
- Include a change signal: one concrete sign the risk is getting worse versus the prior comparable period
Format: "**Risk Name**: Explanation."
Do NOT use generic risks without specific context.]

## 6. Key Metrics
[Write 5 specific metrics YOU would track, explained in PROSE:
"I would watch [metric] because [reason]..."
Do NOT use bullet points or "Monitor X" format.
Write complete sentences explaining WHY each metric matters to your investment thesis.]

## 7. Closing Takeaway
[Your final verdict in YOUR authentic voice:
- 2-3 sentences synthesizing your view
- Clear stance and what would change it
End with these exact lines:

STANCE: [Buy/Hold/Sell/Pass/Watch]
CONVICTION: [High/Medium/Low]

This MUST be the FINAL section - NO content after CONVICTION.]

CRITICAL RULES (VIOLATIONS WILL BE REJECTED):
1. OUTPUT SECTIONS 1-7 IN EXACT ORDER SHOWN - Financial Health Rating FIRST, Closing Takeaway LAST
2. NO numerical ratings or scores (no X/100, no letter grades) - express quality in YOUR words
3. NO generic analyst language - you are {persona_name}, not a Wall Street analyst
4. NO bullet points of "Monitor X" / "Track Y" / "Watch Z" / "Additionally, track..." ANYWHERE
5. NO "Health Score Drivers:" sections outside Key Metrics
6. NO "Key Data Appendix" sections
7. NO arrow notation (→) for metrics
8. NO "Strategic Initiatives & Capital Allocation" as a separate section - fold into MD&A
9. NO "Competitive Landscape" as a separate section - integrate into Risk Factors or Executive Summary
10. Write in flowing prose with natural transitions
11. EVERY section must have substantive content - no filler
12. Each section must be COMPLETE - no trailing "but...", "although...", or unfinished thoughts
13. The FINAL output must end with "CONVICTION: [level]" - nothing after that
"""
        
        return prompt


def get_persona_engine() -> PersonaEngine:
    """Factory function to get a PersonaEngine instance."""
    return PersonaEngine()


# =============================================================================
# Backwards-compatible exports used by tests
# =============================================================================

# Public alias expected by tests.
def _ordered_unique(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if not value:
            continue
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _persona_signature_concepts(persona_id: str, persona: Dict[str, Any]) -> List[str]:
    concepts: List[str] = []
    if isinstance(persona.get("key_metrics"), list):
        concepts.extend([str(item) for item in persona.get("key_metrics") if item])

    if persona_id == "buffett":
        concepts.extend(["moat", "owner earnings"])
    elif persona_id == "marks":
        concepts.extend(["second-level thinking", "pendulum", "cycle"])
    elif persona_id == "munger":
        concepts.extend(["inversion", "incentives"])
    elif persona_id == "graham":
        concepts.extend(["margin of safety", "intrinsic value"])
    elif persona_id == "lynch":
        concepts.extend(["PEG", "story"])
    elif persona_id == "dalio":
        concepts.extend(["economic machine", "cycle"])
    elif persona_id == "wood":
        concepts.extend(["Wright's Law", "S-curve", "disruption"])
    elif persona_id == "greenblatt":
        concepts.extend(["return on capital", "earnings yield", "EBIT"])
    elif persona_id == "bogle":
        concepts.extend(["stay the course", "costs matter"])
    elif persona_id == "ackman":
        concepts.extend(["catalyst", "free cash flow"])

    return _ordered_unique(concepts)[:12]


INVESTOR_PERSONAS: Dict[str, Dict[str, Any]] = {
    persona_id: {
        **persona,
        # Tests expect these fields to exist.
        "signature_concepts": _persona_signature_concepts(persona_id, persona),
        "forbidden_elements": [
            "generic equity research headers",
            "numerical ratings / scores",
            *BANNED_GENERIC_PHRASES[:25],
        ],
    }
    for persona_id, persona in PERSONAS.items()
}


# Few-shot examples are used by older prompt builders and required by tests.
# Keep them long enough to be distinctive (tests require >500 chars per persona).
FEW_SHOT_EXAMPLES: Dict[str, str] = {
    "buffett": (
        "Mr. Market will offer you a price every day, but he doesn't get to set your standards. "
        "I start with the business: is there a durable moat, does it earn owner earnings in a dependable way, "
        "and can a sensible manager reinvest those earnings at good rates? If the answers are yes, I can be patient. "
        "I don't need a spreadsheet to tell me what I already know: a wonderful business bought at a fair price will "
        "do just fine over a decade. If I can't explain it simply, it's outside my circle of competence. "
        "The only real question is whether the economics are durable and whether the price gives me a margin of safety."
    ),
    "munger": (
        "Invert, always invert. Start by asking what would make this a stupid investment and then avoid that. "
        "The big forces are incentives, simplicity, and whether you're dealing with a lollapalooza of good factors or bad ones. "
        "If the model is complicated, the accounting is clever, and management talks like a consultant, you're already in trouble. "
        "I like a few good ideas done well. Most people should do less, not more. "
        "Show me the incentives, and I'll show you the outcome. "
        "If you can't explain the core economics on a napkin, you don't understand it. "
        "And if you don't understand it, you have no business owning it."
    ),
    "graham": (
        "The intelligent investor is a realist who sells to optimists and buys from pessimists. "
        "I am concerned primarily with the relationship between price and intrinsic value. "
        "A margin of safety is not a slogan; it is arithmetic. Balance sheet strength and earnings stability matter, "
        "and the investor must distinguish investment from speculation. "
        "When the price implies perfection, the margin of safety is absent. "
        "When the price implies disaster while assets and earning power remain, opportunity can exist. "
        "In all cases, the discipline is the same: insist on the margin of safety."
    ),
    "lynch": (
        "Here's the story in plain English: what do they sell, who buys it, and why do they come back? "
        "Then I look at growth and what you're paying for it. That's why the PEG matters. "
        "If it's a fast grower, the key is whether growth can persist without the balance sheet blowing up. "
        "If it's a stalwart, you don't need fireworks, you need consistency. "
        "The best ideas are often the ones you can explain to a neighbor in two minutes. "
        "I like to kick the tires: is the product actually used, does the customer love it, and is the growth real? "
        "A tenbagger doesn't come from cleverness; it comes from a great story that keeps getting better while Wall Street isn't paying attention."
    ),
    "dalio": (
        "Think in terms of the machine. Where are we in the cycle, what are the drivers of credit, and how does liquidity move? "
        "A business doesn't exist in a vacuum; it sits inside a system of rates, growth, and risk premia. "
        "I care about how the company behaves through different environments and whether it's pro-cyclical or resilient. "
        "When conditions tighten, leverage and refinancing become the stress points. "
        "When conditions ease, growth assets can benefit, but you must size risk appropriately. "
        "Think about correlations, what could break, and whether you're being paid for the downside. "
        "A good decision is one that is well-calibrated to the probabilities, not one that simply worked last quarter."
    ),
    "wood": (
        "I focus on disruptive innovation. The question is whether the technology is on an S-curve and whether Wright's Law is driving costs down. "
        "If the product is improving and the market is expanding, the incumbents can look strong right up until they're not. "
        "I care about optionality and long-term compounding, not quarter-to-quarter noise. "
        "The key is adoption velocity and platform leverage. "
        "If this is truly a disruption, the next five years can look very different from the last five. "
        "Look for convergence: when multiple technologies compound together, the outcomes can be nonlinear. "
        "The right horizon is 5–10 years, and the right question is whether the company can become a category-defining platform by 2030."
    ),
    "greenblatt": (
        "Keep it simple. Return on Capital tells you if it's a good business. Earnings Yield tells you if it's cheap. "
        "Use EBIT, avoid stories. If ROC is high and EY is high, that's the Magic Formula sweet spot. "
        "If one is high and the other isn't, you're either buying quality at a price or junk that only looks cheap. "
        "No poetry, no narratives. Just the math and the verdict. "
        "Mean reversion is the friend of the patient investor, but only if you start with a business that earns its keep. "
        "If you can't write down the return on invested capital and the earnings yield in one line each, you're probably doing it wrong. "
        "Good and cheap beats great and expensive, and it certainly beats mediocre and cheap-looking."
    ),
    "bogle": (
        "Stay the course. Costs matter, and diversification is the investor's best friend. "
        "Most attempts to pick winning stocks are searching for needles in a haystack, and the arithmetic of active management is unforgiving. "
        "Even when a company looks excellent, the question is whether owning the haystack isn't the better answer. "
        "Turnover, fees, and taxes silently eat returns. "
        "If you insist on individual stocks, keep position sizes sensible and the discipline high. "
        "The miracle of compounding works best when you stop getting in its way. "
        "Own the market, rebalance occasionally, and let time do the heavy lifting."
    ),
    "marks": (
        "The biggest investing errors come not from facts, but from psychology. Think about the pendulum and where we are in the cycle. "
        "Second-level thinking asks: what is priced in, and what is the market getting wrong? "
        "Great companies can be terrible investments when optimism is extreme and valuations are stretched. "
        "I focus on risk control and asymmetry: how much can I lose versus how much I can make? "
        "When the odds are stacked against you, the right move is often to wait. "
        "Being early and being wrong can look the same in the short run, so insist on being paid for taking risk. "
        "In investing, the most important thing is not return maximization; it's the avoidance of disastrous outcomes."
    ),
    "ackman": (
        "I like simple, predictable, free-cash-flow generative businesses—especially when there's a catalyst to unlock value. "
        "The analysis is: what's broken, what can management do, and what will change the market's perception? "
        "If there's no catalyst, you're just hoping. If there is a clear path, you can underwrite a re-rating. "
        "I want focus, accountability, and a plan for capital allocation. "
        "When the setup is right, conviction and concentration can be justified. "
        "A great business with the wrong strategy is an opportunity if you can change the strategy. "
        "The point is to identify the few levers that actually move free cash flow per share and push hard on those."
    ),
}


PERSONA_PROMPT_TEMPLATES: Dict[str, str] = {
    "buffett": (
        "Write in prose (no bullets, no headers). Sound like Warren Buffett. Use 'moat' and 'owner earnings' naturally.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n\n"
        "Discuss circle of competence, durability, and a margin of safety."
    ),
    "munger": (
        "Write in blunt, pithy prose. Use inversion and incentives. No hedging.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "graham": (
        "Write in measured, quantitative prose. Use 'margin of safety' and 'intrinsic value'.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "lynch": (
        "Write accessibly. Tell the story and explicitly reference the PEG ratio (PEG).\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "dalio": (
        "Write systematically. Discuss the cycle and the economic machine.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "wood": (
        "Write visionary, long-horizon analysis. Mention disruption, Wright's Law, or an S-curve when relevant.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "greenblatt": (
        "CLINICAL MODE. No narrative / no emotional language. Maximum 200 words.\n"
        "MUST include ROIC (Return on Capital) and Earnings Yield calculations using EBIT. End with VERDICT.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "bogle": (
        "Write humble, prudent prose. Mention diversification and 'stay the course' and that costs matter.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "marks": (
        "Write reflective, risk-focused prose. Discuss risk/reward asymmetry, the pendulum, and the cycle.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
    "ackman": (
        "Write with activist conviction. Emphasize the catalyst and free cash flow.\n"
        "Company: {company_name}\n\nMetrics:\n{metrics_block}\n"
    ),
}


def _detect_generic_section_headers(text: str, *, allow_markdown_headers: Optional[List[str]] = None) -> List[str]:
    allow = {h.strip().lower() for h in (allow_markdown_headers or [])}
    issues: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Markdown headers like "## Summary" or "# Executive Summary"
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip().lower()
            if header in allow:
                continue
            if header in {"summary", "executive summary", "key risks", "investment thesis"}:
                issues.append(f"Generic section header: '{stripped}'")
            continue
        # Colon-style headers like "Executive Summary: ..."
        m = re.match(r"^(executive summary|key risks|investment thesis|summary)\s*:\s*", stripped, re.IGNORECASE)
        if m:
            issues.append(f"Generic section header: '{m.group(1)}'")
    return issues


def validate_persona_output(persona_id: str, output: str, persona: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Lightweight validator used by tests to enforce persona distinctiveness."""
    issues: List[str] = []
    text = (output or "").strip()
    if not text:
        return False, ["Empty output"]

    lower = text.lower()

    # Reject rating/score patterns
    if re.search(r"\b\d{1,3}\s*/\s*100\b", text) or re.search(r"\b\d{1,2}\s*/\s*10\b", text):
        issues.append("Rating/score detected")
    if re.search(r"\b(score|rating|grade)\b\s*:", lower) or re.search(r"\b\d+\s+out\s+of\s+\d+\b", lower):
        issues.append("Rating/score detected")

    # Reject generic equity research headers
    issues.extend(_detect_generic_section_headers(text))

    pid = normalize_persona_id(persona_id)

    # Persona-specific requirements
    if pid == "buffett":
        if "moat" not in lower and "owner earnings" not in lower:
            issues.append("Missing Buffett signature concepts")

    if pid == "marks":
        if not any(term in lower for term in ["cycle", "pendulum", "second-level"]):
            issues.append("Missing Marks cycle/pendulum/second-level thinking")
        if any(term in lower for term in ["i demand", "demand", "investigation", "transparency"]):
            issues.append("Confrontational tone not allowed for Marks")

    if pid == "munger":
        if any(term in lower for term in ["i believe", "could", "might", "potentially", "seems", "in my opinion"]):
            issues.append("Hedge/believe language not allowed for Munger")

    if pid == "lynch":
        if "peg" not in lower:
            issues.append("PEG required for Lynch")

    if pid == "dalio":
        if not any(term in lower for term in ["cycle", "machine", "paradigm"]):
            issues.append("Cycle/economic machine discussion required for Dalio")

    if pid == "wood":
        if not any(term in lower for term in ["wright", "s-curve", "disruption"]):
            issues.append("Disruption/Wright's Law/S-curve required for Wood")

    if pid == "greenblatt":
        words = len(text.split())
        if words > 200:
            issues.append(f"Too verbose: {words} words (max 200)")
        if any(term in lower for term in ["i worry", "i remain cautious", "i am concerned", "excited", "compelling story"]):
            issues.append("Emotional/narrative language not allowed for Greenblatt")
        if "management should" in lower or "should provide" in lower or "roi" in lower and "provide" in lower:
            issues.append("Unrealistic management disclosure request")
        if any(term in lower for term in ["what inning", "the story", "wall street is missing"]):
            issues.append("Lynch contamination detected")

    return (len(issues) == 0), issues


def validate_persona_output_strict(persona_id: str, output: str, persona: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Stricter variant used by tests; allows only a small whitelist of extra headers."""
    issues: List[str] = []
    text = (output or "").strip()
    if not text:
        return False, ["Empty output"]

    # Run base checks first.
    ok, base_issues = validate_persona_output(persona_id, output, persona)
    issues.extend(base_issues)

    # Re-check generic headers but allow "Final Recommendation Summary".
    # Remove any previously-added generic header issues for this allowed header.
    allowed = "final recommendation summary"
    filtered: List[str] = []
    for issue in issues:
        if "generic section header" in issue.lower() and allowed in issue.lower():
            continue
        filtered.append(issue)
    issues = filtered
    issues.extend(_detect_generic_section_headers(text, allow_markdown_headers=[allowed]))

    return (len(issues) == 0), issues


def generate_closing_persona_message(persona_id: str, company_name: str, ratios: Dict[str, Any]) -> str:
    """Generate a short persona-flavored closing message (used by frontend/dashboard tests)."""
    if not company_name:
        return ""

    pid = normalize_persona_id(persona_id)
    lower_name = company_name.strip()

    gross = ratios.get("gross_margin")
    op = ratios.get("operating_margin")
    fcf = ratios.get("fcf")
    pe = ratios.get("pe_ratio")

    quality_terms: List[str] = []
    if isinstance(gross, (int, float)) and gross >= 0.6:
        quality_terms.append("exceptional margins")
    if isinstance(op, (int, float)) and op >= 0.25:
        quality_terms.append("strong operating leverage")
    if isinstance(fcf, (int, float)) and fcf and fcf > 0:
        quality_terms.append("cash generative")

    quality_clause = " and ".join(quality_terms) if quality_terms else "a mixed quality profile"
    is_high_quality = len(quality_terms) >= 2 and not (isinstance(fcf, (int, float)) and fcf < 0)
    valuation_hot = isinstance(pe, (int, float)) and pe >= 40

    if pid == "marks":
        quality_prefix = "an exceptional, high-quality" if is_high_quality else "a mixed-quality"
        valuation_line = (
            "But the market has likely priced in a lot of perfection already, so the risk/reward asymmetry is not favorable today."
            if valuation_hot
            else "The key is whether the price offers enough margin of safety for the risks we can see."
        )
        return (
            f"{lower_name} looks like {quality_prefix} business on the numbers ({quality_clause}). "
            "Where we are in the cycle matters, and the pendulum can swing too far into optimism. "
            f"{valuation_line}"
        )

    if pid == "buffett":
        return (
            f"With {lower_name}, I start with the business: does it have a durable moat and produce owner earnings you can count on? "
            f"The financial picture suggests {quality_clause}, which is what I like to see over long stretches of time. "
            "The only remaining question is the price you pay versus the value you get."
        )

    if pid == "lynch":
        return (
            f"The story with {lower_name} is what matters: what drives growth, and can it keep going? "
            "If the growth is real, the next step is checking what you're paying for it (think PEG). "
            "If the price matches the story, I'd be excited to buy; if not, I'd wait."
        )

    if pid == "greenblatt":
        return (
            f"For {lower_name}, I only care about two things: is it good (high return on capital) and is it cheap (high earnings yield)? "
            "If you don't have both, you don't have the Magic Formula working for you. "
            "Do the math, then act on the verdict."
        )

    if pid == "dalio":
        return (
            f"{lower_name} should be evaluated in the context of the cycle and the economic machine. "
            "When liquidity tightens, leverage and refinancing become the pressure points; when liquidity eases, growth assets can re-rate quickly. "
            "Size the risk to the environment, not the narrative."
        )

    if pid == "wood":
        return (
            f"The question for {lower_name} is whether it's on the right side of disruption and whether the cost curve is improving via Wright's Law or an S-curve. "
            "If adoption compounds, the next five years can overwhelm what the past would suggest. "
            "That long-horizon optionality is the opportunity."
        )

    if pid == "bogle":
        return (
            f"{lower_name} may be an interesting business, but remember: stay the course and keep costs low. "
            "Most investors are better served owning the diversified haystack than hunting for a single needle. "
            "If you do own it, keep it sized sensibly and avoid chasing performance."
        )

    if pid == "munger":
        return (
            f"With {lower_name}, invert the problem: what would make this a stupid investment? "
            "Then look at incentives and the simplicity of the model, because that's where most failures come from. "
            "A few obvious truths beat a thousand clever details."
        )

    if pid == "graham":
        return (
            f"For {lower_name}, the investor should focus on intrinsic value and a margin of safety, not excitement. "
            "If the price implies optimism, the margin of safety shrinks; if the price implies pessimism, opportunity can appear. "
            "Always separate investment from speculation."
        )

    if pid == "ackman":
        return (
            f"I like {lower_name} when it's simple, free-cash-flow generative, and there is a clear catalyst to unlock value. "
            "If management has the right plan and accountability, the market can re-rate quickly. "
            "Without a catalyst, you're just hoping."
        )

    # Fallback
    return (
        f"{lower_name} has {quality_clause}. The key is weighing durability against valuation and risk. "
        "A disciplined process beats a good story."
    )
