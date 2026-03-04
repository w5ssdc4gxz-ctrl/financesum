import pytest

from app.services.ai_exceptions import AITimeoutError
from app.services import summary_two_agent


def _run_pipeline(
    *,
    force_research_refresh: bool = False,
    build_summary_prompt=None,
    generate_summary=None,
):
    return summary_two_agent.run_two_agent_summary_pipeline(
        company_name="Acme Corp",
        ticker="ACME",
        sector="Industrials",
        industry="Machinery",
        filing_type="10-K",
        model_name="gpt-5.2",
        build_summary_prompt=build_summary_prompt
        or (lambda company_research_block: f"PROMPT\n{company_research_block}\nEND"),
        generate_summary=generate_summary
        or (lambda prompt, timeout_seconds: "## Executive Summary\nDeterministic output."),
        force_research_refresh=force_research_refresh,
        total_timeout_seconds=60.0,
    )


def test_two_agent_pipeline_passes_force_refresh_to_research(monkeypatch):
    force_refresh_calls = []

    def _fake_dossier(**kwargs):
        force_refresh_calls.append(bool(kwargs.get("force_refresh")))
        return "Cached dossier" if not kwargs.get("force_refresh") else "Fresh dossier"

    monkeypatch.setattr(summary_two_agent, "get_company_research_dossier", _fake_dossier)

    first = _run_pipeline(force_research_refresh=False)
    second = _run_pipeline(force_research_refresh=True)

    assert force_refresh_calls == [False, True]
    assert first.background_used is True
    assert second.background_used is True


def test_two_agent_pipeline_injects_background_into_agent2_prompt(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        summary_two_agent,
        "get_company_research_dossier",
        lambda **_kwargs: "Internet context about business model durability.",
    )

    def _build_prompt(company_research_block: str) -> str:
        captured["research_block"] = company_research_block
        return f"FULL FILING CONTEXT\n{company_research_block}\nFINANCIAL SNAPSHOT"

    def _generate(prompt: str, timeout_seconds: float) -> str:
        captured["prompt"] = prompt
        captured["timeout"] = timeout_seconds
        return "## Executive Summary\nGenerated summary."

    result = _run_pipeline(
        build_summary_prompt=_build_prompt,
        generate_summary=_generate,
    )

    research_block = captured["research_block"]
    assert "COMPANY BACKGROUND KNOWLEDGE" in research_block
    assert "Internet context about business model durability." in research_block
    assert research_block in captured["prompt"]
    assert "FULL FILING CONTEXT" in captured["prompt"]
    assert result.summary_text.startswith("## Executive Summary")


def test_two_agent_pipeline_returns_stage_timings(monkeypatch):
    monkeypatch.setattr(
        summary_two_agent,
        "get_company_research_dossier",
        lambda **_kwargs: "Background dossier.",
    )

    result = _run_pipeline()

    assert "agent_1_research_seconds" in result.agent_timings
    assert "agent_2_summary_seconds" in result.agent_timings
    assert result.agent_timings["agent_1_research_seconds"] >= 0.0
    assert result.agent_timings["agent_2_summary_seconds"] >= 0.0
    assert result.agent_1_api == "responses"
    assert result.agent_2_api == "responses"
    assert len(result.agent_stage_calls) == 2
    assert result.agent_stage_calls[0]["stage"] == "agent_1_research"
    assert result.agent_stage_calls[0]["api"] == "responses"
    assert result.agent_stage_calls[1]["stage"] == "agent_2_summary"
    assert result.agent_stage_calls[1]["api"] == "responses"


