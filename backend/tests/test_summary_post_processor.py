from app.services.summary_post_processor import (
    SectionValidationFailure,
    SummaryValidationReport,
    _is_soft_pass,
    _select_regeneration_target,
    _validate_risk_factors,
    validate_summary,
    post_process_summary,
)
from app.services.summary_budget_controller import calculate_section_word_budgets
from app.services.word_surgery import count_words
from scripts.smoke_summary_continuous_v2 import _metrics_lines_for_budget, _section_body


def _build_memo(sections: dict[str, str]) -> str:
    ordered = [
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ]
    return "\n\n".join(
        f"## {section_name}\n{sections[section_name]}"
        for section_name in ordered
        if section_name in sections
    )


def _base_sections() -> dict[str, str]:
    return {
        "Executive Summary": (
            "Management noted that \"enterprise demand remained resilient,\" which frames the central question for this period. "
            "Management still needs to prove new investment can expand returns rather than dilute margins."
        ),
        "Financial Performance": (
            "Gross margin held up because mix improved and cost controls offset infrastructure pressure. "
            "Free cash flow stayed solid, which preserved flexibility for continued product investment."
        ),
        "Management Discussion & Analysis": (
            "Management is leaning into AI investment where customer adoption is already visible in the filing. "
            "That strategy only works if the company keeps translating spend into durable monetization."
        ),
        "Risk Factors": (
            "**Competition:** Competition could intensify and hurt results. "
            "Margins could fall if competition increases."
        ),
        "Key Metrics": (
            "-> Revenue: $10.0B\n"
            "-> Operating Margin: 25%"
        ),
        "Closing Takeaway": (
            "The investment thesis still holds because the core business funds the expansion cycle. "
            "The next watch item is whether monetization keeps pace with infrastructure spend."
        ),
    }


def _valid_risk_factors_body() -> str:
    return (
        "**Deferred Enterprise Renewals:** The filing warns that large customers can push deployments into later quarters, "
        "which means bookings convert more slowly and revenue visibility weakens. That delay can pressure growth, "
        "gross margin, and free cash flow conversion. An early-warning signal is weaker "
        "enterprise pipeline conversion or lower renewal rates.\n\n"
        "**AI Spend Monetization Lag:** If AI infrastructure spending rises faster than product pricing "
        "or usage ramps, operating leverage can erode before incremental demand scales. That mismatch "
        "can compress operating margin and reduce cash generation available for buybacks or reinvestment. "
        "An early-warning signal is rising capex intensity without a matching uplift in monetized usage."
    )


def _valid_large_budget_risk_factors_body() -> str:
    return (
        "**Cloud Capacity Bottlenecks:** The filing notes that cloud capacity remained the delivery constraint, so backlog can convert more slowly when data-center buildouts slip committed customer demand and leave deferred workloads waiting longer to turn into billed usage. "
        "That mismatch delays revenue recognition and leaves server, networking, and support costs in place before utilization catches up, which can squeeze gross margin and cash generation while newer facilities are still ramping. "
        "The downside gets worse if management has to accelerate provisioning or offer service credits to protect customer relationships while capacity is still ramping. "
        "An early-warning signal is rising backlog age, weaker utilization, or repeated commentary that deployment timing remains constrained.\n\n"
        "**Search Compute Monetization:** The filing notes that AI search usage is scaling faster than monetization, so each additional query can become less profitable if pricing and advertiser ROI do not improve at the same pace across the higher-cost product mix. "
        "That gap can pressure operating margin and free cash flow because inference, ranking, and model costs arrive before monetized clicks and mix gains have fully caught up across the installed base. "
        "The risk becomes larger if engagement improves but advertisers do not pay more for the new experience, because the company would be funding a richer product without earning the offsetting revenue needed to support returns. "
        "An early-warning signal is higher cost per query, softer search monetization, or a weaker uplift in monetized clicks despite heavier compute intensity."
    )


def test_post_process_summary_regenerates_only_the_failing_risk_section() -> None:
    sections = _base_sections()
    sections["Key Metrics"] = _metrics_lines_for_budget(12)
    memo = _build_memo(sections)
    budgets = {section_name: count_words(body) for section_name, body in sections.items()}
    budgets["Risk Factors"] = count_words(_valid_risk_factors_body())
    target_words = count_words(
        _build_memo({**sections, "Risk Factors": _valid_risk_factors_body()})
    )
    regenerated_sections: list[str] = []

    def regenerate_section(**kwargs: str) -> str:
        regenerated_sections.append(kwargs["section_name"])
        return _valid_risk_factors_body()

    result = post_process_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt="renewals pipeline monetization capex pricing enterprise demand",
        regenerate_section_fn=regenerate_section,
    )

    assert result.passed
    assert result.retries == 1
    assert regenerated_sections == ["Risk Factors"]
    assert "Deferred Enterprise Renewals" in result.text


