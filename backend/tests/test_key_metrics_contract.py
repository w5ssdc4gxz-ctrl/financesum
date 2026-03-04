from app.api import filings as filings_api


def test_key_metrics_validator_rejects_prose_lines() -> None:
    block = (
        "DATA_GRID_START\n"
        "Revenue | $1.0B\n"
        "Operating Margin | 10.0%\n"
        "This is narrative prose and should fail.\n"
        "DATA_GRID_END"
    )
    issue, row_count = filings_api._validate_key_metrics_numeric_block(
        block,
        min_rows=2,
        require_markers=True,
    )
    assert issue is not None
    assert row_count == 2


def test_key_metrics_validator_requires_minimum_numeric_rows() -> None:
    block = (
        "DATA_GRID_START\n"
        "Revenue | $1.0B\n"
        "Operating Margin | 10.0%\n"
        "Net Margin | 8.0%\n"
        "Free Cash Flow | $0.4B\n"
        "DATA_GRID_END"
    )
    issue, row_count = filings_api._validate_key_metrics_numeric_block(
        block,
        min_rows=5,
        require_markers=True,
    )
    assert issue is not None
    assert "at least 5 numeric rows" in issue
    assert row_count == 4


def test_key_metrics_validator_accepts_numeric_rows_outside_markers() -> None:
    block = (
        "DATA_GRID_START\n"
        "Revenue | $21.73B\n"
        "Operating Margin | 30.3%\n"
        "DATA_GRID_END\n"
        "Operating Cash Flow | $19.04B\n"
    )
    issue, row_count = filings_api._validate_key_metrics_numeric_block(
        block,
        min_rows=3,
        require_markers=True,
    )
    assert issue is None
    assert row_count == 3


def test_key_metrics_validator_accepts_unicode_pipes_and_markdown_wrappers() -> None:
    block = (
        "DATA_GRID_START\n"
        "| **Revenue** ｜ **$21.73B** |\n"
        "__Operating Margin__ ¦ _30.3%_\n"
        "`Free Cash Flow` | `$8.21B`\n"
        "DATA_GRID_END"
    )
    issue, row_count = filings_api._validate_key_metrics_numeric_block(
        block,
        min_rows=3,
        require_markers=True,
    )
    assert issue is None
    assert row_count == 3


def test_short_form_structural_selector_flags_invalid_key_metrics_block() -> None:
    summary_text = (
        "## Financial Health Rating\nHealthy balance sheet.\n\n"
        "## Executive Summary\nThe quarter reset profitability.\n\n"
        "## Financial Performance\nRevenue and margins improved.\n\n"
        "## Management Discussion & Analysis\nManagement kept investing.\n\n"
        "## Risk Factors\nCompetition remains active.\n\n"
        "## Key Metrics\n"
        "0.30x DATA_GRID_END **Alphabet Margin / Reinvestment Risk**\n\n"
        "## Closing Takeaway\nHold while margins remain above 25% over the next year."
    )

    fatal = filings_api._select_short_form_structural_failure_requirements(
        summary_text=summary_text,
        missing_requirements=[
            "Key Metrics must be numeric DATA_GRID rows only. Found invalid line: '0.30x DATA_GRID_END **Alphabet Margin / Reinvestment Risk**'."
        ],
        include_health_rating=True,
    )

    assert any("Key Metrics must be numeric DATA_GRID rows only" in item for item in fatal)
