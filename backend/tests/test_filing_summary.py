import re
from datetime import datetime

from fastapi.testclient import TestClient

from app.api import filings as filings_api
from app.config import get_settings
from app.main import app
from app.services import local_cache

WORD_PATTERN = re.compile(r"\b\w+\b")


def _backend_word_count(text: str) -> int:
    import string
    punct = string.punctuation + "“”’‘—–…"
    count = 0
    for raw_token in text.split():
        token = raw_token.strip(punct)
        if token:
            count += 1
    return count


def build_summary_with_word_count(word_count: int) -> str:
    """Return deterministic markdown text with a trailing WORD COUNT line."""
    base = (
        "Executive Summary\n"
        "Value investors care about durable earnings power and disciplined capital allocation. "
        "Management Discussion & Analysis\n"
        "Management emphasized reinvestment pacing, competitive moats, measured buybacks, disciplined hiring, "
        "and multi-year AI investments across infrastructure and product roadmaps. Leadership reiterated that "
        "capital deployment will prioritize high-IRR data center builds, targeted acquisitions, and steady "
        "international expansion while preserving balance sheet flexibility. "
        "Risk Factors\n"
        "Competition, regulation, and execution remain key watchpoints. "
    )
    text = base.strip()
    current_words = _backend_word_count(text)
    if current_words > word_count:
        tokens = text.split()
        text = " ".join(tokens[:word_count])
    else:
        filler_needed = word_count - current_words
        if filler_needed > 0:
            filler = " ".join(["focus."] * filler_needed)
            text = f"{text} {filler}".strip()
    current_words = _backend_word_count(text)
    actual = _backend_word_count(text)
    return f"{text}\nWORD COUNT: {actual}"


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
                text = "summary\nWORD COUNT: 1"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    print("DEBUG: Sending request from test")
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        if response.status_code != 200:
            print(f"DEBUG: Response status: {response.status_code}")
            print(f"DEBUG: Response body: {response.text}")
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

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "custom summary\nWORD COUNT: 2"

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
        prompt_text = captured_prompts[0]
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


