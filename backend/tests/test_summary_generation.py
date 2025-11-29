import pytest
from app.services.persona_engine import get_persona_engine, validate_persona_output_strict, INVESTOR_PERSONAS

class TestSummaryGeneration:
    def test_prompt_includes_recommendation_summary_instruction(self):
        """Test that the prompt builder includes the instruction for Final Recommendation Summary."""
        engine = get_persona_engine()
        # Mock data
        persona_id = "marks"
        company_name = "Test Corp"
        metrics_block = "Revenue: $1B"
        general_summary = "A good company."
        company_context = {"sector_risks": ["Risk A", "Risk B"]}
        
        prompt = engine._build_prompt(
            persona_id,
            company_name,
            metrics_block,
            general_summary,
            company_context
        )
        
        assert "## Final Recommendation Summary" in prompt
        assert "MANDATORY FINAL SECTION" in prompt
        assert "2-3 sentence summary" in prompt

    def test_validation_accepts_recommendation_summary_header(self):
        """Test that validate_persona_output_strict accepts '## Final Recommendation Summary'."""
        persona_id = "marks"
        persona = INVESTOR_PERSONAS[persona_id]
        
        # Output with the new header
        valid_output = """
        Where is the pendulum? It is currently swinging towards excessive optimism.
        The market has priced in perfection.
        Risk/reward asymmetry is unfavorable.
        We are in the late cycle.
        
        ## Final Recommendation Summary
        As Howard Marks, I recommend a HOLD. While the company is high quality, the current valuation leaves no margin of safety.
        """
        
        is_valid, issues = validate_persona_output_strict(persona_id, valid_output, persona)
        
        # Check that we don't have the "Generic section header" issue for this specific header
        generic_header_issues = [i for i in issues if "Generic section header" in i]
        assert not generic_header_issues, f"Should not flag Final Recommendation Summary as generic header: {issues}"

    def test_validation_still_rejects_other_generic_headers(self):
        """Test that validation still rejects other generic headers like '## Summary'."""
        persona_id = "marks"
        persona = INVESTOR_PERSONAS[persona_id]
        
        # Output with a banned header
        invalid_output = """
        Where is the pendulum?
        
        ## Summary
        This is a bad header.
        """
        
        is_valid, issues = validate_persona_output_strict(persona_id, invalid_output, persona)
        
        generic_header_issues = [i for i in issues if "Generic section header" in i]
        assert generic_header_issues, "Should still flag '## Summary' as generic header"
