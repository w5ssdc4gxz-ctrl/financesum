import json

from app.services import ai_usage


def test_load_pricing_config_defaults_to_gpt52_rates(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_COST_PER_1M_INPUT_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_COST_PER_1M_OUTPUT_TOKENS", raising=False)

    input_rate, output_rate, _target = ai_usage._load_pricing_config()
    assert input_rate == ai_usage.GPT52_INPUT_RATE_PER_M
    assert output_rate == ai_usage.GPT52_OUTPUT_RATE_PER_M


def test_record_ai_usage_uses_token_rate_basis_when_env_rates_unset(
    monkeypatch, tmp_path
) -> None:
    usage_file = tmp_path / "ai_usage.jsonl"
    lock_file = tmp_path / "ai_usage.lock"
    monkeypatch.setattr(ai_usage, "USAGE_LOG_FILE", usage_file)
    monkeypatch.setattr(ai_usage, "USAGE_LOCK_FILE", lock_file)
    monkeypatch.delenv("OPENAI_COST_PER_1M_INPUT_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_COST_PER_1M_OUTPUT_TOKENS", raising=False)

    ai_usage.record_ai_usage(
        prompt="Prompt text",
        response_text="Response text",
        usage_metadata=None,
        model="gpt-5.2",
        usage_context={"request_id": "req-1"},
    )

    lines = usage_file.read_text(encoding="utf-8").splitlines()
    assert lines
    event = json.loads(lines[-1])
    assert event.get("cost_basis") == "per_million_token_rate"
    assert float(event.get("cost_usd") or 0.0) > 0.0
