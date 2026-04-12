from app.services import risk_evidence, summary_agents


def test_long_form_health_local_contract_accepts_expanded_sentence_band() -> None:
    text = (
        "76/100 - Healthy. "
        "Operating margin of 27.8% still supports profitability quality and gives the earnings base room to absorb normal volatility. "
        "Free cash flow of $22.60B shows that cash conversion is carrying a large share of the investment burden rather than leaving the company dependent on external funding. "
        "That bridge from operating income into free cash flow matters because it indicates earnings quality is backed by deployable capital. "
        "Cash and securities keep liquidity flexible during a heavier capex cycle. "
        "Debt remains manageable relative to the cash cushion, so the balance sheet still provides optionality rather than pressure. "
        "The score does not sit higher because reinvestment intensity still has to prove it can stay efficient as infrastructure demand rises. "
        "A weaker cash-conversion cycle would narrow the margin for error quickly even if headline margins remain respectable. "
        "This health snapshot sets the balance-sheet backdrop for the operating analysis that follows."
    )

    failures = summary_agents._validate_health_local_contract(
        text,
        budget=509,
        health_score_data={"overall_score": 76},
    )

    assert failures == []


def test_long_form_risk_local_contract_accepts_four_sentence_risks() -> None:
    text = (
        "**Cloud Capacity Bottlenecks:** If cloud capacity, backlog conversion, and utilization ramp fall out of sync because data-center deployments land later than committed customer demand, contracted workloads take longer to convert into recognized revenue and the company ends up carrying expensive infrastructure before usage catches up. "
        "That timing mismatch can pressure cloud revenue recognition, gross margin absorption, operating margin, and free cash flow because servers, networking, support staffing, and power commitments are already in place before bookings fully translate into billable, high-utilization workloads at the expected pace. "
        "The financial risk gets worse if management has to relieve backlog with discounts, service credits, or unusually fast provisioning promises that lift cost-to-serve just as capital intensity is climbing across the cloud platform and investors are underwriting better returns on incremental capacity. "
        "An early-warning signal is rising backlog, weaker utilization, slower bookings conversion, or repeated commentary that cloud capacity remains the binding constraint on delivery.\n\n"
        "**Search Compute Monetization:** If search monetization fails to keep pace with higher AI serving costs, each additional query can become less profitable even while overall usage, engagement, and product adoption look healthy on the surface. "
        "That mechanism can compress operating margin and reduce free cash flow because the company is spending more on inference, ranking, and model orchestration before it has proven that advertiser pricing, monetized clicks, and usage mix are scaling fast enough to cover the added compute burden. "
        "The downside becomes larger if product changes improve engagement but fail to improve advertiser ROI, because management would then be funding more expensive search experiences without getting the monetization lift needed to protect margins, cash generation, or valuation support. "
        "An early-warning signal is higher cost-per-query, softer monetization in search, or a weaker uplift in monetized clicks and pricing despite heavier compute intensity.\n\n"
        "**Partner Traffic Mix Shift:** If traffic acquisition cost and retention trends move against the company because distribution partners gain bargaining power or usage shifts toward more expensive channels, the ads engine loses part of the funding cushion that currently supports reinvestment. "
        "That can weaken revenue mix, operating margin, and balance-sheet flexibility at the same time the company is trying to scale new workloads, because higher partner payments and lower retention would redirect cash away from internally funded cloud and product investment. "
        "The impact is more severe if management has to protect volume by accepting lower unit economics in core ad surfaces, since that would combine traffic-acquisition-cost pressure with weaker monetization and force tougher capital-allocation tradeoffs across the broader platform. "
        "An early-warning signal is faster traffic-acquisition-cost growth, lower partner retention, weaker ROI in key channels, or a sustained rise in partner concessions."
    )

    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="Cloud Capacity Bottlenecks",
                mechanism="If cloud capacity, backlog conversion, and utilization ramp fall out of sync because data-center deployments land later than committed customer demand, contracted workloads take longer to convert into recognized revenue and the company ends up carrying expensive infrastructure before usage catches up.",
                early_warning="An early-warning signal is rising backlog, weaker utilization, slower bookings conversion, or repeated commentary that cloud capacity remains the binding constraint on delivery.",
                evidence_from_filing="Risk Factors: cloud capacity and backlog conversion remain a key issue.",
                source_section="Risk Factors",
                source_quote="cloud capacity and backlog conversion remain a key issue",
            ),
            summary_agents.CompanyRisk(
                risk_name="Search Compute Monetization",
                mechanism="If search monetization fails to keep pace with higher AI serving costs, each additional query can become less profitable even while overall usage, engagement, and product adoption look healthy on the surface.",
                early_warning="An early-warning signal is higher cost-per-query, softer monetization in search, or a weaker uplift in monetized clicks and pricing despite heavier compute intensity.",
                evidence_from_filing="Risk Factors: search monetization and AI serving costs remain a key issue.",
                source_section="Risk Factors",
                source_quote="search monetization and AI serving costs remain a key issue",
            ),
            summary_agents.CompanyRisk(
                risk_name="Partner Traffic Mix Shift",
                mechanism="If traffic acquisition cost and retention trends move against the company because distribution partners gain bargaining power or usage shifts toward more expensive channels, the ads engine loses part of the funding cushion that currently supports reinvestment.",
                early_warning="An early-warning signal is faster traffic-acquisition-cost growth, lower partner retention, weaker ROI in key channels, or a sustained rise in partner concessions.",
                evidence_from_filing="Risk Factors: partner traffic mix remains a key issue.",
                source_section="Risk Factors",
                source_quote="partner traffic mix remains a key issue",
            ),
        ],
        evidence_map={
            "Risk Factors": [
                "Risk Factors: cloud capacity and backlog conversion remain a key issue.",
                "Risk Factors: search monetization and AI serving costs remain a key issue.",
                "Risk Factors: partner traffic mix remains a key issue.",
            ]
        },
        company_terms=["cloud capacity", "search monetization", "partner traffic"],
    )

    failures = summary_agents._validate_risk_local_contract(
        text,
        budget=460,
        analysis=analysis,
    )

    assert failures == []