def test_post_process_summary_caps_retries_per_section_at_three() -> None:
    sections = _base_sections()
    memo = _build_memo(sections)
    budgets = {section_name: count_words(body) for section_name, body in sections.items()}
    budgets["Risk Factors"] = count_words(_valid_risk_factors_body())
    target_words = count_words(
        _build_memo({**sections, "Risk Factors": _valid_risk_factors_body()})
    )
    regenerated_sections: list[str] = []

    def regenerate_section(**kwargs: str) -> str:
        regenerated_sections.append(kwargs["section_name"])
        return sections["Risk Factors"]

    result = post_process_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt="renewals pipeline monetization capex pricing enterprise demand",
        regenerate_section_fn=regenerate_section,
    )

    assert not result.passed
    # Hard failures (risk_schema) get +2 extra retries before exhaustion
    assert regenerated_sections.count("Risk Factors") == 5
    assert result.retries >= 5


def test_post_process_summary_uses_last_narrative_section_for_global_under_target() -> None:
    sections = _base_sections()
    sections["Risk Factors"] = _valid_risk_factors_body()
    sections["Key Metrics"] = _metrics_lines_for_budget(12)
    memo = _build_memo(sections)
    budgets = {section_name: count_words(body) for section_name, body in sections.items()}
    budgets["Closing Takeaway"] += 12
    final_sections = dict(sections)
    final_sections["Closing Takeaway"] = (
        sections["Closing Takeaway"]
        + " Investors should watch monetized AI usage over the next two quarters."
    )
    target_words = count_words(_build_memo(final_sections))
    regenerated_sections: list[str] = []

    def regenerate_section(**kwargs: str) -> str:
        regenerated_sections.append(kwargs["section_name"])
        if kwargs["section_name"] != "Closing Takeaway":
            return sections[kwargs["section_name"]]
        return final_sections["Closing Takeaway"]

    result = post_process_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt="renewals pipeline monetization capex pricing enterprise demand",
        regenerate_section_fn=regenerate_section,
    )

    assert result.passed
    assert regenerated_sections == ["Closing Takeaway"]
    assert result.validation_report is not None
    assert result.validation_report.lower_bound <= count_words(result.text) <= result.validation_report.upper_bound


def test_post_process_summary_does_not_pad_under_target_output_without_regeneration() -> None:
    sections = _base_sections()
    sections["Risk Factors"] = _valid_risk_factors_body()
    memo = _build_memo(sections)
    budgets = {section_name: count_words(body) for section_name, body in sections.items()}
    target_words = count_words(memo) + 20

    result = post_process_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt="renewals pipeline monetization capex pricing enterprise demand",
        regenerate_section_fn=None,
    )

    assert result.text == memo
    assert not result.passed
    assert result.retries == 0


def test_select_regeneration_target_prioritizes_risk_schema_over_smaller_budget_miss() -> None:
    validation = SummaryValidationReport(
        passed=False,
        total_words=1000,
        lower_bound=970,
        upper_bound=1030,
        section_failures=[
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="section_budget_under",
                message="Closing Takeaway is underweight.",
                actual_words=90,
                budget_words=180,
                severity=0.5,
            ),
            SectionValidationFailure(
                section_name="Risk Factors",
                code="risk_schema",
                message="Risk Factors violated schema.",
                severity=3.5,
            ),
        ],
    )

    chosen = _select_regeneration_target(
        validation,
        text="",
        section_budgets={"Risk Factors": 220, "Closing Takeaway": 180},
        include_health_rating=False,
    )

    assert chosen is not None
    assert chosen.section_name == "Risk Factors"
    assert chosen.code == "risk_schema"


def test_select_regeneration_target_uses_budget_severity_not_alphabetical_order() -> None:
    validation = SummaryValidationReport(
        passed=False,
        total_words=1000,
        lower_bound=970,
        upper_bound=1030,
        section_failures=[
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="section_budget_under",
                message="Closing Takeaway is underweight.",
                actual_words=150,
                budget_words=300,
                severity=0.5,
            ),
            SectionValidationFailure(
                section_name="Financial Health Rating",
                code="section_budget_under",
                message="Financial Health Rating is underweight.",
                actual_words=60,
                budget_words=500,
                severity=0.88,
            ),
        ],
    )

    chosen = _select_regeneration_target(
        validation,
        text="",
        section_budgets={"Financial Health Rating": 500, "Closing Takeaway": 300},
        include_health_rating=True,
    )

    assert chosen is not None
    assert chosen.section_name == "Financial Health Rating"
    assert chosen.code == "section_budget_under"


