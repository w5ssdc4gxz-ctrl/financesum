from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.services.spotlight_kpi.kpi_pipeline_evidence import (
    EvidencePipelineConfig,
    extract_kpi_with_evidence_from_file,
)


class _FakeGeminiEvidenceClient:
    def __init__(self) -> None:
        self.calls = []

    def upload_file_bytes(self, *, data: bytes, mime_type: str, **_kwargs: Any) -> Dict[str, Any]:
        assert data
        assert mime_type
        self.calls.append("upload_file_bytes")
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
        _ = (generation_config_override,)
        assert file_uri
        assert file_mime_type
        assert prompt
        self.calls.append(stage_name or "pass1")

        # Pass 1 returns candidates with evidence, but (intentionally) mislabels the types
        # so the backend must infer value/definition from the quote content.
        return json.dumps(
            {
                "candidates": [
                    {
                        "name": "Widget MAUs",
                        "why_company_specific": "Management-defined metric for widget engagement.",
                        "what_it_measures": "Monthly active widget usage.",
                        "how_calculated_or_defined": "Defined as widgets active at least once per month.",
                        "most_recent_value": "1,234",
                        "period": "Q4",
                        "unit": "widgets",
                        "evidence": [
                            {
                                "page": 1,
                                "quote": (
                                    "We define Widget MAUs as the number of widgets active at least once in the month. "
                                    "Widget MAUs were 1,234 in Q4."
                                ),
                                "type": "definition",
                            }
                        ],
                    }
                ],
                "failure_reason": None,
            }
        )

    def stream_generate_content(
        self,
        prompt: str,
        *,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (prompt, generation_config_override)
        self.calls.append(stage_name or "pass2")

        # Pass 2 selects the KPI but again provides only a definition-labeled evidence quote.
        return json.dumps(
            {
                "selected_kpi": {
                    "name": "Widget MAUs",
                    "why_company_specific": "Management-defined engagement KPI for widgets.",
                    "what_it_measures": "Monthly active widget usage.",
                    "how_calculated_or_defined": "Defined as widgets active at least once per month.",
                    "most_recent_value": "1,234",
                    "period": "Q4",
                    "unit": "widgets",
                    "evidence": [
                        {
                            "page": 1,
                            "quote": (
                                "We define Widget MAUs as the number of widgets active at least once in the month. "
                                "Widget MAUs were 1,234 in Q4."
                            ),
                            "type": "definition",
                        }
                    ],
                    "confidence": 0.9,
                },
                "failure_reason": None,
            }
        )


def test_evidence_pipeline_infers_value_evidence_from_numeric_definition_quote():
    client = _FakeGeminiEvidenceClient()
    config = EvidencePipelineConfig(total_timeout_seconds=20.0)
    doc_text = (
        "We define Widget MAUs as the number of widgets active at least once in the month. "
        "Widget MAUs were 1,234 in Q4."
    )

    candidate, debug = extract_kpi_with_evidence_from_file(
        client,
        file_bytes=doc_text.encode("utf-8"),
        company_name="Example Corp",
        mime_type="text/plain",
        config=config,
    )

    assert candidate is not None, debug
    assert candidate.get("name") == "Widget MAUs"
    assert float(candidate.get("value")) == 1234.0

    evidence = candidate.get("evidence")
    assert isinstance(evidence, list) and evidence, candidate
    types = {str(ev.get("type")) for ev in evidence if isinstance(ev, dict)}
    assert "definition" in types
    assert "value" in types  # duplicated/inferred from numeric quote

    assert "[p. 1]" in str(candidate.get("source_quote") or "")


class _FakeGeminiBannedMetricClient(_FakeGeminiEvidenceClient):
    def stream_generate_content_with_file_uri(  # type: ignore[override]
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (generation_config_override,)
        assert file_uri
        assert file_mime_type
        assert prompt
        self.calls.append(stage_name or "pass1")

        return json.dumps(
            {
                "candidates": [
                    {
                        "name": "Excess tax benefits on stock-based compensation",
                        "why_company_specific": "Accounting disclosure for the quarter.",
                        "what_it_measures": "Tax effects related to stock-based compensation.",
                        "how_calculated_or_defined": "GAAP line item.",
                        "most_recent_value": "42",
                        "period": "Q1 2017",
                        "unit": "",
                        "evidence": [
                            {
                                "page": 1,
                                "quote": "Excess tax benefits on stock-based compensation were 42 in Q1 2017.",
                                "type": "value",
                            }
                        ],
                    }
                ],
                "failure_reason": None,
            }
        )


def test_evidence_pipeline_rejects_gaap_line_item_kpis():
    client = _FakeGeminiBannedMetricClient()
    config = EvidencePipelineConfig(total_timeout_seconds=20.0)
    doc_text = "Excess tax benefits on stock-based compensation were 42 in Q1 2017."

    candidate, debug = extract_kpi_with_evidence_from_file(
        client,
        file_bytes=doc_text.encode("utf-8"),
        company_name="Example Corp",
        mime_type="text/plain",
        config=config,
    )

    assert candidate is None
    assert debug.get("reason") in {"pass1_no_valid_candidates", "selected_kpi_banned_or_missing_name"}


class _FakeGeminiPass2InventsBannedClient(_FakeGeminiEvidenceClient):
    def stream_generate_content_with_file_uri(  # type: ignore[override]
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (generation_config_override,)
        assert file_uri
        assert file_mime_type
        assert prompt
        self.calls.append(stage_name or "pass1")

        return json.dumps(
            {
                "candidates": [
                    {
                        "name": "Excess tax benefits on stock-based compensation",
                        "why_company_specific": "Accounting disclosure for the quarter.",
                        "what_it_measures": "Tax effects related to stock-based compensation.",
                        "how_calculated_or_defined": "GAAP line item.",
                        "most_recent_value": "42",
                        "period": "Q1 2017",
                        "unit": "",
                        "evidence": [
                            {
                                "page": 1,
                                "quote": "Excess tax benefits on stock-based compensation were 42 in Q1 2017.",
                                "type": "value",
                            }
                        ],
                    },
                    {
                        "name": "Widget MAUs",
                        "why_company_specific": "Management-defined metric for widget engagement.",
                        "what_it_measures": "Monthly active widget usage.",
                        "how_calculated_or_defined": "Defined as widgets active at least once per month.",
                        "most_recent_value": "1,234",
                        "period": "Q4",
                        "unit": "widgets",
                        "evidence": [
                            {
                                "page": 1,
                                "quote": (
                                    "We define Widget MAUs as the number of widgets active at least once in the month. "
                                    "Widget MAUs were 1,234 in Q4."
                                ),
                                "type": "definition",
                            }
                        ],
                    },
                ],
                "failure_reason": None,
            }
        )

    def stream_generate_content(  # type: ignore[override]
        self,
        prompt: str,
        *,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (prompt, generation_config_override)
        self.calls.append(stage_name or "pass2")

        # Pass 2 violates instructions and selects a banned GAAP line item.
        return json.dumps(
            {
                "selected_kpi": {
                    "name": "Excess tax benefits on stock-based compensation",
                    "why_company_specific": "This is disclosed in the filing.",
                    "what_it_measures": "Tax effects.",
                    "how_calculated_or_defined": "GAAP line item.",
                    "most_recent_value": "42",
                    "period": "Q1 2017",
                    "unit": "",
                    "evidence": [
                        {
                            "page": 1,
                            "quote": "Excess tax benefits on stock-based compensation were 42 in Q1 2017.",
                            "type": "value",
                        }
                    ],
                    "confidence": 0.95,
                },
                "failure_reason": None,
            }
        )


def test_evidence_pipeline_falls_back_when_pass2_selects_banned_metric():
    client = _FakeGeminiPass2InventsBannedClient()
    config = EvidencePipelineConfig(total_timeout_seconds=20.0)
    doc_text = (
        "Excess tax benefits on stock-based compensation were 42 in Q1 2017.\n"
        "We define Widget MAUs as the number of widgets active at least once in the month. Widget MAUs were 1,234 in Q4."
    )

    candidate, debug = extract_kpi_with_evidence_from_file(
        client,
        file_bytes=doc_text.encode("utf-8"),
        company_name="Example Corp",
        mime_type="text/plain",
        config=config,
    )

    assert candidate is not None, debug
    assert candidate.get("name") == "Widget MAUs"
    assert debug.get("fallback_used") == "pass1_candidate"


class _FakeGeminiOnlyGenericCandidates:
    def __init__(self) -> None:
        self.calls = []

    def upload_file_bytes(self, *, data: bytes, mime_type: str, **_kwargs: Any) -> Dict[str, Any]:
        assert data
        assert mime_type
        self.calls.append("upload_file_bytes")
        return {"uri": "files/FAKE", "mimeType": mime_type}

    def stream_generate_content_with_file_uri(  # type: ignore[override]
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (generation_config_override,)
        assert file_uri
        assert file_mime_type
        assert prompt
        self.calls.append(stage_name or "pass1")
        # Only generic financial metrics -> should be filtered out.
        return json.dumps(
            {
                "candidates": [
                    {
                        "name": "Total revenue",
                        "why_company_specific": "Generic financial statement line item.",
                        "what_it_measures": "Revenue.",
                        "how_calculated_or_defined": "GAAP.",
                        "most_recent_value": "$10.0 million",
                        "period": "Q1",
                        "unit": "$",
                        "evidence": [
                            {"page": 1, "quote": "Total revenue was $10.0 million.", "type": "value"}
                        ],
                    }
                ],
                "failure_reason": None,
            }
        )

    def stream_generate_content(self, *args: Any, **kwargs: Any) -> str:  # noqa: ARG002
        raise AssertionError("Pass 2 should not run when all Pass 1 candidates are generic.")


def test_evidence_pipeline_page_regex_fallback_when_pass1_only_generic_candidates():
    client = _FakeGeminiOnlyGenericCandidates()
    config = EvidencePipelineConfig(total_timeout_seconds=20.0)
    doc_text = "Backlog was $1.2 million as of December 31.\nTotal revenue was $10.0 million."

    candidate, debug = extract_kpi_with_evidence_from_file(
        client,
        file_bytes=doc_text.encode("utf-8"),
        company_name="Example Corp",
        mime_type="text/plain",
        config=config,
    )

    assert candidate is not None, debug
    assert candidate.get("name") == "Backlog"
    assert float(candidate.get("value")) == 1_200_000.0
    assert debug.get("fallback_used") == "regex_page"
    assert debug.get("fallback_reason") == "pass1_no_valid_candidates"


class _FakeGeminiNoCandidates:
    def __init__(self) -> None:
        self.calls = []

    def upload_file_bytes(self, *, data: bytes, mime_type: str, **_kwargs: Any) -> Dict[str, Any]:
        assert data
        assert mime_type
        self.calls.append("upload_file_bytes")
        return {"uri": "files/FAKE", "mimeType": mime_type}

    def stream_generate_content_with_file_uri(  # type: ignore[override]
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (generation_config_override,)
        assert file_uri
        assert file_mime_type
        assert prompt
        self.calls.append(stage_name or "pass1")
        return json.dumps({"candidates": [], "failure_reason": "no_operating_metrics_found"})

    def stream_generate_content(
        self,
        prompt: str,
        *,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        _ = (prompt, generation_config_override)
        self.calls.append(stage_name or "pass_text")
        # Pass 1 text fallback also returns no candidates. Pass 2 should not run.
        if "Pass 1" in (stage_name or ""):
            return json.dumps({"candidates": [], "failure_reason": "no_operating_metrics_found"})
        raise AssertionError(f"Unexpected stage: {stage_name}")


def test_evidence_pipeline_page_regex_fallback_when_pass1_returns_no_candidates():
    client = _FakeGeminiNoCandidates()
    config = EvidencePipelineConfig(total_timeout_seconds=20.0)
    doc_text = "Backlog was $1.2 million as of December 31."

    candidate, debug = extract_kpi_with_evidence_from_file(
        client,
        file_bytes=doc_text.encode("utf-8"),
        company_name="Example Corp",
        mime_type="text/plain",
        config=config,
    )

    assert candidate is not None, debug
    assert candidate.get("name") == "Backlog"
    assert float(candidate.get("value")) == 1_200_000.0
    assert debug.get("fallback_used") == "regex_page"
    assert debug.get("fallback_reason") == "pass1_no_candidates"
