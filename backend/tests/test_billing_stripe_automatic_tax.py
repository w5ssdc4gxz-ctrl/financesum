import types

from fastapi.testclient import TestClient

from app.api import billing as billing_api
from app.main import app


def test_checkout_session_forces_automatic_tax_disabled(monkeypatch):
    """Billing must always disable Stripe automatic tax while we are not collecting taxes."""

    # Ensure billing endpoints don't error on missing Stripe config.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_dummy")

    captured: dict = {}

    async def _fake_resolve_customer_id_for_user(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        billing_api,
        "_resolve_stripe_customer_id_for_user",
        _fake_resolve_customer_id_for_user,
    )

    def _fake_checkout_session_create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(id="cs_test_123", url="https://example.invalid/checkout")

    monkeypatch.setattr(
        billing_api.stripe.checkout.Session,
        "create",
        _fake_checkout_session_create,
    )

    client = TestClient(app)
    response = client.post("/api/v1/billing/create-checkout-session", json={"plan": "pro"})
    assert response.status_code == 200

    assert captured.get("automatic_tax") == {"enabled": False}
