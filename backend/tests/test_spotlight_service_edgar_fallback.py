from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from app.services.spotlight_kpi import service as spotlight_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeGeminiClientForEdgarFallback:
    """Simulates: original doc => no candidates, edgar alt => KPI found."""

    def __init__(self) -> None:
        self.pass1_calls = 0

    def upload_file_bytes(self, *, data: bytes, mime_type: str, **_kwargs: Any) -> Dict[str, Any]:
        assert data
        assert mime_type
        return {"uri": "files/FAKE", "mimeType": mime_type}

    def stream_generate_content_with_file_uri(
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (prompt, generation_config_override)
        assert file_uri
        assert file_mime_type

        if "Pass 1" in stage_name:
            self.pass1_calls += 1
            if self.pass1_calls == 1:
                return json.dumps({"candidates": [], "failure_reason": "no_candidates"})
            return json.dumps(
                {
                    "candidates": [
                        {
                            "name": "Widget MAUs",
                            "why_company_specific": "Operational KPI disclosed by management.",
                            "what_it_measures": "Monthly active widget usage.",
                            "how_calculated_or_defined": "Not explicitly defined in this excerpt.",
                            "most_recent_value": "1,234",
                            "period": "Q4",
                            "unit": "widgets",
                            "evidence": [
                                {
                                    "page": 1,
                                    "quote": "Widget MAUs were 1,234 in Q4.",
                                    "type": "value",
                                }
                            ],
                        }
                    ],
                    "failure_reason": None,
                }
            )

        raise AssertionError(f"Unexpected stage_name: {stage_name}")

    def stream_generate_content(
        self,
        prompt: str,
        *,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (prompt, generation_config_override)

        # Text pipeline Pass 1 (Discovery) — return no metrics so it fails fast.
        if "KPI Pass 1 (Discovery)" in stage_name:
            return json.dumps({"metrics": []})

        # Evidence pipeline Pass 2 — select the KPI when candidates exist.
        if "KPI Evidence Pass 2" in stage_name:
            return json.dumps(
                {
                    "selected_kpi": {
                        "name": "Widget MAUs",
                        "why_company_specific": "Operational KPI disclosed by management.",
                        "what_it_measures": "Monthly active widget usage.",
                        "how_calculated_or_defined": "Not explicitly defined in this excerpt.",
                        "most_recent_value": "1,234",
                        "period": "Q4",
                        "unit": "widgets",
                        "evidence": [
                            {"page": 1, "quote": "Widget MAUs were 1,234 in Q4.", "type": "value"}
                        ],
                        "confidence": 0.9,
                    },
                    "failure_reason": None,
                }
            )

        raise AssertionError(f"Unexpected stage_name: {stage_name}")


class _DummySettings:
    openai_api_key = "test"


@pytest.mark.anyio
async def test_spotlight_service_uses_edgar_fallback_when_pass1_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTLIGHT_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("SPOTLIGHT_ALLOW_NETWORK", "1")
    monkeypatch.setenv("SPOTLIGHT_EDGAR_ARTIFACT_FALLBACK", "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    original_path = Path(tmp_path) / "filing.html"
    original_path.write_text("MANAGEMENT DISCUSSION & ANALYSIS\nNo KPI here.\n", encoding="utf-8")

    def fake_download_filing(url: str, output_path: str, **_kwargs: Any) -> bool:  # noqa: ARG001
        Path(output_path).write_text(
            "OPERATING METRICS\nWidget MAUs were 1,234 in Q4.\n", encoding="utf-8"
        )
        return True

    fake_client = _FakeGeminiClientForEdgarFallback()
    monkeypatch.setattr(spotlight_service, "get_gemini_client", lambda: fake_client)
    monkeypatch.setattr(spotlight_service, "download_filing", fake_download_filing)

    filing = {
        "id": "spotlight-edgar-fallback",
        "filing_type": "10-Q",
        "filing_date": "2026-01-01",
        "period_end": "2025-12-31",
        "source_doc_url": "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/form10q.htm",
        "local_document_path": str(original_path),
    }
    company = {"id": "c1", "name": "Example Corp", "ticker": "EX"}

    payload = await spotlight_service.build_spotlight_payload_for_filing(
        "spotlight-edgar-fallback",
        filing=filing,
        company=company,
        local_document_path=original_path,
        settings=_DummySettings(),
        context_source="fallback",
        debug=True,
    )

    assert payload.get("status") == "ok"
    kpi = payload.get("company_kpi")
    assert isinstance(kpi, dict)
    assert kpi.get("name") == "Widget MAUs"
    assert float(kpi.get("value")) == 1234.0

    dbg = payload.get("debug") or {}
    assert dbg.get("edgar_fallback_attempted") is True