def test_risk_local_contract_allows_fewer_risks_when_only_fewer_are_accepted() -> None:
    text = (
        "**Cloud Capacity Bottlenecks:** If cloud capacity and backlog conversion fall out of sync, contracted workloads take longer to convert into recognized revenue and the company carries expensive infrastructure before utilization catches up. "
        "That timing mismatch can pressure cloud revenue, margin absorption, and free cash flow because servers, networking, and power commitments are already in place before usage fully ramps. "
        "The downside grows if delivery commitments have to be met with discounts or service credits, because that would erode the economics management is trying to protect during the buildout. "
        "An early-warning signal is rising backlog, weaker utilization, or repeated commentary that capacity remains the binding constraint.\n\n"
        "**Search Compute Monetization:** If search monetization fails to keep pace with higher AI serving costs, each additional query can become less profitable even while engagement appears healthy. "
        "That can compress operating margin and reduce free cash flow because the company is spending more on inference before it has proven advertiser pricing and monetized usage are scaling fast enough to cover the added compute burden. "
        "The risk grows if engagement improves without a matching uplift in advertiser ROI, because the company would be funding a more expensive product without earning the monetization needed to support returns. "
        "An early-warning signal is softer monetization in search or a weaker uplift in monetized clicks despite heavier compute intensity."
    )

    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="Cloud Capacity Bottlenecks",
                mechanism="If cloud capacity and backlog conversion fall out of sync, contracted workloads take longer to convert into recognized revenue.",
                early_warning="An early-warning signal is rising backlog or weaker utilization.",
                evidence_from_filing="Risk Factors: cloud capacity and backlog conversion remain a key issue.",
                source_section="Risk Factors",
                source_quote="cloud capacity and backlog conversion remain a key issue",
            ),
            summary_agents.CompanyRisk(
                risk_name="Search Compute Monetization",
                mechanism="If search monetization fails to keep pace with higher AI serving costs, each additional query can become less profitable.",
                early_warning="An early-warning signal is softer monetization in search.",
                evidence_from_filing="Risk Factors: search monetization and AI serving costs remain a key issue.",
                source_section="Risk Factors",
                source_quote="search monetization and AI serving costs remain a key issue",
            ),
        ],
        evidence_map={
            "Risk Factors": [
                "Risk Factors: cloud capacity and backlog conversion remain a key issue.",
                "Risk Factors: search monetization and AI serving costs remain a key issue.",
            ]
        },
        company_terms=["cloud capacity", "search monetization"],
    )

    failures = summary_agents._validate_risk_local_contract(
        text,
        budget=460,
        analysis=analysis,
    )

    assert failures == []


def test_risk_local_contract_rejects_filing_fragment_name_when_no_accepted_risks() -> None:
    text = (
        "**Actual Execution:** If delivery timing slips, revenue conversion can slow before costs reset. "
        "That can pressure operating margin and free cash flow because the company is still carrying the investment base while monetization lags. "
        "An early-warning signal is weaker backlog conversion or renewed commentary about deployment delays.\n\n"
        "**Export Controls / Shipment Risk:** If export controls tighten, shipment timing can slip and backlog conversion can move right before cost plans reset. "
        "That can pressure revenue timing and margin absorption because inventory and support costs are already committed ahead of delivery. "
        "An early-warning signal is delayed export-license approvals or weaker shipment commentary.\n\n"
        "**Data-Center Capacity Ramp Risk:** If capacity ramps land later than expected, enterprise workload deployment can slip before demand converts into monetized usage. "
        "That can weaken revenue timing and free cash flow because infrastructure spend arrives before utilization catches up. "
        "An early-warning signal is slower utilization ramp or larger implementation backlogs."
    )

    failures = summary_agents._validate_risk_local_contract(
        text,
        budget=460,
        analysis=None,
    )

    assert any("filing structure fragment" in failure for failure in failures)


def test_clean_risk_excerpt_for_prompt_strips_filing_structure_debris() -> None:
    cleaned = summary_agents._clean_risk_excerpt_for_prompt(
        "ITEM 1A\nACTUAL EXECUTION\nConversion Risk\nBacklog conversion could slow if customer readiness slips."
    )

    lowered = cleaned.lower()
    assert "item 1a" not in lowered
    assert "actual execution" not in lowered
    assert "conversion risk" not in lowered
    assert "backlog conversion" in lowered


def test_risk_local_contract_rejects_semantic_overlap_before_final_validation() -> None:
    text = (
        "**Antitrust Enforcement Risk:** If DOJ or FTC remedies delay bundle approvals, "
        "enterprise rollout can slip and revenue conversion weakens before cost plans "
        "adjust. That can pressure operating margin and cash flow because product and "
        "sales investment land before the rollout catches up. An early-warning signal "
        "is slower agency review milestones or more explicit remedy commentary.\n\n"
        "**Antitrust Remedy Delay Risk:** If DOJ or FTC remedies delay bundle approvals, "
        "enterprise rollout can slip and revenue conversion weakens before cost plans "
        "adjust. That can pressure operating margin and cash flow because product and "
        "sales investment land before the rollout catches up. An early-warning signal "
        "is slower agency review milestones or more explicit remedy commentary.\n\n"
        "**Export Controls / Shipment Risk:** If export controls tighten, shipments to "
        "certain markets can move right and backlog conversion slows before capacity "
        "plans can reset. That can pressure revenue timing and margin absorption because "
        "inventory and support costs are already committed. An early-warning signal is "
        "longer customs holds or delayed export-license approvals."
    )

    failures = summary_agents._validate_risk_local_contract(
        text,
        budget=460,
        analysis=None,
    )

    assert any("reuses the same mechanism" in failure for failure in failures)


def test_long_form_closing_local_contract_accepts_must_hold_and_breaks_thesis_structure() -> None:
    text = (
        "HOLD is the right stance because the cash engine still funds reinvestment without obvious balance-sheet strain. "
        "The central question is whether current profitability can keep absorbing heavier infrastructure spend without eroding free cash flow. "
        "What must stay true is that operating margin stays above 25% and free cash flow stays above $20B over the next 2-4 quarters. "
        "That condition matters because internally funded growth preserves capital-allocation flexibility and valuation support at the same time. "
        "What breaks the thesis is a stretch in which margins compress below 20% while cash conversion weakens over the next 2-4 quarters. "
        "If that happens, the company would be funding growth from a weaker earnings base and the multiple would deserve to narrow. "
        "Until one of those paths is confirmed, capital allocation should stay disciplined and cash generation should remain the main underwriting anchor."
    )

    failures = summary_agents._validate_closing_local_contract(text, budget=370)

    assert failures == []