def test_is_soft_pass_rejects_missing_management_voice() -> None:
    report = SummaryValidationReport(
        passed=False,
        total_words=620,
        lower_bound=600,
        upper_bound=680,
        section_failures=[
            SectionValidationFailure(
                section_name="Management Discussion & Analysis",
                code="insufficient_management_voice",
                message="MD&A lacks management voice.",
                severity=1.8,
            )
        ],
    )

    assert not _is_soft_pass(
        report,
        section_budgets={"Management Discussion & Analysis": 120},
        target_words=650,
    )


def test_validate_risk_factors_accepts_four_sentence_items_for_large_budgets() -> None:
    memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": _valid_large_budget_risk_factors_body(),
        }
    )

    risk_count, failures = _validate_risk_factors(
        memo,
        risk_budget_words=556,
        risk_factors_excerpt="cloud capacity backlog utilization search monetization traffic acquisition cost retention",
    )

    assert risk_count == 2
    assert failures == []


def test_validate_risk_factors_requires_concrete_early_warning_signal_until_repaired() -> None:
    invalid_memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                "**Deferred Enterprise Implementation Delays:** If enterprise customers defer go-live milestones, revenue conversion slows and free cash flow arrives later than management planned. "
                "That mechanism can compress gross margin through idle service staffing and reduce balance-sheet flexibility while fixed costs stay elevated.\n\n"
                "**Compute Cost Recovery Lag:** If infrastructure expense rises faster than paid usage, operating margin can erode before the company captures enough incremental cash flow. "
                "That mismatch reduces balance-sheet flexibility because serving costs land before realized monetization improves."
            ),
        }
    )

    risk_excerpt = "enterprise renewals usage pricing capex implementation"
    risk_count, failures = _validate_risk_factors(
        invalid_memo,
        risk_budget_words=90,
        risk_factors_excerpt=risk_excerpt,
    )

    assert risk_count == 2
    assert any("concrete early-warning signal" in msg for _code, msg in failures)

    budgets = {section_name: count_words(body) for section_name, body in _base_sections().items()}
    budgets["Risk Factors"] = 90
    report = validate_summary(
        invalid_memo,
        target_words=count_words(invalid_memo),
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt=risk_excerpt,
    )

    assert any(failure.code == "risk_quality" for failure in report.section_failures)
    assert any(
        "concrete early-warning signal" in failure.message
        for failure in report.section_failures
    )

    repaired_memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": _valid_risk_factors_body(),
        }
    )
    repaired_budgets = {section_name: count_words(body) for section_name, body in _base_sections().items()}
    repaired_budgets["Risk Factors"] = count_words(_valid_risk_factors_body())
    repaired_report = validate_summary(
        repaired_memo,
        target_words=count_words(repaired_memo),
        section_budgets=repaired_budgets,
        include_health_rating=False,
        risk_factors_excerpt=risk_excerpt,
    )

    assert not any(failure.code == "risk_schema" for failure in repaired_report.section_failures)


def test_validate_risk_factors_boundary_jaccard_0_50_does_not_trigger_overlap() -> None:
    """Two risk names sharing 2 of 4 union tokens (Jaccard=0.50) must not trigger overlap."""
    memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                "**Backlog Execution Conversion Risk:** If backlog shipment conversion "
                "stalls because fab timing shifts, revenue recognition lags capacity "
                "investment and operating margin compresses before volume ramps. "
                "An early-warning signal is rising backlog age or shipment deferrals.\n\n"
                "**Internet Execution Traffic Risk:** If internet traffic slows because "
                "user acquisition costs rise faster than monetization, the company faces "
                "margin pressure from higher cost-to-serve before revenue catches up. "
                "An early-warning signal is weaker cost-per-acquisition trends."
            ),
        }
    )
    # "backlog execution conversion" vs "internet execution traffic"
    # overlap: {execution}, union: {backlog, execution, conversion, internet, traffic}
    # Jaccard = 1/5 = 0.20 — well below 0.55.
    # Even with 2-token overlap the cardinality gate + 0.55 threshold prevents
    # false positives at the 2/4 = 0.50 boundary.
    risk_count, failures = _validate_risk_factors(
        memo,
        risk_budget_words=90,
        risk_factors_excerpt="backlog internet execution conversion traffic shipment",
    )
    assert risk_count == 2
    overlap_failures = [
        msg for code, msg in failures if code == "risk_schema" and "overlaps" in msg
    ]
    assert overlap_failures == [], f"False positive overlap: {overlap_failures}"


