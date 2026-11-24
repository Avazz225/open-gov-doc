"""The fenced outbound HTTP client for revocation and certificate fetching
(ADR 0248 Decision 4).

The AIA, CRL and OCSP URLs come from certificates inside an uploaded file, so
the uploader picks them. Without a fence, any user could make the cluster call
internal addresses (other services, cloud metadata endpoints). pyHanko's
fetchers take an `aiohttp.ClientSession`; this module builds one that:

- only speaks `http`/`https`, on the default ports or `allowed_ports`
  (80, 443 and 8080 by default - many OCSP responders use 8080);
- refuses hosts that resolve to private, loopback, link-local, multicast,
  reserved, shared (CGNAT) or configured cluster networks - every resolved
  address must be public, so a name can't mix a public and an internal one;
- refuses IP-literal URLs to such addresses (aiohttp skips the resolver for
  them, hence the check on request start);
- follows redirects only to the same host (and re-checks each hop);
- caps every response body and bounds every request in time;
- optionally sends everything through an egress proxy.

Any refusal raises `FetchBlockedError`; pyHanko treats it like an unreachable
server, so the result is "revocation not checkable", never a pass by accident.
"""

import ipaddress
import logging
import socket
from types import SimpleNamespace
from urllib.parse import urljoin

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from signature_service.validation.interface import FetchLimits

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http": 80, "https": 443}
# Not covered by `is_private` in every Python version.
_ALWAYS_BLOCKED = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("0.0.0.0/8")]


class FetchBlockedError(aiohttp.ClientError):
    """An outbound fetch the fence refused."""


def _blocked_networks(limits: FetchLimits) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    extra = [ipaddress.ip_network(n, strict=False) for n in limits.extra_blocked_networks]
    return _ALWAYS_BLOCKED + extra


def is_blocked_address(address: str, limits: FetchLimits) -> bool:
    if limits.allow_local_test_server:
        return False
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return any(
        ip in network for network in _blocked_networks(limits) if ip.version == network.version
    )


def _check_url(url: aiohttp.client.URL | str, limits: FetchLimits) -> None:
    parsed = aiohttp.client.URL(str(url))
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise FetchBlockedError(f"Schema nicht erlaubt: {scheme!r}")
    port_ok = parsed.port in (None, _ALLOWED_SCHEMES[scheme]) or parsed.port in limits.allowed_ports
    if not port_ok and not limits.allow_local_test_server:
        raise FetchBlockedError(f"Port nicht erlaubt: {parsed.port}")
    host = parsed.host or ""
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return  # a name - the resolver checks what it resolves to
    if is_blocked_address(host.strip("[]"), limits):
        raise FetchBlockedError(f"Adresse nicht erlaubt: {host}")


class FencedResolver(AbstractResolver):
    """Resolves through aiohttp's default resolver and refuses the host if
    any resulting address is blocked."""

    def __init__(self, limits: FetchLimits) -> None:
        self._inner = aiohttp.ThreadedResolver()
        self._limits = limits
        # The configured egress proxy usually lives inside the cluster; it is
        # the one internal host this session may connect to.
        self._proxy_host = aiohttp.client.URL(limits.proxy).host if limits.proxy else None

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results = await self._inner.resolve(host, port, family)
        if host == self._proxy_host:
            return results
        for result in results:
            if is_blocked_address(result["host"], self._limits):
                raise FetchBlockedError(f"{host} löst auf eine nicht erlaubte Adresse auf")
        return results

    async def close(self) -> None:
        await self._inner.close()


def _trace_config(limits: FetchLimits, *, same_host_redirects: bool) -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()

    async def on_request_start(_session, ctx: SimpleNamespace, params) -> None:
        _check_url(params.url, limits)
        ctx.received = 0
        if not hasattr(ctx, "origin_host"):
            ctx.origin_host = params.url.host

    async def on_request_redirect(_session, ctx: SimpleNamespace, params) -> None:
        location = params.response.headers.get("Location", "")
        target = aiohttp.client.URL(urljoin(str(params.url), location))
        if same_host_redirects and target.host != ctx.origin_host:
            raise FetchBlockedError(f"Umleitung auf anderen Host nicht erlaubt: {target.host}")

    async def on_response_chunk_received(_session, ctx: SimpleNamespace, params) -> None:
        ctx.received = getattr(ctx, "received", 0) + len(params.chunk)
        if ctx.received > limits.max_response_bytes:
            raise FetchBlockedError("Antwort zu groß")

    trace.on_request_start.append(on_request_start)
    trace.on_request_redirect.append(on_request_redirect)
    trace.on_response_chunk_received.append(on_response_chunk_received)
    return trace


def build_fenced_session(
    limits: FetchLimits, *, same_host_redirects: bool = True, decompress: bool = False
) -> aiohttp.ClientSession:
    """A session for one validation run (or one trusted-list fetch). Must be
    created inside the event loop that uses it and closed afterwards.

    `same_host_redirects=False` is for the EU trusted lists, whose national
    hosts redirect across domains; every hop is still checked by the URL check
    and the resolver. `decompress=True` (trusted lists, which the EU serves
    gzip-compressed) lets aiohttp inflate bodies; the caller must then cap
    the inflated size itself (`trusted_lists._get` does)."""
    connector = aiohttp.TCPConnector(resolver=FencedResolver(limits), limit=8)
    return aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=limits.per_request_timeout_seconds),
        trace_configs=[_trace_config(limits, same_host_redirects=same_host_redirects)],
        proxy=limits.proxy,
        # Off by default: the size cap counts the bytes on the wire, and a
        # small compressed body must not inflate past it unchecked.
        auto_decompress=decompress,
    )