def test_health_rating_only_appears_when_enabled(monkeypatch):
    """Health rating instructions are only injected when explicitly enabled."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "default-health"
    company_id = "default-health-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-01-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "HLTH",
        "name": "Health Default Inc",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2023-02-01",
        "period_end": "2024-01-31",
        "statements": {"income_statement": {"totalRevenue": {"2024-01-31": 1000}}},
    }

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "health summary\nWORD COUNT: 2"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 200
        prompt_text = captured_prompts[-1]
        assert "Financial Health Rating" not in prompt_text

        local_cache.fallback_filing_summaries.pop(filing_id, None)

        response_with_rating = client.post(
            f"/api/v1/filings/{filing_id}/summary",
            json={"mode": "default", "health_rating": {"enabled": True}},
        )
        assert response_with_rating.status_code == 200
        prompt_with_rating = captured_prompts[-1]
        assert "Financial Health Rating" in prompt_with_rating
        assert "letter grade" in prompt_with_rating.lower()
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_custom_health_rating_configuration(monkeypatch):
    """Custom requests can opt-in to health scoring with bespoke settings."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "custom-health"
    company_id = "custom-health-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CSTM",
        "name": "Health Custom Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-04-01",
        "period_end": "2024-06-30",
        "statements": {"income_statement": {"totalRevenue": {"2024-06-30": 2000}}},
    }

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "custom health summary\nWORD COUNT: 3"

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
            "investor_focus": "Call out stress scenarios.",
            "health_rating": {
                "enabled": True,
                "framework": "financial_resilience",
                "primary_factor_weighting": "liquidity_near_term_risk",
                "risk_tolerance": "very_conservative",
                "analysis_depth": "forensic_deep_dive",
                "display_style": "score_plus_pillars",
            },
        },
    )

    try:
        assert response.status_code == 200
        prompt_text = captured_prompts[0]
        assert "Financial Resilience" in prompt_text
        assert "liquidity, leverage, refinancing risk" in prompt_text
        assert "four-pillar breakdown" in prompt_text or "four-pillar" in prompt_text
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_enforces_word_length(monkeypatch):
    """Ensure backend re-prompts LLM until summary length is near requested value."""
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

    target_length = 200
    responses = [build_summary_with_word_count(800), build_summary_with_word_count(target_length)]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            class Response:
                text = responses[min(self.calls, len(responses) - 1)]

            self.calls += 1

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    tolerance = max(5, int(target_length * 0.05))
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        if response.status_code != 200:
            with open("debug_response.txt", "w") as f:
                f.write(f"Status: {response.status_code}\nBody: {response.text}")
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = len(summary.split())
        assert target_length - tolerance <= word_count <= target_length + tolerance
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_trims_when_model_refuses(monkeypatch):
    """Ensure backend trims overlong drafts when the model ignores length instructions."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-trim-filing"
    company_id = "length-trim-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TRIM",
        "name": "Trim Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": {"income_statement": {"totalRevenue": {"2024-09-30": 400}}},
    }

    overlong_response = build_summary_with_word_count(900)

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1
            return type("Resp", (), {"text": overlong_response})()

    dummy_model_holder = {"model": DummyModel()}

    class DummyClient:
        def __init__(self) -> None:
            self.model = dummy_model_holder["model"]

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    target_length = 500
    tolerance = max(5, int(target_length * 0.05))
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = len(summary.split())
        assert word_count <= target_length + tolerance
        assert word_count < 900  # Confirm it was trimmed
        assert summary.rstrip().endswith((".", "!", "?"))
        assert dummy_model_holder["model"].calls == filings_api.MAX_SUMMARY_ATTEMPTS + filings_api.MAX_REWRITE_ATTEMPTS
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_rewrite_produces_compact_output(monkeypatch):
    """Ensure rewrite fallback generates an on-length memo instead of trimming content."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-rewrite-filing"
    company_id = "length-rewrite-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "REWR",
        "name": "Rewrite Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "statements": {"income_statement": {"totalRevenue": {"2024-12-31": 800}}},
    }

    overlong = build_summary_with_word_count(900)
    compact = build_summary_with_word_count(510)
    responses = [overlong] * filings_api.MAX_SUMMARY_ATTEMPTS + [compact]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            text = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return type("Resp", (), {"text": text})()

    dummy_model_holder = {"model": DummyModel()}

    class DummyClient:
        def __init__(self) -> None:
            self.model = dummy_model_holder["model"]

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    target_length = 500
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
        assert "Executive Summary" in summary
        assert "Management Discussion" in summary
        assert filings_api.MAX_SUMMARY_ATTEMPTS < dummy_model_holder["model"].calls <= (
            filings_api.MAX_SUMMARY_ATTEMPTS + filings_api.MAX_REWRITE_ATTEMPTS
        )
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_final_clamp_holds_length_after_postprocessing(monkeypatch):
    """Post-processing additions should still respect the requested word target."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-postprocess-filing"
    company_id = "length-postprocess-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-11-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "POST",
        "name": "Post Process Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-08-01",
        "period_end": "2024-10-31",
        "statements": {"income_statement": {"totalRevenue": {"2024-10-31": 700}}},
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)

    target_length = 150
    call_counter = {"calls": 0}

    class DummyModel:
        def generate_content(self, prompt: str):
            call_counter["calls"] += 1

            class Response:
                pass

            resp = Response()
            if call_counter["calls"] == 1:
                # Initial draft lands inside the tolerance band
                resp.text = build_summary_with_word_count(target_length - 5)
            else:
                # Rewrite attempts stubbornly stay long; final clamp must fix it
                resp.text = build_summary_with_word_count(target_length + 40)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = _backend_word_count(summary)
        assert target_length - 10 <= word_count <= target_length + 10
        # Ensure sections survive trimming
        assert "Key Data Appendix" in summary
        assert "Strategic Initiatives" in summary
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_final_output_clamped_to_target_band(monkeypatch):
    """Even stubbornly short drafts are padded into the requested ±10 word band."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-clamp-filing"
    company_id = "length-clamp-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-10-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CLMP",
        "name": "Clamp Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": {"income_statement": {"totalRevenue": {"2024-09-30": 500}}},
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)
    # Skip section completeness validation to focus purely on length enforcement
    monkeypatch.setattr(filings_api, "_make_section_completeness_validator", lambda include: (lambda _txt: None))

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1

            class Resp:
                pass

            # Always return a stubbornly short draft (around 617 words)
            resp = Resp()
            resp.text = build_summary_with_word_count(617)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    target_length = 650
    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = _backend_word_count(summary)
        assert target_length - 10 <= word_count <= target_length + 10
        # Ensure key sections remain present after padding/clamping
        assert "Executive Summary" in summary
        assert "Key Data Appendix" in summary
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_overlong_output_is_trimmed_but_complete(monkeypatch):
    """Overlong drafts are trimmed into band while preserving Key Data Appendix rows."""
    settings = get_settings()
    settings.gemini_api_key = "test-key"

    filing_id = "length-trim-band"
    company_id = "length-trim-band-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-10-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TRMB",
        "name": "Trim Band Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": {"income_statement": {"totalRevenue": {"2024-09-30": 500}}},
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)
    # Skip section completeness validation to isolate band enforcement
    monkeypatch.setattr(filings_api, "_make_section_completeness_validator", lambda include: (lambda _txt: None))

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1

            class Resp:
                pass

            # Return an overlong draft (~700 words)
            resp = Resp()
            resp.text = build_summary_with_word_count(700)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    target_length = 650
    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = _backend_word_count(summary)
        assert target_length - 10 <= word_count <= target_length + 10
        assert "Key Data Appendix" in summary
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

    def mdna_response(text: str) -> str:
        body = text.strip()
        count = len(body.split())
        return f"{body}\nWORD COUNT: {count}"

    responses = [
        mdna_response("Executive Summary\n\nManagement Discussion & Analysis\nInformation not available."),
        mdna_response("Executive Summary\n\nManagement Discussion & Analysis\nManagement highlighted strategic expansion."),
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
