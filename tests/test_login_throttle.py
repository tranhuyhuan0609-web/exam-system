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


def test_a_success_clears_the_counters_and_idle_keys_do_not_pile_up():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        throttle.record_failure("1.1.1.1", "a@x.test")
    throttle.clear("1.1.1.1", "a@x.test")
    assert throttle.check("1.1.1.1", "a@x.test").allowed
    throttle.record_failure("4.4.4.4", "z@x.test")
    clock.now += 301
    throttle.check("4.4.4.4", "z@x.test")
    assert not throttle._by_ip and not throttle._by_email


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
