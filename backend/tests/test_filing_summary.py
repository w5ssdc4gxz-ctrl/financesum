from datetime import datetime

from fastapi.testclient import TestClient

from app.api import filings as filings_api
from app.config import get_settings
from app.main import app
from app.services import local_cache


def test_summary_uses_serialized_statements(monkeypatch):
    """Ensure summary endpoint can serialize cached statements that include datetimes."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "test-summary-filing"
    company_id = "test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2023-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TEST",
        "name": "Test Corp",
        "cik": None,  # Force document download to be skipped so statements are used
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2023-01-01",
        "period_end": "2023-12-31",
        "statements": {"income_statement": {"totalRevenue": {"2023-12-31": 123456}}},
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    class DummyModel:
        def generate_content(self, prompt: str):
            class Response:
                text = "summary"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 200
        assert response.json()["summary"] == "summary"
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_custom_preferences_influence_prompt(monkeypatch):
    """Custom summary requests should embed investor preferences into the prompt and skip caching."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "custom-pref-filing"
    company_id = "custom-pref-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-03-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CSTM",
        "name": "Custom Pref Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-03-31",
        "statements": {"income_statement": {"totalRevenue": {"2024-03-31": 555}}}
    }

    captured_prompt = {}

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompt["text"] = prompt

            class Response:
                text = "custom summary"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={
            "mode": "custom",
            "investor_focus": "Focus on downside protection and liquidity.",
            "focus_areas": ["Risk Factors", "Liquidity"],
            "tone": "cautiously bearish",
            "target_length": 200,
        },
    )

    try:
        assert response.status_code == 200
        summary_text = response.json()["summary"]
        assert summary_text.startswith("custom summary")
        prompt_text = captured_prompt["text"]
        assert "Investor brief (absolute priority): Focus on downside protection and liquidity" in prompt_text
        assert "Begin the memo with a labeled 'Investor Lens' paragraph" in prompt_text
        assert "explicitly reference this persona by name" in prompt_text
        assert "Primary focus areas (cover strictly in this order" in prompt_text
        assert "Focus area execution order" in prompt_text
        assert "Final deliverable must contain 200 words" in prompt_text
        assert "Tone must remain cautiously bearish" in prompt_text
        assert local_cache.fallback_filing_summaries.get(filing_id) is None
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_enforces_word_length(monkeypatch):
    """Ensure backend trims or pads summaries to stay near requested length."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-test-filing"
    company_id = "length-test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "LEN",
        "name": "Length Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-04-01",
        "period_end": "2024-06-30",
        "statements": {"income_statement": {"totalRevenue": {"2024-06-30": 1000}}},
    }

    long_response = "word " * 1000

    class DummyModel:
        def generate_content(self, prompt: str):
            class Response:
                text = long_response

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    target_length = 200
    tolerance = max(5, int(target_length * 0.05))
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = len(summary.split())
        assert target_length - tolerance <= word_count <= target_length + tolerance
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_requires_mdna_section(monkeypatch):
    """Ensure backend retries when MD&A content is missing."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "mdna-test-filing"
    company_id = "mdna-test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MDNA",
        "name": "MDNA Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "statements": {"income_statement": {"totalRevenue": {"2024-12-31": 500}}},
    }

    responses = [
        "Executive Summary\n\nManagement Discussion & Analysis\nInformation not available.\n",
        "Executive Summary\n\nManagement Discussion & Analysis\nManagement highlighted strategic expansion.\n",
    ]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            text = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1

            class Response:
                pass

            r = Response()
            r.text = text
            return r

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary", json={"mode": "custom"})

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert "strategic expansion" in summary
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)
