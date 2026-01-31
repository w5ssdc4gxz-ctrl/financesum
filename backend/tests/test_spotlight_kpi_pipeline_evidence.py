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

