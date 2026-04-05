from app.api import filings as filings_api


class _StubGeminiClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: int = 0

    def stream_generate_content(self, prompt: str, **_kwargs) -> str:  # noqa: ARG002
        self.calls += 1
        if not self._responses:
            raise AssertionError("No stub response remaining")
        return self._responses.pop(0)


def test_quality_control_single_shot_does_not_retry_with_rewrite_client() -> None:
    draft = _StubGeminiClient(["bad output"])
    rewrite = _StubGeminiClient(["good output"])

    def _validator(text: str):
        return "Bad output detected" if "bad output" in (text or "") else None

    result = filings_api._generate_summary_with_quality_control(
        draft,
        base_prompt="prompt",
        target_length=None,
        quality_validators=[_validator],
        rewrite_client=rewrite,
        filing_id="test-filing",
        timeout_seconds=5,
    )

    assert result == "bad output"
    assert draft.calls == 1
    assert rewrite.calls == 0


def test_quality_control_target_length_one_shot_disables_regeneration_and_rewrites() -> None:
    client = _StubGeminiClient(["short output"])
    rewrite_calls = {"count": 0}

    def _validator(text: str):
        return "force rewrite" if "short output" in (text or "") else None

    def _unexpected_rewrite(*_args, **_kwargs):
        rewrite_calls["count"] += 1
        return "rewritten output", (2, 10)

    original_rewrite = filings_api._rewrite_summary_to_length
    try:
        filings_api._rewrite_summary_to_length = _unexpected_rewrite
        out = filings_api._generate_summary_with_quality_control(
            client,
            base_prompt="prompt",
            target_length=300,
            quality_validators=[_validator],
            allow_llm_rewrites=False,
            generation_stats={"fast_summary_mode": False},
            filing_id="test-filing",
            timeout_seconds=5,
        )
    finally:
        filings_api._rewrite_summary_to_length = original_rewrite

    assert out
    assert client.calls == 1
    assert rewrite_calls["count"] == 0
