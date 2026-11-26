"""Website crawler for knowledge sources.

Breadth-first from the root URL and the site's sitemaps, staying on the same site (the host and its www.
twin), honouring robots.txt, include/exclude path prefixes and a depth limit. Main content is extracted
with trafilatura; pages are hashed so a re-sync only re-embeds what changed.

Server-side request forgery: every URL, including each redirect hop, is resolved and refused if any address
is private, loopback, link-local (cloud metadata), multicast or reserved. When the crawler connects
directly (no egress proxy), it connects to the address it checked, so DNS can't be re-pointed between the
check and the request.
"""

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import get_settings

log = logging.getLogger("verity.crawler")

SKIP_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp[34]|m4a|wav|avi|mov|webm|zip|gz|tgz|rar|7z|tar|exe|dmg|pkg|iso|"
    r"css|js|mjs|json|xml|rss|atom|woff2?|ttf|otf|eot|pdf|docx?|xlsx?|pptx?|csv)$",
    re.I,
)
TRACKING = re.compile(r"^(?:utm_[a-z]+|gclid|fbclid|mc_cid|mc_eid|ref|_ga|_hs[a-z]+)$", re.I)
MAX_REDIRECTS = 5


class CrawlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- URLs ---------------------------------------------------------------------------------------


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL without fragment, default port or tracking parameters; None if not crawlable."""
    try:
        url = urljoin(base, url.strip()) if base else url.strip()
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port in (None, 80 if parts.scheme == "http" else 443) else f"{host}:{port}"
    if parts.username or parts.password:
        return None
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def site_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, domain: str) -> bool:
    return site_host(url) == domain


def normalize_root(url: str) -> str:
    """Validates the URL a customer typed; adds https:// when it's missing."""
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    norm = normalize_url(url)
    if norm is None:
        raise CrawlError("invalid_url", "Enter a website address such as https://help.example.com.")
    host = urlsplit(norm).hostname or ""
    if "." not in host and not get_settings().crawl_allow_private:
        raise CrawlError("invalid_url", "Enter a public website address.")
    return norm


def path_allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    path = urlsplit(url).path or "/"
    if any(path.startswith(p) for p in exclude):
        return False
    return not include or any(path.startswith(p) for p in include)


# ---- SSRF guard ---------------------------------------------------------------------------------


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


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


