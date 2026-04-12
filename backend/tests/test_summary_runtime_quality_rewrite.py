from types import SimpleNamespace

from app.api import filings as filings_api
from app.services import summary_agents
from app.services.repetition_guard import check_repetition
from app.services.summary_post_processor import (
    SectionValidationFailure,
    SummaryValidationReport,
    validate_summary,
)
from app.services.word_surgery import count_words


def _company_intelligence() -> summary_agents.CompanyIntelligenceProfile:
    return summary_agents.CompanyIntelligenceProfile(
        business_identity="Cloud software platform serving enterprise customers.",
        competitive_moat="High switching costs and workflow lock-in.",
        primary_kpis=[],
        key_competitors=["ServiceNow"],
        competitive_dynamics="Conversion, renewals, and monetization quality drive the story.",
        investor_focus_areas=["Cloud backlog conversion", "AI monetization"],
        industry_kpi_norms="",
        raw_brief="",
    )


def _filing_analysis() -> summary_agents.FilingAnalysis:
    return summary_agents.FilingAnalysis(
        central_tension="That leaves Common Stock as the balance-sheet pressure point.",
        tension_evidence="The real underwriting question is whether cloud backlog converts into durable margin.",
        kpi_findings=[
            summary_agents.KPIFinding(
                kpi_name="Cloud Backlog Conversion",
                current_value="118%",
                prior_value="104%",
                change="+1400 bps YoY",
                insight="Backlog is converting faster as new capacity lands.",
                source_quote='Management said "conversion improved as capacity came online."',
            ),
            summary_agents.KPIFinding(
                kpi_name="Operating Margin",
                current_value="29%",
                prior_value="27%",
                change="+200 bps YoY",
                insight="Margin held despite the heavier infrastructure cycle.",
            ),
        ],
        period_specific_insights=[
            "Cloud backlog conversion is improving as capacity ramps.",
            "The valuation case depends on turning that conversion into durable margins.",
        ],
        management_quotes=[
            summary_agents.ManagementQuote(
                quote="conversion improved as capacity came online",
                attribution="Management",
                topic="capacity ramp",
                suggested_section="Management Discussion & Analysis",
            )
        ],
        management_strategy_summary=(
            "Cloud backlog conversion is the operating proof point because the next phase "
            "depends on monetizing the new capacity buildout."
        ),
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="Capacity Ramp Slippage",
                mechanism=(
                    "If capacity ramps slip, backlog conversion can stall before margin gains "
                    "show up in reported results."
                ),
                early_warning="An early-warning signal is slower backlog conversion or lower utilization.",
                evidence_from_filing="The filing warns that deployment timing remains a gating factor.",
                source_section="Risk Factors",
                source_quote="deployment timing remains a gating factor",
            )
        ],
        evidence_map={
            "Financial Performance": [
                "Operating margin expanded 200 bps as cloud utilization improved.",
                "Free cash flow stayed positive even as capex remained elevated.",
            ],
            "Management Discussion & Analysis": [
                "Management expects backlog conversion to keep improving as new capacity lands.",
                'Management said "conversion improved as capacity came online."',
            ],
            "Risk Factors": [
                "The filing warns that deployment timing remains a gating factor.",
            ],
            "Closing Takeaway": [
                "A faster conversion cycle would justify keeping the stance constructive.",
            ],
        },
        company_terms=["Cloud backlog conversion", "AI monetization", "capacity ramp"],
        management_expectations=[
            summary_agents.ManagementExpectation(
                topic="Cloud backlog conversion",
                expectation="Management expects cloud backlog conversion to improve over the next 12 months.",
                timeframe="next 12 months",
                evidence="Management expects conversion to improve as capacity comes online.",
            )
        ],
        management_strategic_bets=["Cloud backlog conversion"],
        forward_guidance_summary="Management expects conversion and monetization to improve over the next year.",
    )


