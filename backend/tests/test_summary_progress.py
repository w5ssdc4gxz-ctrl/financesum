from app.services.local_cache import progress_cache, summary_progress_cache
from app.services.summary_progress import (
    complete_summary_progress,
    get_summary_progress_snapshot,
    set_summary_progress,
    start_summary_progress,
)


def setup_function() -> None:
    progress_cache.clear()
    summary_progress_cache.clear()


def test_progress_snapshot_exposes_error_details() -> None:
    filing_id = "filing-1"
    start_summary_progress(filing_id, expected_total_seconds=120)
    set_summary_progress(
        filing_id,
        status="Unable to satisfy one-shot deterministic summary contract.",
        stage_percent=0,
        error=True,
        last_failure_code="SUMMARY_ONE_SHOT_CONTRACT_FAILED",
        last_error_message="Unable to satisfy one-shot deterministic summary contract.",
        last_error_details={
            "failure_code": "SUMMARY_ONE_SHOT_CONTRACT_FAILED",
            "missing_requirements": [
                "Final word-count band violation: expected 630-670, got split=608, stripped=611.",
                "Section balance issue: 'Risk Factors' is underweight.",
            ],
            "diagnostic_missing_requirements": [
                "Section balance issue: 'Risk Factors' is underweight.",
            ],
        },
    )

    snapshot = get_summary_progress_snapshot(filing_id)

    assert snapshot.error is True
    assert snapshot.last_failure_code == "SUMMARY_ONE_SHOT_CONTRACT_FAILED"
    assert "one-shot deterministic summary contract" in (snapshot.last_error_message or "")
    assert snapshot.last_error_details is not None
    assert snapshot.last_error_details.get("failure_code") == "SUMMARY_ONE_SHOT_CONTRACT_FAILED"


def test_complete_progress_clears_error_fields() -> None:
    filing_id = "filing-2"
    start_summary_progress(filing_id, expected_total_seconds=120)
    set_summary_progress(
        filing_id,
        status="Summary budget exceeded.",
        stage_percent=0,
        error=True,
        last_failure_code="SUMMARY_BUDGET_EXCEEDED",
        last_error_message="Budget exceeded",
        last_error_details={"failure_code": "SUMMARY_BUDGET_EXCEEDED"},
    )

    complete_summary_progress(filing_id)
    snapshot = get_summary_progress_snapshot(filing_id)

    assert snapshot.error is False
    assert snapshot.last_failure_code is None
    assert snapshot.last_error_message is None
    assert snapshot.last_error_details is None

