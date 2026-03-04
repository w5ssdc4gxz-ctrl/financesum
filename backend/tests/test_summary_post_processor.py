from app.services.summary_post_processor import (
    SectionValidationFailure,
    SummaryValidationReport,
    _select_regeneration_target,
    _validate_risk_factors,
    validate_summary,
    post_process_summary,
)
from app.services.word_surgery import count_words


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
            "Revenue growth stayed resilient because enterprise demand held and pricing remained disciplined. "
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
            "The underwriting case still works because the core business funds the investment cycle. "
            "The next watch item is whether monetization keeps pace with infrastructure spend."
        ),
    }


def _valid_risk_factors_body() -> str:
    return (
        "**Deferred Enterprise Renewals:** If large customers push deployments into later quarters, "
        "bookings convert more slowly and revenue visibility weakens. That delay can pressure growth, "
        "gross margin absorption, and free cash flow conversion. An early-warning signal is weaker "
        "enterprise pipeline conversion or lower renewal rates.\n\n"
        "**AI Spend Monetization Lag:** If AI infrastructure spending rises faster than product pricing "
        "or usage ramps, operating leverage can erode before incremental demand scales. That mismatch "
        "can compress operating margin and reduce cash generation available for buybacks or reinvestment. "
        "An early-warning signal is rising capex intensity without a matching uplift in monetized usage."
    )


def test_post_process_summary_regenerates_only_the_failing_risk_section() -> None:
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
    assert regenerated_sections.count("Risk Factors") == 3
    assert result.retries >= 3


def test_post_process_summary_uses_last_narrative_section_for_global_under_target() -> None:
    sections = _base_sections()
    sections["Risk Factors"] = _valid_risk_factors_body()
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


def test_validate_risk_factors_accepts_four_sentence_items_for_large_budgets() -> None:
    memo = _build_memo(
        {
            **_base_sections(),
            "Risk Factors": (
                "**Cloud Capacity Bottlenecks:** If cloud capacity, backlog conversion, and utilization ramp fall out of sync because data-center deployments land later than committed customer demand, contracted workloads take longer to convert into recognized revenue and the company ends up carrying expensive infrastructure before usage catches up. "
                "That timing mismatch can pressure cloud revenue recognition, gross margin absorption, operating margin, and cash flow because servers, networking, support staffing, and power commitments are already in place before bookings fully translate into billable workloads at the expected pace. "
                "The financial risk gets worse if management has to relieve backlog with discounts, service credits, or unusually fast provisioning promises that lift cost-to-serve just as capital intensity is climbing across the cloud platform and investors are underwriting better returns on incremental capacity. "
                "An early-warning signal is rising backlog, weaker utilization, slower bookings conversion, or repeated commentary that cloud capacity remains the binding constraint on delivery.\n\n"
                "**Search Compute Monetization:** If search monetization fails to keep pace with higher AI serving costs, each additional query can become less profitable even while overall usage, engagement, and product adoption look healthy on the surface. "
                "That mechanism can compress operating margin and reduce free cash flow because the company is spending more on inference, ranking, and model orchestration before it has proven that pricing, monetized clicks, and usage mix are scaling fast enough to cover the added compute burden. "
                "The downside becomes larger if product changes improve engagement but fail to improve advertiser ROI, because management would then be funding more expensive search experiences without getting the monetization lift needed to protect margins, cash generation, or valuation support. "
                "An early-warning signal is higher cost-per-query, softer search monetization, or a weaker uplift in monetized clicks and pricing despite heavier compute intensity.\n\n"
                "**Partner Traffic Mix Shift:** If traffic-acquisition cost and retention trends move against the company because distribution partners gain bargaining power or usage shifts toward more expensive channels, the ads engine loses part of the funding cushion that currently supports reinvestment. "
                "That can weaken revenue mix, operating margin, and balance-sheet flexibility at the same time the company is trying to scale new workloads, because higher partner payments and lower retention would redirect cash away from internally funded cloud and product investment. "
                "The impact is more severe if management has to protect volume by accepting lower unit economics in core ad surfaces, since that would combine traffic-acquisition-cost pressure with weaker monetization and force tougher capital-allocation tradeoffs across the broader platform. "
                "An early-warning signal is faster traffic-acquisition-cost growth, lower partner retention, weaker ROI in key channels, or a sustained rise in partner concessions."
            ),
        }
    )

    risk_count, failures = _validate_risk_factors(
        memo,
        risk_budget_words=556,
        risk_factors_excerpt="cloud capacity backlog utilization search monetization traffic acquisition cost retention",
    )

    assert risk_count == 3
    assert failures == []


def test_validate_summary_ignores_ngram_only_repetition_when_no_duplicate_sentences_exist() -> None:
    memo = _build_memo(
        {
            **_base_sections(),
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
        "Executive Summary": count_words(_base_sections()["Executive Summary"]),
        "Financial Performance": count_words(
            "Derivative hedging can create quarter-to-quarter treasury noise when settlements and marks move at different times. "
            "That treasury noise matters because derivatives to manage foreign exchange and other exposures can distort the bridge between reported earnings and cash generation."
        ),
        "Management Discussion & Analysis": count_words(
            "Management said treasury volatility should be read separately from operating performance because derivatives to manage foreign exchange and other exposures can create accounting noise. "
            "The operating question is whether the core business still converts demand into cash after stripping out those treasury swings."
        ),
        "Risk Factors": count_words(_valid_risk_factors_body()),
        "Key Metrics": count_words(_base_sections()["Key Metrics"]),
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
        "Executive Summary": two_sentence_body("exec", 420),
        "Financial Performance": (
            "Derivative hedging can create quarter to quarter treasury noise when settlements and marks move at different times. "
            "That treasury noise matters because derivatives to manage foreign exchange and other exposures can distort the bridge between reported earnings and cash generation."
        ),
        "Management Discussion & Analysis": (
            "Management said treasury volatility should be read separately from operating performance because derivatives to manage foreign exchange and other exposures can create accounting noise. "
            "The operating question is whether the core business still converts demand into cash after stripping out those treasury swings."
        ),
        "Risk Factors": (
            "**Deferred Enterprise Renewals:** If large customers push deployments into later quarters, bookings convert more slowly and revenue visibility weakens. "
            "That delay can pressure growth, gross margin absorption, and free cash flow conversion. "
            "An early-warning signal is weaker enterprise pipeline conversion, lower renewal rates, or slower implementation timing.\n\n"
            "**AI Spend Monetization Lag:** If AI infrastructure spending rises faster than product pricing or usage ramps, operating leverage can erode before incremental demand scales. "
            "That mismatch can compress operating margin and reduce cash generation available for buybacks or reinvestment. "
            "An early-warning signal is rising capex intensity without a matching uplift in monetized usage or pricing.\n\n"
            "**Traffic Acquisition Mix Shift:** If distribution costs rise because traffic shifts toward more expensive channels, the ads engine loses part of the funding cushion that supports reinvestment. "
            "That can weaken revenue mix, operating margin, and balance-sheet flexibility while investment demands stay elevated. "
            "An early-warning signal is faster traffic-acquisition-cost growth without matching monetization improvement or partner retention stability."
        ),
        "Key Metrics": "-> Revenue: $10.0B\n-> Operating Margin: 25%",
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
