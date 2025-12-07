"""Financial health score calculation service."""
from typing import Dict, Optional, List, Any
import statistics


class HealthScorer:
    """Calculate composite health score with percentile normalization."""
    
    # Default weights for composite score (must sum to 100)
    DEFAULT_WEIGHTS = {
        "financial_performance": 25,
        "profitability": 18,
        "leverage": 15,
        "liquidity": 12,
        "cash_flow": 12,
        "governance": 10,
        "growth": 8
    }
    
    # Weight presets based on user's primary_factor_weighting selection
    WEIGHT_PRESETS = {
        "profitability_margins": {
            "financial_performance": 20,
            "profitability": 30,  # Dominant
            "leverage": 10,
            "liquidity": 10,
            "cash_flow": 12,
            "governance": 10,
            "growth": 8
        },
        "cash_flow_conversion": {
            "financial_performance": 18,
            "profitability": 15,
            "leverage": 12,
            "liquidity": 10,
            "cash_flow": 30,  # Dominant
            "governance": 8,
            "growth": 7
        },
        "balance_sheet_strength": {
            "financial_performance": 15,
            "profitability": 12,
            "leverage": 30,  # Dominant
            "liquidity": 18,
            "cash_flow": 10,
            "governance": 10,
            "growth": 5
        },
        "liquidity_near_term_risk": {
            "financial_performance": 15,
            "profitability": 12,
            "leverage": 18,
            "liquidity": 30,  # Dominant
            "cash_flow": 12,
            "governance": 8,
            "growth": 5
        },
        "execution_competitiveness": {
            "financial_performance": 28,  # Dominant (includes execution)
            "profitability": 18,
            "leverage": 10,
            "liquidity": 8,
            "cash_flow": 10,
            "governance": 12,
            "growth": 14
        },
    }
    
    # Alias for backwards compatibility
    WEIGHTS = DEFAULT_WEIGHTS
    
    # Component descriptions based on user's weighting preference
    COMPONENT_DESCRIPTIONS = {
        "default": {
            "financial_performance": "Revenue growth and operating efficiency",
            "profitability": "Margins, ROE, and ROA quality",
            "leverage": "Debt levels and interest coverage",
            "liquidity": "Short-term cash and working capital",
            "cash_flow": "Free cash flow generation strength",
            "governance": "Earnings quality and capital discipline",
            "growth": "Revenue expansion and margin trends"
        },
        "profitability_margins": {
            "financial_performance": "Revenue growth supporting margin expansion",
            "profitability": "★ PRIMARY: Gross, operating, and net margins",
            "leverage": "Debt impact on profitability ratios",
            "liquidity": "Working capital efficiency for margins",
            "cash_flow": "Cash conversion of reported profits",
            "governance": "Management focus on margin protection",
            "growth": "Profitable growth trajectory"
        },
        "cash_flow_conversion": {
            "financial_performance": "Cash-generating efficiency of operations",
            "profitability": "Earnings quality relative to cash flow",
            "leverage": "Debt serviceability from operating cash",
            "liquidity": "Cash position and burn rate",
            "cash_flow": "★ PRIMARY: FCF generation and yield",
            "governance": "Capital allocation and cash discipline",
            "growth": "Cash flow growth trajectory"
        },
        "balance_sheet_strength": {
            "financial_performance": "Asset utilization and efficiency",
            "profitability": "Return on assets and equity",
            "leverage": "★ PRIMARY: Debt ratios and solvency",
            "liquidity": "★ PRIMARY: Current and quick ratios",
            "cash_flow": "Cash coverage of obligations",
            "governance": "Conservative capital structure",
            "growth": "Sustainable growth capacity"
        },
        "liquidity_near_term_risk": {
            "financial_performance": "Short-term cash generation",
            "profitability": "Margin stability under stress",
            "leverage": "Near-term debt maturities",
            "liquidity": "★ PRIMARY: Cash runway and burn rate",
            "cash_flow": "Operating cash adequacy",
            "governance": "Liquidity management discipline",
            "growth": "Growth vs. cash preservation tradeoff"
        },
        "execution_competitiveness": {
            "financial_performance": "★ PRIMARY: Execution vs. peers",
            "profitability": "Competitive margin positioning",
            "leverage": "Financial flexibility for competition",
            "liquidity": "Resources for strategic moves",
            "cash_flow": "Cash for reinvestment",
            "governance": "Strategic capital deployment",
            "growth": "★ PRIMARY: Market share momentum"
        },
    }
    
    # Which metrics to highlight for each component based on weighting preference
    # These labels will be shown in the health score breakdown UI
    COMPONENT_METRIC_LABELS = {
        "default": {
            "financial_performance": ["revenue_growth_yoy", "operating_margin"],
            "profitability": ["gross_margin", "net_margin", "roe"],
            "leverage": ["debt_to_equity", "interest_coverage"],
            "liquidity": ["current_ratio", "quick_ratio"],
            "cash_flow": ["fcf", "fcf_margin"],
            "governance": ["fcf", "net_income"],
            "growth": ["revenue_growth_yoy"]
        },
        "profitability_margins": {
            "financial_performance": ["revenue_growth_yoy", "operating_margin"],
            "profitability": ["gross_margin", "operating_margin", "net_margin"],
            "leverage": ["debt_to_equity"],
            "liquidity": ["current_ratio"],
            "cash_flow": ["fcf_margin"],
            "governance": ["net_margin", "roe"],
            "growth": ["revenue_growth_yoy", "operating_margin"]
        },
        "cash_flow_conversion": {
            "financial_performance": ["operating_cash_flow"],
            "profitability": ["net_margin", "fcf_margin"],
            "leverage": ["debt_to_equity", "interest_coverage"],
            "liquidity": ["current_ratio"],
            "cash_flow": ["fcf", "fcf_margin", "operating_cash_flow"],
            "governance": ["fcf", "net_income"],
            "growth": ["fcf_margin"]
        },
        "balance_sheet_strength": {
            "financial_performance": ["roa"],
            "profitability": ["roa", "roe"],
            "leverage": ["debt_to_equity", "net_debt_to_ebitda"],
            "liquidity": ["current_ratio", "quick_ratio"],
            "cash_flow": ["fcf"],
            "governance": ["debt_to_equity"],
            "growth": ["roe"]
        },
        "liquidity_near_term_risk": {
            "financial_performance": ["operating_cash_flow"],
            "profitability": ["operating_margin"],
            "leverage": ["debt_to_equity"],
            "liquidity": ["current_ratio", "quick_ratio", "dso"],
            "cash_flow": ["operating_cash_flow", "fcf"],
            "governance": ["current_ratio"],
            "growth": ["revenue_growth_yoy"]
        },
        "execution_competitiveness": {
            "financial_performance": ["operating_margin", "revenue_growth_yoy"],
            "profitability": ["gross_margin", "operating_margin"],
            "leverage": ["interest_coverage"],
            "liquidity": ["current_ratio"],
            "cash_flow": ["fcf"],
            "governance": ["roe", "roa"],
            "growth": ["revenue_growth_yoy", "operating_margin"]
        },
    }
    
    # Score bands (min threshold, label) - uses >= comparison in order
    SCORE_BANDS = [
        (85, "Very Healthy"),
        (70, "Healthy"),
        (50, "Watch"),
        (0, "At Risk")
    ]
    
    def __init__(self, ratios: Dict[str, Optional[float]], peer_data: Optional[List[Dict]] = None, custom_weights: Optional[Dict[str, int]] = None, ai_growth_assessment: Optional[Dict[str, Any]] = None):
        """
        Initialize health scorer.

        Args:
            ratios: Dictionary of calculated ratios
            peer_data: Optional list of peer company ratios for percentile calculation
            custom_weights: Optional custom weights dict (must sum to 100) or weighting preset name
            ai_growth_assessment: Optional AI-generated growth assessment dict with 'score' and 'description'
        """
        self.ratios = ratios
        self.peer_data = peer_data or []
        self.normalized_scores = {}
        self.component_scores = {}
        self.data_sources = {}  # Track data derivation: 'primary', 'fallback_1', etc.
        self.component_metrics = {}  # Store actual metric values for display
        self.ai_growth_assessment = ai_growth_assessment  # Store AI growth assessment

        # Set weights based on custom_weights parameter
        if custom_weights is None:
            self.weights = self.DEFAULT_WEIGHTS.copy()
            self.weighting_preset = "default"
        elif isinstance(custom_weights, str):
            # Preset name provided
            self.weights = self.WEIGHT_PRESETS.get(custom_weights, self.DEFAULT_WEIGHTS).copy()
            self.weighting_preset = custom_weights if custom_weights in self.WEIGHT_PRESETS else "default"
        elif isinstance(custom_weights, dict):
            self.weights = custom_weights.copy()
            self.weighting_preset = "default"  # Custom dict doesn't have preset descriptions
        else:
            self.weights = self.DEFAULT_WEIGHTS.copy()
            self.weighting_preset = "default"
    
    def calculate_health_score(self) -> Dict[str, Any]:
        """
        Calculate composite health score.
        
        Returns:
            Dictionary with overall score, band, component scores, applied weights, and descriptions
        """
        # Normalize individual ratios to 0-100 scale
        self._normalize_ratios()
        
        # Calculate component scores
        self._calculate_component_scores()
        
        # Calculate weighted overall score
        overall_score = self._calculate_weighted_score()
        
        # Determine score band
        score_band = self._get_score_band(overall_score)
        
        # Get component descriptions based on weighting preset
        component_descriptions = self.COMPONENT_DESCRIPTIONS.get(
            self.weighting_preset, 
            self.COMPONENT_DESCRIPTIONS["default"]
        )
        
        # Build component metrics display: actual values used for each component
        self._build_component_metrics()
        
        return {
            "overall_score": round(overall_score, 2),
            "score_band": score_band,
            "component_scores": self.component_scores,
            "component_weights": self.weights,  # Include applied weights
            "component_descriptions": component_descriptions,  # Include descriptions
            "component_metrics": self.component_metrics,  # Include actual metric values
            "normalized_scores": self.normalized_scores,
            "data_sources": self.data_sources  # Track primary vs fallback data
        }
    
    def _normalize_ratios(self):
        """Normalize ratios to 0-100 scale."""
        # If we have peer data, use percentile normalization
        if self.peer_data:
            self._normalize_with_percentiles()
        else:
            # Use rule-based normalization without peer data
            self._normalize_without_peers()
    
    def _normalize_with_percentiles(self):
        """Normalize using percentile ranks against peers."""
        for ratio_name, value in self.ratios.items():
            if value is None:
                self.normalized_scores[ratio_name] = 50  # Neutral score for missing data
                continue
            
            # Get peer values for this ratio
            peer_values = [
                peer.get(ratio_name)
                for peer in self.peer_data
                if peer.get(ratio_name) is not None
            ]
            
            if not peer_values:
                self.normalized_scores[ratio_name] = 50
                continue
            
            # Calculate percentile
            peer_values_sorted = sorted(peer_values)
            percentile = self._calculate_percentile(value, peer_values_sorted)
            
            # Invert for "lower is better" metrics
            if self._is_lower_better(ratio_name):
                percentile = 100 - percentile
            
            self.normalized_scores[ratio_name] = percentile
    
    def _normalize_without_peers(self):
        """Normalize using rule-based thresholds."""
        # Profitability (higher is better)
        self.normalized_scores["revenue_growth_yoy"] = self._normalize_growth(
            self.ratios.get("revenue_growth_yoy"), target=0.10, excellent=0.25
        )
        self.normalized_scores["gross_margin"] = self._normalize_margin(
            self.ratios.get("gross_margin"), target=0.30, excellent=0.60
        )
        self.normalized_scores["operating_margin"] = self._normalize_margin(
            self.ratios.get("operating_margin"), target=0.10, excellent=0.25
        )
        self.normalized_scores["net_margin"] = self._normalize_margin(
            self.ratios.get("net_margin"), target=0.08, excellent=0.20
        )
        self.normalized_scores["roa"] = self._normalize_margin(
            self.ratios.get("roa"), target=0.05, excellent=0.15
        )
        self.normalized_scores["roe"] = self._normalize_margin(
            self.ratios.get("roe"), target=0.10, excellent=0.25
        )
        
        # Liquidity (higher is better, with some optimal ranges)
        self.normalized_scores["current_ratio"] = self._normalize_ratio_optimal(
            self.ratios.get("current_ratio"), optimal=1.5, min_good=1.0, max_good=3.0
        )
        self.normalized_scores["quick_ratio"] = self._normalize_ratio_optimal(
            self.ratios.get("quick_ratio"), optimal=1.0, min_good=0.8, max_good=2.0
        )
        self.normalized_scores["dso"] = self._normalize_lower_better(
            self.ratios.get("dso"), target=45, excellent=30
        )
        self.normalized_scores["inventory_turnover"] = self._normalize_margin(
            self.ratios.get("inventory_turnover"), target=4, excellent=12
        )
        
        # Leverage (lower is better)
        self.normalized_scores["debt_to_equity"] = self._normalize_lower_better(
            self.ratios.get("debt_to_equity"), target=1.0, excellent=0.3
        )
        self.normalized_scores["net_debt_to_ebitda"] = self._normalize_lower_better(
            self.ratios.get("net_debt_to_ebitda"), target=3.0, excellent=1.0
        )
        self.normalized_scores["interest_coverage"] = self._normalize_margin(
            self.ratios.get("interest_coverage"), target=3.0, excellent=8.0
        )
        
        # Cash Flow (higher is better)
        fcf = self.ratios.get("fcf")
        if fcf is not None:
            # Normalize based on whether FCF is positive/negative
            if fcf > 0:
                self.normalized_scores["fcf"] = 70  # Base score for positive FCF
            else:
                self.normalized_scores["fcf"] = 30  # Penalty for negative FCF
        else:
            self.normalized_scores["fcf"] = 50
        
        self.normalized_scores["fcf_margin"] = self._normalize_margin(
            self.ratios.get("fcf_margin"), target=0.10, excellent=0.20
        )
        
        # Altman Z-Score
        z_score = self.ratios.get("altman_z_score")
        if z_score is not None:
            # Z-Score interpretation: >2.99 safe, 1.81-2.99 grey, <1.81 distress
            if z_score > 2.99:
                self.normalized_scores["altman_z_score"] = 90
            elif z_score > 1.81:
                self.normalized_scores["altman_z_score"] = 60
            else:
                self.normalized_scores["altman_z_score"] = 20
        else:
            self.normalized_scores["altman_z_score"] = 50
    
    def _calculate_percentile(self, value: float, sorted_values: List[float]) -> float:
        """Calculate percentile rank of value in sorted list."""
        if not sorted_values:
            return 50
        
        count_below = sum(1 for v in sorted_values if v < value)
        percentile = (count_below / len(sorted_values)) * 100
        
        return max(0, min(100, percentile))
    
    def _is_lower_better(self, ratio_name: str) -> bool:
        """Check if lower values are better for this ratio."""
        lower_better = [
            "debt_to_equity",
            "net_debt_to_ebitda",
            "dso"
        ]
        return ratio_name in lower_better
    
    def _normalize_margin(self, value: Optional[float], target: float, excellent: float) -> float:
        """Normalize a margin/ratio where higher is better."""
        if value is None:
            return 50
        
        if value >= excellent:
            return 95
        elif value >= target:
            # Scale between target (70) and excellent (95)
            return 70 + (25 * (value - target) / (excellent - target))
        elif value >= 0:
            # Scale between 0 (40) and target (70)
            return 40 + (30 * value / target)
        else:
            # Negative values
            return max(0, 40 + (40 * value / target))
    
    def _normalize_growth(self, value: Optional[float], target: float, excellent: float) -> float:
        """Normalize a growth rate."""
        if value is None:
            return 50
        
        if value >= excellent:
            return 95
        elif value >= target:
            return 70 + (25 * (value - target) / (excellent - target))
        elif value >= 0:
            return 50 + (20 * value / target)
        else:
            # Negative growth
            return max(0, 50 + (50 * value / target))
    
    def _normalize_lower_better(self, value: Optional[float], target: float, excellent: float) -> float:
        """Normalize a ratio where lower is better."""
        if value is None:
            return 50
        
        if value <= excellent:
            return 95
        elif value <= target:
            # Scale between excellent (95) and target (70)
            return 95 - (25 * (value - excellent) / (target - excellent))
        else:
            # Worse than target
            ratio = value / target
            return max(0, 70 - (30 * (ratio - 1)))
    
    def _normalize_ratio_optimal(
        self,
        value: Optional[float],
        optimal: float,
        min_good: float,
        max_good: float
    ) -> float:
        """Normalize a ratio with an optimal range."""
        if value is None:
            return 50
        
        if min_good <= value <= max_good:
            # Within good range
            if value == optimal:
                return 95
            elif value < optimal:
                return 70 + (25 * (value - min_good) / (optimal - min_good))
            else:
                return 70 + (25 * (max_good - value) / (max_good - optimal))
        elif value < min_good:
            # Below good range
            return max(0, 70 * (value / min_good))
        else:
            # Above good range
            excess_ratio = (value - max_good) / max_good
            return max(0, 70 - (40 * excess_ratio))
    
    def _calculate_component_scores(self):
        """Calculate scores for each major component."""
        # Financial Performance - combines revenue growth and operating performance
        financial_perf_scores = [
            self.normalized_scores.get("revenue_growth_yoy", 50),
            self.normalized_scores.get("operating_margin", 50),
        ]
        # Only include scores that have actual data
        valid_scores = [s for s in financial_perf_scores if s != 50 or 
                       "revenue_growth_yoy" in self.ratios or "operating_margin" in self.ratios]
        self.component_scores["financial_performance"] = statistics.mean(valid_scores) if valid_scores else 50

        # Calculate growth component score - ONLY if we have revenue_growth_yoy data
        revenue_growth = self.ratios.get("revenue_growth_yoy")
        if revenue_growth is not None:
            if revenue_growth >= 0.25:  # 25%+ growth
                growth_score = 90
            elif revenue_growth >= 0.15:  # 15-25% growth
                growth_score = 75
            elif revenue_growth >= 0.10:  # 10-15% growth
                growth_score = 65
            elif revenue_growth >= 0.05:  # 5-10% growth
                growth_score = 55
            elif revenue_growth >= 0:  # 0-5% growth
                growth_score = 45
            elif revenue_growth >= -0.05:  # 0 to -5% decline
                growth_score = 30
            elif revenue_growth >= -0.10:  # -5 to -10% decline
                growth_score = 20
            else:  # More than -10% decline
                growth_score = 10
            self.component_scores["growth"] = growth_score
        # If no revenue_growth_yoy data, don't include growth in component_scores at all
        
        # Profitability
        profitability_scores = [
            self.normalized_scores.get("gross_margin", 50),
            self.normalized_scores.get("operating_margin", 50),
            self.normalized_scores.get("net_margin", 50),
            self.normalized_scores.get("roa", 50),
            self.normalized_scores.get("roe", 50)
        ]
        self.component_scores["profitability"] = statistics.mean(profitability_scores)
        
        # Leverage & Solvency
        leverage_scores = [
            self.normalized_scores.get("debt_to_equity", 50),
            self.normalized_scores.get("net_debt_to_ebitda", 50),
            self.normalized_scores.get("interest_coverage", 50),
            self.normalized_scores.get("altman_z_score", 50)
        ]
        self.component_scores["leverage"] = statistics.mean(leverage_scores)
        
        # Liquidity & Efficiency
        liquidity_scores = [
            self.normalized_scores.get("current_ratio", 50),
            self.normalized_scores.get("quick_ratio", 50),
            self.normalized_scores.get("dso", 50),
            self.normalized_scores.get("inventory_turnover", 50)
        ]
        self.component_scores["liquidity"] = statistics.mean(liquidity_scores)
        
        # Cash Flow Strength - calculate based on FCF, FCF margin, and fallbacks
        # Even if FCF is negative, we should show the score (not hide it)
        fcf = self.ratios.get("fcf")
        fcf_margin = self.ratios.get("fcf_margin")
        cash_flow_calculated = False
        
        if fcf is not None or fcf_margin is not None:
            cash_flow_scores = []
            if fcf is not None:
                # More granular FCF scoring based on magnitude
                if fcf > 0:
                    # Score positive FCF based on margin if available
                    if fcf_margin is not None:
                        if fcf_margin >= 0.15:  # 15%+ FCF margin = excellent
                            cash_flow_scores.append(90)
                        elif fcf_margin >= 0.10:  # 10-15%
                            cash_flow_scores.append(80)
                        elif fcf_margin >= 0.05:  # 5-10%
                            cash_flow_scores.append(70)
                        else:  # <5%
                            cash_flow_scores.append(60)
                        self.data_sources["cash_flow"] = "primary_fcf_margin"
                    else:
                        cash_flow_scores.append(72)  # Positive FCF without margin data
                        self.data_sources["cash_flow"] = "primary_fcf_only"
                else:
                    cash_flow_scores.append(25)  # Negative FCF is concerning
                    self.data_sources["cash_flow"] = "primary_fcf_negative"
            elif fcf_margin is not None:
                cash_flow_scores.append(self.normalized_scores.get("fcf_margin", 50))
                self.data_sources["cash_flow"] = "primary_fcf_margin_only"
            self.component_scores["cash_flow"] = statistics.mean(cash_flow_scores) if cash_flow_scores else 50
            cash_flow_calculated = True
        
        if not cash_flow_calculated:
            # Fallback 1: Use operating cash flow (capped lower to differentiate from FCF)
            ocf = self.ratios.get("operating_cash_flow")
            if ocf is not None:
                if ocf > 0:
                    self.component_scores["cash_flow"] = 65  # Lower than FCF primary
                else:
                    self.component_scores["cash_flow"] = 28
                self.data_sources["cash_flow"] = "fallback_ocf"
                cash_flow_calculated = True
        
        if not cash_flow_calculated:
            # Fallback 2: Use net margin as cash flow quality proxy (capped even lower)
            net_margin = self.ratios.get("net_margin")
            if net_margin is not None:
                if net_margin >= 0.15:  # 15%+ net margin
                    self.component_scores["cash_flow"] = 62
                elif net_margin >= 0.10:  # 10-15%
                    self.component_scores["cash_flow"] = 55
                elif net_margin >= 0.05:  # 5-10%
                    self.component_scores["cash_flow"] = 48
                elif net_margin >= 0:  # 0-5%
                    self.component_scores["cash_flow"] = 40
                else:  # Negative
                    self.component_scores["cash_flow"] = 25
                self.data_sources["cash_flow"] = "fallback_net_margin"
                cash_flow_calculated = True
        
        if not cash_flow_calculated:
            # Fallback 3: Use operating margin (lowest confidence)
            operating_margin = self.ratios.get("operating_margin")
            if operating_margin is not None:
                if operating_margin >= 0.20:
                    self.component_scores["cash_flow"] = 58
                elif operating_margin >= 0.10:
                    self.component_scores["cash_flow"] = 50
                elif operating_margin >= 0.05:
                    self.component_scores["cash_flow"] = 42
                elif operating_margin >= 0:
                    self.component_scores["cash_flow"] = 35
                else:
                    self.component_scores["cash_flow"] = 25
                self.data_sources["cash_flow"] = "fallback_operating_margin"
                cash_flow_calculated = True
        
        if not cash_flow_calculated:
            self.component_scores["cash_flow"] = 50  # Neutral only when no data at all
            self.data_sources["cash_flow"] = "no_data"
        
        # Governance - Use earnings quality as primary, with multiple fallbacks
        # Compare Net Income to FCF - high quality earnings convert to cash
        net_income = self.ratios.get("net_income")
        governance_calculated = False
        
        if fcf is not None and net_income is not None and net_income != 0:
            # Earnings quality: FCF should be close to or higher than Net Income
            earnings_quality_ratio = fcf / abs(net_income)
            if earnings_quality_ratio >= 1.0:
                governance_score = 85  # FCF >= Net Income: excellent
            elif earnings_quality_ratio >= 0.7:
                governance_score = 70  # FCF is 70%+ of Net Income
            elif earnings_quality_ratio >= 0.4:
                governance_score = 55  # FCF is 40-70% of Net Income
            elif earnings_quality_ratio >= 0:
                governance_score = 40  # FCF positive but low
            else:
                governance_score = 25  # Negative FCF
            self.component_scores["governance"] = governance_score
            self.data_sources["governance"] = "primary_earnings_quality"
            governance_calculated = True
        
        if not governance_calculated:
            # Fallback 1: Use debt levels as governance proxy (capped to differentiate)
            debt_to_equity = self.ratios.get("debt_to_equity")
            if debt_to_equity is not None:
                if debt_to_equity < 0.5:
                    self.component_scores["governance"] = 70  # Conservative (was 75)
                elif debt_to_equity < 1.0:
                    self.component_scores["governance"] = 58  # Moderate
                elif debt_to_equity < 2.0:
                    self.component_scores["governance"] = 42  # Elevated
                else:
                    self.component_scores["governance"] = 28  # Aggressive
                self.data_sources["governance"] = "fallback_debt_to_equity"
                governance_calculated = True
        
        if not governance_calculated:
            # Fallback 2: Use ROE as governance proxy (capped lower)
            roe = self.ratios.get("roe")
            if roe is not None:
                if roe >= 0.20:  # 20%+ ROE
                    self.component_scores["governance"] = 68
                elif roe >= 0.15:  # 15-20%
                    self.component_scores["governance"] = 60
                elif roe >= 0.10:  # 10-15%
                    self.component_scores["governance"] = 52
                elif roe >= 0.05:  # 5-10%
                    self.component_scores["governance"] = 44
                elif roe >= 0:  # 0-5%
                    self.component_scores["governance"] = 36
                else:  # Negative ROE
                    self.component_scores["governance"] = 25
                self.data_sources["governance"] = "fallback_roe"
                governance_calculated = True
        
        if not governance_calculated:
            # Fallback 3: Use ROA as governance proxy (even lower cap)
            roa = self.ratios.get("roa")
            if roa is not None:
                if roa >= 0.10:  # 10%+ ROA
                    self.component_scores["governance"] = 65
                elif roa >= 0.05:  # 5-10%
                    self.component_scores["governance"] = 54
                elif roa >= 0:  # 0-5%
                    self.component_scores["governance"] = 42
                else:  # Negative ROA
                    self.component_scores["governance"] = 28
                self.data_sources["governance"] = "fallback_roa"
                governance_calculated = True
        
        if not governance_calculated:
            # Fallback 4: Use interest coverage as governance proxy (lowest confidence)
            interest_coverage = self.ratios.get("interest_coverage")
            if interest_coverage is not None:
                if interest_coverage >= 8.0:
                    self.component_scores["governance"] = 63
                elif interest_coverage >= 5.0:
                    self.component_scores["governance"] = 52
                elif interest_coverage >= 3.0:
                    self.component_scores["governance"] = 42
                elif interest_coverage >= 1.5:
                    self.component_scores["governance"] = 32
                else:
                    self.component_scores["governance"] = 20
                self.data_sources["governance"] = "fallback_interest_coverage"
                governance_calculated = True
        
        if not governance_calculated:
            self.component_scores["governance"] = 50  # Neutral only when no data at all
            self.data_sources["governance"] = "no_data"

        # Growth component - use AI assessment when available, otherwise fallback
        if self.ai_growth_assessment and "score" in self.ai_growth_assessment:
            # Use AI-generated growth assessment
            self.component_scores["growth"] = self.ai_growth_assessment["score"]
            self.data_sources["growth"] = "ai_assessment"
        else:
            # Fallback: use operating margin as growth quality proxy (capped lower)
            # High margins often indicate growth potential but less reliable than AI assessment
            operating_margin = self.ratios.get("operating_margin")
            if operating_margin is not None:
                if operating_margin >= 0.25:
                    self.component_scores["growth"] = 60
                elif operating_margin >= 0.15:
                    self.component_scores["growth"] = 52
                elif operating_margin >= 0.10:
                    self.component_scores["growth"] = 45
                elif operating_margin >= 0.05:
                    self.component_scores["growth"] = 38
                elif operating_margin >= 0:
                    self.component_scores["growth"] = 32
                else:
                    self.component_scores["growth"] = 22  # Negative margin
                self.data_sources["growth"] = "fallback_operating_margin"
            else:
                self.component_scores["growth"] = 50  # Neutral when no data
                self.data_sources["growth"] = "no_data"
    
    def _calculate_weighted_score(self) -> float:
        """Calculate weighted overall score."""
        total_score = 0
        total_weight = 0
        
        for component, weight in self.weights.items():
            if component in self.component_scores:
                total_score += self.component_scores[component] * weight
                total_weight += weight
        
        if total_weight == 0:
            return 50
        
        return total_score / total_weight
    
    def _build_component_metrics(self):
        """Build formatted metric values for each component to display in UI.
        
        Shows the ACTUAL metrics used to calculate each component's score.
        When fallback metrics would duplicate another component's display,
        shows a descriptive label instead to avoid confusion.
        """
        # Metric display name mapping
        METRIC_DISPLAY_NAMES = {
            "revenue_growth_yoy": "Revenue Growth",
            "operating_margin": "Operating Margin",
            "gross_margin": "Gross Margin",
            "net_margin": "Net Margin",
            "roe": "ROE",
            "roa": "ROA",
            "debt_to_equity": "Debt/Equity",
            "interest_coverage": "Interest Coverage",
            "current_ratio": "Current Ratio",
            "quick_ratio": "Quick Ratio",
            "dso": "Days Sales Outstanding",
            "fcf": "Free Cash Flow",
            "fcf_margin": "FCF Margin",
            "operating_cash_flow": "Operating Cash Flow",
            "net_income": "Net Income",
            "net_debt_to_ebitda": "Net Debt/EBITDA",
            "inventory_turnover": "Inventory Turnover",
        }
        
        def format_value(metric_key: str, value) -> str:
            """Format a metric value for display."""
            if value is None:
                return None
            
            # Percentage metrics (stored as decimals)
            if metric_key in ["revenue_growth_yoy", "operating_margin", "gross_margin", 
                              "net_margin", "roe", "roa", "fcf_margin"]:
                return f"{value * 100:.1f}%"
            
            # Ratio metrics
            if metric_key in ["debt_to_equity", "current_ratio", "quick_ratio", 
                              "net_debt_to_ebitda", "inventory_turnover"]:
                return f"{value:.2f}x"
            
            # Coverage ratio
            if metric_key == "interest_coverage":
                return f"{value:.1f}x"
            
            # Days metric
            if metric_key == "dso":
                return f"{value:.0f} days"
            
            # Large dollar amounts
            if metric_key in ["fcf", "operating_cash_flow", "net_income"]:
                if abs(value) >= 1_000_000_000:
                    return f"${value / 1_000_000_000:.2f}B"
                elif abs(value) >= 1_000_000:
                    return f"${value / 1_000_000:.2f}M"
                else:
                    return f"${value:,.0f}"
            
            return str(value)
        
        def format_metric(key: str) -> Optional[str]:
            """Format a single metric for display."""
            value = self.ratios.get(key)
            if value is not None:
                display_name = METRIC_DISPLAY_NAMES.get(key, key)
                formatted_value = format_value(key, value)
                if formatted_value:
                    return f"{display_name}: {formatted_value}"
            return None
        
        # Track which metrics are already used by other components
        # This prevents showing the same metric multiple times
        used_metrics = set()
        
        # Build metrics for each component based on what was ACTUALLY used for scoring
        # Order matters here - components that use "primary" data are processed first
        
        # 1. Financial Performance: revenue growth + operating margin for fuller picture
        fp_metrics = []
        rev_growth = format_metric("revenue_growth_yoy")
        if rev_growth:
            fp_metrics.append(rev_growth)
            used_metrics.add("revenue_growth_yoy")
        op_margin = format_metric("operating_margin")
        if op_margin:
            fp_metrics.append(op_margin)
            used_metrics.add("operating_margin")
        # Add context if we have growth data
        if not fp_metrics:
            fp_metrics.append("Financial data limited")
        self.component_metrics["financial_performance"] = " • ".join(fp_metrics) if fp_metrics else ""
        
        # 2. Profitability: uses margin stack (unique combo)
        prof_metrics = []
        for key in ["gross_margin", "net_margin", "roe"]:
            m = format_metric(key)
            if m:
                prof_metrics.append(m)
                used_metrics.add(key)
        self.component_metrics["profitability"] = " • ".join(prof_metrics) if prof_metrics else ""
        
        # 3. Leverage: uses debt ratios
        lev_metrics = []
        for key in ["debt_to_equity", "interest_coverage"]:
            m = format_metric(key)
            if m:
                lev_metrics.append(m)
                used_metrics.add(key)
        self.component_metrics["leverage"] = " • ".join(lev_metrics) if lev_metrics else ""
        
        # 4. Liquidity: uses current/quick ratios with user-friendly context
        liq_metrics = []
        current = self.ratios.get("current_ratio")
        quick = self.ratios.get("quick_ratio")

        if current is not None:
            used_metrics.add("current_ratio")
            # Provide context on what the ratio means
            if current >= 2.0:
                liq_metrics.append(f"Strong short-term coverage ({current:.1f}x assets vs liabilities)")
            elif current >= 1.5:
                liq_metrics.append(f"Healthy liquidity ({current:.1f}x current assets to liabilities)")
            elif current >= 1.0:
                liq_metrics.append(f"Adequate liquidity ({current:.1f}x) - can cover near-term obligations")
            else:
                liq_metrics.append(f"Tight liquidity ({current:.1f}x) - liabilities exceed current assets")

        if quick is not None and quick != current:  # Only show if different from current
            used_metrics.add("quick_ratio")
            if quick >= 1.0:
                liq_metrics.append(f"Quick ratio: {quick:.1f}x (excludes inventory)")
            else:
                liq_metrics.append(f"Quick ratio: {quick:.1f}x (relies on inventory)")

        if not liq_metrics:
            liq_metrics.append("Liquidity data unavailable")

        self.component_metrics["liquidity"] = " • ".join(liq_metrics) if liq_metrics else ""
        
        # 5. Cash Flow: show FCF margin or meaningful fallback
        cf_data_source = self.data_sources.get("cash_flow", "no_data")
        cf_metrics = []

        fcf = self.ratios.get("fcf")
        fcf_margin = self.ratios.get("fcf_margin")
        ocf = self.ratios.get("operating_cash_flow")
        net_income = self.ratios.get("net_income")

        if fcf_margin is not None:
            # FCF Margin is the most useful metric - show it prominently
            used_metrics.add("fcf_margin")
            if fcf_margin >= 0.15:
                cf_metrics.append(f"Strong FCF margin: {fcf_margin * 100:.1f}% of revenue")
            elif fcf_margin >= 0.08:
                cf_metrics.append(f"Healthy FCF margin: {fcf_margin * 100:.1f}% of revenue")
            elif fcf_margin >= 0:
                cf_metrics.append(f"Modest FCF margin: {fcf_margin * 100:.1f}% of revenue")
            else:
                cf_metrics.append(f"Negative FCF margin: {fcf_margin * 100:.1f}% (cash burn)")
        elif fcf is not None:
            # Show absolute FCF with context
            used_metrics.add("fcf")
            fcf_str = format_value("fcf", fcf)
            if fcf > 0:
                cf_metrics.append(f"Positive FCF: {fcf_str}")
            else:
                cf_metrics.append(f"Negative FCF: {fcf_str} (cash burn)")
        elif ocf is not None:
            # Fallback to operating cash flow
            used_metrics.add("operating_cash_flow")
            ocf_str = format_value("operating_cash_flow", ocf)
            if ocf > 0:
                cf_metrics.append(f"Operating cash flow: {ocf_str} (FCF not reported)")
            else:
                cf_metrics.append(f"Operating cash flow: {ocf_str}")
        elif net_income is not None:
            # Use profitability as proxy with clear explanation
            net_margin = self.ratios.get("net_margin")
            if net_margin is not None:
                if net_margin >= 0.10:
                    cf_metrics.append(f"Estimated from {net_margin * 100:.1f}% profit margin (cash flow not reported)")
                else:
                    cf_metrics.append(f"Estimated from profit margin (direct cash flow data unavailable)")
            else:
                cf_metrics.append("Estimated from profitability (cash flow not reported)")
        else:
            cf_metrics.append("Cash flow data not available in filing")

        self.component_metrics["cash_flow"] = " • ".join(cf_metrics) if cf_metrics else ""
        
        # 6. Governance: show earnings quality or indicate what fallback is used
        gov_data_source = self.data_sources.get("governance", "no_data")
        gov_metrics = []
        
        if gov_data_source == "primary_earnings_quality":
            # Show the actual FCF to Net Income ratio (unique to governance)
            fcf = self.ratios.get("fcf")
            ni = self.ratios.get("net_income")
            if fcf is not None and ni is not None and ni != 0:
                ratio = fcf / abs(ni)
                if ratio >= 1.0:
                    gov_metrics.append(f"FCF/Net Income: {ratio:.0%} (excellent)")
                elif ratio >= 0.7:
                    gov_metrics.append(f"FCF/Net Income: {ratio:.0%} (good)")
                else:
                    gov_metrics.append(f"FCF/Net Income: {ratio:.0%}")
        elif gov_data_source == "fallback_debt_to_equity":
            if "debt_to_equity" not in used_metrics:
                d2e = format_metric("debt_to_equity")
                if d2e:
                    gov_metrics.append(d2e)
            else:
                gov_metrics.append("Based on debt levels")
        elif gov_data_source == "fallback_roe":
            if "roe" not in used_metrics:
                roe_val = format_metric("roe")
                if roe_val:
                    gov_metrics.append(roe_val)
            else:
                gov_metrics.append("Based on return on equity")
        elif gov_data_source == "fallback_roa":
            roa_val = format_metric("roa")
            if roa_val and "roa" not in used_metrics:
                gov_metrics.append(roa_val)
                used_metrics.add("roa")
            else:
                gov_metrics.append("Based on asset efficiency")
        elif gov_data_source == "fallback_interest_coverage":
            if "interest_coverage" not in used_metrics:
                ic_val = format_metric("interest_coverage")
                if ic_val:
                    gov_metrics.append(ic_val)
            else:
                gov_metrics.append("Based on debt service ability")
        
        self.component_metrics["governance"] = " • ".join(gov_metrics) if gov_metrics else ""
        
        # 7. Growth: show AI assessment description or fallback
        growth_data_source = self.data_sources.get("growth", "no_data")
        growth_metrics = []

        if growth_data_source == "ai_assessment":
            # Show AI-generated growth description
            if self.ai_growth_assessment and "description" in self.ai_growth_assessment:
                growth_metrics.append(self.ai_growth_assessment["description"])
            else:
                growth_metrics.append("AI growth assessment")
        elif growth_data_source == "fallback_operating_margin":
            # Show qualitative description based on management/sector perspective
            om = self.ratios.get("operating_margin")
            if om is not None:
                if om >= 0.20:
                    growth_metrics.append("Strong margin supports reinvestment capacity")
                elif om >= 0.10:
                    growth_metrics.append("Moderate margins indicate stable growth potential")
                elif om >= 0.05:
                    growth_metrics.append("Thin margins may limit growth investment")
                else:
                    growth_metrics.append("Margin pressure constrains growth outlook")
            else:
                growth_metrics.append("Growth outlook based on sector positioning")
        else:
            # No data at all - use neutral description
            growth_metrics.append("Growth outlook based on sector positioning")

        self.component_metrics["growth"] = " • ".join(growth_metrics) if growth_metrics else ""
    
    def _get_score_band(self, score: float) -> str:
        """Get score band label based on thresholds."""
        # Round to avoid floating point issues at boundaries
        rounded_score = round(score, 1)
        for threshold, label in self.SCORE_BANDS:
            if rounded_score >= threshold:
                return label
        return "At Risk"  # Fallback for any edge case


def calculate_health_score(
    ratios: Dict[str, Optional[float]],
    peer_data: Optional[List[Dict]] = None,
    weighting_preset: Optional[str] = None,
    ai_growth_assessment: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Calculate composite health score.

    Args:
        ratios: Dictionary of calculated ratios
        peer_data: Optional peer comparison data
        weighting_preset: Optional weighting preset name (e.g., 'cash_flow_conversion')
        ai_growth_assessment: Optional AI-generated growth assessment dict with 'score' and 'description'

    Returns:
        Health score breakdown including applied weights
    """
    scorer = HealthScorer(ratios, peer_data, custom_weights=weighting_preset, ai_growth_assessment=ai_growth_assessment)
    return scorer.calculate_health_score()

