def test_build_section_prompt_includes_company_specific_kpis_period_insights_and_quotes() -> None:
    intelligence = summary_agents.CompanyIntelligenceProfile(
        business_identity="Enterprise SaaS platform that sells workflow software through seat expansion and multi-product bundles.",
        competitive_moat="High switching costs from embedded workflows and admin standardization.",
        primary_kpis=[],
        key_competitors=["ServiceNow", "Atlassian"],
        competitive_dynamics="Expansion depends on enterprise standardization and upsell depth.",
        investor_focus_areas=[],
        industry_kpi_norms="",
        raw_brief="",
    )
    analysis = summary_agents.FilingAnalysis(
        central_tension="Can seat expansion stay durable while AI upsell investment accelerates?",
        tension_evidence="",
        kpi_findings=[
            summary_agents.KPIFinding(
                kpi_name="Net Revenue Retention",
                current_value="114%",
                prior_value="112%",
                change="+200 bps YoY",
                insight="Expansion remains healthy even as management pushes a higher-priced AI bundle.",
            ),
            summary_agents.KPIFinding(
                kpi_name="Remaining Performance Obligations",
                current_value="$4.2B",
                prior_value="$3.7B",
                change="+13% YoY",
                insight="Forward demand visibility improved as larger enterprise deals renewed earlier.",
            ),
        ],
        period_specific_insights=[
            "Enterprise customers standardized on the new AI workflow tier faster than the prior quarter.",
            "Management said the sales motion is shifting toward larger multi-product renewals.",
            "The next phase depends on converting AI attach into durable renewal expansion.",
        ],
        management_quotes=[
            summary_agents.ManagementQuote(
                quote="we are seeing stronger expansion inside our largest customers",
                attribution="Management",
                topic="enterprise upsell",
                suggested_section="Executive Summary",
            )
        ],
        management_strategy_summary="Management is prioritizing AI attach inside the installed base instead of chasing low-quality new logos.",
        company_specific_risks=[],
        evidence_map={
            "Executive Summary": [
                "AI upsell is landing first inside the largest enterprise accounts."
            ]
        },
        company_terms=[
            "AI workflow tier",
            "largest enterprise accounts",
            "multi-product renewals",
        ],
        management_expectations=[
            summary_agents.ManagementExpectation(
                topic="renewal expansion",
                expectation="Management expects AI attach to expand inside large renewals before it broadens to new logos.",
                timeframe="next 1-2 quarters",
                evidence="the sales motion is shifting toward larger multi-product renewals",
            )
        ],
        promise_scorecard_items=[
            summary_agents.PromiseScorecardItem(
                commitment="land AI inside the installed base without sacrificing expansion",
                status="on_track",
                assessment="Net Revenue Retention improved while enterprise customers standardized on the AI tier faster than the prior quarter.",
                evidence="Enterprise customers standardized on the new AI workflow tier faster than the prior quarter.",
            )
        ],
    )

    prompt = summary_agents._build_section_prompt(
        section_name="Executive Summary",
        company_intelligence=intelligence,
        filing_analysis=analysis,
        company_name="Example SaaS Co.",
        target_length=650,
        budget=120,
        prior_section_text="",
        used_claims=[],
        section_memory={
            "used_claims": [
                "Earlier sections already framed AI attach as the commercial hinge."
            ],
            "used_theme_keys": ["AI attach"],
            "used_anchor_metrics": ["Net Revenue Retention"],
            "used_company_terms": ["largest enterprise accounts"],
            "used_management_topics": ["enterprise upsell"],
            "used_promise_items": [
                "land AI inside the installed base without sacrificing expansion"
            ],
        },
        narrative_blueprint=summary_agents._build_narrative_blueprint(
            company_name="Example SaaS Co.",
            company_intelligence=intelligence,
            filing_analysis=analysis,
        ),
        financial_snapshot="- Revenue: $1.8B",
        metrics_lines="→ Revenue: $1.8B",
        health_score_data=None,
        depth_plan=summary_agents.compute_depth_plan(summary_agents.compute_scale_factor(650)),
    )

    assert "MEMO THREAD" in prompt
    assert "SECTION JOB" in prompt
    assert "SECTION QUESTION TO ANSWER" in prompt
    assert "PRIMARY EVIDENCE THIS SECTION OWNS" in prompt
    assert "SECONDARY CALLBACK EVIDENCE ONLY" in prompt
    assert "BANNED OVERLAP FOR THIS SECTION" in prompt
    assert "SUBTLE HANDOFF INSTRUCTION" in prompt
    assert "EARLIER SECTION MEMORY" in prompt
    assert "KPI FINDINGS TO PRIORITIZE" in prompt
    assert "Net Revenue Retention" in prompt
    assert "FILING-PERIOD INSIGHTS TO USE" in prompt
    assert "AI workflow tier" in prompt
    assert "COMPANY TERMS TO REUSE" in prompt
    assert "largest enterprise accounts" in prompt
    assert "MANAGEMENT EXPECTATIONS TO USE" in prompt
    assert "next 1-2 quarters" in prompt
    assert "PROMISE SCORECARD ITEMS TO USE" in prompt
    assert "on_track" in prompt
    assert 'AVAILABLE QUOTES:\n- "we are seeing stronger expansion inside our largest customers"' in prompt
    assert "Budget-aware quote range for this memo: 2-3 total direct quote(s)." in prompt
    assert "The same anchor or theme can appear in at most two narrative sections" in prompt
    assert "Favor subtle handoffs over explicit 'the next section' phrasing." in prompt


