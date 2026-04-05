import pytest
from pydantic import ValidationError

from app.models.schemas import FilingSummaryPreferences
from app.services import summary_agents
from app.services.prompt_pack import PromptContext, build_single_pass_prompt


def _company_intelligence() -> summary_agents.CompanyIntelligenceProfile:
    return summary_agents.CompanyIntelligenceProfile(
        business_identity="Enterprise software company with subscription revenue.",
        competitive_moat="High switching costs from embedded workflows.",
        primary_kpis=[],
        key_competitors=["Competitor A"],
        competitive_dynamics="Demand depends on renewal durability and upsell execution.",
        investor_focus_areas=[],
        industry_kpi_norms="",
        raw_brief="",
    )


def _filing_analysis() -> summary_agents.FilingAnalysis:
    return summary_agents.FilingAnalysis(
        central_tension="Can reinvestment stay disciplined while growth holds?",
        tension_evidence="Margins remain solid, but investment is rising.",
        kpi_findings=[],
        period_specific_insights=["Management emphasized disciplined investment."],
        management_quotes=[],
        management_strategy_summary="Management is prioritizing selective product investment.",
        company_specific_risks=[],
        evidence_map={"Closing Takeaway": ["Free cash flow stayed resilient despite heavier spend."]},
        company_terms=["workflow automation", "renewal base"],
    )


def test_filing_summary_preferences_accepts_valid_section_instructions() -> None:
    prefs = FilingSummaryPreferences(
        section_instructions={
            "Executive Summary": " Lead with the key surprise. ",
            "Closing Takeaway": "Focus on future outlook.",
        }
    )

    assert prefs.section_instructions == {
        "Executive Summary": "Lead with the key surprise.",
        "Closing Takeaway": "Focus on future outlook.",
    }


def test_filing_summary_preferences_rejects_invalid_section_instruction_key() -> None:
    with pytest.raises(ValidationError, match="invalid keys"):
        FilingSummaryPreferences(
            section_instructions={"Key Metrics": "Do not allow custom instructions here."}
        )


def test_filing_summary_preferences_rejects_overlong_section_instruction() -> None:
    with pytest.raises(ValidationError, match="exceeds 1000 characters"):
        FilingSummaryPreferences(
            section_instructions={"Executive Summary": "x" * 1001}
        )


def test_build_single_pass_prompt_injects_section_instruction_only_into_matching_section() -> None:
    prompt = build_single_pass_prompt(
        PromptContext(
            company_name="TestCo",
            section_instructions={
                "Closing Takeaway": "Focus on company performance and future outlook."
            },
            include_health_rating=False,
        )
    )

    closing_block = prompt.split("## Closing Takeaway", 1)[1]
    executive_block = prompt.split("## Executive Summary", 1)[1].split("## Financial Performance", 1)[0]

    assert "USER INSTRUCTION FOR THIS SECTION (absolute priority):" in closing_block
    assert "Focus on company performance and future outlook." in closing_block
    assert "USER INSTRUCTION FOR THIS SECTION (absolute priority):" not in executive_block


def test_build_single_pass_prompt_omits_section_instruction_block_when_empty() -> None:
    prompt = build_single_pass_prompt(
        PromptContext(
            company_name="TestCo",
            section_instructions={},
            include_health_rating=False,
        )
    )

    assert "USER INSTRUCTION FOR THIS SECTION (absolute priority):" not in prompt


def test_build_section_prompt_includes_matching_section_instruction() -> None:
    prompt = summary_agents._build_section_prompt(
        section_name="Closing Takeaway",
        company_intelligence=_company_intelligence(),
        filing_analysis=_filing_analysis(),
        company_name="TestCo",
        target_length=1000,
        budget=120,
        prior_section_text="",
        used_claims=[],
        section_memory=None,
        narrative_blueprint=None,
        financial_snapshot="",
        metrics_lines="",
        health_score_data=None,
        depth_plan=summary_agents.compute_depth_plan(summary_agents.compute_scale_factor(1000)),
        section_instructions={
            "Closing Takeaway": "Focus on company performance and future outlook."
        },
    )

    assert "USER INSTRUCTION FOR THIS SECTION (absolute priority" in prompt
    assert "Focus on company performance and future outlook." in prompt


def test_regenerate_pipeline_section_body_forwards_section_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_generate_section_body_to_budget(**kwargs: object) -> str:
        captured.update(kwargs)
        return "Section body"

    monkeypatch.setattr(
        summary_agents,
        "generate_section_body_to_budget",
        _fake_generate_section_body_to_budget,
    )

    result = summary_agents.regenerate_pipeline_section_body(
        pipeline_result=summary_agents.PipelineResult(
            summary_text="",
            company_intelligence=_company_intelligence(),
            filing_analysis=_filing_analysis(),
        ),
        section_name="Closing Takeaway",
        company_name="TestCo",
        target_length=1000,
        financial_snapshot="",
        metrics_lines="",
        health_score_data=None,
        budget=120,
        prior_section_text="",
        used_claims=[],
        section_memory=None,
        openai_client=object(),
        failure_reason="",
        section_instructions={
            "Closing Takeaway": "Focus on company performance and future outlook."
        },
    )

    assert result == "Section body"
    assert captured["section_instructions"] == {
        "Closing Takeaway": "Focus on company performance and future outlook."
    }