def _memo_with_repeated_leadin() -> tuple[str, dict[str, int]]:
    sections = {
        "Executive Summary": (
            'Management noted that "cloud backlog is finally converting," which sets the '
            "underwriting thread for this filing. That leaves cloud backlog conversion as "
            "the decisive operating proof point."
        ),
        "Financial Performance": (
            "Operating margin expanded 200 basis points as utilization improved and backlog "
            "converted into recognized revenue. Free cash flow stayed positive despite the "
            "heavier capex cycle."
        ),
        "Management Discussion & Analysis": (
            'Management said "conversion improved as capacity came online," and the plan now '
            "depends on turning that capacity into durable monetization over the next 12 months."
        ),
        "Risk Factors": (
            "**Capacity Ramp Slippage:** If new capacity arrives later than customer demand, "
            "backlog conversion can stall before revenue and margin catch up. That mechanism "
            "would pressure recognized revenue, utilization, and operating margin because the "
            "company is funding infrastructure before monetization fully lands. The impact "
            "matters now because the current thesis already assumes better conversion from the "
            "new capacity build. An early-warning signal is lower utilization, slower backlog "
            "conversion, or repeated commentary that deployment timing is still the bottleneck.\n\n"
            "**AI Monetization Shortfall:** If AI product usage grows faster than paid conversion, "
            "the company can absorb higher compute cost without earning the pricing needed to "
            "protect margins. That mechanism would weaken free cash flow and reduce the funding "
            "cushion for the rest of the investment cycle. The risk matters now because the "
            "company is already spending into the next phase of the rollout before the economics "
            "are fully proven. An early-warning signal is rising inference cost, weaker paid "
            "conversion, or commentary that pricing is lagging usage growth."
        ),
        "Key Metrics": (
            "-> Cloud Backlog Conversion: 118%\n"
            "-> Operating Margin: 29%\n"
            "-> Free Cash Flow: $4.2B"
        ),
        "Closing Takeaway": (
            "That leaves cloud backlog conversion as the decisive next test for the current stance. "
            "The non-obvious implication is that valuation can stay supported even with heavy capex "
            "if conversion keeps funding margin resilience. Investors should revisit the stance if "
            "cloud backlog conversion stalls below 110% over the next two quarters."
        ),
    }
    memo = "\n\n".join(f"## {title}\n{body}" for title, body in sections.items())
    budgets = {title: count_words(body) for title, body in sections.items()}
    return memo, budgets


def test_arbitrate_thread_rejects_common_stock_anchor() -> None:
    decision = summary_agents._arbitrate_thread(
        company_name="TestCo",
        company_intelligence=_company_intelligence(),
        filing_analysis=_filing_analysis(),
        focus_areas=["Cloud backlog conversion"],
        investor_focus="future outlook",
    )

    assert decision.anchor.lower() != "common stock"
    assert any(
        candidate.anchor.lower() == "common stock" and not candidate.accepted
        for candidate in decision.rejected_threads
    )


def test_check_repetition_detects_repeated_that_leaves_leadin() -> None:
    report = check_repetition(
        "That leaves cloud backlog conversion as the hinge. "
        "Margins held steady. "
        "That leaves free cash flow as the valuation support."
    )

    assert "that leaves" in report.repeated_leadins
    assert "repeated_leadins" in report.violation_types


def test_judge_sectioned_summary_hard_fails_repeated_leadins() -> None:
    failures = summary_agents._judge_sectioned_summary(
        section_bodies={
            "Executive Summary": (
                "That leaves cloud backlog conversion as the decisive operating proof point. "
                "Management still has to prove the improvement can hold."
            ),
            "Closing Takeaway": (
                "That leaves cloud backlog conversion as the decisive next test for the stance. "
                "Investors should revisit the stance if conversion slips below 110% over the next two quarters."
            ),
        },
        include_health_rating=False,
        thread_decision=summary_agents.ThreadDecision(
            final_thread="Cloud backlog conversion is the underwriting hinge.",
            anchor="Cloud backlog conversion",
            anchor_class="operating_kpi",
            aha_insight="Margin support depends on conversion staying durable.",
        ),
        section_plans={
            "Executive Summary": summary_agents.SectionPlan(
                section_name="Executive Summary",
                job="State the thread.",
                question="What changed?",
                owned_evidence=["Cloud backlog conversion"],
            ),
            "Closing Takeaway": summary_agents.SectionPlan(
                section_name="Closing Takeaway",
                job="Resolve the stance.",
                question="What changes the stance?",
                owned_evidence=["Cloud backlog conversion"],
            ),
        },
    )

    assert any(failure.code == "repeated_leadin" for failure in failures)


