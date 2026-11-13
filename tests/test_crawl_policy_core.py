"""Unit tests for A02 crawl policy, redirect verifier, rate limiter & drift monitor (P12)."""

from __future__ import annotations

import pytest

from app.modules.crawler.public import (
    CrawlPolicyError,
    CrawlSourcePolicy,
    DomainPolitenessLimiter,
    ParserDriftMonitor,
    RobotsPolicy,
    RobotsStatus,
    SSRFDNSRebindingError,
    verify_redirect_chain,
    verify_url_or_redirect_hop,
)


def _policy(**overrides) -> CrawlSourcePolicy:
    defaults = {
        "allowed_domains": frozenset({"vietjack.com", "cdn.vietjack.com"}),
        "rights_status": "LICENSED",
        "allowed_path_prefixes": ("/de-thi/", "/tai-lieu/"),
    }
    defaults.update(overrides)
    return CrawlSourcePolicy(**defaults)


def _robots_map(policy: RobotsPolicy | None = None) -> dict[str, RobotsPolicy]:
    p = policy if policy is not None else RobotsPolicy.not_found()
    return {"vietjack.com": p, "cdn.vietjack.com": p}


def test_accepts_valid_https_443_and_respects_robots_txt():
    robots = RobotsPolicy.with_content("User-agent: *\nDisallow: /private/\nAllow: /de-thi/\n")
    res = verify_url_or_redirect_hop(
        _policy(),
        "https://vietjack.com/de-thi/toan-12.html",
        lambda _h: ["93.184.216.34"],
        robots=robots,
    )
    assert res.host == "vietjack.com"
    assert res.port == 443
    assert res.ssrf_result.pinned_ip == "93.184.216.34"

    with pytest.raises(CrawlPolicyError) as exc_info:
        verify_url_or_redirect_hop(
            _policy(allowed_path_prefixes=("/",)),
            "https://vietjack.com/private/secret.html",
            lambda _h: ["93.184.216.34"],
            robots=robots,
        )
    assert exc_info.value.reason_code == "ROBOTS_TXT_DISALLOWED"

    # Percent-encoded path normalized before robots.can_fetch check.
    with pytest.raises(CrawlPolicyError) as encoded_exc:
        verify_url_or_redirect_hop(
            _policy(allowed_path_prefixes=("/",)),
            "https://vietjack.com/%70rivate/secret.html",
            lambda _h: ["93.184.216.34"],
            robots=robots,
        )
    assert encoded_exc.value.reason_code == "ROBOTS_TXT_DISALLOWED"


@pytest.mark.parametrize(
    ("bad_url", "expected_code"),
    [
        ("http://vietjack.com/de-thi/1", "DISALLOWED_SCHEME"),
        ("ftp://vietjack.com/de-thi/1", "DISALLOWED_SCHEME"),
        ("https://user:pass@vietjack.com/de-thi/1", "USERINFO_NOT_ALLOWED"),
        ("https://admin@vietjack.com/de-thi/1", "USERINFO_NOT_ALLOWED"),
        ("https://93.184.216.34/de-thi/1", "BARE_IP_HOST_NOT_ALLOWED"),
        ("https://[::1]/de-thi/1", "BARE_IP_HOST_NOT_ALLOWED"),
        ("https://evil.example.com/de-thi/1", "CROSS_DOMAIN_REDIRECT_BLOCKED"),
        ("https://vietjack.com:8443/de-thi/1", "DISALLOWED_PORT"),
        ("https://vietjack.com/admin/export", "PATH_NOT_ALLOWED"),
    ],
)
def test_rejects_disallowed_scheme_userinfo_bare_ip_domain_port_and_path(bad_url, expected_code):
    with pytest.raises(CrawlPolicyError) as exc_info:
        verify_url_or_redirect_hop(
            _policy(),
            bad_url,
            lambda _h: ["93.184.216.34"],
            robots=RobotsPolicy.not_found(),
        )
    assert exc_info.value.reason_code == expected_code


