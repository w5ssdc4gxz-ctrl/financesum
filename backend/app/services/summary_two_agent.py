"""Two-agent summary pipeline.

Agent 1: internet/company background research (cached dossier)
Agent 2: full filing summary generation with background injected
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.web_research import get_company_research_dossier

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_MODEL_NAME = "gpt-5.4-mini"
DEFAULT_AGENT1_TIMEOUT_SECONDS = 25.0
DEFAULT_AGENT2_TIMEOUT_SECONDS = 120.0
DEFAULT_AGENT2_MIN_RESERVED_SECONDS = 20.0


@dataclass
class TwoAgentSummaryPipelineResult:
    """Output from the two-agent summary pipeline."""

    summary_text: str
    model_used: str
    pipeline_mode: str = "two_agent"
    background_used: bool = False
    background_text: str = ""
    agent_timings: Dict[str, float] = field(default_factory=dict)
    agent_stage_calls: List[Dict[str, Any]] = field(default_factory=list)
    agent_1_api: str = "responses"
    agent_2_api: str = "responses"
    total_llm_calls: int = 0


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _agent1_timeout_seconds(explicit_timeout: Optional[float]) -> float:
    timeout = (
        float(explicit_timeout)
        if explicit_timeout is not None
        else _float_env("SUMMARY_AGENT1_TIMEOUT_SECONDS", DEFAULT_AGENT1_TIMEOUT_SECONDS)
    )
    return max(5.0, float(timeout))


def _agent2_timeout_seconds(
    explicit_timeout: Optional[float], *, total_timeout_seconds: Optional[float]
) -> float:
    timeout = (
        float(explicit_timeout)
        if explicit_timeout is not None
        else _float_env("SUMMARY_AGENT2_TIMEOUT_SECONDS", DEFAULT_AGENT2_TIMEOUT_SECONDS)
    )
    timeout = max(8.0, float(timeout))
    if total_timeout_seconds is not None:
        timeout = min(timeout, max(8.0, float(total_timeout_seconds)))
    return timeout


def _agent2_min_reserved_seconds(explicit_reserved: Optional[float] = None) -> float:
    if explicit_reserved is not None:
        return max(8.0, float(explicit_reserved))
    return max(
        8.0,
        _float_env(
            "SUMMARY_AGENT2_MIN_RESERVED_SECONDS",
            DEFAULT_AGENT2_MIN_RESERVED_SECONDS,
        ),
    )


def build_company_research_block(company_research_brief: str) -> str:
    """Render the Agent 1 research output as a prompt-safe background block."""
    brief = (company_research_brief or "").strip()
    if not brief:
        return ""
    return (
        "\nCOMPANY BACKGROUND KNOWLEDGE (internal reference - do NOT reproduce this section in output):\n"
        f"{brief}\n\n"
        "Use this background to inform your analysis. Ground every claim in the filing data provided, "
        "but let this context guide which aspects of the business to emphasize, which risks matter most, "
        "and what management's strategic direction means in the competitive landscape.\n"
        "Do NOT copy or paraphrase this background section directly - use it as deeper context only.\n"
    )


def run_two_agent_summary_pipeline(
    *,
    company_name: str,
    ticker: str,
    sector: str,
    industry: str,
    filing_type: str,
    filing_date: str = "",
    model_name: str,
    build_summary_prompt: Callable[[str], str],
    generate_summary: Callable[[str, float], str],
    progress_callback: Optional[Callable[[str, int], None]] = None,
    research_timeout_seconds: Optional[float] = None,
    summary_timeout_seconds: Optional[float] = None,
    total_timeout_seconds: Optional[float] = None,
    agent2_min_reserved_seconds: Optional[float] = None,
    force_research_refresh: bool = False,
    usage_context: Optional[Dict[str, Any]] = None,
) -> TwoAgentSummaryPipelineResult:
    """Run Agent 1 (research) then Agent 2 (summary generation)."""
    timings: Dict[str, float] = {}
    stage_calls: List[Dict[str, Any]] = []
    pipeline_started = time.monotonic()

    def _remaining_total_seconds() -> Optional[float]:
        if total_timeout_seconds is None:
            return None
        elapsed = max(0.0, float(time.monotonic() - pipeline_started))
        return max(0.0, float(total_timeout_seconds) - elapsed)

    company_research_brief = ""
    run_agent1_research = True
    research_timeout = _agent1_timeout_seconds(research_timeout_seconds)
    min_agent2_reserved = _agent2_min_reserved_seconds(agent2_min_reserved_seconds)
    remaining_before_research = _remaining_total_seconds()
    if remaining_before_research is not None:
        max_research_budget = max(
            0.0, float(remaining_before_research) - float(min_agent2_reserved)
        )
        if max_research_budget < 5.0:
            run_agent1_research = False
        else:
            research_timeout = min(float(research_timeout), float(max_research_budget))

    if run_agent1_research:
        if progress_callback:
            progress_callback("Researching Company Background...", 80)

        research_started = time.monotonic()
        company_research_brief = get_company_research_dossier(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
            filing_type=filing_type,
            filing_date=filing_date,
            timeout_seconds=research_timeout,
            force_refresh=force_research_refresh,
            usage_context=usage_context,
        )
        timings["agent_1_research_seconds"] = time.monotonic() - research_started
        stage_calls.append(
            {
                "stage": "agent_1_research",
                "api": "responses",
                "duration_seconds": timings["agent_1_research_seconds"],
            }
        )
    else:
        timings["agent_1_research_seconds"] = 0.0
        stage_calls.append(
            {
                "stage": "agent_1_research",
                "api": "responses",
                "duration_seconds": 0.0,
                "skipped": True,
            }
        )
        logger.info(
            "Skipping Agent 1 research for %s (%s): preserving runtime budget for Agent 2.",
            company_name,
            ticker,
        )

    research_block = build_company_research_block(company_research_brief)
    background_used = bool((company_research_brief or "").strip())

    if progress_callback:
        progress_callback("Synthesizing Investor Insights...", 85)

    remaining_before_summary = _remaining_total_seconds()
    if remaining_before_summary is not None and remaining_before_summary <= 0.0:
        raise TimeoutError("Summary generation exceeded total timeout before Agent 2.")

    summary_started = time.monotonic()
    summary_timeout = _agent2_timeout_seconds(
        summary_timeout_seconds, total_timeout_seconds=remaining_before_summary
    )
    if remaining_before_summary is not None:
        summary_timeout = max(
            1.0, min(float(summary_timeout), float(remaining_before_summary))
        )
    prompt = build_summary_prompt(research_block)
    summary_text = generate_summary(prompt, summary_timeout)
    timings["agent_2_summary_seconds"] = time.monotonic() - summary_started
    stage_calls.append(
        {
            "stage": "agent_2_summary",
            "api": "responses",
            "duration_seconds": timings["agent_2_summary_seconds"],
        }
    )

    # Agent 1 may return from cache, so total_llm_calls is best-effort.
    total_llm_calls = 1 + (1 if background_used else 0)
    logger.info(
        "Two-agent summary pipeline complete for %s (%s): model=%s background_used=%s timings=%s",
        company_name,
        ticker,
        model_name,
        background_used,
        {k: f"{v:.2f}s" for k, v in timings.items()},
    )

    return TwoAgentSummaryPipelineResult(
        summary_text=summary_text or "",
        model_used=model_name or DEFAULT_SUMMARY_MODEL_NAME,
        background_used=background_used,
        background_text=(company_research_brief or "").strip(),
        agent_timings=timings,
        agent_stage_calls=stage_calls,
        agent_1_api="responses",
        agent_2_api="responses",
        total_llm_calls=total_llm_calls,
    )
