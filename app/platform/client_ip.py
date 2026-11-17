"""The real client address of a request, with `X-Forwarded-For` believed only through trusted proxies.

A header any client can send must never decide who a request is: the sign-in throttle would be one header away from
useless. So the header is read only when the TCP peer is itself a trusted proxy (`TRUSTED_PROXIES`, a comma-separated
list of addresses or CIDR ranges, e.g. the Next.js server in front of the API: ``127.0.0.1,::1``). Then the chain is
walked from the right, the way each proxy appended it, and the first address that is not a trusted proxy is the
client; entries to its left were supplied by the client (or an earlier hop we do not trust) and are ignored.

Empty `TRUSTED_PROXIES` (the default): the peer address is always the client and the header is never read.
Stdlib only.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from functools import lru_cache

type IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN_CLIENT = "unknown"
MAX_FORWARDED_ENTRIES = 20  # a longer chain is not a real proxy chain: only the right-most 20 entries are looked at


@lru_cache(maxsize=16)
def parse_trusted_proxies(raw: str) -> tuple[IpNetwork, ...]:
    """`"127.0.0.1, 10.0.0.0/8, ::1"` as networks; an entry that is not an address or range is an error, not skipped.

    A typo that silently trusted nothing would hide a broken deployment, one that trusted too much is worse: fail loud.
    """
    networks: list[IpNetwork] = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXIES: {entry!r} is not an IP address or CIDR range") from exc
    return tuple(networks)


def _parse_address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = raw.strip()
    if value.startswith("[") and "]" in value:  # "[2001:db8::1]:443"
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and "." in value:  # "203.0.113.7:51234"
        value = value.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped  # "::ffff:10.0.0.1" is the IPv4 client, and must match an IPv4 trusted range
    return address


def _is_trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address, trusted: Iterable[IpNetwork]) -> bool:
    return any(address.version == net.version and address in net for net in trusted)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted_proxies: str = "") -> str:
    """The client address: `peer` (the TCP source) unless it is a trusted proxy, then the chain of `forwarded_for`."""
    peer_address = _parse_address(peer) if peer else None
    if peer_address is None:
        return UNKNOWN_CLIENT
    trusted = parse_trusted_proxies(trusted_proxies)
    if not trusted or not forwarded_for or not _is_trusted(peer_address, trusted):
        return str(peer_address)
    chain = [e for e in forwarded_for.split(",") if e.strip()][-MAX_FORWARDED_ENTRIES:]
    for entry in reversed(chain):
        address = _parse_address(entry)
        if address is None:
            return str(peer_address)  # a garbled hop: nothing to its left can be believed
        if not _is_trusted(address, trusted):
            return str(address)
    return str(peer_address)  # every hop was a trusted proxy: the request came from inside


def client_ip_of(request: object, trusted_proxies: str = "") -> str:
    """`resolve_client_ip` for a Starlette/FastAPI request (duck-typed: `.client.host` and `.headers`)."""
    client = getattr(request, "client", None)
    headers = getattr(request, "headers", {})
    return resolve_client_ip(getattr(client, "host", None), headers.get("x-forwarded-for"), trusted_proxies)
