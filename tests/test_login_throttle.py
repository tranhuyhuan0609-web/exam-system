"""LoginThrottle (unit) and the 429 on POST /api/v1/auth/login."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.platform.login_throttle import LoginThrottle, login_throttle, normalise_email


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_five_failures_for_one_email_block_it_until_the_oldest_leaves_the_window():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(5):
        assert throttle.check("1.1.1.1", "a@x.test").allowed
        throttle.record_failure("1.1.1.1", "a@x.test")
        clock.now += 10
    blocked = throttle.check("9.9.9.9", "  A@X.test ")  # another address, same e-mail once normalised
    assert not blocked.allowed and 200 < blocked.retry_after_seconds <= 301
    assert throttle.check("9.9.9.9", "b@x.test").allowed, "another e-mail is not affected"
    clock.now += 300
    assert throttle.check("9.9.9.9", "a@x.test").allowed


def test_ten_failures_from_one_address_block_it_whatever_the_email():
    throttle = LoginThrottle(clock=Clock())
    for i in range(10):
        throttle.record_failure("2.2.2.2", f"user{i}@x.test")
    assert not throttle.check("2.2.2.2", "fresh@x.test").allowed
    assert throttle.check("3.3.3.3", "fresh@x.test").allowed


def test_a_success_clears_only_the_email_and_idle_keys_do_not_pile_up():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        throttle.record_failure("1.1.1.1", "a@x.test")
    throttle.clear_email("A@x.test")
    assert throttle.check("9.9.9.9", "a@x.test").allowed, "the e-mail starts again"
    assert len(throttle._by_ip["1.1.1.1"]) == 4, "the shared address keeps the failures made from it"
    for _ in range(6):
        throttle.record_failure("1.1.1.1", "other@x.test")
    assert not throttle.check("1.1.1.1", "fresh@x.test").allowed, "ten failures from the address still block it"
    throttle.record_failure("4.4.4.4", "z@x.test")
    clock.now += 301
    throttle.check("4.4.4.4", "z@x.test")
    assert "4.4.4.4" not in throttle._by_ip and "z@x.test" not in throttle._by_email


def test_the_email_is_normalised_like_the_blind_index():
    assert normalise_email(" Ｔeacher@H2EDU.demo ") == "teacher@h2edu.demo"


@pytest.fixture
def clean_throttle():
    login_throttle._by_ip.clear()
    login_throttle._by_email.clear()
    yield
    login_throttle._by_ip.clear()
    login_throttle._by_email.clear()


def test_the_sixth_wrong_password_is_a_429_problem_with_retry_after(clean_throttle):
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    body = {"email": f"nobody-{uuid.uuid4().hex[:6]}@x.test", "password": "wrong-wrong-1"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
    blocked = client.post("/api/v1/auth/login", json=body)
    assert blocked.status_code == 429, blocked.text
    assert blocked.headers["content-type"].startswith("application/problem+json")
    assert blocked.json()["error_code"] == "RATE_LIMIT_EXCEEDED" and int(blocked.headers["retry-after"]) > 0


def test_a_valid_sign_in_does_not_reset_the_failures_other_people_made_from_the_same_address(clean_throttle):
    """A school behind one NAT: a teacher signing in correctly must not give an attacker a fresh budget."""
    import asyncio

    from app.ops.seed_demo import DEFAULT_PASSWORD, seed_demo

    asyncio.run(seed_demo())
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("203.0.113.88", 40000))

    def fail() -> int:
        body = {"email": f"nobody-{uuid.uuid4().hex[:8]}@x.test", "password": "wrong-wrong-1"}
        return client.post("/api/v1/auth/login", json=body).status_code

    assert [fail() for _ in range(9)] == [401] * 9
    good = {"email": "teacher@h2edu.demo", "password": DEFAULT_PASSWORD, "tenant_slug": "h2edu-demo"}
    assert client.post("/api/v1/auth/login", json=good).status_code == 200
    assert fail() == 401, "the tenth failure from the address"
    assert fail() == 429, "the address is blocked: the success did not wipe its nine earlier failures"


def test_a_valid_sign_in_does_clear_the_failures_of_its_own_email(clean_throttle):
    import asyncio

    from app.ops.seed_demo import DEFAULT_PASSWORD, seed_demo

    asyncio.run(seed_demo())
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("203.0.113.89", 40000))
    wrong = {"email": "teacher@h2edu.demo", "password": "wrong-wrong-1", "tenant_slug": "h2edu-demo"}
    good = {**wrong, "password": DEFAULT_PASSWORD}
    for _ in range(4):
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert client.post("/api/v1/auth/login", json=good).status_code == 200
    for _ in range(4):  # the e-mail starts again from zero: four more failures do not block it
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert client.post("/api/v1/auth/login", json=good).status_code == 200
