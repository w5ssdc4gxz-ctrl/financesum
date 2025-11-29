"""Financial health score calculation service."""
from typing import Dict, Optional, List, Any
import statistics


class HealthScorer:
    """Calculate composite health score with percentile normalization."""
    
    # Weights for composite score (must sum to 100)
    WEIGHTS = {
        "financial_performance": 35,
        "profitability": 20,
        "leverage": 15,
        "liquidity": 10,
        "cash_flow": 10,
        "governance": 5,
        "growth": 5
    }
    
    # Score bands
    SCORE_BANDS = [
        (0, 49, "At Risk"),
        (50, 69, "Watch"),
        (70, 84, "Healthy"),
        (85, 100, "Very Healthy")
    ]
    
    def __init__(self, ratios: Dict[str, Optional[float]], peer_data: Optional[List[Dict]] = None):
        """
        Initialize health scorer.
        
        Args:
            ratios: Dictionary of calculated ratios
            peer_data: Optional list of peer company ratios for percentile calculation
        """
        self.ratios = ratios
        self.peer_data = peer_data or []
        self.normalized_scores = {}
        self.component_scores = {}
    
    def calculate_health_score(self) -> Dict[str, Any]:
        """
        Calculate composite health score.
        
        Returns:
            Dictionary with overall score, band, and component scores
        """
        # Normalize individual ratios to 0-100 scale
        self._normalize_ratios()
        
        # Calculate component scores
        self._calculate_component_scores()
        
        # Calculate weighted overall score
        overall_score = self._calculate_weighted_score()
        
        # Determine score band
        score_band = self._get_score_band(overall_score)
        
        return {
            "overall_score": round(overall_score, 2),
            "score_band": score_band,
            "component_scores": self.component_scores,
            "normalized_scores": self.normalized_scores
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
        # Financial Performance & Growth
        growth_scores = [
            self.normalized_scores.get("revenue_growth_yoy", 50)
        ]
        self.component_scores["financial_performance"] = statistics.mean(growth_scores)

        # Calculate growth component score more granularly
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
        else:
            self.component_scores["growth"] = 50  # Neutral if no data
        
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
        
        # Cash Flow Strength
        cash_flow_scores = [
            self.normalized_scores.get("fcf", 50),
            self.normalized_scores.get("fcf_margin", 50)
        ]
        self.component_scores["cash_flow"] = statistics.mean(cash_flow_scores)
        
        # Governance (placeholder - would need footnote/red flag analysis)
        self.component_scores["governance"] = 70  # Default score

        # Growth component is now calculated above based on revenue_growth_yoy
        # (replaces the old placeholder default)
    
    def _calculate_weighted_score(self) -> float:
        """Calculate weighted overall score."""
        total_score = 0
        total_weight = 0
        
        for component, weight in self.WEIGHTS.items():
            if component in self.component_scores:
                total_score += self.component_scores[component] * weight
                total_weight += weight
        
        if total_weight == 0:
            return 50
        
        return total_score / total_weight
    
    def _get_score_band(self, score: float) -> str:
        """Get score band label."""
        for min_score, max_score, label in self.SCORE_BANDS:
            if min_score <= score <= max_score:
                return label
        
        return "Unknown"


def calculate_health_score(
    ratios: Dict[str, Optional[float]],
    peer_data: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """
    Calculate composite health score.
    
    Args:
        ratios: Dictionary of calculated ratios
        peer_data: Optional peer comparison data
    
    Returns:
        Health score breakdown
    """
    scorer = HealthScorer(ratios, peer_data)
    return scorer.calculate_health_score()
















