from fastapi.testclient import TestClient

from app.main import app


def test_admin_gemini_usage_forbidden_for_non_owner(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setenv("FINANCESUM_OWNER_EMAIL", "owner@example.com")

    resp = client.get("/api/v1/admin/gemini-usage?days=0&limit=1")
    assert resp.status_code == 403


def test_admin_gemini_usage_allowed_for_owner(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setenv("FINANCESUM_OWNER_EMAIL", "demo@financesum.com")

    resp = client.get("/api/v1/admin/gemini-usage?days=0&limit=1")
    assert resp.status_code == 200
    payload = resp.json()
    assert "window" in payload
    assert "budget_usd" in payload
    assert "totals" in payload
    assert "requests" in payload