def test_validate_risk_factors_accepts_distinct_regulatory_anchors_and_rejects_overlapping_regulatory_names() -> None:
    passing_memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                "**Antitrust Enforcement Risk:** If DOJ or FTC remedies delay product rollout, launch timing and revenue recognition can slip. "
                "An early-warning signal is slower remedy milestones and more explicit agency commentary.\n\n"
                "**Export Controls / Shipment Risk:** If export controls tighten, shipments to certain markets can move right and backlog conversion can slow. "
                "An early-warning signal is lower shipment rates or repeated customs commentary."
            ),
        }
    )
    passing_count, passing_failures = _validate_risk_factors(
        passing_memo,
        risk_budget_words=172,
        risk_factors_excerpt=(
            "antitrust remedies export controls shipments privacy compliance regulatory scrutiny"
        ),
        company_name="Example Corp",
    )
    assert passing_count == 2
    assert passing_failures == []

    failing_memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                "**Regulatory / Remedy Risk:** If regulatory remedies delay product rollout, launch timing and revenue recognition can slip. "
                "That can pressure revenue timing, margins, and cash flow. "
                "An early-warning signal is slower remedy milestones.\n\n"
                "**Regulatory / Remedy Risk:** If regulatory remedies delay product rollout, launch timing and revenue recognition can slip. "
                "That can pressure revenue timing, margins, and cash flow. "
                "An early-warning signal is slower remedy milestones."
            ),
        }
    )
    failing_count, failing_failures = _validate_risk_factors(
        failing_memo,
        risk_budget_words=172,
        risk_factors_excerpt=(
            "regulatory remedies export controls shipments regulatory scrutiny"
        ),
        company_name="Example Corp",
    )
    overlap_messages = [
        msg
        for code, msg in failing_failures
        if code == "risk_schema" and "overlaps too much" in msg
    ]
    assert failing_count == 2
    assert failing_failures, "Expected duplicate-risk failure"
    assert overlap_messages or any(
        code == "risk_schema" for code, _msg in failing_failures
    ), f"Expected structural duplicate-risk failure, got: {failing_failures}"


def test_validate_risk_factors_rejects_same_body_restatement_even_with_new_name() -> None:
    duplicate_body = (
        "If data-center deployments slip, backlog conversion slows and gross margin "
        "compresses before utilization catches up. An early-warning signal is weaker "
        "rack deployment, lower utilization, or repeated delivery-timing commentary."
    )
    memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                f"**Data-Center Capacity Ramp Risk:** {duplicate_body}\n\n"
                f"**GPU Deployment Bottleneck Risk:** {duplicate_body}"
            ),
        }
    )

    risk_count, failures = _validate_risk_factors(
        memo,
        risk_budget_words=90,
        risk_factors_excerpt=(
            "data center deployments backlog conversion rack deployment utilization"
        ),
    )

    assert risk_count == 2
    assert any(
        code == "risk_quality" and "body is too similar to a previous risk body" in msg
        for code, msg in failures
    )

    budgets = {section_name: count_words(body) for section_name, body in _base_sections().items()}
    budgets["Risk Factors"] = 90
    report = validate_summary(
        memo,
        target_words=count_words(memo),
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt=(
            "data center deployments backlog conversion rack deployment utilization"
        ),
    )

    assert any(
        failure.code == "risk_quality"
        and "body is too similar to a previous risk body" in failure.message
        for failure in report.section_failures
    )


