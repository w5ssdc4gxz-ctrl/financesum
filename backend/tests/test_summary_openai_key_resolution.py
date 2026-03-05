from types import SimpleNamespace

from app.api import filings as filings_api


def test_resolve_summary_openai_api_key_prefers_configured_settings_value() -> None:
    settings = SimpleNamespace(openai_api_key="configured-key")
    assert filings_api._resolve_summary_openai_api_key(settings) == "configured-key"


def test_resolve_summary_openai_api_key_reads_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-runtime  ")
    monkeypatch.delenv("OPENAI-API-KEY", raising=False)
    settings = SimpleNamespace(openai_api_key="")

    resolved = filings_api._resolve_summary_openai_api_key(settings)
    assert resolved == "sk-runtime"
    assert settings.openai_api_key == "sk-runtime"


def test_resolve_summary_openai_api_key_uses_legacy_env_alias(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI-API-KEY", "  legacy-runtime-key  ")
    settings = SimpleNamespace(openai_api_key="")

    resolved = filings_api._resolve_summary_openai_api_key(settings)
    assert resolved == "legacy-runtime-key"
    assert settings.openai_api_key == "legacy-runtime-key"