def test_configurable_port_and_scheme_and_rights_gate():
    custom = _policy(allowed_ports=frozenset({443, 8443}))
    res = verify_url_or_redirect_hop(
        custom,
        "https://vietjack.com:8443/de-thi/1",
        lambda _h: ["93.184.216.34"],
        robots=RobotsPolicy.not_found(),
    )
    assert res.port == 8443

    with pytest.raises(CrawlPolicyError) as blocked:
        verify_url_or_redirect_hop(
            _policy(rights_status="UNKNOWN"),
            "https://vietjack.com/de-thi/1",
            lambda _h: ["93.184.216.34"],
            robots=RobotsPolicy.not_found(),
        )
    assert blocked.value.reason_code == "RIGHTS_GATE_BLOCKED"


def test_redirect_chain_rechecks_dns_per_hop_and_blocks_ssrf_loops_and_over_limit():
    # Hop 1 resolves public, hop 2 rebinds to private IP -> SSRFDNSRebindingError!
    def dns_rebind(host: str) -> list[str]:
        if host == "vietjack.com":
            return ["93.184.216.34"]
        return ["10.0.0.5"]

    with pytest.raises(SSRFDNSRebindingError):
        verify_redirect_chain(
            _policy(),
            [
                "https://vietjack.com/de-thi/a",
                "https://cdn.vietjack.com/de-thi/a",
            ],
            dns_rebind,
            robots_by_host=_robots_map(),
        )

    # Loop detection: A -> B -> A
    with pytest.raises(CrawlPolicyError) as loop_exc:
        verify_redirect_chain(
            _policy(),
            [
                "https://vietjack.com/de-thi/1",
                "https://vietjack.com/de-thi/2",
                "https://vietjack.com/de-thi/1#fragment",
            ],
            lambda _h: ["93.184.216.34"],
            robots_by_host=_robots_map(),
        )
    assert loop_exc.value.reason_code == "REDIRECT_LOOP_DETECTED"

    # Max redirects default = 5 -> hops 0..5 allowed (6 URLs), 7th URL fails at hop 6!
    urls_6 = [f"https://vietjack.com/de-thi/{i}" for i in range(6)]
    ok_chain = verify_redirect_chain(
        _policy(),
        urls_6,
        lambda _h: ["93.184.216.34"],
        robots_by_host=_robots_map(),
    )
    assert len(ok_chain) == 6

    urls_7 = [f"https://vietjack.com/de-thi/{i}" for i in range(7)]
    with pytest.raises(CrawlPolicyError) as limit_exc:
        verify_redirect_chain(
            _policy(),
            urls_7,
            lambda _h: ["93.184.216.34"],
            robots_by_host=_robots_map(),
        )
    assert limit_exc.value.reason_code == "TOO_MANY_REDIRECTS"


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://vietjack.com/allowed/../admin",
        "https://vietjack.com/allowed/%2e%2e/admin",
        "https://vietjack.com/allowed/%2E%2E/admin",
        "https://vietjack.com/allowed/%252e%252e/admin",
        "https://vietjack.com/allowed/file%zz",
        "https://vietjack.com/allowed\\admin",
        "https://vietjack.com/allowed/%5cadmin",
        "https://vietjack.com/allowed/%00admin",
        "https://vietjack.com/allowed/%0aadmin",
        "https://vietjack.com/allowed/a\nb",
        "https://vietjack.com/allowed/%7f",
    ],
)
def test_path_normalization_rejects_traversal_double_encoding_backslash_and_control_chars(unsafe_url):
    with pytest.raises(CrawlPolicyError) as exc_info:
        verify_url_or_redirect_hop(
            _policy(allowed_path_prefixes=("/allowed/",)),
            unsafe_url,
            lambda _h: ["93.184.216.34"],
            robots=RobotsPolicy.not_found(),
        )
    assert exc_info.value.reason_code == "PATH_NOT_ALLOWED"


def test_path_prefix_matches_on_segment_boundaries_and_normalizes_dot_segments():
    policy = _policy(allowed_path_prefixes=("/a",))
    for ok_url in [
        "https://vietjack.com/a",
        "https://vietjack.com/a/",
        "https://vietjack.com/a/b",
        "https://vietjack.com/./a/./b",
    ]:
        res = verify_url_or_redirect_hop(
            policy,
            ok_url,
            lambda _h: ["93.184.216.34"],
            robots=RobotsPolicy.not_found(),
        )
        assert res.host == "vietjack.com"

    for bad_url in [
        "https://vietjack.com/ab",
        "https://vietjack.com/ab/c",
    ]:
        with pytest.raises(CrawlPolicyError) as exc_info:
            verify_url_or_redirect_hop(
                policy,
                bad_url,
                lambda _h: ["93.184.216.34"],
                robots=RobotsPolicy.not_found(),
            )
        assert exc_info.value.reason_code == "PATH_NOT_ALLOWED"


