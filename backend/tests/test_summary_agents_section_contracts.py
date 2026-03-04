from app.services import summary_agents


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

    failures = summary_agents._validate_risk_local_contract(text, budget=460)

    assert failures == []


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
