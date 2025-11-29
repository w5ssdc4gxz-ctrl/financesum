"""Tests for the premium persona engine - Radically Distinctive Voice Implementation."""
import pytest
from app.services.persona_engine import (
    INVESTOR_PERSONAS,
    FEW_SHOT_EXAMPLES,
    PERSONA_PROMPT_TEMPLATES,
    extract_persona_relevant_metrics,
    validate_persona_output,
    generate_closing_persona_message,
)


class TestPersonaDefinitions:
    """Test persona definitions are complete and distinctive."""
    
    def test_all_personas_have_few_shot_examples(self):
        """Every persona should have distinctive few-shot examples."""
        for persona_id in INVESTOR_PERSONAS.keys():
            assert persona_id in FEW_SHOT_EXAMPLES, f"Missing few-shot examples for {persona_id}"
            assert len(FEW_SHOT_EXAMPLES[persona_id]) > 500, f"Few-shot examples too short for {persona_id}"
    
    def test_all_personas_have_prompt_templates(self):
        """Every persona should have a distinctive prompt template."""
        for persona_id in INVESTOR_PERSONAS.keys():
            assert persona_id in PERSONA_PROMPT_TEMPLATES, f"Missing prompt template for {persona_id}"
            assert "{metrics_block}" in PERSONA_PROMPT_TEMPLATES[persona_id], f"Template missing metrics placeholder for {persona_id}"
    
    def test_all_personas_have_signature_concepts(self):
        """Every persona should have signature concepts that distinguish them."""
        for persona_id, persona in INVESTOR_PERSONAS.items():
            assert "signature_concepts" in persona, f"Missing signature_concepts for {persona_id}"
            assert len(persona["signature_concepts"]) >= 3, f"Not enough signature concepts for {persona_id}"
    
    def test_personas_have_forbidden_elements(self):
        """Each persona should define what they DON'T do."""
        for persona_id, persona in INVESTOR_PERSONAS.items():
            assert "forbidden_elements" in persona, f"Missing forbidden_elements for {persona_id}"


class TestMetricExtraction:
    """Test persona-specific metric extraction."""
    
    @pytest.fixture
    def sample_ratios(self):
        return {
            "fcf": 5000000000,
            "fcf_margin": 0.15,
            "gross_margin": 0.40,
            "operating_margin": 0.20,
            "net_margin": 0.12,
            "roe": 0.25,
            "roa": 0.10,
            "current_ratio": 1.5,
            "debt_to_equity": 0.8,
            "revenue_growth_yoy": 0.15,
        }
    
    @pytest.fixture
    def sample_financial_data(self):
        return {
            "income_statement": {
                "revenue": {"2024": 50000000000},
                "net_income": {"2024": 6000000000},
            },
            "balance_sheet": {
                "cash": {"2024": 15000000000},
                "total_equity": {"2024": 40000000000},
                "current_assets": {"2024": 25000000000},
                "total_liabilities": {"2024": 30000000000},
            },
        }
    
    def test_metrics_extraction_works_for_all_personas(self, sample_ratios, sample_financial_data):
        """Metrics should extract for all personas without error."""
        for persona_id in INVESTOR_PERSONAS.keys():
            metrics = extract_persona_relevant_metrics(
                persona_id, sample_ratios, sample_financial_data, "Test Company"
            )
            assert len(metrics) > 100, f"Metrics too short for {persona_id}"
            assert "Revenue:" in metrics or "revenue" in metrics.lower()