def test_build_section_plans_assigns_distinct_evidence_and_instruction_checks() -> None:
    analysis = _filing_analysis()
    blueprint = summary_agents._build_narrative_blueprint(
        company_name="TestCo",
        company_intelligence=_company_intelligence(),
        filing_analysis=analysis,
    )
    thread_decision = summary_agents._arbitrate_thread(
        company_name="TestCo",
        company_intelligence=_company_intelligence(),
        filing_analysis=analysis,
        focus_areas=["Cloud backlog conversion"],
        investor_focus="future outlook",
    )

    plans = summary_agents._build_section_plans(
        narrative_blueprint=blueprint,
        thread_decision=thread_decision,
        tone="objective",
        detail_level="balanced",
        output_style="narrative",
        section_instructions={
            "Closing Takeaway": "Focus on company performance and future outlook."
        },
    )

    assert "Operating margin expanded 200 bps as cloud utilization improved." in plans["Financial Performance"].owned_evidence
    assert "Operating margin expanded 200 bps as cloud utilization improved." not in plans["Management Discussion & Analysis"].owned_evidence
    assert "Management expects backlog conversion to keep improving as new capacity lands." in plans["Management Discussion & Analysis"].owned_evidence
    assert "Management expects backlog conversion to keep improving as new capacity lands." not in plans["Closing Takeaway"].owned_evidence
    assert any(
        check.check_type == "must_be_forward_looking"
        for check in plans["Closing Takeaway"].instruction_checks
    )


def test_regenerate_pipeline_section_body_uses_metadata_section_instructions() -> None:
    captured: dict[str, object] = {}

    def _fake_generate_section_body_to_budget(**kwargs: object) -> str:
        captured.update(kwargs)
        return "Rewritten closing body"

    original = summary_agents.generate_section_body_to_budget
    summary_agents.generate_section_body_to_budget = _fake_generate_section_body_to_budget
    try:
        result = summary_agents.regenerate_pipeline_section_body(
            pipeline_result=summary_agents.PipelineResult(
                summary_text="",
                company_intelligence=_company_intelligence(),
                filing_analysis=_filing_analysis(),
                metadata={
                    "section_instructions": {
                        "Closing Takeaway": "Focus on company performance and future outlook."
                    },
                    "thread_decision": summary_agents._serialize_thread_decision(
                        summary_agents.ThreadDecision(
                            final_thread="Cloud backlog conversion is the underwriting hinge.",
                            anchor="Cloud backlog conversion",
                            anchor_class="operating_kpi",
                            aha_insight="Margin support depends on conversion staying durable.",
                        )
                    ),
                    "section_plans": {
                        "Closing Takeaway": summary_agents._serialize_section_plan(
                            summary_agents.SectionPlan(
                                section_name="Closing Takeaway",
                                job="Resolve the thread.",
                                question="What changes the stance?",
                            )
                        )
                    },
                },
            ),
            section_name="Closing Takeaway",
            company_name="TestCo",
            target_length=900,
            financial_snapshot="",
            metrics_lines="",
            health_score_data=None,
            budget=130,
            prior_section_text="",
            used_claims=[],
            section_memory=None,
            openai_client=object(),
            failure_reason="instruction_miss",
        )
    finally:
        summary_agents.generate_section_body_to_budget = original

    assert result == "Rewritten closing body"
    assert captured["section_instructions"] == {
        "Closing Takeaway": "Focus on company performance and future outlook."
    }


def test_judge_sectioned_summary_flags_instruction_miss_and_soft_closing() -> None:
    thread_decision = summary_agents.ThreadDecision(
        final_thread="Cloud backlog conversion is the underwriting hinge.",
        anchor="Cloud backlog conversion",
        anchor_class="operating_kpi",
        aha_insight="Margin support depends on conversion staying durable.",
    )
    plan = summary_agents.SectionPlan(
        section_name="Closing Takeaway",
        job="Resolve the stance with a measurable trigger.",
        question="What changes the stance?",
        owned_evidence=["Cloud backlog conversion is the underwriting hinge."],
        instruction_checks=[
            summary_agents.InstructionCheck(
                section_name="Closing Takeaway",
                check_type="must_be_forward_looking",
                target="future outlook",
                guidance="Focus on future outlook.",
            )
        ],
    )

    failures = summary_agents._judge_sectioned_summary(
        section_bodies={
            "Closing Takeaway": (
                "HOLD still makes sense because cloud backlog conversion held up this quarter. "
                "The current stance is intact."
            )
        },
        include_health_rating=False,
        thread_decision=thread_decision,
        section_plans={"Closing Takeaway": plan},
    )

    failure_codes = {failure.code for failure in failures}
    assert "instruction_miss" in failure_codes
    assert "closing_soft" in failure_codes


