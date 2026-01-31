from app.services import analysis_fallback
from app.services.gemini_client import GeminiClient


def test_gemini_clamp_tldr_length_max_10_words() -> None:
    client = GeminiClient.__new__(GeminiClient)
    tldr = "one two three four five six seven eight nine ten eleven twelve"
    clamped = client._clamp_tldr_length(tldr, max_words=10)
    assert len(clamped.split()) <= 10


def test_fallback_summary_tldr_max_10_words() -> None:
    summary = analysis_fallback._generate_fallback_summary(
        company_name="Very Long Company Name With Many Words Incorporated",
        ratios={},
        health_score=72.3,
        narrative="Test narrative.",
    )
    assert len(summary["tldr"].split()) <= 10