def test_risk_evidence_helpers_reject_generic_metric_only_numeric_led_and_boilerplate() -> None:
    generic = risk_evidence.RiskEvidenceCandidate(
        risk_name="Margin / Reinvestment Risk",
        source_section="Risk Factors",
        source_quote="Operating margin of 24.5% leaves less cushion if growth slows.",
        source_anchor_terms=("margin",),
        mechanism_seed="Operating margin of 24.5% leaves less cushion.",
        early_warning_seed="Watch operating margin.",
    )
    ok, reason = risk_evidence.candidate_is_strictly_acceptable(
        generic,
        company_terms=["margin"],
    )
    assert not ok
    assert "generic" in reason or "metric" in reason

    numeric_led = risk_evidence.RiskEvidenceCandidate(
        risk_name="Backlog Shipment Conversion Risk",
        source_section="Risk Factors",
        source_quote="24.5% operating margin leaves less room for execution error.",
        source_anchor_terms=("backlog",),
        mechanism_seed="24.5% operating margin leaves less room for execution error.",
        early_warning_seed="Watch backlog timing.",
    )
    ok, reason = risk_evidence.candidate_is_strictly_acceptable(
        numeric_led,
        company_terms=["backlog"],
    )
    assert not ok
    assert "numeric" in reason or "metric" in reason

    boilerplate = risk_evidence.RiskEvidenceCandidate(
        risk_name="Backlog Shipment Conversion Risk",
        source_section="Risk Factors",
        source_quote="The transmission path runs through weaker unit economics.",
        source_anchor_terms=("backlog",),
        mechanism_seed="The transmission path runs through weaker unit economics.",
        early_warning_seed="Watch backlog timing.",
    )
    ok, reason = risk_evidence.candidate_is_strictly_acceptable(
        boilerplate,
        company_terms=["backlog"],
    )
    assert not ok
    assert "boilerplate" in reason or "anchor" in reason


def test_build_risk_evidence_candidates_extracts_source_backed_risks() -> None:
    candidates = risk_evidence.build_risk_evidence_candidates(
        {
            "Risk Factors": (
                "Backlog conversion may slow if customer fab readiness slips. "
                "Installed-base service mix can weaken if upgrade timing moves out."
            ),
            "Management Discussion & Analysis": (
                "Management said customer fab readiness and installed-base service mix remain key watchpoints."
            ),
            "Filing Language Snippets": '"customer fab readiness slips" "upgrade timing moves out"',
        },
        company_terms=["backlog", "installed base", "customer fab readiness"],
        limit=3,
    )

    assert candidates
    assert all(
        risk_evidence.candidate_is_strictly_acceptable(
            candidate,
            company_terms=["backlog", "installed base", "customer fab readiness"],
        )[0]
        for candidate in candidates
    )
    assert any("backlog" in candidate.risk_name.lower() for candidate in candidates)


def test_build_risk_evidence_candidates_filters_generic_risks_and_ranks_distinct_source_backed_candidates() -> None:
    candidates = risk_evidence.build_risk_evidence_candidates(
        {
            "Risk Factors": (
                "Backlog conversion may slip if customer fab readiness moves right. "
                "Export controls could delay shipments to certain markets. "
                "Margin risk could weigh on results."
            ),
            "Management Discussion & Analysis": (
                "Management said backlog conversion and export-control timing remain key watchpoints."
            ),
        },
        company_terms=["backlog", "customer fab readiness", "export controls", "shipments"],
        limit=3,
    )

    assert len(candidates) == 2
    assert all(
        risk_evidence.candidate_is_strictly_acceptable(
            candidate,
            company_terms=["backlog", "customer fab readiness", "export controls", "shipments"],
        )[0]
        for candidate in candidates
    )
    assert all("margin risk" not in candidate.risk_name.lower() for candidate in candidates)
    assert "backlog" in candidates[0].risk_name.lower()
    assert "export" in candidates[1].risk_name.lower() or "shipment" in candidates[1].risk_name.lower()
    assert len(candidates[0].source_anchor_terms) >= len(candidates[1].source_anchor_terms)


def test_assess_risk_overlap_allows_distinct_regulatory_anchors() -> None:
    overlap = risk_evidence.assess_risk_overlap(
        risk_name="Antitrust Enforcement Risk",
        risk_body=(
            "If DOJ or FTC remedies delay bundle approvals, enterprise rollout can slip "
            "and revenue conversion weakens. An early-warning signal is slower agency "
            "review milestones or more explicit remedy commentary."
        ),
        other_risk_name="Export Controls / Shipment Risk",
        other_risk_body=(
            "If export controls tighten, shipments to certain markets can move right and "
            "product revenue can slip. An early-warning signal is longer customs holds "
            "or delayed export-license approvals."
        ),
    )

    assert not overlap.exact_name_match
    assert not overlap.names_overlap
    assert not overlap.bodies_overlap


def test_assess_risk_overlap_flags_same_body_restatement_even_with_new_name() -> None:
    duplicate_body = (
        "If data-center deployments slip, backlog conversion slows and gross margin "
        "compresses before utilization catches up. An early-warning signal is weaker "
        "rack deployment, lower utilization, or repeated delivery-timing commentary."
    )

    overlap = risk_evidence.assess_risk_overlap(
        risk_name="Data-Center Capacity Ramp Risk",
        risk_body=duplicate_body,
        other_risk_name="GPU Deployment Bottleneck Risk",
        other_risk_body=duplicate_body,
    )

    assert not overlap.exact_name_match
    assert not overlap.names_overlap
    assert overlap.bodies_overlap
    assert overlap.body_jaccard >= 0.75


