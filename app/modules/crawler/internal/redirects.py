"""Redirect-chain & URL policy verifier for permitted web crawling (module A02).

Performs zero socket I/O and zero direct DNS calls: callers inject a DNS resolver
returning pre-resolved IP addresses, validated on every hop via `check_resolved_addresses`.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.robotparser
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.modules.crawler.internal.ssrf import SSRFCheckResult, check_resolved_addresses

ALLOWED_RIGHTS_STATUSES: frozenset[str] = frozenset(
    {"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"}
)
DEFAULT_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})
DEFAULT_ALLOWED_PORTS: frozenset[int] = frozenset({443})
DEFAULT_ALLOWED_PATH_PREFIXES: tuple[str, ...] = ("/",)
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_USER_AGENT = "ULPComplianceBot/9.0 (+https://ulp.edu.vn/bot-policy)"
_EMPTY_VISITED: frozenset[str] = frozenset()

DnsResolver = Callable[[str], Sequence[str]]


class CrawlPolicyError(PermissionError):
    """Raised when a target URL or redirect hop violates crawl policy."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        message = f"{reason_code}: {detail}" if detail else reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CrawlSourcePolicy:
    """Strict allowlist and security limits for a permitted crawl source."""

    allowed_domains: frozenset[str]
    rights_status: str = "OWNED"
    allowed_path_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PATH_PREFIXES
    allowed_schemes: frozenset[str] = DEFAULT_ALLOWED_SCHEMES
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True, slots=True)
class VerifiedHopResult:
    """Validated single hop result including pinned IP and updated visited set."""

    url: str
    host: str
    port: int
    ssrf_result: SSRFCheckResult
    hop_count: int
    visited_urls: frozenset[str]


def _normalize_url_for_loop_check(parsed: urllib.parse.ParseResult) -> str:
    # Fragments are client-side only; strip fragment and lowercase scheme/netloc.
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    return urllib.parse.urlunparse(normalized)


def _is_bare_ip_host(hostname: str) -> bool:
    candidate = hostname.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def verify_url_or_redirect_hop(
    policy: CrawlSourcePolicy,
    target_url: str,
    resolve_dns: DnsResolver,
    *,
    robots_txt_content: str | None = None,
    hop_count: int = 0,
    visited_urls: frozenset[str] = _EMPTY_VISITED,
) -> VerifiedHopResult:
    """Validate rights, URL structure, allowlist, robots.txt, redirect loop/limit and DNS SSRF."""
    if policy.rights_status not in ALLOWED_RIGHTS_STATUSES:
        raise CrawlPolicyError("RIGHTS_GATE_BLOCKED", f"rights_status={policy.rights_status!r}")

    if hop_count < 0:
        raise CrawlPolicyError("INVALID_HOP_COUNT", f"hop_count={hop_count}")

    if hop_count > policy.max_redirects:
        raise CrawlPolicyError("TOO_MANY_REDIRECTS", f"exceeded max_redirects={policy.max_redirects}")

    parsed = urllib.parse.urlparse(target_url)
    scheme = parsed.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise CrawlPolicyError("DISALLOWED_SCHEME", f"scheme={scheme!r}")

    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise CrawlPolicyError("USERINFO_NOT_ALLOWED", "credentials in URL are forbidden")

    raw_host = parsed.hostname
    if not raw_host:
        raise CrawlPolicyError("INVALID_URL_HOST", f"url={target_url!r}")

    host = raw_host.lower().rstrip(".")
    if _is_bare_ip_host(host):
        raise CrawlPolicyError("BARE_IP_HOST_NOT_ALLOWED", f"host={host!r}")

    normalized_allowed_domains = {d.lower().rstrip(".") for d in policy.allowed_domains}
    if host not in normalized_allowed_domains:
        raise CrawlPolicyError("CROSS_DOMAIN_REDIRECT_BLOCKED", f"host={host!r}")

    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise CrawlPolicyError("INVALID_PORT", str(exc)) from exc

    port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)
    if port not in policy.allowed_ports:
        raise CrawlPolicyError("DISALLOWED_PORT", f"port={port}")

    path = parsed.path or "/"
    if not any(path.startswith(prefix) for prefix in policy.allowed_path_prefixes):
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"path={path!r}")

    canonical_url = _normalize_url_for_loop_check(parsed)
    if canonical_url in visited_urls:
        raise CrawlPolicyError("REDIRECT_LOOP_DETECTED", f"url={canonical_url!r}")

    if robots_txt_content is not None:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_txt_content.splitlines())
        if not rp.can_fetch(policy.user_agent, target_url):
            raise CrawlPolicyError("ROBOTS_TXT_DISALLOWED", f"url={canonical_url!r}")

    resolved_ips = resolve_dns(host)
    ssrf_result = check_resolved_addresses(host, resolved_ips)

    return VerifiedHopResult(
        url=canonical_url,
        host=host,
        port=port,
        ssrf_result=ssrf_result,
        hop_count=hop_count,
        visited_urls=visited_urls | {canonical_url},
    )


def verify_redirect_chain(
    policy: CrawlSourcePolicy,
    urls: Sequence[str],
    resolve_dns: DnsResolver,
    *,
    robots_txt_by_host: dict[str, str] | None = None,
) -> tuple[VerifiedHopResult, ...]:
    """Validate an entire sequence of redirect hops starting at hop 0."""
    if not urls:
        raise CrawlPolicyError("EMPTY_URL_CHAIN", "at least one URL is required")

    visited: frozenset[str] = frozenset()
    results: list[VerifiedHopResult] = []
    for hop_idx, url in enumerate(urls):
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
        robots = robots_txt_by_host.get(host) if robots_txt_by_host is not None else None
        hop_result = verify_url_or_redirect_hop(
            policy,
            url,
            resolve_dns,
            robots_txt_content=robots,
            hop_count=hop_idx,
            visited_urls=visited,
        )
        visited = hop_result.visited_urls
        results.append(hop_result)
    return tuple(results)
