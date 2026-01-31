import json

from app.services.spotlight_kpi import pdf_pipeline
from app.services.spotlight_kpi.pdf_pipeline import (
    extract_company_specific_spotlight_kpi_from_pdf,
)


def _make_test_pdf_bytes() -> bytes:
    # A tiny dummy byte-string; the pipeline test forces `fitz=None` so no parsing occurs.
    return b"%PDF-1.4\n% test\n%%EOF\n"


class DummyGeminiClient:
    def __init__(
        self,
        *,
        pass4_status: str = "approved",
        value_in_pass3=1234,
        pass3_excerpt: str = "Paid subscribers with ≥3 devices connected: 1,234",
    ):
        self.pass4_status = pass4_status
        self.value_in_pass3 = value_in_pass3
        self.pass3_excerpt = pass3_excerpt
        self.calls = []

    def upload_file_bytes(
        self, *, data: bytes, mime_type: str, display_name=None, timeout_seconds=None
    ):
        assert mime_type == "application/pdf"
        assert data and isinstance(data, (bytes, bytearray))
        return {"uri": "files/test.pdf", "mimeType": "application/pdf"}

    def stream_generate_content_with_file_uri(
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        progress_callback=None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context=None,
        generation_config_override=None,
    ) -> str:
        self.calls.append(stage_name)
        assert file_uri
        assert file_mime_type == "application/pdf"
        assert "STRICT JSON" in prompt

        if "Pass 1" not in stage_name:
            raise AssertionError("Only Pass 1 should include the PDF file")

        return json.dumps(
            {
                "metrics": [
                    {
                        "name_as_written": "Paid subscribers with ≥3 devices connected",
                        "definition_or_context": None,
                        "page_ref": "original_page_1",
                        "excerpt": "Paid subscribers with ≥3 devices connected: 1,234",
                    },
                    {
                        "name_as_written": "Total revenue",
                        "definition_or_context": None,
                        "page_ref": "original_page_2",
                        "excerpt": "Total revenue: $9,999",
                    },
                ]
            }
        )

    def stream_generate_content(
        self,
        prompt: str,
        progress_callback=None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context=None,
        generation_config_override=None,
    ) -> str:
        self.calls.append(stage_name)
        assert "STRICT JSON" in prompt

        if "Pass 2" in stage_name:
            return json.dumps(
                {
                    "kept": [
                        {
                            "name_as_written": "Paid subscribers with ≥3 devices connected",
                            "reason_kept": "Management-defined usage KPI specific to the product experience.",
                            "page_ref": "original_page_1",
                            "excerpt": "Paid subscribers with ≥3 devices connected: 1,234",
                        }
                    ],
                    "removed": [
                        {
                            "name_as_written": "Total revenue",
                            "reason_removed": "Generic accounting line item.",
                        }
                    ],
                }
            )
        if "Pass 3" in stage_name:
            return json.dumps(
                {
                    "company_specific_kpi": {
                        "kpi_name": "Paid subscribers with ≥3 devices connected",
                        "value": self.value_in_pass3,
                        "unit": "subscribers",
                        "page_ref": "original_page_1",
                        "what_it_measures": "High-intent subscriber cohort with multi-device usage.",
                        "why_it_represents_this_company": "Captures sticky product engagement tied to the business model.",
                        "why_not_generic": "Highly specific cohort definition.",
                        "scores": {
                            "uniqueness": 5,
                            "representativeness": 4,
                            "signal_quality": 4,
                        },
                        "supporting_excerpt": self.pass3_excerpt,
                    },
                    "fallback_if_none": {"company_specific_kpi": None, "reason": None},
                }
            )
        if "Pass 4" in stage_name:
            return json.dumps(
                {"status": self.pass4_status, "reason": "ok", "confidence": 0.9}
            )
        raise AssertionError(f"Unexpected stage_name: {stage_name}")


def test_pdf_pipeline_happy_path(monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "fitz", None, raising=False)
    pdf_bytes = _make_test_pdf_bytes()
    client = DummyGeminiClient()
    kpi, debug = extract_company_specific_spotlight_kpi_from_pdf(
        client, pdf_bytes=pdf_bytes, company_name="TestCo"
    )
    assert kpi is not None
    assert kpi["name"] == "Paid subscribers with ≥3 devices connected"
    assert kpi["value"] == 1234.0
    assert "original_page_1" in kpi["source_quote"]
    assert debug.get("verifier_status") == "approved"


def test_pdf_pipeline_rejected_by_verifier(monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "fitz", None, raising=False)
    pdf_bytes = _make_test_pdf_bytes()
    client = DummyGeminiClient(pass4_status="rejected")
    kpi, debug = extract_company_specific_spotlight_kpi_from_pdf(
        client, pdf_bytes=pdf_bytes, company_name="TestCo"
    )
    assert kpi is None
    assert debug.get("reason") == "verifier_rejected"


def test_pdf_pipeline_requires_numeric_value(monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "fitz", None, raising=False)
    pdf_bytes = _make_test_pdf_bytes()
    client = DummyGeminiClient(value_in_pass3=None, pass3_excerpt="Paid subscribers with three devices connected")
    kpi, debug = extract_company_specific_spotlight_kpi_from_pdf(
        client, pdf_bytes=pdf_bytes, company_name="TestCo"
    )
    assert kpi is None
    assert debug.get("reason") == "kpi_missing_numeric_value"


def test_pdf_pipeline_recovers_numeric_from_excerpt_when_value_missing(monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "fitz", None, raising=False)
    pdf_bytes = _make_test_pdf_bytes()
    client = DummyGeminiClient(value_in_pass3=None, pass3_excerpt="Paid subscribers with ≥3 devices connected: 1,234")
    kpi, debug = extract_company_specific_spotlight_kpi_from_pdf(
        client, pdf_bytes=pdf_bytes, company_name="TestCo"
    )
    assert kpi is not None
    assert kpi["value"] == 1234.0
    assert debug.get("verifier_status") == "approved"