def test_accepted_company_risks_filters_semantic_duplicates_before_section_generation() -> None:
    shared_antitrust_body = (
        "If DOJ or FTC remedies delay bundle approvals, enterprise rollout can slip and "
        "revenue conversion weakens. An early-warning signal is slower agency review "
        "milestones or more explicit remedy commentary."
    )
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="Antitrust Remedy Delay Risk",
                mechanism=shared_antitrust_body,
                early_warning=(
                    "An early-warning signal is slower agency review milestones or more "
                    "explicit remedy commentary."
                ),
                evidence_from_filing=(
                    "Management Discussion & Analysis: DOJ remedy talks could delay "
                    "bundle approvals and enterprise rollout."
                ),
                source_section="Management Discussion & Analysis",
                source_quote=(
                    "DOJ remedy talks could delay bundle approvals and enterprise rollout."
                ),
            ),
            summary_agents.CompanyRisk(
                risk_name="Antitrust Enforcement Risk",
                mechanism=shared_antitrust_body,
                early_warning=(
                    "An early-warning signal is slower agency review milestones or more "
                    "explicit remedy commentary."
                ),
                evidence_from_filing=(
                    "Risk Factors: DOJ or FTC remedies could delay bundle approvals and "
                    "enterprise rollout."
                ),
                source_section="Risk Factors",
                source_quote=(
                    "DOJ or FTC remedies could delay bundle approvals and enterprise rollout."
                ),
            ),
            summary_agents.CompanyRisk(
                risk_name="Export Controls / Shipment Risk",
                mechanism=(
                    "If export controls tighten, shipments to certain markets can move "
                    "right and backlog conversion slows."
                ),
                early_warning=(
                    "An early-warning signal is longer customs holds or delayed "
                    "export-license approvals."
                ),
                evidence_from_filing=(
                    "Risk Factors: export controls could delay shipments to certain markets."
                ),
                source_section="Risk Factors",
                source_quote=(
                    "export controls could delay shipments to certain markets."
                ),
            ),
            summary_agents.CompanyRisk(
                risk_name="Enterprise Renewal Slippage Risk",
                mechanism=(
                    "If enterprise renewals slip, backlog conversion and revenue timing "
                    "can weaken before cost plans adjust."
                ),
                early_warning=(
                    "An early-warning signal is lower renewal conversion or slower seat "
                    "activation inside large accounts."
                ),
                evidence_from_filing=(
                    "Risk Factors: enterprise renewals may convert more slowly than "
                    "management expects."
                ),
                source_section="Risk Factors",
                source_quote=(
                    "enterprise renewals may convert more slowly than management expects."
                ),
            ),
        ],
        evidence_map={},
        company_terms=[
            "DOJ remedies",
            "bundle approvals",
            "enterprise rollout",
            "export controls",
            "shipments",
            "enterprise renewals",
        ],
    )

    accepted = summary_agents._accepted_company_risks(analysis)
    accepted_names = {risk.risk_name for risk in accepted}

    assert accepted_names == {
        "Antitrust Enforcement Risk",
        "Export Controls / Shipment Risk",
        "Enterprise Renewal Slippage Risk",
    }
    assert "Antitrust Remedy Delay Risk" not in accepted_names


def test_quotes_for_risk_factors_use_only_accepted_risk_quotes() -> None:
    accepted_risk = summary_agents.CompanyRisk(
        risk_name="Backlog Shipment Conversion Risk",
        mechanism="Backlog can slip if customer fab readiness moves right.",
        early_warning="Watch backlog conversion and shipment timing.",
        evidence_from_filing="Risk Factors: Backlog conversion may slow if customer fab readiness slips.",
        source_section="Risk Factors",
        source_quote="Backlog conversion may slow if customer fab readiness slips.",
    )
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[
            summary_agents.ManagementQuote(
                quote="Management expects backlog conversion to improve next quarter.",
                attribution="Management",
                topic="backlog",
                suggested_section="Management Discussion & Analysis",
            )
        ],
        management_strategy_summary="",
        company_specific_risks=[accepted_risk],
        evidence_map={"Risk Factors": [summary_agents._risk_source_evidence_line(accepted_risk)]},
        company_terms=["backlog", "customer fab readiness"],
    )

    quotes = summary_agents._quotes_for_section_with_fallback(analysis, "Risk Factors")

    assert [quote.quote for quote in quotes] == [
        "Backlog conversion may slow if customer fab readiness slips."
    ]
    assert all(quote.suggested_section == "Risk Factors" for quote in quotes)
    assert all("Management Discussion & Analysis" not in quote.attribution for quote in quotes)


def test_accepted_company_risks_prioritize_management_echoed_material_risks() -> None:
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[
            summary_agents.ManagementQuote(
                quote="Power availability remains the gating factor on capacity coming online next quarter.",
                attribution="Management",
                topic="power availability",
                suggested_section="Management Discussion & Analysis",
            )
        ],
        management_strategy_summary=(
            "Management is sequencing infrastructure so capacity comes online against real demand rather than speculative supply."
        ),
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="Anti-Corruption Policy Violation Risk",
                mechanism=(
                    "If distributors or sales agents violate anti-corruption requirements, public-sector bids and channel expansion can slow while remediation costs rise."
                ),
                early_warning=(
                    "An early-warning signal is third-party review findings or enforcement inquiries."
                ),
                evidence_from_filing=(
                    "Risk Factors: employees, vendors, or agents may violate anti-corruption policies."
                ),
                source_section="Risk Factors",
                source_quote="employees, vendors, or agents may violate anti-corruption policies.",
            ),
            summary_agents.CompanyRisk(
                risk_name="Power Availability Capacity Ramp Risk",
                mechanism=(
                    "If power availability or data-center construction slips, capacity comes online later and backlog conversion stretches before utilization catches up."
                ),
                early_warning=(
                    "An early-warning signal is slower power-availability milestones or backlog conversion."
                ),
                evidence_from_filing=(
                    "Risk Factors: delays in power availability and data-center construction could defer capacity coming online and slow backlog conversion."
                ),
                source_section="Risk Factors",
                source_quote=(
                    "delays in power availability and data-center construction could defer capacity coming online and slow backlog conversion."
                ),
            ),
            summary_agents.CompanyRisk(
                risk_name="Enterprise Renewal Slippage Risk",
                mechanism=(
                    "If enterprise renewals convert more slowly, revenue visibility and free-cash-flow conversion weaken before the sales plan adjusts."
                ),
                early_warning=(
                    "An early-warning signal is slower renewal conversion or weaker seat expansion."
                ),
                evidence_from_filing=(
                    "Risk Factors: enterprise renewals may convert more slowly than management expects."
                ),
                source_section="Risk Factors",
                source_quote="enterprise renewals may convert more slowly than management expects.",
            ),
        ],
        evidence_map={},
        company_terms=[
            "power availability",
            "data-center construction",
            "backlog conversion",
            "enterprise renewals",
            "anti-corruption policies",
        ],
        management_expectations=[
            summary_agents.ManagementExpectation(
                topic="enterprise renewals",
                expectation="Management expects enterprise renewals to remain the main proof point over the next two quarters.",
                timeframe="next two quarters",
                evidence="Management expects enterprise renewals to remain the main proof point over the next two quarters.",
            )
        ],
        promise_scorecard_items=[
            summary_agents.PromiseScorecardItem(
                commitment="power availability",
                status="new_commitment",
                assessment="Management is prioritizing power availability and data-center sequencing before additional customer ramps.",
                evidence="Management is prioritizing power availability and data-center sequencing before additional customer ramps.",
            )
        ],
    )

    accepted = summary_agents._accepted_company_risks(analysis)
    accepted_names = [risk.risk_name for risk in accepted]

    assert accepted_names[:2] == [
        "Power Availability Capacity Ramp Risk",
        "Enterprise Renewal Slippage Risk",
    ]
    assert accepted_names[-1] == "Anti-Corruption Policy Violation Risk"