class TestOutputValidation:
    """Test output validation enforces persona distinctiveness."""

    def test_validation_blocks_ratings(self):
        """Validation should reject any ratings or scores."""
        bad_outputs = [
            "Financial Health Rating: 72/100. " + "This is a good business." * 30,
            "Score: 8/10. " + "The fundamentals look solid." * 30,
            "Rating: 75. " + "Margins are improving." * 30,
            "I give this a 7 out of 10. " + "Management is capable." * 30,
        ]
        for bad in bad_outputs:
            is_valid, issues = validate_persona_output(
                "buffett", bad, INVESTOR_PERSONAS["buffett"]
            )
            assert not is_valid, f"Should have rejected output with rating"
            # Validation catches ratings via instant fail patterns
            all_issues = " ".join(issues).lower()
            has_rating_issue = "rating" in all_issues or "score" in all_issues or "/100" in all_issues or "/10" in all_issues
            assert has_rating_issue, f"Issues should mention ratings: {issues}"

    def test_greenblatt_rejects_verbose_output(self):
        """Greenblatt output over 200 words should be rejected."""
        verbose_output = """
        Return on Capital: 25%
        EBIT of $10B ÷ Invested Capital of $40B = 25%. Good company.

        Earnings Yield: 8%
        EBIT of $10B ÷ Enterprise Value of $125B = 8%. Fair valuation.

        VERDICT: Good but Expensive. Pass.
        """ + "This is extra narrative padding that makes the output too long. " * 20
        is_valid, issues = validate_persona_output(
            "greenblatt", verbose_output, INVESTOR_PERSONAS["greenblatt"]
        )
        assert not is_valid
        assert any("verbose" in i.lower() or "words" in i.lower() for i in issues)

    def test_greenblatt_rejects_narrative_language(self):
        """Greenblatt should reject emotional/narrative language."""
        # Make output long enough but still emotional
        emotional_output = """
        I worry about the valuation here. I am always searching for potential weaknesses.
        I remain cautious about this investment. Return on Capital is 20%.
        EBIT of $5B divided by Invested Capital of $25B = 20%. This is above the S&P average.
        Earnings Yield is 6%. EBIT of $5B divided by Enterprise Value of $83B = 6%.
        This is slightly above Treasury rates. VERDICT: Good but Expensive.
        The business quality is there but the price gives me pause. I remain cautious.
        """ + "The fundamentals continue to be solid. " * 10
        is_valid, issues = validate_persona_output(
            "greenblatt", emotional_output, INVESTOR_PERSONAS["greenblatt"]
        )
        assert not is_valid
        assert any("emotional" in i.lower() or "narrative" in i.lower() for i in issues)

    def test_greenblatt_rejects_unrealistic_mda_requests(self):
        """Greenblatt should reject requests for unrealistic management disclosures."""
        mda_output = """
        Return on Capital: 30%. Earnings Yield: 7%.
        Management should provide ROI on R&D projects. They should offer margin guidance.
        VERDICT: Good AND Cheap.
        """
        is_valid, issues = validate_persona_output(
            "greenblatt", mda_output, INVESTOR_PERSONAS["greenblatt"]
        )
        assert not is_valid
        assert any("unrealistic" in i.lower() or "disclosure" in i.lower() for i in issues)

    def test_greenblatt_rejects_lynch_contamination(self):
        """Greenblatt should not use Lynch-style language."""
        lynch_style = """
        The story is compelling. Return on Capital: 25%. Earnings Yield: 9%.
        Wall Street is missing this one. What inning are we in?
        VERDICT: Good AND Cheap.
        """
        is_valid, issues = validate_persona_output(
            "greenblatt", lynch_style, INVESTOR_PERSONAS["greenblatt"]
        )
        assert not is_valid
        assert any("lynch" in i.lower() or "contamination" in i.lower() for i in issues)

    def test_validation_blocks_generic_sections(self):
        """Validation should reject equity research section headers."""
        bad_output = """
        Executive Summary: This is a good company.
        Key Risks: Competition, regulation, macro volatility.
        Investment Thesis: Buy for growth.
        """
        is_valid, issues = validate_persona_output(
            "marks", bad_output, INVESTOR_PERSONAS["marks"]
        )
        assert not is_valid
        assert any("generic section" in i.lower() or "executive summary" in i.lower() for i in issues)
    
    def test_validation_requires_buffett_concepts(self):
        """Buffett output must include his signature concepts."""
        generic_output = """
        This company has good revenue growth and decent margins. 
        The business seems solid with reasonable financials.
        Management appears competent and the outlook is positive.
        I think this could be a good investment for growth-oriented investors.
        """ * 3  # Make it long enough
        is_valid, issues = validate_persona_output(
            "buffett", generic_output, INVESTOR_PERSONAS["buffett"]
        )
        assert not is_valid
        assert any("signature" in i.lower() or "concept" in i.lower() for i in issues)
    
    def test_validation_requires_marks_cycle_concepts(self):
        """Howard Marks must discuss cycles, pendulum, or second-level thinking."""
        non_marks_output = """
        This company has strong fundamentals with good growth prospects.
        The management team is experienced and the market position is solid.
        I recommend buying based on the attractive valuation metrics.
        The risk-reward appears favorable for long-term investors here.
        """ * 3
        is_valid, issues = validate_persona_output(
            "marks", non_marks_output, INVESTOR_PERSONAS["marks"]
        )
        assert not is_valid
        assert any("cycle" in i.lower() or "pendulum" in i.lower() or "second-level" in i.lower() for i in issues)
    
    def test_marks_cannot_be_confrontational(self):
        """Howard Marks should be reflective, not confrontational."""
        confrontational_output = """
        This raises serious questions about management. I demand greater transparency.
        The accounting practices demand investigation. This is concerning and troubling.
        """ * 3
        is_valid, issues = validate_persona_output(
            "marks", confrontational_output, INVESTOR_PERSONAS["marks"]
        )
        assert not is_valid
        assert any("confrontational" in i.lower() or "reflective" in i.lower() for i in issues)
    
    def test_munger_cannot_hedge(self):
        """Munger should not use hedging language."""
        hedging_output = """
        I believe this could potentially be a good investment. In my opinion,
        the company seems to have some competitive advantages that might work out.
        It seems like management is trying to do the right things.
        """ * 3
        is_valid, issues = validate_persona_output(
            "munger", hedging_output, INVESTOR_PERSONAS["munger"]
        )
        assert not is_valid
        assert any("hedge" in i.lower() or "believe" in i.lower() for i in issues)
    
    def test_lynch_requires_peg_ratio(self):
        """Peter Lynch must discuss PEG ratio."""
        no_peg_output = """
        This is a great growth story. The company makes products people love.
        Revenue is growing fast and the stock seems cheap for what you get.
        This could be a big winner over the next few years.
        Wall Street is missing this one completely.
        """ * 3
        is_valid, issues = validate_persona_output(
            "lynch", no_peg_output, INVESTOR_PERSONAS["lynch"]
        )
        assert not is_valid
        assert any("peg" in i.lower() for i in issues)
    
    def test_dalio_requires_cycle_discussion(self):
        """Ray Dalio must discuss cycles and the economic machine."""
        no_macro_output = """
        This company has good management and solid products.
        The financials look healthy with strong cash generation.
        I recommend buying based on the attractive fundamentals.
        """ * 3
        is_valid, issues = validate_persona_output(
            "dalio", no_macro_output, INVESTOR_PERSONAS["dalio"]
        )
        assert not is_valid
        assert any("cycle" in i.lower() or "machine" in i.lower() or "paradigm" in i.lower() for i in issues)
    
    def test_wood_requires_disruption_concepts(self):
        """Cathie Wood must discuss Wright's Law, S-curves, or disruption."""
        traditional_output = """
        The P/E ratio looks attractive at current levels.
        Earnings are growing steadily and management is experienced.
        This is a solid value investment with good current profitability.
        """ * 3
        is_valid, issues = validate_persona_output(
            "wood", traditional_output, INVESTOR_PERSONAS["wood"]
        )
        assert not is_valid
        assert any("wright" in i.lower() or "s-curve" in i.lower() or "disruption" in i.lower() for i in issues)