async def check_host(url: str, resolve: Resolver, allow_private: bool) -> str:
    """Returns a checked address for the URL's host, or raises CrawlError('blocked_address')."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        ips = [host] if _is_ip(host) else await resolve(host, port)
    except (OSError, UnicodeError):
        raise CrawlError("dns_failed", f"Couldn't find the site {host}.") from None
    if not ips:
        raise CrawlError("dns_failed", f"Couldn't find the site {host}.")
    if not allow_private and any(ip_blocked(ip) for ip in ips):
        raise CrawlError("blocked_address", "This address points to a private network, which Verity doesn't crawl.")
    return ips[0]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# ---- Fetching -----------------------------------------------------------------------------------


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    body: bytes


class Fetcher:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, resolve: Resolver = system_resolve):
        s = get_settings()
        self.allow_private = s.crawl_allow_private
        self.max_bytes = s.crawl_max_page_bytes
        self.resolve = resolve
        # Pin the connection to the checked address only when we connect directly; behind an egress proxy
        # the proxy resolves (and polices) the destination itself.
        proxied = transport is None and any(k in os.environ for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"))
        self.pin = transport is None and not proxied
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(s.crawl_timeout_seconds, connect=10.0),
            headers={"User-Agent": s.crawl_user_agent, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1"},
            follow_redirects=False,
            trust_env=transport is None,
        )

    async def aclose(self):
        await self.client.aclose()

    async def get(self, url: str) -> Response:
        for _ in range(MAX_REDIRECTS + 1):
            ip = await check_host(url, self.resolve, self.allow_private)
            req_url, headers, ext = url, {}, {}
            if self.pin and not _is_ip(urlsplit(url).hostname or ""):
                parts = urlsplit(url)
                ip_host = f"[{ip}]" if ":" in ip else ip
                netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
                req_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
                headers["Host"] = parts.netloc
                ext = {"sni_hostname": parts.hostname}
            try:
                req = self.client.build_request("GET", req_url, headers=headers, extensions=ext)
                resp = await self.client.send(req, stream=True)
            except httpx.TimeoutException:
                raise CrawlError("timeout", "The site took too long to respond.") from None
            except httpx.HTTPError as e:
                raise CrawlError("fetch_failed", f"Couldn't connect to the site ({type(e).__name__}).") from None
            try:
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    nxt = normalize_url(resp.headers["location"], url)
                    if nxt is None:
                        raise CrawlError("bad_redirect", "The site redirected to an address Verity can't crawl.")
                    url = nxt
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                declared = int(resp.headers.get("content-length") or 0)
                if declared > self.max_bytes:
                    return Response(url, resp.status_code, ctype, b"")
                body = bytearray()
                async for part in resp.aiter_bytes():
                    body += part
                    if len(body) > self.max_bytes:
                        break
                return Response(url, resp.status_code, ctype, bytes(body[: self.max_bytes]))
            finally:
                await resp.aclose()
        raise CrawlError("too_many_redirects", "The site redirected too many times.")


# ---- Extraction ---------------------------------------------------------------------------------


@dataclass
class Page:
    url: str
    status: int
    title: str = ""
    text: str = ""
    content_hash: str | None = None
    error_code: str | None = None
    depth: int = 0
    links: list[str] = field(default_factory=list)


def extract(html: str, url: str) -> tuple[str, str, list[str], bool, bool]:
    """(title, main text, links, noindex, nofollow)"""
    import lxml.html
    import trafilatura

    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return "", "", [], False, False
    robots = " ".join(m.get("content", "") for m in doc.xpath('//meta[@name="robots" or @name="ROBOTS"]')).lower()
    title = " ".join((doc.findtext(".//title") or "").split())[:500]
    links = []
    base = doc.xpath("//base/@href")
    base_url = urljoin(url, base[0]) if base else url
    for a in doc.xpath("//a[@href]"):
        if "nofollow" in (a.get("rel") or "").lower():
            continue
        n = normalize_url(a.get("href"), base_url)
        if n:
            links.append(n)
    text = trafilatura.extract(html, url=url, include_tables=True, include_comments=False, favor_recall=True) or ""
    return title, text.strip(), links, "noindex" in robots, "nofollow" in robots


def parse_sitemap(xml: bytes) -> tuple[list[str], list[str]]:
    """(page URLs, nested sitemap URLs)"""
    text = xml.decode("utf-8", errors="replace")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
    if re.search(r"<sitemapindex", text, re.I):
        return [], locs
    return locs, []


# ---- Crawl --------------------------------------------------------------------------------------


@dataclass
class CrawlConfig:
    root_url: str
    domain: str
    max_depth: int = 3
    max_pages: int = 500
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


class Crawler:
    def __init__(self, cfg: CrawlConfig, fetcher: Fetcher, *, concurrency: int = 4):
        self.cfg = cfg
        self.fetcher = fetcher
        self.concurrency = concurrency
        self.robots: RobotFileParser | None = None
        self.delay = 0.0
        self.user_agent = get_settings().crawl_user_agent

    async def _load_robots(self) -> list[str]:
        root = urlsplit(self.cfg.root_url)
        url = urlunsplit((root.scheme, root.netloc, "/robots.txt", "", ""))
        rp = RobotFileParser()
        try:
            r = await self.fetcher.get(url)
            if r.status >= 500:
                rp.disallow_all = True  # RFC 9309: unreachable robots.txt means don't crawl
            elif r.status == 200:
                rp.parse(r.body.decode("utf-8", errors="replace").splitlines())
            else:
                rp.allow_all = True
        except CrawlError as e:
            if e.code == "blocked_address":
                raise
            rp.allow_all = True
        self.robots = rp
        self.delay = min(float(rp.crawl_delay(self.user_agent) or 0), 5.0)
        return list(rp.site_maps() or [])

    def allowed(self, url: str) -> bool:
        if not same_site(url, self.cfg.domain) or SKIP_EXT.search(urlsplit(url).path):
            return False
        if not path_allowed(url, self.cfg.include, self.cfg.exclude):
            return False
        return self.robots is None or self.robots.can_fetch(self.user_agent, url)

    async def _sitemap_urls(self, extra: list[str]) -> list[str]:
        root = urlsplit(self.cfg.root_url)
        todo = [*extra, urlunsplit((root.scheme, root.netloc, "/sitemap.xml", "", ""))]
        seen, pages = set(), []
        while todo and len(seen) < 10 and len(pages) < self.cfg.max_pages * 2:
            sm = normalize_url(todo.pop(0))
            if not sm or sm in seen or not same_site(sm, self.cfg.domain):
                continue
            seen.add(sm)
            try:
                r = await self.fetcher.get(sm)
            except CrawlError:
                continue
            if r.status != 200:
                continue
            locs, nested = parse_sitemap(r.body)
            pages += [u for u in (normalize_url(x) for x in locs) if u]
            todo += nested
        return pages

    async def run(self) -> AsyncIterator[Page]:
        """Yields each crawled page (including failures) until the frontier or max_pages runs out."""
        root = self.cfg.root_url
        await check_host(root, self.fetcher.resolve, self.fetcher.allow_private)  # fail fast on a private root
        sitemaps = await self._load_robots()
        if not self.robots.can_fetch(self.user_agent, root):
            raise CrawlError("robots_disallowed", "This site's robots.txt doesn't allow Verity to crawl it.")
        frontier: list[tuple[str, int]] = [(root, 0)]
        seen = {root}
        for u in await self._sitemap_urls(sitemaps):
            if u not in seen and self.allowed(u):
                seen.add(u)
                frontier.append((u, 1))
        fetched = 0
        while frontier and fetched < self.cfg.max_pages:
            batch_size = 1 if self.delay else self.concurrency
            batch = []
            while frontier and len(batch) < min(batch_size, self.cfg.max_pages - fetched):
                batch.append(frontier.pop(0))
            pages = await asyncio.gather(*(self._fetch_page(u, d) for u, d in batch))
            for page in pages:
                fetched += 1
                if page.depth < self.cfg.max_depth:
                    for link in page.links:
                        if link not in seen and self.allowed(link):
                            seen.add(link)
                            frontier.append((link, page.depth + 1))
                yield page
            if self.delay:
                await asyncio.sleep(self.delay)

    async def _fetch_page(self, url: str, depth: int) -> Page:
        try:
            r = await self.fetcher.get(url)
        except CrawlError as e:
            return Page(url, 0, error_code=e.code, depth=depth)
        final = normalize_url(r.url) or url
        if not same_site(final, self.cfg.domain):
            return Page(url, r.status, error_code="off_site_redirect", depth=depth)
        if r.status != 200:
            return Page(final, r.status, error_code=f"http_{r.status}", depth=depth)
        if r.content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
            return Page(final, r.status, error_code="not_html", depth=depth)
        if not r.body:
            return Page(final, r.status, error_code="too_large", depth=depth)
        html = r.body.decode("utf-8", errors="replace")
        if r.content_type == "text/plain":
            title, text, links, noindex, nofollow = final.rsplit("/", 1)[-1], html.strip(), [], False, True
        else:
            title, text, links, noindex, nofollow = await asyncio.to_thread(extract, html, final)
        page = Page(final, r.status, title=title or final, depth=depth, links=[] if nofollow else links)
        if noindex:
            page.error_code = "noindex"
        elif not text:
            page.error_code = "no_content"
        else:
            page.text = text
            page.content_hash = hashlib.sha256(text.encode()).hexdigest()
        return page
