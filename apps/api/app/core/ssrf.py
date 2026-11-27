"""Outbound requests to addresses customers choose: the server-side request forgery guard.

Used by the crawler (websites), the mail transport (IMAP/SMTP hosts) and API tools. Every host is resolved
and refused if any address it resolves to is private, loopback, link-local (cloud metadata), multicast,
reserved, unspecified or carrier-grade NAT, including IPv4 addresses wrapped in IPv6. When Verity connects
directly (no egress proxy), the connection goes to the address that was checked, so DNS can't be re-pointed
between the check and the request.
"""

import asyncio
import ipaddress
import os
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]


class OutboundError(Exception):
    def __init__(self, code: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def ip_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"))  # carrier NAT
    )


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


async def checked_address(host: str, port: int, resolve: Resolver, *, allow_private: bool = False) -> str:
    """The address to connect to, or OutboundError('dns_failed' | 'blocked_address')."""
    try:
        ips = [host] if is_ip(host) else await resolve(host, port)
    except (OSError, UnicodeError):
        raise OutboundError("dns_failed", f"Couldn't find the host {host}.") from None
    if not ips:
        raise OutboundError("dns_failed", f"Couldn't find the host {host}.")
    if not allow_private and any(ip_blocked(ip) for ip in ips):
        raise OutboundError("blocked_address", "This address points to a private network, which Verity doesn't connect to.")
    return ips[0]


def behind_proxy() -> bool:
    return any(k in os.environ for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"))


def pinned(url: str, ip: str) -> tuple[str, dict, dict]:
    """(url with the checked IP as host, headers, extensions) so the request goes to that address, with the
    original Host header and TLS server name."""
    parts = urlsplit(url)
    if is_ip(parts.hostname or ""):
        return url, {}, {}
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
    return (
        urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")),
        {"Host": parts.netloc},
        {"sni_hostname": parts.hostname},
    )


@dataclass
class OutboundResponse:
    status: int
    content_type: str
    body: bytes
    latency_ms: int


class GuardedClient:
    """One guarded HTTPS request: checked and pinned address, no redirects, a timeout and a byte cap."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Resolver = system_resolve,
        pin: bool | None = None,
        allow_private: bool = False,
        user_agent: str = "Verity-Tools/1.0",
    ):
        self.transport, self.resolve, self.allow_private, self.user_agent = transport, resolve, allow_private, user_agent
        self.pin = (transport is None and not behind_proxy()) if pin is None else pin

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        json: dict | None = None,
        timeout: float = 8.0,  # noqa: ASYNC109 - per-tool setting, applied with asyncio.timeout below
        max_bytes: int = 32 * 1024,
    ) -> OutboundResponse:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise OutboundError("https_required", "Only https addresses are allowed.")
        if parts.username or parts.password:
            raise OutboundError("invalid_url", "Addresses with credentials in them aren't allowed.")
        if params:
            url = str(httpx.URL(url).copy_merge_params(params))
        started = time.perf_counter()
        ip = await checked_address(parts.hostname, parts.port or 443, self.resolve, allow_private=self.allow_private)
        req_url, pin_headers, ext = pinned(url, ip) if self.pin else (url, {}, {})
        all_headers = {"User-Agent": self.user_agent, "Accept": "application/json, text/plain;q=0.8, */*;q=0.1"}
        all_headers.update(headers or {})
        all_headers.update(pin_headers)
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            follow_redirects=False,
            trust_env=self.transport is None,
        ) as client:
            try:
                req = client.build_request(method, req_url, headers=all_headers, json=json, extensions=ext)
                async with asyncio.timeout(timeout):
                    resp = await client.send(req, stream=True)
                    try:
                        if 300 <= resp.status_code < 400:
                            raise OutboundError(
                                "redirect_refused",
                                "The API redirected to another address, which Verity doesn't follow.",
                                http_status=resp.status_code,
                            )
                        declared = int(resp.headers.get("content-length") or 0)
                        if declared > max_bytes:
                            raise OutboundError(
                                "too_large", "The API's response is larger than the tool's limit.", http_status=resp.status_code
                            )
                        body = bytearray()
                        async for chunk in resp.aiter_bytes():
                            body += chunk
                            if len(body) > max_bytes:
                                raise OutboundError(
                                    "too_large", "The API's response is larger than the tool's limit.", http_status=resp.status_code
                                )
                    finally:
                        await resp.aclose()
            except (TimeoutError, httpx.TimeoutException):
                raise OutboundError("timeout", "The API took too long to respond.") from None
            except httpx.HTTPError as e:
                raise OutboundError("connect_failed", f"Couldn't connect to the API ({type(e).__name__}).") from None
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return OutboundResponse(resp.status_code, ctype, bytes(body), int((time.perf_counter() - started) * 1000))
