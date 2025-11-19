"""Unit tests for health scorer."""
import pytest
from app.services.health_scorer import HealthScorer, calculate_health_score


@pytest.fixture
def sample_ratios():
    """Sample ratios for testing."""
    return {
        'revenue_growth_yoy': 0.15,
        'gross_margin': 0.45,
        'operating_margin': 0.20,
        'net_margin': 0.15,
        'roa': 0.12,
        'roe': 0.18,
        'current_ratio': 2.0,
        'quick_ratio': 1.5,
        'dso': 45.0,
        'inventory_turnover': 8.0,
        'debt_to_equity': 0.5,
        'net_debt_to_ebitda': 1.5,
        'interest_coverage': 10.0,
        'fcf': 50000,
        'fcf_margin': 0.15,
        'altman_z_score': 3.5
    }


class TestHealthScorer:
    """Test HealthScorer class."""

    def test_calculate_health_score_without_peers(self, sample_ratios):
        """Test health score calculation without peer data."""
        scorer = HealthScorer(sample_ratios, peer_data=None)
        result = scorer.calculate_health_score()
        
        assert 'overall_score' in result
        assert 'score_band' in result
        assert 'component_scores' in result
        assert 'normalized_scores' in result
        
        # Score should be between 0 and 100
        assert 0 <= result['overall_score'] <= 100
        
        # Should have all component scores
        expected_components = [
            'financial_performance', 'profitability', 'leverage',
            'liquidity', 'cash_flow', 'governance', 'growth'
        ]
        for component in expected_components:
            assert component in result['component_scores']

    def test_score_bands(self):
        """Test score band assignment."""
        # Test "At Risk" band
        ratios_poor = {
            'revenue_growth_yoy': -0.10,
            'gross_margin': 0.10,
            'operating_margin': 0.02,
            'net_margin': 0.01,
            'roa': 0.01,
            'roe': 0.02,
            'current_ratio': 0.8,
            'quick_ratio': 0.5,
            'dso': 90.0,
            'inventory_turnover': 2.0,
            'debt_to_equity': 3.0,
            'net_debt_to_ebitda': 5.0,
            'interest_coverage': 1.5,
            'fcf': -10000,
            'fcf_margin': -0.05,
            'altman_z_score': 1.5
        }
        
        scorer = HealthScorer(ratios_poor)
        result = scorer.calculate_health_score()
        
        # Should be in lower score range
        assert result['overall_score'] < 70

    def test_excellent_company(self):
        """Test scoring for an excellent company."""
        ratios_excellent = {
            'revenue_growth_yoy': 0.30,
            'gross_margin': 0.70,
            'operating_margin': 0.30,
            'net_margin': 0.25,
            'roa': 0.20,
            'roe': 0.30,
            'current_ratio': 2.5,
            'quick_ratio': 2.0,
            'dso': 30.0,
            'inventory_turnover': 15.0,
            'debt_to_equity': 0.2,
            'net_debt_to_ebitda': 0.5,
            'interest_coverage': 15.0,
            'fcf': 100000,
            'fcf_margin': 0.25,
            'altman_z_score': 5.0
        }
        
        scorer = HealthScorer(ratios_excellent)
        result = scorer.calculate_health_score()
        
        # Should have high score
        assert result['overall_score'] > 70
        assert result['score_band'] in ['Healthy', 'Very Healthy']

    def test_normalize_with_missing_ratios(self):
        """Test normalization with missing ratio values."""
        ratios_incomplete = {
            'revenue_growth_yoy': 0.15,
            'gross_margin': 0.45,
            'operating_margin': None,
            'net_margin': None,
            'roa': None,
            'roe': None,
            'current_ratio': 2.0,
            'quick_ratio': None,
            'dso': None,
            'inventory_turnover': None,
            'debt_to_equity': None,
            'net_debt_to_ebitda': None,
            'interest_coverage': None,
            'fcf': None,
            'fcf_margin': None,
            'altman_z_score': None
        }
        
        scorer = HealthScorer(ratios_incomplete)
        result = scorer.calculate_health_score()
        
        # Should still return valid result
        assert result['overall_score'] is not None
        assert 0 <= result['overall_score'] <= 100

    def test_weighted_score_calculation(self, sample_ratios):
        """Test that weights sum correctly."""
        scorer = HealthScorer(sample_ratios)
        
        # Verify weights sum to 100
        total_weight = sum(scorer.WEIGHTS.values())
        assert total_weight == 100

    def test_calculate_health_score_function(self, sample_ratios):
        """Test convenience function."""
        result = calculate_health_score(sample_ratios)
        
        assert 'overall_score' in result
        assert 'score_band' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
