def test_cv2_editorial_guard_distinguishes_editorial_from_structural_failures() -> None:
    editorial_validation = SummaryValidationReport(
        passed=False,
        total_words=900,
        lower_bound=870,
        upper_bound=930,
        section_failures=[
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="repeated_leadin",
                message="Closing Takeaway repeats a rhetorical lead-in already used elsewhere.",
                severity=2.7,
            )
        ],
    )
    structural_validation = SummaryValidationReport(
        passed=False,
        total_words=900,
        lower_bound=870,
        upper_bound=930,
        section_failures=[
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="section_budget_under",
                message="Closing Takeaway is underweight.",
                actual_words=80,
                budget_words=130,
                severity=0.5,
            )
        ],
    )

    editorial_flags = filings_api._issue_flags_from_validation_report(editorial_validation)
    structural_flags = filings_api._issue_flags_from_validation_report(structural_validation)

    assert filings_api._cv2_has_editorial_failures(editorial_validation, editorial_flags)
    assert not filings_api._cv2_has_editorial_failures(structural_validation, structural_flags)


def test_scrub_executive_summary_numeric_density_never_emits_placeholder_artifacts() -> None:
    summary_text = (
        "## Executive Summary\n"
        "Revenue reached $161.86 billion and operating income hit $52.1 billion in FY25. "
        "Operating margin improved to 32.1% while free cash flow rose to $71.4 billion. "
        'Management said "the next phase depends on keeping conversion high." '
        "Capex was $24.5 billion as the company expanded AI capacity.\n\n"
        "## Financial Performance\n"
        "Margins improved as utilization held.\n\n"
        "## Management Discussion & Analysis\n"
        'Management said "the next phase depends on keeping conversion high."\n\n'
        "## Risk Factors\n"
        "**Capacity Ramp Slippage:** If capacity lands late, utilization and margins can weaken. "
        "An early-warning signal is lower utilization over the next two quarters.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $161.86B\nOperating Margin | 32.1%\nFree Cash Flow | $71.4B\n"
        "Capex | $24.5B\nOperating Income | $52.1B\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD remains appropriate if conversion stays durable over the next two quarters."
    )

    cleaned, changed = filings_api._scrub_executive_summary_numeric_density(
        summary_text,
        target_length=900,
    )

    assert changed > 0
    assert "$that figure" not in cleaned
    assert "that figure%" not in cleaned
    assert "that figure billion" not in cleaned.lower()
    assert "the cited amount" not in cleaned.lower()


def test_key_metrics_intro_block_survives_validation_and_canonicalization() -> None:
    summary_text = (
        "## Executive Summary\n"
        "Thesis.\n\n"
        "## Financial Performance\n"
        "Performance.\n\n"
        "## Management Discussion & Analysis\n"
        "MD&A.\n\n"
        "## Risk Factors\n"
        "Risks.\n\n"
        "## Key Metrics\n"
        "What Matters:\n"
        "- Cloud backlog conversion is the proof point for the next leg of the thesis.\n"
        "- Operating margin shows whether the heavier AI cycle is still self-funding.\n\n"
        "DATA_GRID_START\n"
        "Revenue | $10.0B\n"
        "Operating Margin | 25%\n"
        "Free Cash Flow | $4.2B\n"
        "Net Debt | $1.1B\n"
        "Current Ratio | 1.8x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD with a trigger."
    )

    key_metrics_body = filings_api._extract_markdown_section_body(summary_text, "Key Metrics")
    issue, numeric_rows = filings_api._validate_key_metrics_numeric_block(
        key_metrics_body or "",
        min_rows=5,
        require_markers=True,
    )
    canonicalized = filings_api._canonicalize_key_metrics_section(
        summary_text,
        "DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 25%\nFree Cash Flow | $4.2B\nNet Debt | $1.1B\nCurrent Ratio | 1.8x\nDATA_GRID_END",
    )

    assert issue is None
    assert numeric_rows == 5
    assert "What Matters:" in canonicalized
    assert "- Cloud backlog conversion is the proof point for the next leg of the thesis." in canonicalized


