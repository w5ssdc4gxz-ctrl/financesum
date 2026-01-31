import json

from app.services.spotlight_kpi.gemini_flash import extract_spotlight_kpis_via_gemini_flash
from app.services.spotlight_kpi.context import build_operational_spotlight_context
from app.services.spotlight_kpi.json_parse import parse_json_object
from app.services.spotlight_kpi.ranker import pick_best_spotlight_kpi


class DummyGeminiClient:
    def __init__(
        self,
        *,
        pass4_status: str = "approved",
        value_in_pass3=680,
        pass3_excerpt: str = "We ended the quarter with 250 million Monthly Active Users (MAUs).",
    ):
        self.pass4_status = pass4_status
        self.value_in_pass3 = value_in_pass3
        self.pass3_excerpt = pass3_excerpt
        self.calls = []

    def stream_generate_content(
        self,
        prompt: str,
        progress_callback=None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context=None,
        generation_config_override=None,
        timeout_seconds=None,
        retry: bool = True,
    ) -> str:
        _ = (
            progress_callback,
            expected_tokens,
            use_persona_model,
            usage_context,
            generation_config_override,
            timeout_seconds,
            retry,
        )
        self.calls.append(stage_name)
        assert "STRICT JSON" in prompt

        if "Pass 1" in stage_name:
            return json.dumps(
                {
                    "metrics": [
                        {
                            "name_as_written": "Monthly Active Users (MAUs)",
                            "definition_or_context": None,
                            "excerpt": "We ended the quarter with 250 million Monthly Active Users (MAUs).",
                        },
                        {
                            "name_as_written": "Total revenue",
                            "definition_or_context": None,
                            "excerpt": "Total revenue was $9.0B.",
                        },
                    ]
                }
            )
        if "Pass 2" in stage_name:
            return json.dumps(
                {
                    "kept": [
                        {
                            "name_as_written": "Monthly Active Users (MAUs)",
                            "reason_kept": "Operational metric tied to the customer base.",
                            "excerpt": "We ended the quarter with 250 million Monthly Active Users (MAUs).",
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
                        "kpi_name": "Monthly Active Users (MAUs)",
                        "value": self.value_in_pass3,
                        "unit": "users",
                        "what_it_measures": "Customer base scale.",
                        "why_it_represents_this_company": "Core business driver.",
                        "why_not_generic": "Explicit operational KPI in filing.",
                        "scores": {
                            "uniqueness": 3,
                            "representativeness": 5,
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


def test_parse_json_object_strips_code_fences():
    data = parse_json_object(
        "```json\n{ \"candidates\": [{\"name\":\"Customers\",\"value\": 1, \"source_quote\":\"x\"}] }\n```"
    )
    assert isinstance(data, dict)
    assert "candidates" in data


def test_parse_json_object_prefers_first_balanced_object_when_trailing_braces_exist():
    data = parse_json_object(
        'Here is the payload:\\n{"ok": true, "value": 1, "msg": "x"}\\nNote: {not json}'
    )
    assert isinstance(data, dict)
    assert data.get("ok") is True
    assert data.get("value") == 1


def test_parse_json_object_handles_multiple_json_objects_back_to_back():
    data = parse_json_object('{"first": 1}{"second": 2}')
    assert isinstance(data, dict)
    assert data.get("first") == 1


def test_ranker_rejects_mix_and_prefers_operational_kpi():
    context = (
        "We ended the quarter with 250 million Monthly Active Users (MAUs).\n"
        "Revenue by segment: Product A $100, Product B $50.\n"
    )
    candidates = [
        {
            "name": "Revenue Mix",
            "value": 150,
            "unit": "$",
            "chart_type": "donut",
            "source_quote": "Revenue by segment: Product A $100, Product B $50.",
            "ban_flags": [],
        },
        {
            "name": "Monthly Active Users (MAUs)",
            "value": 250000000,
            "unit": "users",
            "chart_type": "metric",
            "source_quote": "We ended the quarter with 250 million Monthly Active Users (MAUs).",
            "ban_flags": [],
        },
    ]
    best = pick_best_spotlight_kpi(candidates, context_text=context)
    assert best is not None
    assert "Monthly Active Users" in best["name"]
    assert best["value"] == 250000000.0


def test_gemini_flash_extractor_requires_quote_in_context():
    context = "We ended the quarter with 250 million Monthly Active Users (MAUs).\n"
    fake = DummyGeminiClient(pass3_excerpt="NOT IN TEXT")
    out = extract_spotlight_kpis_via_gemini_flash(
        fake,
        company_name="Example Corp",
        context_text=context,
        summary_snippet="",
        candidate_quotes=[],
        token_budget=None,
    )
    assert out == []


def test_gemini_flash_extractor_picks_best_candidate():
    context = (
        "We ended the quarter with 250 million Monthly Active Users (MAUs).\n"
        "Revenue by segment: Product A $100, Product B $50.\n"
    )
    fake = DummyGeminiClient()
    out = extract_spotlight_kpis_via_gemini_flash(
        fake,
        company_name="Example Corp",
        context_text=context,
        summary_snippet="",
        candidate_quotes=[],
        token_budget=None,
    )
    assert out and "Monthly Active Users" in out[0]["name"]


def test_ranker_allows_segment_mix_when_no_operational_kpi_exists():
    context = "Revenue by segment: Product A $100, Product B $50.\n"
    candidates = [
        {
            "name": "Segment Revenue",
            "value": 150,
            "unit": "$",
            "chart_type": "donut",
            "segments": [{"label": "Product A", "value": 100}, {"label": "Product B", "value": 50}],
            "source_quote": "Revenue by segment: Product A $100, Product B $50.",
            "ban_flags": [],
        }
    ]
    best = pick_best_spotlight_kpi(candidates, context_text=context)
    assert best is not None
    assert best["chart_type"] == "donut"
    assert isinstance(best.get("segments"), list)


def test_operational_context_excludes_obligations_lines():
    text = (
        "We ended the quarter with 250 million Monthly Active Users (MAUs).\n"
        "Content obligations were $24.04 billion.\n"
        "Revenue was $9.0B.\n"
    )
    ctx = build_operational_spotlight_context(text, max_chars=50_000)
    assert "Monthly Active Users" in ctx
    assert "Content obligations" not in ctx


def test_gemini_flash_extractor_rejects_when_value_missing():
    context = "We ended the quarter with 250 million Monthly Active Users (MAUs).\n"
    fake = DummyGeminiClient(value_in_pass3=None)
    out = extract_spotlight_kpis_via_gemini_flash(
        fake,
        company_name="Example Corp",
        context_text=context,
        summary_snippet="",
        candidate_quotes=[],
        token_budget=None,
    )
    assert out and "Monthly Active Users" in out[0]["name"]
    assert float(out[0]["value"]) == 250000000.0
