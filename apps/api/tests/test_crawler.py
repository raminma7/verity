import httpx
import pytest

from app.services.crawler import CrawlConfig, Crawler, CrawlError, Fetcher, check_host, ip_blocked, normalize_root, normalize_url

SITE = {
    "/robots.txt": ("text/plain", "User-agent: *\nDisallow: /private\nSitemap: https://docs.example.com/sitemap.xml\n"),
    "/sitemap.xml": ("application/xml", "<urlset><url><loc>https://docs.example.com/from-sitemap</loc></url></urlset>"),
    "/": (
        "text/html",
        "<html><head><title>Home</title></head><body><main><p>{}</p>"
        '<a href="/guide?utm_source=x#top">Guide</a> <a href="/private/secret">Secret</a>'
        ' <a href="https://other.example.org/">Elsewhere</a> <a href="/logo.png">Logo</a></main></body></html>',
    ),
    "/guide": (
        "text/html",
        "<html><head><title>Guide</title></head><body><article><h1>Setup guide</h1><p>{}</p>"
        '<a href="/guide/deep">Deep</a></article></body></html>',
    ),
    "/guide/deep": ("text/html", "<html><body><article><p>{}</p><a href='/guide/deeper'>x</a></article></body></html>"),
    "/guide/deeper": ("text/html", "<html><body><article><p>{}</p></article></body></html>"),
    "/from-sitemap": ("text/html", "<html><head><meta name='robots' content='noindex'></head><body><p>{}</p></body></html>"),
}
FILLER = "Verity answers customer questions from your documentation and cites every passage it uses. " * 4


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/old":
        return httpx.Response(301, headers={"location": "/guide"})
    if path == "/to-metadata":
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    if path not in SITE:
        return httpx.Response(404, text="missing")
    ctype, body = SITE[path]
    return httpx.Response(200, headers={"content-type": f"{ctype}; charset=utf-8"}, text=body.replace("{}", FILLER))


async def public_resolver(host: str, port: int) -> list[str]:
    return {"docs.example.com": ["93.184.215.14"], "evil.example.com": ["10.0.0.5"]}.get(host, ["93.184.215.20"])


def fetcher(allow_private=False) -> Fetcher:
    f = Fetcher(transport=httpx.MockTransport(handler), resolve=public_resolver)
    f.allow_private = allow_private
    return f


async def crawl(**kw) -> list:
    cfg = CrawlConfig(root_url="https://docs.example.com/", domain="docs.example.com", **kw)
    f = fetcher()
    try:
        return [p async for p in Crawler(cfg, f).run()]
    finally:
        await f.aclose()


def test_normalize_url():
    assert normalize_url("HTTPS://Docs.Example.com:443/a//b?utm_source=x&b=2&a=1#frag") == "https://docs.example.com/a/b?a=1&b=2"
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("https://user:pw@example.com/") is None
    assert normalize_root("help.example.com") == "https://help.example.com/"


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.169.254", "::1", "fd00::1", "::ffff:127.0.0.1", "100.64.0.1", "0.0.0.0"]
)
def test_private_addresses_blocked(ip):
    assert ip_blocked(ip)


def test_public_address_allowed():
    assert not ip_blocked("93.184.215.14")


async def test_host_resolving_to_private_ip_is_refused():
    with pytest.raises(CrawlError) as e:
        await check_host("https://evil.example.com/", public_resolver, allow_private=False)
    assert e.value.code == "blocked_address"
    with pytest.raises(CrawlError):
        await check_host("http://127.0.0.1:8000/", public_resolver, allow_private=False)


async def test_redirect_to_metadata_service_is_refused():
    f = fetcher()
    with pytest.raises(CrawlError) as e:
        await f.get("https://docs.example.com/to-metadata")
    assert e.value.code == "blocked_address"
    r = await f.get("https://docs.example.com/old")
    assert r.url == "https://docs.example.com/guide" and r.status == 200
    await f.aclose()


async def test_crawl_follows_links_robots_sitemap_and_depth():
    pages = await crawl(max_depth=2)
    by_url = {p.url: p for p in pages}
    assert "https://docs.example.com/guide" in by_url  # tracking params and fragment dropped
    assert "https://docs.example.com/guide/deep" in by_url
    assert "https://docs.example.com/guide/deeper" not in by_url  # depth 3
    assert not any("/private" in u for u in by_url)  # robots.txt
    assert not any("other.example.org" in u or u.endswith(".png") for u in by_url)
    assert by_url["https://docs.example.com/from-sitemap"].error_code == "noindex"
    guide = by_url["https://docs.example.com/guide"]
    assert guide.title == "Guide" and "cites every passage" in guide.text and guide.content_hash


async def test_crawl_respects_page_cap_and_include_paths():
    assert len(await crawl(max_pages=2)) == 2
    pages = await crawl(include=["/guide"], max_depth=5)
    assert {p.url for p in pages} == {
        "https://docs.example.com/",
        "https://docs.example.com/guide",
        "https://docs.example.com/guide/deep",
        "https://docs.example.com/guide/deeper",
    }


async def test_private_root_fails_fast():
    cfg = CrawlConfig(root_url="https://evil.example.com/", domain="evil.example.com")
    f = fetcher()
    with pytest.raises(CrawlError) as e:
        [p async for p in Crawler(cfg, f).run()]
    assert e.value.code == "blocked_address"
    await f.aclose()


async def test_direct_connection_is_pinned_to_the_checked_address(monkeypatch):
    """Without a proxy the crawler connects to the IP it checked, keeping the real Host header."""
    import http.server
    import threading

    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers["Host"]
            body = b"<html><body><p>pinned</p></body></html>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)

    async def resolve(host, port):
        return ["127.0.0.1"] if host == "site.test" else []

    f = Fetcher(resolve=resolve)
    f.allow_private = True
    assert f.pin
    try:
        r = await f.get(f"http://site.test:{port}/page")
    finally:
        await f.aclose()
        server.shutdown()
    assert r.status == 200 and b"pinned" in r.body
    assert seen["host"] == f"site.test:{port}"
