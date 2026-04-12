from app.services.prompt_pack import (
    PromptContext,
    _budget_instruction,
    assemble_structured_summary,
    build_structured_output_contract,
    extract_structured_section_payload,
)


def test_build_structured_output_contract_includes_budget_and_counting_instructions() -> None:
    contract = build_structured_output_contract(
        section_budgets={
            "Executive Summary": 150,
            "Financial Performance": 200,
            "Management Discussion & Analysis": 200,
            "Risk Factors": 150,
            "Key Metrics": 80,
            "Closing Takeaway": 100,
        },
        include_health_rating=False,
        target_length=1000,
    )

    assert "Return a single JSON object only." in contract
    assert '"Executive Summary": target 150 body words' in contract
    assert "allowed range 140-160" in contract  # ±floor 10 for budget 150
    assert "use code to calculate the word count" in contract
    assert "Financial Health Rating" not in contract


def test_extract_and_assemble_structured_summary_uses_canonical_headings_once() -> None:
    raw = """
```json
{
  "central_tension": "Margins remain strong but reinvestment is rising.",
  "reported_total_word_count": 630,
  "sections": {
    "Financial Health Rating": {"body": "## Financial Health Rating\\nHealthy balance sheet and durable profitability."},
    "Executive Summary": {"body": "Executive Summary: Core ads fund the rest of the portfolio."},
    "Financial Performance": {"body": "Revenue expanded while margins stayed resilient."},
    "Management Discussion & Analysis": {"body": "Management is prioritizing AI and cloud investment."},
    "Risk Factors": {"body": "Execution risk rises if capex outruns monetization."},
    "Key Metrics": {"body": "## Key Metrics\\nRevenue | $10B\\nOperating Margin | 25%"},
    "Closing Takeaway": {"body": "Maintain discipline while monitoring capex returns."}
  }
}
```
""".strip()

    payload = extract_structured_section_payload(raw)
    assert payload is not None

    assembled = assemble_structured_summary(payload, include_health_rating=True)
    assert assembled is not None
    assert assembled.count("## Executive Summary") == 1
    assert assembled.count("## Financial Health Rating") == 1
    assert assembled.count("## Key Metrics") == 1
    assert "Executive Summary: Core ads fund" not in assembled
    assert "## Financial Health Rating\n## Financial Health Rating" not in assembled
    assert "## Key Metrics\n## Key Metrics" not in assembled


def test_structured_body_normalizer_strips_repeated_titles_recursively() -> None:
    raw = """
```json
{
  "central_tension": "Execution quality remains the key variable.",
  "reported_total_word_count": 120,
  "sections": {
    "Executive Summary": {
      "body": "## Executive Summary\\nExecutive Summary: Executive Summary: Core economics remain durable."
    },
    "Financial Performance": {
      "body": "Revenue held up while margins compressed."
    },
    "Management Discussion & Analysis": {
      "body": "Management is prioritizing targeted investment."
    },
    "Risk Factors": {
      "body": "Execution risk rises if spend outruns monetization."
    },
    "Key Metrics": {
      "body": "Revenue | $10B"
    },
    "Closing Takeaway": {
      "body": "HOLD while margin discipline is tested."
    }
  }
}
```
""".strip()

    payload = extract_structured_section_payload(raw)
    assert payload is not None

    assembled = assemble_structured_summary(payload, include_health_rating=False)
    assert assembled is not None
    assert "Executive Summary: Executive Summary" not in assembled
    assert assembled.count("## Executive Summary") == 1


def test_long_form_budget_instruction_uses_budget_aware_closing_contract() -> None:
    ctx = PromptContext(
        company_name="Alphabet Inc.",
        section_budgets={"Closing Takeaway": 370},
    )

    closing_instruction = _budget_instruction(ctx, "Closing Takeaway")

    assert "2-3 sentences" not in closing_instruction
    assert "7-9 sentences" in closing_instruction
    assert "what must stay true" in closing_instruction
    assert "what breaks the thesis" in closing_instruction


def test_long_form_budget_instruction_uses_probability_first_natural_prose_for_risks() -> None:
    ctx = PromptContext(
        company_name="Alphabet Inc.",
        section_budgets={"Risk Factors": 556},
    )

    risk_instruction = _budget_instruction(ctx, "Risk Factors")

    assert "NO early-warning" not in risk_instruction
    assert "probability first" in risk_instruction
    assert "natural prose" in risk_instruction
    assert "4-5 sentences" in risk_instruction