class TestPromptTemplates:
    """Test prompt template quality and distinctiveness."""

    def test_buffett_template_contains_key_concepts(self):
        """Buffett template should focus on moat and owner earnings."""
        template = PERSONA_PROMPT_TEMPLATES["buffett"]
        assert "moat" in template.lower()
        assert "owner earnings" in template.lower()

    def test_marks_template_focuses_on_risk(self):
        """Marks template should focus on risk asymmetry."""
        template = PERSONA_PROMPT_TEMPLATES["marks"]
        assert "risk" in template.lower()
        assert "asymmetry" in template.lower() or "psychology" in template.lower()

    def test_templates_specify_prose_format(self):
        """Templates should specify prose output format or direct/concise style."""
        # Buffett and Marks require prose/narrative style
        for persona_id in ["buffett", "marks"]:
            template = PERSONA_PROMPT_TEMPLATES[persona_id]
            assert "prose" in template.lower() or "no headers" in template.lower() or "no bullet" in template.lower()

    def test_greenblatt_template_is_brief(self):
        """Greenblatt template should specify word count limits."""
        template = PERSONA_PROMPT_TEMPLATES["greenblatt"]
        # Updated: Greenblatt now allows 200-300 words for complete analysis
        assert "200" in template or "300" in template or "maximum" in template.lower()

    def test_greenblatt_template_requires_roc_and_ey(self):
        """Greenblatt template must require ROC and Earnings Yield calculations."""
        template = PERSONA_PROMPT_TEMPLATES["greenblatt"]
        template_lower = template.lower()
        assert "return on capital" in template_lower
        assert "earnings yield" in template_lower
        assert "ebit" in template_lower
        assert "verdict" in template_lower

    def test_greenblatt_template_forbids_narrative(self):
        """Greenblatt template must forbid narrative and conversational language."""
        template = PERSONA_PROMPT_TEMPLATES["greenblatt"]
        template_lower = template.lower()
        assert "no narrative" in template_lower or "not narrative" in template_lower or "narrative" in template_lower
        # Template forbids conversational phrases
        assert "conversational" in template_lower or "clinical" in template_lower

    def test_each_template_has_signature_vocabulary(self):
        """Each template should include the persona's signature vocabulary."""
        vocabulary_checks = {
            "buffett": ["moat", "owner earnings"],
            "munger": ["inversion", "incentive"],  # Changed from "invert" to "inversion"
            "graham": ["margin of safety", "intrinsic value"],
            "lynch": ["peg"],  # Lynch uses PEG ratio prominently
            "dalio": ["cycle"],  # Cycle is the core concept
            "wood": ["disruption"],  # Core disruption concept
            "marks": ["risk", "asymmetry"],  # Risk asymmetry is key
            "ackman": ["catalyst"],  # Catalyst is signature concept
            "greenblatt": ["roic", "ebit"],  # Magic Formula core metrics
        }
        for persona_id, vocab in vocabulary_checks.items():
            template = PERSONA_PROMPT_TEMPLATES[persona_id].lower()
            for word in vocab:
                assert word in template, f"{persona_id} template missing '{word}'"


