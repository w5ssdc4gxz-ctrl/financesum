from fastapi.testclient import TestClient

from app.main import app


def test_security_headers_are_present() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=(), payment=()"


def test_hsts_header_is_present_behind_https_proxy() -> None:
    client = TestClient(app)

    response = client.get("/", headers={"x-forwarded-proto": "https"})

    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
