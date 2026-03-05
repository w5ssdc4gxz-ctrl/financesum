from types import SimpleNamespace

from app.api import filings as filings_api


def test_persona_id_access_is_backwards_compatible_when_missing() -> None:
    preferences = SimpleNamespace(investor_focus="Long-term durability")
    persona_id = str(getattr(preferences, "persona_id", "") or "").strip().lower()
    assert persona_id == ""


def test_section_weight_overrides_access_is_backwards_compatible_when_missing() -> None:
    preferences = SimpleNamespace(target_length=900)
    user_weight_overrides = (
        getattr(preferences, "section_weight_overrides", None) if preferences else None
    )
    if not isinstance(user_weight_overrides, dict) or not user_weight_overrides:
        user_weight_overrides = None

    budgets = filings_api._calculate_section_word_budgets(
        900,
        include_health_rating=False,
        weight_overrides=user_weight_overrides,
    )
    assert isinstance(budgets, dict)
    assert budgets.get("Executive Summary", 0) > 0
    assert budgets.get("Risk Factors", 0) > 0