def test_build_key_metrics_body_includes_what_matters_intro() -> None:
    body = summary_agents._build_key_metrics_body(
        metrics_lines="→ Revenue: $10.0B\n→ Operating Margin: 25%\n→ Free Cash Flow: $4.2B",
        analysis=_filing_analysis(),
        max_words=120,
    )

    assert "What Matters:" in body
    assert "→ Revenue: $10.0B" in body
    assert "- Watch" in body or "- Cloud backlog conversion" in body


def test_short_form_structural_seal_preserve_mode_skips_generic_transition_injection() -> None:
    summary_text = (
        "## Executive Summary\n"
        "Management frames the filing around conversion and monetization.\n\n"
        "## Financial Performance\n"
        "Operating margin expanded as utilization improved.\n\n"
        "## Management Discussion & Analysis\n"
        'Management said "conversion improved as capacity came online."\n\n'
        "## Risk Factors\n"
        "**Capacity Ramp Slippage:** If new capacity lands late, backlog conversion can stall. "
        "An early-warning signal is lower utilization over the next two quarters.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 25%\nFree Cash Flow | $4.2B\nNet Debt | $1.1B\nCurrent Ratio | 1.8x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD if conversion remains above management's implied threshold."
    )

    default_text = filings_api._apply_short_form_structural_seal(
        summary_text,
        include_health_rating=False,
        metrics_lines="DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 25%\nFree Cash Flow | $4.2B\nNet Debt | $1.1B\nCurrent Ratio | 1.8x\nDATA_GRID_END",
        calculated_metrics={"operating_margin": 25.0, "free_cash_flow": 4_200_000_000},
        company_name="TestCo",
        target_length=900,
    )
    preserved_text = filings_api._apply_short_form_structural_seal(
        summary_text,
        include_health_rating=False,
        metrics_lines="DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 25%\nFree Cash Flow | $4.2B\nNet Debt | $1.1B\nCurrent Ratio | 1.8x\nDATA_GRID_END",
        calculated_metrics={"operating_margin": 25.0, "free_cash_flow": 4_200_000_000},
        company_name="TestCo",
        target_length=900,
        preserve_pipeline_editorial=True,
    )

    assert "The next question is" in default_text or "That leaves" in default_text
    assert "The next question is" not in preserved_text
    assert "That leaves" not in preserved_text


def test_strict_contract_seal_preserve_mode_allows_safe_contract_repairs() -> None:
    filing_snippets = (
        '"we expect conversion to keep improving next year as new capacity comes online."\n'
        '"our priority is monetizing the current capacity buildout with disciplined margins."'
    )
    summary_text = (
        "## Executive Summary\n"
        "Revenue reached $10.0B while operating income hit $3.0B, margin was 30%, free cash flow was $2.0B, and cash ended at $5.0B.\n\n"
        "## Financial Performance\n"
        "Revenue, margin, and cash conversion improved versus the prior year.\n\n"
        "## Management Discussion & Analysis\n"
        "The company is still investing in capacity for the next phase of growth.\n\n"
        "## Risk Factors\n"
        "**Capacity Ramp Slippage:** If capacity lands late, monetization and margin conversion can slip. "
        "An early-warning signal is lower utilization over the next two quarters.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 30%\nFree Cash Flow | $2.0B\nCash | $5.0B\nCurrent Ratio | 1.8x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD remains appropriate if conversion keeps improving over the next year."
    )

    sealed = filings_api._apply_strict_contract_seal(
        summary_text,
        include_health_rating=False,
        target_length=1000,
        calculated_metrics={"operating_margin": 30.0, "free_cash_flow": 2_000_000_000},
        metrics_lines=(
            "DATA_GRID_START\nRevenue | $10.0B\nOperating Margin | 30%\nFree Cash Flow | $2.0B\n"
            "Cash | $5.0B\nCurrent Ratio | 1.8x\nDATA_GRID_END"
        ),
        filing_language_snippets=filing_snippets,
        strict_quote_contract=False,
        company_name="TestCo",
        persona_requested=False,
        final_issue_flags={
            "management_voice_issue": True,
            "numbers_discipline_issue": True,
            "numbers_discipline_sections": ["Executive Summary"],
        },
        preserve_pipeline_editorial=True,
    )

    exec_body = filings_api._extract_markdown_section_body(sealed, "Executive Summary") or ""
    assert filings_api._count_numeric_tokens(exec_body) <= filings_api._numbers_discipline_caps(
        1000
    )["Executive Summary"]
    assert (
        filings_api._make_management_forward_looking_validator(
            filing_language_snippets=filing_snippets
        )(sealed)
        is None
    )