class TestClosingMessage:
    """Test closing persona message generation."""

    @pytest.fixture
    def high_quality_ratios(self):
        """Ratios for a high-quality company (e.g., NVDA-like)."""
        return {
            "gross_margin": 0.75,
            "operating_margin": 0.55,
            "net_margin": 0.45,
            "roe": 0.80,
            "roa": 0.40,
            "fcf": 30000000000,
            "current_ratio": 4.0,
            "debt_to_equity": 0.4,
            "revenue_growth_yoy": 1.22,  # 122% growth
            "pe_ratio": 55,
        }

    @pytest.fixture
    def moderate_quality_ratios(self):
        """Ratios for a moderate-quality company."""
        return {
            "gross_margin": 0.35,
            "operating_margin": 0.12,
            "net_margin": 0.08,
            "roe": 0.12,
            "roa": 0.06,
            "fcf": 1000000000,
            "current_ratio": 1.3,
            "debt_to_equity": 1.2,
            "revenue_growth_yoy": 0.05,
            "pe_ratio": 18,
        }

    @pytest.fixture
    def concerning_ratios(self):
        """Ratios for a concerning company."""
        return {
            "gross_margin": 0.15,
            "operating_margin": 0.02,
            "net_margin": 0.01,
            "roe": 0.05,
            "roa": 0.02,
            "fcf": -500000000,
            "current_ratio": 0.8,
            "debt_to_equity": 3.0,
            "revenue_growth_yoy": -0.10,
        }

    def test_closing_message_generated_for_all_personas(self, high_quality_ratios):
        """Every persona should generate a closing message."""
        for persona_id in INVESTOR_PERSONAS.keys():
            message = generate_closing_persona_message(
                persona_id, "NVDA", high_quality_ratios
            )
            assert len(message) > 50, f"Closing message too short for {persona_id}"
            assert "NVDA" in message, f"Company name missing in {persona_id} message"

    def test_high_quality_companies_get_positive_assessment(self, high_quality_ratios):
        """High-quality companies should get positive assessments."""
        message = generate_closing_persona_message(
            "marks", "NVDA", high_quality_ratios
        )
        positive_terms = ["exceptional", "high-quality", "quality"]
        has_positive = any(term in message.lower() for term in positive_terms)
        assert has_positive, f"High-quality company should get positive assessment: {message}"

    def test_concerning_companies_get_cautious_assessment(self, concerning_ratios):
        """Concerning companies should get cautious assessments."""
        message = generate_closing_persona_message(
            "marks", "WEAK Inc", concerning_ratios
        )
        # Should mention concerns or mixed picture
        cautious_terms = ["concern", "mixed", "warning", "careful", "risk"]
        has_cautious = any(term in message.lower() for term in cautious_terms)
        assert has_cautious, f"Concerning company should get cautious assessment: {message}"

    def test_high_valuation_triggers_caution(self, high_quality_ratios):
        """High P/E should trigger valuation caution."""
        message = generate_closing_persona_message(
            "marks", "NVDA", high_quality_ratios
        )
        valuation_caution_terms = ["priced in", "caution", "already", "elevated", "wait"]
        has_valuation_caution = any(term in message.lower() for term in valuation_caution_terms)
        assert has_valuation_caution, f"High P/E should trigger valuation caution: {message}"

    def test_persona_voice_is_distinct(self, high_quality_ratios):
        """Each persona's closing message should use their signature language."""
        # Marks should mention cycles, risk, or priced in
        marks_message = generate_closing_persona_message(
            "marks", "NVDA", high_quality_ratios
        )
        marks_terms = ["market", "valuation", "priced", "risk", "cycle"]
        has_marks_voice = any(term in marks_message.lower() for term in marks_terms)
        assert has_marks_voice, f"Marks message should use his vocabulary: {marks_message}"

        # Buffett should mention quality or moat
        buffett_message = generate_closing_persona_message(
            "buffett", "NVDA", high_quality_ratios
        )
        buffett_terms = ["quality", "durable", "moat", "business", "economics"]
        has_buffett_voice = any(term in buffett_message.lower() for term in buffett_terms)
        assert has_buffett_voice, f"Buffett message should use his vocabulary: {buffett_message}"

        # Lynch should mention story or growth
        lynch_message = generate_closing_persona_message(
            "lynch", "NVDA", high_quality_ratios
        )
        lynch_terms = ["story", "growth", "price", "buy", "excited"]
        has_lynch_voice = any(term in lynch_message.lower() for term in lynch_terms)
        assert has_lynch_voice, f"Lynch message should use his vocabulary: {lynch_message}"

    def test_empty_company_name_returns_empty(self, high_quality_ratios):
        """Empty company name should return empty message."""
        message = generate_closing_persona_message(
            "marks", "", high_quality_ratios
        )
        assert message == "", "Empty company name should return empty message"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
