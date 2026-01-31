from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.spotlight_kpi.kpi_pipeline_v3 import PipelineConfig, extract_kpi_from_file


class _FakeGeminiClientCompat:
    """Fake Gemini client that rejects newer generationConfig fields.

    The v3 KPI pipeline should recover by retrying without unsupported fields.
    """

    def __init__(self) -> None:
        self._calls: Dict[str, int] = {"file": 0, "text": 0}

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
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        assert file_uri
        assert file_mime_type
        assert prompt
        self._calls["file"] += 1

        cfg = generation_config_override or {}
        # First attempt: reject thinkingConfig (simulating older endpoint).
        if self._calls["file"] == 1 and "thinkingConfig" in cfg:
            raise RuntimeError('Invalid JSON payload received. Unknown name "thinkingConfig".')

        return (
            '{"metrics":[{"name_as_written":"Digital Media ARR","definition_or_context":null,'
            '"excerpt":"Digital Media ARR was $1,234 million as of quarter end."}]}'
        )

    def stream_generate_content(
        self,
        prompt: str,
        *,
        stage_name: str = "",
        generation_config_override: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> str:
        assert prompt
        self._calls["text"] += 1
        cfg = generation_config_override or {}

        # Simulate an endpoint that also rejects responseMimeType and thinkingConfig.
        if self._calls["text"] == 1 and (
            "thinkingConfig" in cfg or "responseMimeType" in cfg
        ):
            raise RuntimeError(
                'Invalid JSON payload received. Unknown name "responseMimeType". Unknown name "thinkingConfig".'
            )

        if "KPI Pass 2" in stage_name:
            return (
                '{"kept":[{"name_as_written":"Digital Media ARR","reason_kept":"Company-defined recurring metric",'
                '"excerpt":"Digital Media ARR was $1,234 million as of quarter end."}],'
                '"removed":[]}'
            )
        if "KPI Pass 3" in stage_name:
            return (
                '{"company_specific_kpi":{"kpi_name":"Digital Media ARR","value":null,"unit":"USD",'
                '"what_it_measures":"Recurring revenue run-rate","why_it_represents_this_company":"Core engine metric",'
                '"why_not_generic":"Management-defined ARR","scores":{"uniqueness":4,"representativeness":4,"signal_quality":4},'
                '"supporting_excerpt":"Digital Media ARR was $1,234 million as of quarter end."},'
                '"fallback_if_none":{"company_specific_kpi":null,"reason":null}}'
            )
        if "KPI Pass 4" in stage_name:
            return '{"status":"approved","reason":"Clear metric + numeric evidence","confidence":0.82}'
        return "{}"


def test_kpi_pipeline_v3_retries_without_unsupported_generation_fields():
    client = _FakeGeminiClientCompat()
    candidate, debug = extract_kpi_from_file(
        client,
        file_bytes=b"%PDF-FAKE",
        company_name="Example Corp",
        mime_type="application/pdf",
        config=PipelineConfig(total_timeout_seconds=20.0),
    )

    assert candidate is not None
    assert candidate.get("name") == "Digital Media ARR"
    assert isinstance(candidate.get("value"), float)
    assert candidate.get("value") == 1234_000_000.0  # "1,234 million" -> 1.234B

    removed = debug.get("compat_removed_generation_fields")
    assert isinstance(removed, list)
    assert any("thinkingConfig" in str(item) for item in removed)
