"""Unit tests for ratio calculator."""
import pytest
from app.services.ratio_calculator import RatioCalculator, calculate_ratios


@pytest.fixture
def sample_financial_data():
    """Sample financial data for testing."""
    return {
        "income_statement": {
            "revenue": {"2023": 100000, "2022": 90000},
            "gross_profit": {"2023": 40000},
            "operating_income": {"2023": 20000},
            "net_income": {"2023": 15000},
            "cost_of_revenue": {"2023": 60000},
            "interest_expense": {"2023": 2000},
            "ebitda": {"2023": 25000}
        },
        "balance_sheet": {
            "total_assets": {"2023": 150000, "2022": 140000},
            "current_assets": {"2023": 50000},
            "current_liabilities": {"2023": 30000},
            "total_equity": {"2023": 80000, "2022": 75000},
            "cash": {"2023": 20000},
            "accounts_receivable": {"2023": 15000},
            "inventories": {"2023": 10000, "2022": 9000},
            "short_term_debt": {"2023": 10000},
            "long_term_debt": {"2023": 30000},
            "total_liabilities": {"2023": 70000},
            "retained_earnings": {"2023": 50000}
        },
        "cash_flow": {
            "operating_cash_flow": {"2023": 18000},
            "capital_expenditures": {"2023": -5000}
        }
    }


class TestRatioCalculator:
    """Test RatioCalculator class."""

    def test_revenue_growth(self, sample_financial_data):
        """Test revenue growth calculation."""
        calculator = RatioCalculator(sample_financial_data)
        growth = calculator.calculate_revenue_growth()
        
        # (100000 - 90000) / 90000 = 0.1111...
        assert growth is not None
        assert abs(growth - 0.1111) < 0.001

    def test_gross_margin(self, sample_financial_data):
        """Test gross margin calculation."""
        calculator = RatioCalculator(sample_financial_data)
        margin = calculator.calculate_gross_margin()
        
        # 40000 / 100000 = 0.4
        assert margin == 0.4

    def test_operating_margin(self, sample_financial_data):
        """Test operating margin calculation."""
        calculator = RatioCalculator(sample_financial_data)
        margin = calculator.calculate_operating_margin()
        
        # 20000 / 100000 = 0.2
        assert margin == 0.2

    def test_net_margin(self, sample_financial_data):
        """Test net margin calculation."""
        calculator = RatioCalculator(sample_financial_data)
        margin = calculator.calculate_net_margin()
        
        # 15000 / 100000 = 0.15
        assert margin == 0.15

    def test_roa(self, sample_financial_data):
        """Test return on assets calculation."""
        calculator = RatioCalculator(sample_financial_data)
        roa = calculator.calculate_roa()
        
        # 15000 / ((150000 + 140000) / 2) = 15000 / 145000 = 0.1034...
        assert roa is not None
        assert abs(roa - 0.1034) < 0.001

    def test_roe(self, sample_financial_data):
        """Test return on equity calculation."""
        calculator = RatioCalculator(sample_financial_data)
        roe = calculator.calculate_roe()
        
        # 15000 / ((80000 + 75000) / 2) = 15000 / 77500 = 0.1935...
        assert roe is not None
        assert abs(roe - 0.1935) < 0.001

    def test_current_ratio(self, sample_financial_data):
        """Test current ratio calculation."""
        calculator = RatioCalculator(sample_financial_data)
        ratio = calculator.calculate_current_ratio()
        
        # 50000 / 30000 = 1.6667
        assert ratio is not None
        assert abs(ratio - 1.6667) < 0.001

    def test_quick_ratio(self, sample_financial_data):
        """Test quick ratio calculation."""
        calculator = RatioCalculator(sample_financial_data)
        ratio = calculator.calculate_quick_ratio()
        
        # (50000 - 10000) / 30000 = 1.3333
        assert ratio is not None
        assert abs(ratio - 1.3333) < 0.001

    def test_dso(self, sample_financial_data):
        """Test days sales outstanding calculation."""
        calculator = RatioCalculator(sample_financial_data)
        dso = calculator.calculate_dso()
        
        # (15000 / 100000) * 365 = 54.75
        assert dso is not None
        assert abs(dso - 54.75) < 0.1

    def test_inventory_turnover(self, sample_financial_data):
        """Test inventory turnover calculation."""
        calculator = RatioCalculator(sample_financial_data)
        turnover = calculator.calculate_inventory_turnover()
        
        # 60000 / ((10000 + 9000) / 2) = 60000 / 9500 = 6.3158
        assert turnover is not None
        assert abs(turnover - 6.3158) < 0.001

    def test_debt_to_equity(self, sample_financial_data):
        """Test debt-to-equity calculation."""
        calculator = RatioCalculator(sample_financial_data)
        ratio = calculator.calculate_debt_to_equity()
        
        # (10000 + 30000) / 80000 = 0.5
        assert ratio == 0.5

    def test_net_debt_to_ebitda(self, sample_financial_data):
        """Test net debt/EBITDA calculation."""
        calculator = RatioCalculator(sample_financial_data)
        ratio = calculator.calculate_net_debt_to_ebitda()
        
        # (10000 + 30000 - 20000) / 25000 = 0.8
        assert ratio == 0.8

    def test_interest_coverage(self, sample_financial_data):
        """Test interest coverage calculation."""
        calculator = RatioCalculator(sample_financial_data)
        coverage = calculator.calculate_interest_coverage()
        
        # 20000 / 2000 = 10.0
        assert coverage == 10.0

    def test_fcf(self, sample_financial_data):
        """Test free cash flow calculation."""
        calculator = RatioCalculator(sample_financial_data)
        fcf = calculator.calculate_fcf()
        
        # 18000 - 5000 = 13000
        assert fcf == 13000

    def test_fcf_margin(self, sample_financial_data):
        """Test FCF margin calculation."""
        calculator = RatioCalculator(sample_financial_data)
        margin = calculator.calculate_fcf_margin()
        
        # 13000 / 100000 = 0.13
        assert margin == 0.13

    def test_altman_z_score(self, sample_financial_data):
        """Test Altman Z-Score calculation."""
        calculator = RatioCalculator(sample_financial_data)
        z_score = calculator.calculate_altman_z_score()
        
        # Should return a positive number
        assert z_score is not None
        assert z_score > 0

    def test_calculate_all(self, sample_financial_data):
        """Test calculating all ratios at once."""
        ratios = calculate_ratios(sample_financial_data)
        
        # Should return dictionary with all ratio keys
        expected_keys = [
            'revenue_growth_yoy', 'gross_margin', 'operating_margin', 'net_margin',
            'roa', 'roe', 'current_ratio', 'quick_ratio', 'dso', 'inventory_turnover',
            'debt_to_equity', 'net_debt_to_ebitda', 'interest_coverage', 
            'fcf', 'fcf_margin', 'altman_z_score'
        ]
        
        for key in expected_keys:
            assert key in ratios

    def test_missing_data_handling(self):
        """Test handling of missing data."""
        incomplete_data = {
            "income_statement": {"revenue": {"2023": 100000}},
            "balance_sheet": {},
            "cash_flow": {}
        }
        
        calculator = RatioCalculator(incomplete_data)
        ratios = calculator.calculate_all()
        
        # Should not raise errors, should return None for unavailable ratios
        assert ratios['current_ratio'] is None
        assert ratios['roe'] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])












