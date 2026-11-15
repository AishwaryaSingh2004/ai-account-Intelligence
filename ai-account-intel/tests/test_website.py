"""Reading the company's website: safety (public app, user-supplied URLs),
time limits, and text extraction."""
import http.server
import threading
import time
import types

import pytest
import requests

import src.website as W

REAL_GET, REAL_SESSION_REQUEST = requests.get, requests.Session.request


def test_only_domains_trigger_a_fetch():
    assert W.site_url("Fello", "https://www.Fello.ai/pricing") == "https://fello.ai/"
    assert W.site_url("stripe.com") == "https://stripe.com/"
    assert W.site_url("BrightPath Lending") is None
    assert W.site_url("127.0.0.1") is None and W.site_url("localhost") is None and W.site_url(None, "169.254.169.254") is None


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/",
                                 "http://example.com:22/", "http://metadata.google.internal/"])
def test_internal_and_odd_urls_are_blocked_before_any_request(url):
    assert W._safe_get(url) is None  # the autouse fixture fails the test if a request is sent


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "_is_public_host", lambda host: host == "example.com")
    monkeypatch.setattr(W.requests, "get", lambda url, **k: calls.append(url) or types.SimpleNamespace(
        is_redirect=True, status_code=302, headers={"Location": "http://169.254.169.254/latest/"}, close=lambda: None, raw=None))
    assert W._safe_get("https://example.com/") is None and calls == ["https://example.com/"]


def test_dns_rebinding_is_caught_at_connection_time(monkeypatch):
    """The name looked public when checked, but the connection went to an internal address."""
    monkeypatch.setattr(W, "_is_public_host", lambda host: True)
    closed = []
    monkeypatch.setattr(W.requests, "get", lambda url, **k: types.SimpleNamespace(
        is_redirect=False, status_code=200, headers={"Content-Type": "text/html"},
        close=lambda: closed.append(1), raw=types.SimpleNamespace(read1=lambda n, decode_content=True: b"")))
    monkeypatch.setattr(W, "_connected_ip", lambda resp: "10.0.0.7")
    assert W._safe_get("https://looks-public.example/") is None and closed


class _Drip(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        try:
            for _ in range(60):  # 1 byte per 0.25 s, forever-ish
                self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.25)
        except Exception:
            pass

    def log_message(self, *a):
        pass


def test_slow_dripping_site_is_cut_off(monkeypatch):
    monkeypatch.setattr(requests, "get", REAL_GET)
    monkeypatch.setattr(requests.Session, "request", REAL_SESSION_REQUEST)
    monkeypatch.setattr(W, "_is_public_host", lambda host: True)  # allow the local test server
    monkeypatch.setattr(W, "_connected_ip", lambda resp: None)
    monkeypatch.setattr(W, "ALLOWED_PORTS", (None, 80, 443, "any"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Drip)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start = time.monotonic()
        monkeypatch.setattr(W, "ALLOWED_PORTS", (None, 80, 443, server.server_port))
        result = W._safe_get(f"http://127.0.0.1:{server.server_port}/", deadline=time.monotonic() + 2)
        assert time.monotonic() - start < 4  # stopped near the 2 s budget, not after 15 s
        assert result is not None and 0 < len(result[1]) < 60  # kept what arrived in time
    finally:
        server.shutdown()


def test_text_extraction_and_about_link():
    html = (b'<html><head><title>Acme | AI for lenders</title><meta name="description" content="Acme helps lenders.">'
            b'<script>var secret=1</script></head><body><nav>Home Pricing</nav><h1>Grow</h1><p>Founded 2019 in Austin.</p>'
            b'<a href="/products/x">Learn about marketing</a><a href="/about-us">About us</a><footer>(c)</footer></body></html>')
    text, soup = W._page_text(html)
    assert "AI for lenders" in text and "Acme helps lenders" in text and "Austin" in text
    assert "secret" not in text and "Home Pricing" not in text
    assert W._about_link(soup, "https://acme.com/") == "https://acme.com/about-us"
    off_site = W.BeautifulSoup(b'<a href="https://evil.com/about">About</a>', "html.parser")
    assert W._about_link(off_site, "https://acme.com/") is None


def test_unreachable_site_returns_empty_text(monkeypatch):
    monkeypatch.setattr(W, "_is_public_host", lambda host: False)
    assert W.fetch_company_site("Nope", "nope.example")["text"] == ""


def _fake_response(chunks, encoding=""):
    stream = iter(chunks)
    return types.SimpleNamespace(headers={"Content-Encoding": encoding},
                                 raw=types.SimpleNamespace(read1=lambda n, decode_content=True: next(stream, b"")))


def test_compressed_pages_are_read_completely():
    """Regression: decoding while reading stopped at the first empty read,
    keeping only ~10% of gzip-compressed pages."""
    import gzip
    page = b"<html>" + b"<p>HubSpot tracking js.hs-scripts.com</p>" * 20000 + b"</html>"
    packed = gzip.compress(page)
    chunks = [packed[i:i + 1000] for i in range(0, len(packed), 1000)]
    assert W._read_until(_fake_response(chunks, "gzip"), time.monotonic() + 5) == page[:W.MAX_BYTES]


def test_zip_bomb_is_capped():
    import gzip
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))  # ~50 KB that expands to 50 MB
    out = W._read_until(_fake_response([bomb], "gzip"), time.monotonic() + 5)
    assert len(out) <= W.MAX_BYTES


def test_unknown_encoding_is_not_misread():
    assert W._read_until(_fake_response([b"\x8b\x00garbage"], "br"), time.monotonic() + 5) == b""
