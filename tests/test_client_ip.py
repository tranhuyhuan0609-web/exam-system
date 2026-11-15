"""resolve_client_ip: X-Forwarded-For is believed only through trusted proxies, and the sign-in route obeys it."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.platform.client_ip import UNKNOWN_CLIENT, parse_trusted_proxies, resolve_client_ip
from app.platform.login_throttle import login_throttle


def test_without_trusted_proxies_the_header_is_never_read():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", "") == "203.0.113.9"
    assert resolve_client_ip("10.0.0.1", "1.2.3.4, 5.6.7.8", "") == "10.0.0.1"


def test_a_header_from_an_untrusted_peer_is_ignored():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", "10.0.0.0/8") == "203.0.113.9"


def test_a_trusted_peer_names_the_client_by_the_rightmost_untrusted_hop():
    trusted = "127.0.0.1, ::1, 10.0.0.0/8"
    assert resolve_client_ip("127.0.0.1", "198.51.100.7", trusted) == "198.51.100.7"
    # the client sent "9.9.9.9" itself; the proxy appended the address it really saw
    assert resolve_client_ip("127.0.0.1", "9.9.9.9, 198.51.100.7", trusted) == "198.51.100.7"
    # a load balancer in front of the Next.js server: both hops are ours, the client is the first outside
    assert resolve_client_ip("127.0.0.1", "9.9.9.9, 198.51.100.7, 10.1.2.3", trusted) == "198.51.100.7"
    assert resolve_client_ip("::1", "198.51.100.7", trusted) == "198.51.100.7"


def test_garbage_in_the_chain_falls_back_to_the_peer_never_to_what_the_client_wrote():
    assert resolve_client_ip("127.0.0.1", "1.2.3.4, not-an-ip", "127.0.0.1") == "127.0.0.1"
    assert resolve_client_ip("127.0.0.1", "", "127.0.0.1") == "127.0.0.1"
    assert resolve_client_ip("127.0.0.1", "127.0.0.1, 127.0.0.1", "127.0.0.1") == "127.0.0.1"


def test_ports_brackets_and_ipv4_mapped_addresses_are_understood():
    assert resolve_client_ip("127.0.0.1", "198.51.100.7:51234", "127.0.0.1") == "198.51.100.7"
    assert resolve_client_ip("127.0.0.1", "[2001:db8::7]:443", "127.0.0.1") == "2001:db8::7"
    assert resolve_client_ip("::ffff:10.0.0.1", "198.51.100.7", "10.0.0.0/8") == "198.51.100.7"


def test_no_peer_is_unknown_and_a_long_chain_is_capped():
    assert resolve_client_ip(None, "1.2.3.4", "127.0.0.1") == UNKNOWN_CLIENT
    assert resolve_client_ip("not-an-ip", None, "") == UNKNOWN_CLIENT
    chain = ", ".join(["8.8.8.8"] + ["127.0.0.1"] * 30)  # the first hop is beyond the 20 entries looked at
    assert resolve_client_ip("127.0.0.1", chain, "127.0.0.1") == "127.0.0.1"


def test_a_malformed_trusted_proxy_entry_is_an_error_not_silently_ignored():
    with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
        parse_trusted_proxies("127.0.0.1, 10.0.0.0/33")
    assert parse_trusted_proxies(" , ") == ()


@pytest.fixture
def clean_throttle():
    login_throttle._by_ip.clear()
    login_throttle._by_email.clear()
    yield
    login_throttle._by_ip.clear()
    login_throttle._by_email.clear()


def _fail(client: TestClient, **headers: str) -> int:
    body = {"email": f"nobody-{uuid.uuid4().hex[:8]}@x.test", "password": "wrong-wrong-1"}
    return client.post("/api/v1/auth/login", json=body, headers=headers).status_code


def test_login_throttle_cannot_be_dodged_by_forging_x_forwarded_for(clean_throttle):
    """Default settings: ten failures from one address block it, whatever the header says."""
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("203.0.113.50", 40000))
    for n in range(10):
        assert _fail(client, **{"X-Forwarded-For": f"10.9.9.{n}"}) == 401
    assert _fail(client, **{"X-Forwarded-For": "10.9.9.200"}) == 429


def test_behind_a_trusted_proxy_each_real_client_has_its_own_budget(clean_throttle, monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "127.0.0.1")
    proxy = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 40000))
    for _ in range(10):
        assert _fail(proxy, **{"X-Forwarded-For": "198.51.100.1"}) == 401
    assert _fail(proxy, **{"X-Forwarded-For": "198.51.100.1"}) == 429
    assert _fail(proxy, **{"X-Forwarded-For": "198.51.100.2"}) == 401, "another visitor of the same proxy"
    # a client that prepends its own entries gains nothing: the proxy's appended address is what counts
    assert _fail(proxy, **{"X-Forwarded-For": "7.7.7.7, 198.51.100.1"}) == 429


def test_the_same_header_from_a_peer_that_is_not_a_trusted_proxy_is_ignored(clean_throttle, monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "127.0.0.1")
    outsider = TestClient(app, base_url="http://127.0.0.1:8000", client=("203.0.113.77", 40000))
    for n in range(10):
        assert _fail(outsider, **{"X-Forwarded-For": f"198.51.100.{n}"}) == 401
    assert _fail(outsider, **{"X-Forwarded-For": "198.51.100.250"}) == 429