def test_build_section_prompt_risk_factors_uses_source_backed_quotes() -> None:
    accepted_risk = summary_agents.CompanyRisk(
        risk_name="Backlog Shipment Conversion Risk",
        mechanism="Backlog can slip if customer fab readiness moves right.",
        early_warning="Watch backlog conversion and shipment timing.",
        evidence_from_filing="Risk Factors: Backlog conversion may slow if customer fab readiness slips.",
        source_section="Risk Factors",
        source_quote="Backlog conversion may slow if customer fab readiness slips.",
    )
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[
            summary_agents.ManagementQuote(
                quote="Management expects backlog conversion to improve next quarter.",
                attribution="Management",
                topic="backlog",
                suggested_section="Management Discussion & Analysis",
            )
        ],
        management_strategy_summary="",
        company_specific_risks=[accepted_risk],
        evidence_map={"Risk Factors": [summary_agents._risk_source_evidence_line(accepted_risk)]},
        company_terms=["backlog", "customer fab readiness"],
    )
    intelligence = summary_agents.CompanyIntelligenceProfile(
        business_identity="Semicap hardware company with installed-base service exposure.",
        competitive_moat="",
        primary_kpis=[],
        key_competitors=[],
        competitive_dynamics="",
        investor_focus_areas=[],
        industry_kpi_norms="",
        raw_brief="",
        business_archetype="semicap_hardware",
    )

    prompt = summary_agents._build_section_prompt(
        section_name="Risk Factors",
        company_intelligence=intelligence,
        filing_analysis=analysis,
        company_name="Example Semiconductor Co.",
        target_length=650,
        budget=460,
        prior_section_text="",
        used_claims=[],
        section_memory={},
        narrative_blueprint=summary_agents._build_narrative_blueprint(
            company_name="Example Semiconductor Co.",
            company_intelligence=intelligence,
            filing_analysis=analysis,
        ),
        financial_snapshot="",
        metrics_lines="",
        health_score_data=None,
        depth_plan=summary_agents.compute_depth_plan(summary_agents.compute_scale_factor(650)),
    )

    assert "Accepted source-backed risks:" in prompt
    assert "Backlog conversion may slow if customer fab readiness slips." in prompt
    assert "Management expects backlog conversion to improve next quarter." not in prompt
    assert "do not synthesize new ones" in prompt.lower()
    assert "Write up to 3 risks" in prompt
    assert "1 accepted source-backed risk(s) are currently available" in prompt


def test_build_source_backed_risk_section_body_refuses_invention_when_insufficient() -> None:
    accepted_risk = summary_agents.CompanyRisk(
        risk_name="Backlog Shipment Conversion Risk",
        mechanism="Backlog can slip if customer fab readiness moves right.",
        early_warning="Watch backlog conversion and shipment timing.",
        evidence_from_filing="Risk Factors: Backlog conversion may slow if customer fab readiness slips.",
        source_section="Risk Factors",
        source_quote="Backlog conversion may slow if customer fab readiness slips.",
    )
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[accepted_risk],
        evidence_map={"Risk Factors": [summary_agents._risk_source_evidence_line(accepted_risk)]},
        company_terms=["backlog", "customer fab readiness"],
    )

    body = summary_agents._build_source_backed_risk_section_body(analysis, limit=3)

    assert "Backlog conversion may slow if customer fab readiness slips." in body
    assert "Backlog Shipment Conversion Risk" in body
    assert "Operating Model" not in body


def test_infer_business_archetype_across_sector_matrix() -> None:
    fixtures = [
        {
            "sector": "Technology",
            "industry": "Semiconductor Equipment",
            "business_identity": "ASML sells EUV and DUV lithography systems and an installed-base upgrade stream.",
            "context_text": "Backlog, shipments, lithography scanners, and node transitions still drive the quarter.",
            "expected": "semicap_hardware",
        },
        {
            "sector": "Financials",
            "industry": "Regional Banks",
            "business_identity": "The bank earns spread income from deposits and loans while managing CET1 and charge-offs.",
            "context_text": "Deposit mix, net interest margin, and credit quality remain the core issues.",
            "expected": "bank",
        },
        {
            "sector": "Healthcare",
            "industry": "Biotechnology",
            "business_identity": "The company depends on launch uptake, pipeline milestones, reimbursement, and regulatory approvals.",
            "context_text": "Pipeline timing and launch uptake matter more than the current quarter's reported revenue.",
            "expected": "pharma_biotech_medtech",
        },
        {
            "sector": "Consumer Discretionary",
            "industry": "Retail",
            "business_identity": "The retailer depends on same-store sales, traffic, inventory turns, and promotional discipline.",
            "context_text": "Traffic and inventory markdown pressure remain the underwriting lens.",
            "expected": "retail_consumer",
        },
    ]

    for fixture in fixtures:
        inferred = summary_agents._infer_business_archetype(
            sector=fixture["sector"],
            industry=fixture["industry"],
            business_identity=fixture["business_identity"],
            context_text=fixture["context_text"],
        )
        assert inferred == fixture["expected"]