def test_two_agent_pipeline_forwards_usage_context_to_research(monkeypatch):
    captured = {}

    def _fake_dossier(**kwargs):
        captured["usage_context"] = kwargs.get("usage_context")
        return "Background dossier."

    monkeypatch.setattr(summary_two_agent, "get_company_research_dossier", _fake_dossier)

    _ = summary_two_agent.run_two_agent_summary_pipeline(
        company_name="Acme Corp",
        ticker="ACME",
        sector="Industrials",
        industry="Machinery",
        filing_type="10-K",
        model_name="gpt-5.2",
        build_summary_prompt=lambda company_research_block: f"PROMPT\n{company_research_block}",
        generate_summary=lambda prompt, timeout_seconds: "## Executive Summary\nGenerated.",
        usage_context={
            "request_id": "req-1",
            "filing_id": "fil-1",
            "company_id": "co-1",
            "user_id": "user-1",
            "pipeline_mode": "two_agent",
        },
    )

    assert captured["usage_context"]["request_id"] == "req-1"
    assert captured["usage_context"]["pipeline_mode"] == "two_agent"


def test_two_agent_pipeline_propagates_agent2_timeout(monkeypatch):
    monkeypatch.setattr(
        summary_two_agent,
        "get_company_research_dossier",
        lambda **_kwargs: "Background dossier.",
    )

    def _timeout(*_args, **_kwargs):
        raise AITimeoutError("Agent 2 summary timed out")

    with pytest.raises(AITimeoutError):
        _run_pipeline(generate_summary=_timeout)


def test_two_agent_pipeline_skips_research_when_budget_too_tight(monkeypatch):
    monkeypatch.setenv("SUMMARY_AGENT2_MIN_RESERVED_SECONDS", "90")
    dossier_calls = []
    captured = {}

    def _fake_dossier(**kwargs):
        dossier_calls.append(kwargs)
        return "Background dossier that should be skipped."

    monkeypatch.setattr(summary_two_agent, "get_company_research_dossier", _fake_dossier)

    def _generate(prompt: str, timeout_seconds: float) -> str:
        captured["prompt"] = prompt
        captured["timeout_seconds"] = timeout_seconds
        return "## Executive Summary\nGenerated summary."

    result = summary_two_agent.run_two_agent_summary_pipeline(
        company_name="Acme Corp",
        ticker="ACME",
        sector="Industrials",
        industry="Machinery",
        filing_type="10-K",
        model_name="gpt-5.2",
        build_summary_prompt=lambda company_research_block: f"PROMPT\n{company_research_block}",
        generate_summary=_generate,
        total_timeout_seconds=30.0,
    )

    assert dossier_calls == []
    assert result.background_used is False
    assert result.agent_stage_calls[0]["stage"] == "agent_1_research"
    assert result.agent_stage_calls[0].get("skipped") is True
    assert "COMPANY BACKGROUND KNOWLEDGE" not in captured["prompt"]


def test_two_agent_pipeline_clamps_research_timeout_to_preserve_agent2_budget(
    monkeypatch,
):
    monkeypatch.setenv("SUMMARY_AGENT2_MIN_RESERVED_SECONDS", "90")
    captured = {}

    def _fake_dossier(**kwargs):
        captured["research_timeout_seconds"] = kwargs.get("timeout_seconds")
        return "Background dossier."

    monkeypatch.setattr(summary_two_agent, "get_company_research_dossier", _fake_dossier)

    def _generate(prompt: str, timeout_seconds: float) -> str:
        captured["summary_timeout_seconds"] = timeout_seconds
        return "## Executive Summary\nGenerated summary."

    result = summary_two_agent.run_two_agent_summary_pipeline(
        company_name="Acme Corp",
        ticker="ACME",
        sector="Industrials",
        industry="Machinery",
        filing_type="10-K",
        model_name="gpt-5.2",
        build_summary_prompt=lambda company_research_block: f"PROMPT\n{company_research_block}",
        generate_summary=_generate,
        total_timeout_seconds=100.0,
    )

    assert float(captured["research_timeout_seconds"]) <= 10.5
    assert float(captured["summary_timeout_seconds"]) >= 1.0
    assert float(captured["summary_timeout_seconds"]) <= 100.0
    assert result.background_used is True