def test_robots_policy_states_and_unavailable_rejection():
    with pytest.raises(ValueError):
        RobotsPolicy(status=RobotsStatus.PRESENT, content=None)
    with pytest.raises(ValueError):
        RobotsPolicy(status=RobotsStatus.NOT_FOUND, content="User-agent: *")

    with pytest.raises(CrawlPolicyError) as unavail_exc:
        verify_url_or_redirect_hop(
            _policy(),
            "https://vietjack.com/de-thi/1",
            lambda _h: ["93.184.216.34"],
            robots=RobotsPolicy.unavailable(),
        )
    assert unavail_exc.value.reason_code == "ROBOTS_TXT_UNAVAILABLE"

    # Missing host entry in verify_redirect_chain defaults fail-closed to UNAVAILABLE.
    with pytest.raises(CrawlPolicyError) as missing_exc:
        verify_redirect_chain(
            _policy(),
            ["https://vietjack.com/de-thi/1"],
            lambda _h: ["93.184.216.34"],
            robots_by_host={},
        )
    assert missing_exc.value.reason_code == "ROBOTS_TXT_UNAVAILABLE"


def test_domain_politeness_limiter_with_injected_clock():
    now = 100.0
    limiter = DomainPolitenessLimiter(min_interval_sec=3.0, clock=lambda: now)

    d1 = limiter.try_acquire("vietjack.com")
    assert d1.allowed_now is True
    assert d1.wait_seconds == 0.0

    # Different domain is completely independent.
    d_other = limiter.try_acquire("loigiaihay.com")
    assert d_other.allowed_now is True

    # 1.2s later -> still must wait 1.8s, state not advanced.
    now = 101.2
    d2 = limiter.try_acquire("VietJack.com")
    assert d2.allowed_now is False
    assert d2.wait_seconds == pytest.approx(1.8)
    assert limiter.wait_seconds_for("vietjack.com") == pytest.approx(1.8)

    # At exact 3.0s elapsed -> allowed again.
    now = 103.0
    d3 = limiter.try_acquire("vietjack.com")
    assert d3.allowed_now is True
    assert d3.wait_seconds == 0.0


def test_parser_drift_monitor_window_30_threshold_15_percent_and_min_10_samples():
    m1 = ParserDriftMonitor()
    m2 = ParserDriftMonitor()
    # Confirm no shared mutable state across instances.
    assert m1.record_page_yield(0).sample_count == 1
    assert m2.record_page_yield(5).sample_count == 1

    # Even with 100% empty yield for first 9 pages (< 10 samples), drift is NOT flagged.
    mon = ParserDriftMonitor()
    for _ in range(9):
        st = mon.record_page_yield(0)
        assert st.is_drifted is False
        assert st.reason_code == "INSUFFICIENT_SAMPLES"

    # At 10th sample: 10 empty out of 10 -> 1.0 > 0.15 -> drifted!
    st10 = mon.record_page_yield(0)
    assert st10.is_drifted is True
    assert st10.empty_rate == 1.0
    assert st10.reason_code == "PARSER_DRIFT_DETECTED"

    # Exact 15% boundary with 20 samples (3 empty, 17 non-empty) does NOT trigger (> 0.15).
    boundary = ParserDriftMonitor()
    for idx in range(20):
        res = boundary.record_page_yield(0 if idx < 3 else 5)
    assert res.sample_count == 20
    assert res.empty_rate == 0.15
    assert res.is_drifted is False
    assert res.reason_code is None

    # Sliding window of 30 recovers once 26+ non-empty pages push old empties out.
    recover = ParserDriftMonitor()
    for _ in range(10):
        recover.record_page_yield(0)
    for _ in range(26):
        last = recover.record_page_yield(3)
    # Last 30 has 4 zeros / 30 = 0.133 <= 0.15 -> recovered!
    assert last.sample_count == 30
    assert last.empty_count == 4
    assert last.is_drifted is False