def test_build_fallback_profile_is_archetype_aware_for_bank() -> None:
    class DummyClient:
        def research_company_background(self, **_kwargs):
            return (
                "Regional lender with deposit-funded earnings, loan growth exposure, "
                "credit quality pressure, and CET1 discipline."
            )

    profile = summary_agents._build_fallback_profile(
        "Example Regional Bank",
        "ERBK",
        "Financials",
        "Regional Banks",
        DummyClient(),
    )

    assert profile.business_archetype == "bank"
    kpi_names = [kpi.name for kpi in profile.primary_kpis]
    assert "Net Interest Margin" in kpi_names
    assert "Deposit Growth / Mix" in kpi_names
    assert "Credit Quality" in kpi_names
    assert "deposit mix and funding costs" in " ".join(profile.investor_focus_areas)


def test_parse_filing_analysis_preserves_editorial_anchor_fields() -> None:
    parsed = summary_agents._parse_filing_analysis(
        {
            "central_tension": "Can backlog conversion fund the next capex cycle?",
            "tension_evidence": "Backlog improved while capex is accelerating.",
            "kpi_findings": [],
            "period_specific_insights": [],
            "management_quotes": [],
            "management_strategy_summary": "Management is prioritizing monetization ahead of pure footprint expansion.",
            "company_specific_risks": [],
            "company_terms": ["backlog conversion", "AI attach", "enterprise agreements"],
            "management_expectations": [
                {
                    "topic": "AI attach",
                    "expectation": "Management expects AI attach to deepen in large renewals.",
                    "timeframe": "next two quarters",
                    "evidence": "Management highlighted stronger AI attach inside renewals.",
                }
            ],
            "promise_scorecard_items": [
                {
                    "commitment": "turn backlog into recognized revenue",
                    "status": "on_track",
                    "assessment": "Conversion improved without a drop in renewal quality.",
                    "evidence": "Backlog conversion is improving.",
                }
            ],
            "management_strategic_bets": [],
            "forward_guidance_summary": "",
            "promise_scorecard": "Management appears on track against its commercialization goals.",
            "evidence_map": {"Executive Summary": ["Backlog conversion is the gating question."]},
        }
    )

    assert parsed.company_terms == ["backlog conversion", "AI attach", "enterprise agreements"]
    assert parsed.management_expectations[0].timeframe == "next two quarters"
    assert parsed.promise_scorecard_items[0].status == "on_track"


def test_parse_filing_analysis_rejects_generic_risks_and_keeps_source_backed_ones() -> None:
    parsed = summary_agents._parse_filing_analysis(
        {
            "central_tension": "Can ASML convert EUV backlog into shipments before customer fab timing turns less supportive?",
            "tension_evidence": "",
            "kpi_findings": [],
            "period_specific_insights": [],
            "management_quotes": [],
            "management_strategy_summary": "",
            "company_specific_risks": [
                {
                    "risk_name": "Margin / Reinvestment Risk",
                    "mechanism": "Installed Base service mix can soften if EUV shipments slip and fab-utilization plans move right.",
                    "early_warning": "Watch Installed Base activity, EUV utilization, and service-margin commentary.",
                    "evidence_from_filing": "ASML highlighted installed-base activity and shipment timing around customer node transitions.",
                    "source_section": "Risk Factors",
                    "source_quote": "ASML highlighted installed-base activity and shipment timing around customer node transitions.",
                },
                {
                    "risk_name": "Backlog Shipment Conversion Risk",
                    "mechanism": "Backlog can fail to convert into shipments on schedule if customer fabs delay tool acceptance.",
                    "early_warning": "Track backlog conversion, shipment timing, and customer fab-capex commentary.",
                    "evidence_from_filing": "Backlog conversion may slow if customer fab readiness slips.",
                    "source_section": "Risk Factors",
                    "source_quote": "Backlog conversion may slow if customer fab readiness slips.",
                },
            ],
            "company_terms": [
                "EUV",
                "Installed Base",
                "Backlog",
            ],
            "management_expectations": [],
            "promise_scorecard_items": [],
            "management_strategic_bets": [],
            "forward_guidance_summary": "",
            "promise_scorecard": "",
            "evidence_map": {},
        }
    )

    names = [risk.risk_name for risk in parsed.company_specific_risks]
    assert names == ["Backlog Shipment Conversion Risk"]
    assert parsed.company_specific_risks[0].source_section == "Risk Factors"
    assert parsed.company_specific_risks[0].source_quote == "Backlog conversion may slow if customer fab readiness slips."
    assert parsed.evidence_map["Risk Factors"] == [
        "Risk Factors: Backlog conversion may slow if customer fab readiness slips."
    ]


def test_build_fallback_analysis_uses_source_mined_risks_only() -> None:
    fallback = summary_agents._build_fallback_analysis(
        "Example Semiconductor Co.",
        company_intelligence=summary_agents.CompanyIntelligenceProfile(
            business_identity="Semicap hardware company with installed-base service exposure.",
            competitive_moat="",
            primary_kpis=[],
            key_competitors=[],
            competitive_dynamics="",
            investor_focus_areas=[],
            industry_kpi_norms="",
            raw_brief="",
            business_archetype="semicap_hardware",
        ),
        context_excerpt="Management says backlog conversion may slow if customer fab readiness slips.",
        mda_excerpt="Installed-base service mix can weaken if upgrade timing moves out.",
        risk_factors_excerpt=(
            "Backlog conversion may slow if customer fab readiness slips. "
            "Installed-base service mix can weaken if upgrade timing moves out."
        ),
        filing_language_snippets='"customer fab readiness slips" "upgrade timing moves out"',
    )

    assert fallback.company_specific_risks
    assert all(risk.source_section for risk in fallback.company_specific_risks)
    assert all(risk.source_quote for risk in fallback.company_specific_risks)
    assert all(
        risk.source_quote in risk.evidence_from_filing
        or risk.evidence_from_filing.startswith(risk.source_section)
        for risk in fallback.company_specific_risks
    )
    assert all(
        not risk_evidence.is_generic_risk_name(risk.risk_name)
        for risk in fallback.company_specific_risks
    )
    assert any(
        "backlog" in risk.risk_name.lower() or "installed" in risk.risk_name.lower()
        for risk in fallback.company_specific_risks
    )


