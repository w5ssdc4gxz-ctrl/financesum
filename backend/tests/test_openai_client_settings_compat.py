from types import SimpleNamespace

from app.services import openai_client


def test_get_openai_client_falls_back_to_defaults_when_retry_settings_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-runtime-key")
    monkeypatch.delenv("OPENAI-API-KEY", raising=False)
    monkeypatch.setattr(openai_client, "get_settings", lambda: SimpleNamespace())

    client = openai_client.get_openai_client(model_name="gpt-5.4-mini")

    assert client.api_key == "sk-runtime-key"
    assert client.max_retries == openai_client.DEFAULT_MAX_RETRIES
    assert client.initial_wait == openai_client.DEFAULT_INITIAL_WAIT
    assert client.max_wait == openai_client.DEFAULT_MAX_WAIT


def test_get_openai_client_prefers_settings_key_and_retry_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    monkeypatch.setattr(
        openai_client,
        "get_settings",
        lambda: SimpleNamespace(
            openai_api_key="sk-settings-key",
            openai_max_retries=5,
            openai_initial_wait=2,
            openai_max_wait=42,
        ),
    )

    client = openai_client.get_openai_client(model_name="gpt-5.4-mini")

    assert client.api_key == "sk-settings-key"
    assert client.max_retries == 5
    assert client.initial_wait == 2
    assert client.max_wait == 42


def test_get_openai_client_uses_legacy_env_alias_when_primary_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI-API-KEY", "sk-legacy-key")
    monkeypatch.setattr(openai_client, "get_settings", lambda: SimpleNamespace())

    client = openai_client.get_openai_client(model_name="gpt-5.4-mini")

    assert client.api_key == "sk-legacy-key"