def test_validate_summary_ignores_ngram_only_repetition_when_no_duplicate_sentences_exist() -> None:
    base_sections = _base_sections()
    base_sections["Key Metrics"] = _metrics_lines_for_budget(12)
    memo = _build_memo(
        {
            **base_sections,
            "Financial Performance": (
                "Derivative hedging can create quarter-to-quarter treasury noise when settlements and marks move at different times. "
                "That treasury noise matters because derivatives to manage foreign exchange and other exposures can distort the bridge between reported earnings and cash generation."
            ),
            "Management Discussion & Analysis": (
                "Management said treasury volatility should be read separately from operating performance because derivatives to manage foreign exchange and other exposures can create accounting noise. "
                "The operating question is whether the core business still converts demand into cash after stripping out those treasury swings."
            ),
            "Risk Factors": _valid_risk_factors_body(),
            "Closing Takeaway": (
                "The underwriting case still works because the core business funds the investment cycle and management has room to absorb treasury volatility. "
                "The next watch item is whether monetization keeps pace with infrastructure spend and whether hedging noise stays contained."
            ),
        }
    )
    budgets = {
        "Executive Summary": count_words(base_sections["Executive Summary"]),
        "Financial Performance": count_words(
            "Derivative hedging can create quarter-to-quarter treasury noise when settlements and marks move at different times. "
            "That treasury noise matters because derivatives to manage foreign exchange and other exposures can distort the bridge between reported earnings and cash generation."
        ),
        "Management Discussion & Analysis": count_words(
            "Management said treasury volatility should be read separately from operating performance because derivatives to manage foreign exchange and other exposures can create accounting noise. "
            "The operating question is whether the core business still converts demand into cash after stripping out those treasury swings."
        ),
        "Risk Factors": count_words(_valid_risk_factors_body()),
        "Key Metrics": count_words(base_sections["Key Metrics"]),
        "Closing Takeaway": count_words(
            "The underwriting case still works because the core business funds the investment cycle and management has room to absorb treasury volatility. "
            "The next watch item is whether monetization keeps pace with infrastructure spend and whether hedging noise stays contained."
        ),
    }
    target_words = count_words(memo)

    report = validate_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt="enterprise renewals pipeline conversion monetized usage capex pricing operating leverage",
    )

    assert report.passed
    assert report.repetition_report.repeated_ngrams
    assert not any(failure.code == "repetition" for failure in report.section_failures)


def test_validate_summary_uses_wider_tolerance_for_high_budget_long_form_sections() -> None:
    sections = {
        "Financial Health Rating": " ".join(["health"] * 485),
        "Executive Summary": " ".join(["summary"] * 420),
        "Financial Performance": " ".join(["performance"] * 509),
        "Management Discussion & Analysis": " ".join(["analysis"] * 509),
        "Risk Factors": _valid_risk_factors_body(),
        "Key Metrics": _base_sections()["Key Metrics"],
        "Closing Takeaway": " ".join(["closing"] * 371),
    }
    memo = "\n\n".join(
        f"## {name}\n{body}" for name, body in sections.items()
    )
    budgets = {
        "Financial Health Rating": 510,
        "Executive Summary": 440,
        "Financial Performance": 509,
        "Management Discussion & Analysis": 509,
        "Risk Factors": count_words(_valid_risk_factors_body()),
        "Key Metrics": count_words(_base_sections()["Key Metrics"]),
        "Closing Takeaway": 371,
    }
    target_words = count_words(memo)

    report = validate_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt="renewals pipeline monetization capex pricing enterprise demand",
    )

    assert not any(
        failure.section_name == "Financial Health Rating"
        and failure.code == "section_budget_under"
        for failure in report.section_failures
    )


def test_validate_summary_uses_short_mid_precision_band_for_1225_target() -> None:
    target_words = 1225
    budgets = calculate_section_word_budgets(
        target_words, include_health_rating=False
    )
    memo = "\n\n".join(
        [
            "## Executive Summary\n{}".format(
                _section_body(
                    "Executive Summary",
                    "- Target {} body words.".format(
                        int(budgets["Executive Summary"])
                    ),
                )
            ),
            "## Financial Performance\n{}".format(
                _section_body(
                    "Financial Performance",
                    "- Target {} body words.".format(
                        int(budgets["Financial Performance"])
                    ),
                )
            ),
            "## Management Discussion & Analysis\n{}".format(
                _section_body(
                    "Management Discussion & Analysis",
                    "- Target {} body words.".format(
                        int(budgets["Management Discussion & Analysis"])
                    ),
                )
            ),
            "## Risk Factors\n{}".format(
                _valid_large_budget_risk_factors_body()
            ),
            "## Key Metrics\n{}".format(
                _metrics_lines_for_budget(int(budgets["Key Metrics"]))
            ),
            "## Closing Takeaway\n{}".format(
                _section_body(
                    "Closing Takeaway",
                    "- Target {} body words.".format(
                        int(budgets["Closing Takeaway"])
                    ),
                )
            ),
        ]
    )

    report = validate_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt=(
            "renewals pipeline monetization capex pricing enterprise demand "
            "channel partner execution friction backlog conversion"
        ),
    )

    assert report.lower_bound == 1185
    assert report.upper_bound == 1265
    assert report.passed