def test_extract_fallback_management_quotes_rejects_accounting_disclosure_quotes() -> None:
    quotes = summary_agents._extract_fallback_management_quotes(
        context_text=(
            'Management noted that "we remain focused on backlog conversion and customer demand next quarter."\n'
            'Note 1 states that "Investments with maturities beyond one year may be classified as short-term based on their highly liquid nature."\n'
        ),
        company_terms=["backlog conversion", "customer demand"],
        archetype="semicap_hardware",
    )

    assert [quote.quote for quote in quotes] == [
        "we remain focused on backlog conversion and customer demand next quarter."
    ]


def test_build_key_metrics_body_prepends_company_specific_kpis_before_generic_metrics() -> None:
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[
            summary_agents.KPIFinding(
                kpi_name="Annual Recurring Revenue",
                current_value="$2.3B",
                change="+18% YoY",
            ),
            summary_agents.KPIFinding(
                kpi_name="Net Revenue Retention",
                current_value="114%",
                change="+200 bps YoY",
            ),
        ],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[],
        evidence_map={},
    )

    body = summary_agents._build_key_metrics_body(
        metrics_lines="→ Revenue: $1.8B\n→ Operating Margin: 21.4%",
        analysis=analysis,
    )

    lines = body.splitlines()
    assert lines[0] == "→ Annual Recurring Revenue: $2.3B | +18% YoY"
    assert lines[1] == "→ Net Revenue Retention: 114% | +200 bps YoY"
    assert lines[2] == "→ Revenue: $1.8B"
    assert lines[3] == "→ Operating Margin: 21.4%"


def test_build_key_metrics_body_dedupes_duplicate_labels_and_respects_budget() -> None:
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[
            summary_agents.KPIFinding(
                kpi_name="Free Cash Flow",
                current_value="$650M",
                change="+$40M",
            ),
        ],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[],
        evidence_map={},
    )

    body = summary_agents._build_key_metrics_body(
        metrics_lines=(
            "→ Revenue: $2.4B enterprise recurring backlog conversion m0 m5 m10 m15\n"
            "→ Operating Income: $0.7B margin discipline durability m1 m6 m11 m16\n"
            "→ Operating Margin: 29.0% mix quality absorption m2 m7 m12 m17\n"
            "→ Free Cash Flow: $0.65B self-funded investment capacity m3 m8 m13 m18\n"
            "→ Current Ratio: 2.3x liquidity cushion flexibility m4 m9 m14"
        ),
        analysis=analysis,
        max_words=55,
    )

    assert body.count("→ Free Cash Flow:") == 1
    assert summary_agents.count_words(body) <= 55


def test_regression_business_model_specific_kpis_survive_in_key_metrics_body() -> None:
    fixtures = [
        (
            "SaaS",
            [
                summary_agents.KPIFinding(kpi_name="ARR", current_value="$5.2B", change="+17% YoY"),
                summary_agents.KPIFinding(kpi_name="Net Dollar Retention", current_value="117%", change="+100 bps YoY"),
            ],
        ),
        (
            "Industrial",
            [
                summary_agents.KPIFinding(kpi_name="Backlog", current_value="$9.4B", change="+11% YoY"),
                summary_agents.KPIFinding(kpi_name="Book-to-Bill", current_value="1.2x", change="+0.1x YoY"),
            ],
        ),
        (
            "Financials",
            [
                summary_agents.KPIFinding(kpi_name="Net Interest Margin", current_value="3.4%", change="+20 bps YoY"),
                summary_agents.KPIFinding(kpi_name="Deposit Growth", current_value="8%", change="+8% YoY"),
            ],
        ),
    ]

    for _label, findings in fixtures:
        analysis = summary_agents.FilingAnalysis(
            central_tension="",
            tension_evidence="",
            kpi_findings=findings,
            period_specific_insights=[],
            management_quotes=[],
            management_strategy_summary="",
            company_specific_risks=[],
            evidence_map={},
        )
        body = summary_agents._build_key_metrics_body(
            metrics_lines="→ Revenue: $1.0B",
            analysis=analysis,
        )
        assert findings[0].kpi_name in body
        assert findings[1].kpi_name in body


def test_risk_fallback_only_triggers_on_zero_accepted_risks() -> None:
    """The early fallback path in _generate_section should only fire when
    zero risks survive acceptance — not when 1 or 2 are accepted."""
    # Build an analysis with 2 accepted risks (out of a required 3)
    analysis = summary_agents.FilingAnalysis(
        central_tension="",
        tension_evidence="",
        kpi_findings=[],
        period_specific_insights=[],
        management_quotes=[],
        management_strategy_summary="",
        company_specific_risks=[
            summary_agents.CompanyRisk(
                risk_name="TSMC Allocation Constraint",
                mechanism="If TSMC tightens allocation, product launches slip.",
                early_warning="Longer TSMC lead times.",
                evidence_from_filing="Risk Factors: TSMC allocation remains tight.",
                source_section="Risk Factors",
                source_quote="TSMC allocation remains tight",
            ),
            summary_agents.CompanyRisk(
                risk_name="EU DMA Compliance Pressure",
                mechanism="If EU DMA forces unbundling, distribution costs rise.",
                early_warning="Formal non-compliance proceedings.",
                evidence_from_filing="Risk Factors: EU DMA unbundling risk.",
                source_section="Risk Factors",
                source_quote="EU DMA unbundling risk",
            ),
        ],
        evidence_map={
            "Risk Factors": [
                "Risk Factors: TSMC allocation remains tight.",
                "Risk Factors: EU DMA unbundling risk.",
            ]
        },
        company_terms=["TSMC", "EU DMA"],
    )

    accepted = summary_agents._accepted_company_risks(analysis)
    # With 2 specific risks, at least 1 should be accepted
    assert len(accepted) >= 1

    # The condition should NOT trigger fallback when accepted >= 1
    # (required_risks would be 3 for budget > 109)
    required_risks = 3
    # Old code: len(accepted) < required_risks → would trigger fallback
    # New code: len(accepted) == 0 → does NOT trigger fallback
    assert not (required_risks > 0 and len(accepted) == 0)
