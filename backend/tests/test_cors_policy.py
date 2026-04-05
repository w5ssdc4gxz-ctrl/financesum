from fastapi.testclient import TestClient

from app.main import app


def _preflight(origin: str):
    client = TestClient(app)
    return client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_cors_preflight_allows_financesums_origin():
    origin = "https://financesums.com"
    response = _preflight(origin)

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_allows_tagged_cloud_run_frontend_origin():
    origin = "https://canary---financesums-frontend-xbw6ttfgka-ew.a.run.app"
    response = _preflight(origin)

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_rejects_untrusted_origin():
    response = _preflight("https://evil.example")

    assert response.status_code in (200, 400)
    assert response.headers.get("access-control-allow-origin") is None
