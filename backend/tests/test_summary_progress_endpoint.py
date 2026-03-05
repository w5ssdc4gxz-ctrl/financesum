from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import filings as filings_api
from app.main import app


def test_progress_endpoint_handles_legacy_snapshot_without_error_field(monkeypatch) -> None:
    legacy_snapshot = SimpleNamespace(
        status="Initializing AI Agent...",
        percent=12,
        percent_exact=12.0,
        eta_seconds=90,
        last_failure_code=None,
        last_error_message=None,
        last_error_details=None,
    )
    monkeypatch.setattr(
        filings_api, "get_summary_progress_snapshot", lambda _filing_id: legacy_snapshot
    )

    client = TestClient(app)
    response = client.get("/api/v1/filings/legacy-progress-id/progress")
    assert response.status_code == 200
    payload = response.json() or {}
    assert payload.get("status") == "Initializing AI Agent..."
    assert payload.get("error") is False


def test_progress_endpoint_returns_safe_error_payload_on_snapshot_failure(
    monkeypatch,
) -> None:
    def _raise_snapshot_error(_filing_id: str):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(filings_api, "get_summary_progress_snapshot", _raise_snapshot_error)

    client = TestClient(app)
    response = client.get("/api/v1/filings/failing-progress-id/progress")
    assert response.status_code == 200
    payload = response.json() or {}
    assert payload.get("error") is True
    assert payload.get("last_failure_code") == "SUMMARY_PROGRESS_UNAVAILABLE"
    assert "snapshot unavailable" in str(payload.get("last_error_message") or "")
