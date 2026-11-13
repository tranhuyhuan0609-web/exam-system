"""Redirect-chain & URL policy verifier for permitted web crawling (module A02).

Performs zero socket I/O and zero direct DNS calls: callers inject a DNS resolver
returning pre-resolved IP addresses, validated on every hop via `check_resolved_addresses`.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.robotparser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

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


class RobotsStatus(StrEnum):
    """Explicit state of a host's robots.txt policy fetch."""

    PRESENT = "PRESENT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RobotsPolicy:
    """Explicit robots.txt outcome for a host (content, NOT_FOUND, or UNAVAILABLE)."""

    status: RobotsStatus
    content: str | None = None

    def __post_init__(self) -> None:
        if self.status == RobotsStatus.PRESENT and self.content is None:
            raise ValueError("RobotsPolicy with status=PRESENT requires non-None content")
        if self.status != RobotsStatus.PRESENT and self.content is not None:
            raise ValueError(f"RobotsPolicy with status={self.status} must not carry content")

    @classmethod
    def with_content(cls, content: str) -> RobotsPolicy:
        return cls(status=RobotsStatus.PRESENT, content=content)

    @classmethod
    def not_found(cls) -> RobotsPolicy:
        return cls(status=RobotsStatus.NOT_FOUND)

    @classmethod
    def unavailable(cls) -> RobotsPolicy:
        return cls(status=RobotsStatus.UNAVAILABLE)


def _normalize_url_for_loop_check(
    parsed: urllib.parse.ParseResult,
    normalized_path: str,
) -> str:
    # Fragments are client-side only; strip fragment and lowercase scheme/netloc.
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=normalized_path,
        fragment="",
    )
    return urllib.parse.urlunparse(normalized)


def _normalize_and_validate_path(raw_path: str) -> tuple[str, tuple[str, ...]]:
    """Decode percent-encoding once, reject unsafe forms, and return canonical path + segments."""
    candidate = raw_path or "/"
    decoded = urllib.parse.unquote(candidate)

    # Reject remaining '%' (blocks double encoding e.g. %252e or invalid escapes),
    # backslashes (literal or %5c), and ASCII control characters (including DEL 0x7f).
    if "%" in decoded or "\\" in decoded:
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"unsafe escape or backslash in path={raw_path!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in decoded):
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"control character in path={raw_path!r}")
    if not decoded.startswith("/"):
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"path must be absolute: {raw_path!r}")

    raw_segments = decoded.split("/")
    if ".." in raw_segments:
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"dot-dot traversal disallowed: {raw_path!r}")

    segments = tuple(seg for seg in raw_segments if seg and seg != ".")
    trailing_slash = decoded.endswith("/") and bool(segments)
    normalized_path = "/" + "/".join(segments) + ("/" if trailing_slash else "")
    return normalized_path, segments


def _matches_allowed_prefix(path_segments: tuple[str, ...], prefix: str) -> bool:
    if not prefix.startswith("/"):
        return False
    prefix_decoded = urllib.parse.unquote(prefix)
    if "%" in prefix_decoded or "\\" in prefix_decoded:
        return False
    prefix_raw = prefix_decoded.split("/")
    if ".." in prefix_raw:
        return False
    prefix_segments = tuple(seg for seg in prefix_raw if seg and seg != ".")
    if not prefix_segments:
        return True
    return (
        len(path_segments) >= len(prefix_segments)
        and path_segments[: len(prefix_segments)] == prefix_segments
    )


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
    robots: RobotsPolicy,
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

    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target_url):
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"control character in target_url={target_url!r}")

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

    normalized_path, path_segments = _normalize_and_validate_path(parsed.path)
    if not any(_matches_allowed_prefix(path_segments, prefix) for prefix in policy.allowed_path_prefixes):
        raise CrawlPolicyError("PATH_NOT_ALLOWED", f"path={normalized_path!r}")

    canonical_url = _normalize_url_for_loop_check(parsed, normalized_path)
    if canonical_url in visited_urls:
        raise CrawlPolicyError("REDIRECT_LOOP_DETECTED", f"url={canonical_url!r}")

    if robots.status == RobotsStatus.UNAVAILABLE:
        raise CrawlPolicyError("ROBOTS_TXT_UNAVAILABLE", f"host={host!r}")
    if robots.status == RobotsStatus.PRESENT:
        rp = urllib.robotparser.RobotFileParser()
        assert robots.content is not None
        rp.parse(robots.content.splitlines())
        if not rp.can_fetch(policy.user_agent, canonical_url):
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
    robots_by_host: Mapping[str, RobotsPolicy],
) -> tuple[VerifiedHopResult, ...]:
    """Validate an entire sequence of redirect hops starting at hop 0."""
    if not urls:
        raise CrawlPolicyError("EMPTY_URL_CHAIN", "at least one URL is required")

    normalized_robots = {k.lower().rstrip("."): v for k, v in robots_by_host.items()}
    visited: frozenset[str] = frozenset()
    results: list[VerifiedHopResult] = []
    for hop_idx, url in enumerate(urls):
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
        robots = normalized_robots.get(host, RobotsPolicy.unavailable())
        hop_result = verify_url_or_redirect_hop(
            policy,
            url,
            resolve_dns,
            robots=robots,
            hop_count=hop_idx,
            visited_urls=visited,
        )
        visited = hop_result.visited_urls
        results.append(hop_result)
    return tuple(results)
