"""CSRF middleware: stateless Bearer API calls pass the proxy, cookie/HTML routes keep the Origin check."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, base_url="http://127.0.0.1:8000")
PROXY = {"Origin": "http://localhost:3000", "Host": "127.0.0.1:8000"}


def test_bearer_api_call_passes_the_proxy_origin() -> None:
    r = client.post("/api/v1/attempts/not-a-uuid/submit", headers={**PROXY, "Authorization": "Bearer x"})
    assert r.status_code != 403 or "CSRF" not in r.text


def test_cross_origin_without_bearer_is_blocked() -> None:
    r = client.post("/api/v1/attempts/not-a-uuid/submit", headers=PROXY)
    assert r.status_code == 403 and "CSRF" in r.text


def test_cross_origin_html_route_with_bearer_is_still_blocked() -> None:
    r = client.post("/login", headers={**PROXY, "Authorization": "Bearer x"})
    assert r.status_code == 403 and "CSRF" in r.text


def test_session_cookie_request_keeps_the_origin_check() -> None:
    client.cookies.set("exam_session", "abc")
    try:
        r = client.post("/api/v1/attempts/not-a-uuid/submit", headers={**PROXY, "Authorization": "Bearer x"})
    finally:
        client.cookies.clear()
    assert r.status_code == 403 and "CSRF" in r.text
