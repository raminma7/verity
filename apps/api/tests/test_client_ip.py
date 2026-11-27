"""Per-IP rate limits can't be dodged with a forged X-Forwarded-For: it's only believed from a trusted proxy."""

import pytest
from starlette.requests import Request

from app.core.config import get_settings
from app.core.deps import client_ip
from tests.conftest import new_client


def req(peer: str | None, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", f.encode()) for f in forwarded]
    return Request({"type": "http", "headers": headers, "client": (peer, 4711) if peer else None})


@pytest.fixture
def proxies():
    s = get_settings()
    saved = s.trusted_proxies
    yield s
    s.trusted_proxies = saved


def test_direct_connections_ignore_the_header():
    assert client_ip(req("203.0.113.7")) == "203.0.113.7"
    assert client_ip(req("203.0.113.7", "198.51.100.1")) == "203.0.113.7"  # forged, not from a proxy


def test_from_the_trusted_proxy_the_right_most_untrusted_hop_counts():
    assert client_ip(req("127.0.0.1", "198.51.100.1")) == "198.51.100.1"
    # The client prepends a fake address; the proxy appended the real one.
    assert client_ip(req("127.0.0.1", "10.9.9.9, 198.51.100.1")) == "198.51.100.1"
    assert client_ip(req("127.0.0.1", "1.1.1.1", "198.51.100.2")) == "198.51.100.2"  # repeated headers
    assert client_ip(req("127.0.0.1")) == "127.0.0.1"


def test_chained_proxies_and_ports(proxies):
    proxies.trusted_proxies = ["127.0.0.1/32", "10.0.0.0/8"]
    assert client_ip(req("10.0.0.5", "198.51.100.9, 10.1.2.3")) == "198.51.100.9"
    assert client_ip(req("10.0.0.5", "198.51.100.9:5555, 10.1.2.3")) == "198.51.100.9"
    assert client_ip(req("10.0.0.5", "10.1.1.1, 10.1.2.3")) == "10.1.1.1"  # only proxies: the farthest one
    assert client_ip(req("10.0.0.5", "garbage, 10.1.2.3")) == "10.1.2.3"  # nothing trusted left of a bad hop


def test_ipv6(proxies):
    proxies.trusted_proxies = ["::1/128", "fd00::/8"]
    assert client_ip(req("::1", "2001:db8::7")) == "2001:db8::7"
    assert client_ip(req("::1", "[2001:db8::8]:443")) == "2001:db8::8"
    assert client_ip(req("fd00::1", "2001:db8::9, fd00::2")) == "2001:db8::9"
    assert client_ip(req("2001:db8::1", "198.51.100.1")) == "2001:db8::1"
    assert client_ip(req("::ffff:127.0.0.1", "198.51.100.3")) == "::ffff:127.0.0.1"  # IPv4-mapped is not ::1


async def test_rate_limit_counts_the_real_client(proxies):
    # The ASGI test client connects from 127.0.0.1; trust nothing so the header is ignored.
    proxies.trusted_proxies = []
    async with new_client() as c:
        codes = []
        for i in range(12):
            r = await c.post(
                "/api/auth/login",
                json={"email": f"x{i}@example.com", "password": "wrong-password-1"},
                headers={"X-Forwarded-For": f"198.51.100.{i}"},
            )
            codes.append(r.status_code)
    assert 429 in codes  # a new forged address per request doesn't reset the limit