def test_validate_summary_surfaces_key_metrics_contract_underflow_for_850_target() -> None:
    target_words = 850
    budgets = calculate_section_word_budgets(
        target_words, include_health_rating=False
    )
    memo = "\n\n".join(
        [
            "## Executive Summary\n{}".format(
                _section_body(
                    "Executive Summary",
                    "- Target {} body words.".format(
                        int(budgets["Executive Summary"])
                    ),
                )
            ),
            "## Financial Performance\n{}".format(
                _section_body(
                    "Financial Performance",
                    "- Target {} body words.".format(
                        int(budgets["Financial Performance"])
                    ),
                )
            ),
            "## Management Discussion & Analysis\n{}".format(
                _section_body(
                    "Management Discussion & Analysis",
                    "- Target {} body words.".format(
                        int(budgets["Management Discussion & Analysis"])
                    ),
                )
            ),
            "## Risk Factors\n{}".format(
                _valid_risk_factors_body()
            ),
            "## Key Metrics\n{}".format(
                "DATA_GRID_START\n"
                "Revenue: $10.0B\n"
                "Operating Income: $2.8B\n"
                "Operating Margin: 28%\n"
                "Free Cash Flow: $1.9B\n"
                "Cash: $3.2B\n"
                "DATA_GRID_END"
            ),
            "## Closing Takeaway\n{}".format(
                _section_body(
                    "Closing Takeaway",
                    "- Target {} body words.".format(
                        int(budgets["Closing Takeaway"])
                    ),
                )
            ),
        ]
    )

    report = validate_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=False,
        risk_factors_excerpt=(
            "renewals pipeline monetization capex pricing enterprise demand "
            "channel partner execution friction backlog conversion"
        ),
    )

    key_metrics_failures = [
        failure
        for failure in report.section_failures
        if failure.section_name == "Key Metrics"
    ]
    assert any(
        failure.code == "key_metrics_contract_under"
        for failure in key_metrics_failures
    )
    assert not any(
        failure.code == "section_budget_under"
        for failure in key_metrics_failures
    )


def test_post_process_summary_uses_global_under_retry_when_only_long_form_gap_remains() -> None:
    def sentence_with_words(prefix: str, words: int) -> str:
        filler_needed = max(0, int(words) - 1)
        filler = " ".join(f"{prefix}{idx}" for idx in range(filler_needed))
        return (f"{filler}." if filler else f"{prefix}.").strip()

    def two_sentence_body(prefix: str, total_words: int) -> str:
        first = max(2, total_words // 2)
        second = max(2, total_words - first)
        return f"{sentence_with_words(prefix + 'a', first)} {sentence_with_words(prefix + 'b', second)}"

    sections = {
        "Financial Health Rating": two_sentence_body("health", 486),
        "Executive Summary": "Management noted that \"demand stayed resilient across enterprise accounts.\" " + two_sentence_body("exec", 412),
        "Financial Performance": (
            "Hedging marks can create quarter-to-quarter treasury noise when settlements and valuation changes land in different periods. "
            "That accounting swing matters because foreign-exchange protection can distort the bridge between reported earnings and cash generation even when the operating business is stable."
        ),
        "Management Discussion & Analysis": (
            "Management said treasury volatility should be read separately from operating performance because mark-to-market hedging can blur the income statement without changing customer demand. "
            "The operating question is whether the core business still converts demand into cash after stripping out those non-operating swings."
        ),
        "Risk Factors": _valid_large_budget_risk_factors_body(),
        "Key Metrics": _metrics_lines_for_budget(90),
        "Closing Takeaway": two_sentence_body("close", 360),
    }
    memo = "\n\n".join(f"## {name}\n{body}" for name, body in sections.items())
    budgets = {
        "Financial Health Rating": 510,
        "Executive Summary": 440,
        "Financial Performance": count_words(sections["Financial Performance"]),
        "Management Discussion & Analysis": count_words(
            sections["Management Discussion & Analysis"]
        ),
        "Risk Factors": count_words(sections["Risk Factors"]),
        "Key Metrics": count_words(sections["Key Metrics"]),
        "Closing Takeaway": 371,
    }
    target_words = count_words(memo) + 30
    regenerated_sections: list[str] = []

    def regenerate_section(**kwargs: str) -> str:
        regenerated_sections.append(kwargs["section_name"])
        if kwargs["section_name"] != "Closing Takeaway":
            return sections[kwargs["section_name"]]
        return (
            sections["Closing Takeaway"]
            + " Investors should watch whether monetized demand keeps pace with infrastructure intensity over the next two quarters."
        )

    result = post_process_summary(
        memo,
        target_words=target_words,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt="enterprise renewals pipeline conversion monetized usage capex pricing traffic acquisition distribution costs",
        regenerate_section_fn=regenerate_section,
    )

    assert result.passed
    assert regenerated_sections == ["Closing Takeaway"]
    assert result.validation_report is not None
    assert (
        result.validation_report.lower_bound
        <= count_words(result.text)
        <= result.validation_report.upper_bound
    )


# ---------------------------------------------------------------------------
# Soft-pass: Risk Factors / Closing Takeaway seesaw
# ---------------------------------------------------------------------------

def test_soft_pass_accepts_risk_closing_seesaw() -> None:
    """When Risk is underweight and Closing overweight (classic seesaw),
    soft pass should accept both within 3x tolerance."""
    report = SummaryValidationReport(
        passed=False,
        total_words=984,
        lower_bound=960,
        upper_bound=1040,
        global_failures=[],
        section_failures=[
            SectionValidationFailure(
                section_name="Risk Factors",
                code="section_budget_under",
                message="underweight",
                budget_words=172,
                actual_words=136,
            ),
            SectionValidationFailure(
                section_name="Closing Takeaway",
                code="section_budget_over",
                message="overweight",
                budget_words=127,
                actual_words=147,
            ),
        ],
        risk_count=2,
    )
    budgets = {"Risk Factors": 172, "Closing Takeaway": 127}
    assert _is_soft_pass(report, section_budgets=budgets, target_words=1000)


def test_soft_pass_rejects_extreme_seesaw() -> None:
    """Even with 4x multiplier for Risk Factors, an extreme deviation should still fail."""
    report = SummaryValidationReport(
        passed=False,
        total_words=928,
        lower_bound=960,
        upper_bound=1040,
        global_failures=[],
        section_failures=[
            SectionValidationFailure(
                section_name="Risk Factors",
                code="section_budget_under",
                message="underweight",
                budget_words=172,
                actual_words=80,  # 92 words off — beyond 4x21=84
            ),
        ],
        risk_count=1,
    )
    budgets = {"Risk Factors": 172}
    assert not _is_soft_pass(report, section_budgets=budgets, target_words=1000)


# ---------------------------------------------------------------------------
# Borderline risk count: quality checks still run
# ---------------------------------------------------------------------------

def test_validate_risk_factors_two_well_formed_risks_pass_under_two_risk_contract() -> None:
    """Well-formed two-risk sections should pass now that the contract caps the section at two items."""
    risk_section = (
        "## Risk Factors\n"
        "**[TSMC Allocation Constraint Risk]:** If TSMC tightens advanced-node allocation "
        "because competing customers take priority, the company loses access to leading-edge "
        "silicon and product launch timelines slip. That delay can compress revenue growth and "
        "operating margin. An early-warning signal is longer TSMC lead times or reduced wafer starts.\n\n"
        "**[EU Digital Markets Act Compliance Risk]:** If the EU DMA forces unbundling of "
        "pre-installed apps, distribution economics weaken and user acquisition costs rise. "
        "That mechanism can pressure advertising revenue and operating leverage. An early-warning "
        "signal is formal non-compliance proceedings or mandated remedy timelines."
    )
    count, failures = _validate_risk_factors(
        risk_section,
        risk_budget_words=250,
        risk_factors_excerpt="TSMC allocation silicon wafer EU DMA unbundling compliance",
        company_name="TestCorp",
    )
    assert count == 2
    assert failures == []


def test_validate_risk_factors_borderline_continues_quality_checks() -> None:
    """At tight per-risk budgets, 2 risks instead of 3 should still get
    quality-checked — the count mismatch is appended, not returned alone."""
    risk_section = (
        "## Risk Factors\n"
        "**[Supply Chain Concentration Risk]:** The company sources 60% of components "
        "from a single supplier in Taiwan. A disruption would cut production capacity "
        "within weeks. Watch for supplier diversification updates in the next 10-Q.\n\n"
        "**[Margin Compression from Input Costs]:** Raw material costs rose 12% YoY, "
        "outpacing pricing power. Continued inflation would compress gross margins "
        "below the 35% floor. Watch quarterly COGS trajectory relative to ASP trends."
    )
    count, failures = _validate_risk_factors(
        risk_section,
        risk_budget_words=172,  # per-risk = 57 < 65 → borderline
        risk_factors_excerpt="supply chain taiwan semiconductor components margin inflation",
        company_name="TestCorp",
    )
    assert count == 2
    # Quality checks should have run (mechanism/transmission are risk_quality
    # at tight per-risk budgets < 90 words).
    codes = [f[0] for f in failures]
    assert "risk_quality" in codes or "risk_specificity" in codes


# ---------------------------------------------------------------------------
# Instruction leak detection in post-processor
# ---------------------------------------------------------------------------


def test_instruction_leak_detected_in_validation() -> None:
    """validate_summary must catch 'should frame' instruction leakage."""
    sections = _base_sections()
    sections["Executive Summary"] = (
        "You should frame Alphabet as financially sound given its strong cash position. "
        "Management noted that enterprise demand remained resilient."
    )
    memo = _build_memo(sections)
    budgets = {s: 40 for s in sections}
    report = validate_summary(memo, target_words=200, section_budgets=budgets)
    assert not report.passed
    leak_msgs = [f for f in report.global_failures if "instruction leakage" in f.lower() or "Instruction leakage" in f]
    assert len(leak_msgs) >= 1


# ---------------------------------------------------------------------------
# Weak exec opening detection
# ---------------------------------------------------------------------------


def test_weak_exec_opening_with_quote() -> None:
    sections = _base_sections()
    sections["Executive Summary"] = (
        '"Enterprise demand remained resilient," management noted. '
        "That frames the central question for this period. "
        "Management still needs to prove investment expands returns."
    )
    memo = _build_memo(sections)
    budgets = {s: 40 for s in sections}
    report = validate_summary(memo, target_words=200, section_budgets=budgets)
    assert any(
        f.code == "weak_exec_opening" for f in report.section_failures
    )


def test_clean_exec_opening_passes() -> None:
    sections = _base_sections()
    # Default _base_sections has a clean opening — not a quote
    memo = _build_memo(sections)
    budgets = {s: 40 for s in sections}
    report = validate_summary(memo, target_words=200, section_budgets=budgets)
    assert not any(
        f.code == "weak_exec_opening" for f in report.section_failures
    )


# ---------------------------------------------------------------------------
# Casual first-person in Closing Takeaway
# ---------------------------------------------------------------------------


def test_casual_first_person_closing_flagged() -> None:
    sections = _base_sections()
    sections["Closing Takeaway"] = (
        "For my own portfolio, this is a clear buy. "
        "The next watch item is whether monetization keeps pace."
    )
    memo = _build_memo(sections)
    budgets = {s: 40 for s in sections}
    report = validate_summary(memo, target_words=200, section_budgets=budgets)
    assert any(
        f.code == "casual_first_person" for f in report.section_failures
    )


def test_institutional_closing_passes() -> None:
    sections = _base_sections()
    # Default closing is institutional — should pass
    memo = _build_memo(sections)
    budgets = {s: 40 for s in sections}
    report = validate_summary(memo, target_words=200, section_budgets=budgets)
    assert not any(
        f.code == "casual_first_person" for f in report.section_failures
    )


# ---------------------------------------------------------------------------
# Punctuation repair
# ---------------------------------------------------------------------------


def test_punctuation_repair() -> None:
    from app.services.summary_post_processor import _repair_punctuation

    assert _repair_punctuation("growth is being bought efficiently.:") == "growth is being bought efficiently."
    assert _repair_punctuation("That matters..") == "That matters."
    assert _repair_punctuation("Revenue grew,.") == "Revenue grew."
    assert _repair_punctuation(". .") == "."
    assert _repair_punctuation("Clean text stays clean.") == "Clean text stays clean."


# ---------------------------------------------------------------------------
# Regulatory minutiae risk filtering
# ---------------------------------------------------------------------------


def test_regulatory_minutiae_risk_rejected() -> None:
    risk_section = (
        "## Risk Factors\n"
        "**FinSA Compliance Uncertainty:** Swiss Financial Market "
        "Supervisory Authority may require additional FinSA compliance "
        "steps. The disclosure requirements could change based on "
        "evolving interpretations. Watch for updated guidance.\n\n"
        "**Cloud Adoption Slowdown:** Enterprise customers may slow "
        "cloud migration because budgets tighten, which would pressure "
        "revenue growth and cash flow conversion. Watch pipeline "
        "conversion rates."
    )
    count, failures = _validate_risk_factors(
        risk_section,
        risk_budget_words=150,
        risk_factors_excerpt="FinSA compliance cloud migration enterprise",
        company_name="TestCorp",
    )
    codes = [f[0] for f in failures]
    assert "risk_specificity" in codes
    # The FinSA risk should be flagged as regulatory minutiae
    msgs = [f[1] for f in failures if f[0] == "risk_specificity"]
    assert any("regulatory minutiae" in m for m in msgs)
