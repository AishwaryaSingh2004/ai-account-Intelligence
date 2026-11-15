"""Fetches a company's own website (homepage + About page) so the Researcher
can work from what the company says about itself instead of model memory.

Best-effort: any failure returns no text and the pipeline carries on as
before. The URL comes from user input on a public app, so fetches are
limited to public http(s) hosts (no localhost, private networks or cloud
metadata addresses), every redirect is re-checked, and time and size are
capped."""
import ipaddress
import re
import socket
import time
import zlib
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src import config

TIMEOUT_SECONDS = config.HTTP_TIMEOUT_SECONDS
TOTAL_BUDGET_SECONDS = config.WEBSITE_TIME_BUDGET_SECONDS   # homepage + About page together
MAX_BYTES = 2_000_000          # per page
MAX_REDIRECTS = 3
ALLOWED_PORTS = (None, 80, 443)   # normal web ports only
MAX_CHARS_PER_PAGE = 3500      # text kept per page, to keep prompts small
ABOUT_PATHS = ("/about", "/about-us", "/company", "/company/about")
TEAM_PATHS = ("/team", "/leadership", "/about/team", "/about/leadership", "/our-team", "/company/leadership")
USER_AGENT = config.USER_AGENT

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def site_url(company_name: str = None, domain: str = None):
    """The homepage to fetch: the given domain, or the company name if it is
    itself a domain (e.g. "fello.ai"). None if neither looks like a domain."""
    for candidate in (domain, company_name):
        host = _host_of(candidate)
        if host:
            return f"https://{host}/"
    return None


def _host_of(value):
    if not value:
        return None
    v = str(value).strip().lower()
    if "://" not in v:
        v = "https://" + v
    host = (urlparse(v).hostname or "").rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host if _DOMAIN_RE.match(host) else None


def _is_public_host(host: str) -> bool:
    """True only if every address the host resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _connected_ip(resp):
    """The IP address the request actually connected to (None if unknown)."""
    try:
        return resp.raw._connection.sock.getpeername()[0]
    except Exception:
        return None


def _read_until(resp, deadline: float) -> bytes:
    """Reads the body until the size cap or the deadline, keeping what
    arrived in time.

    Reads the raw (still compressed) bytes with read1, which returns whatever
    has arrived, with the socket wait set to the time left: a site sending a
    byte at a time can't stall past the deadline (fixed-size reads wait for
    each full chunk). Decompression happens afterwards, capped at MAX_BYTES
    so a tiny "zip bomb" can't expand into gigabytes. (Reading decoded data
    instead stopped early: read1 can return nothing while the decompressor is
    still buffering, which looked like the end of the page.)"""
    raw, data = resp.raw, b""
    while len(data) <= MAX_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            raw._connection.sock.settimeout(max(0.05, remaining))
        except Exception:
            pass
        try:
            chunk = raw.read1(65536, decode_content=False)
        except Exception:
            break  # timed out or connection dropped: keep what we have
        if not chunk:
            break  # end of the (raw) stream
        data += chunk
    encoding = resp.headers.get("Content-Encoding", "").lower().strip()
    if encoding in ("", "identity"):
        return data[:MAX_BYTES]
    if encoding in ("gzip", "x-gzip", "deflate"):
        for wbits in (47, -15):  # gzip or zlib (auto-detected), then raw deflate
            try:
                return zlib.decompressobj(wbits).decompress(data, MAX_BYTES)
            except zlib.error:
                continue
    return b""  # an encoding we didn't ask for


def _safe_get(url: str, deadline: float = None):
    """GET with redirects followed by hand so each hop's host is checked.

    Safety: the host must resolve only to public addresses, and the address
    actually connected to is checked again (a malicious DNS server could
    answer differently the second time). Time: every request and the body
    read share `deadline` (a time.monotonic() value), so a slow or
    slow-dripping site can't stall the report."""
    deadline = deadline or time.monotonic() + TIMEOUT_SECONDS * 2
    for _ in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.port not in ALLOWED_PORTS:
            return None
        if not _is_public_host(parsed.hostname):
            return None
        resp = requests.get(
            url, timeout=min(TIMEOUT_SECONDS, remaining), allow_redirects=False, stream=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
                     "Accept-Encoding": "gzip, deflate"},  # formats _read_until can decompress
        )
        peer = _connected_ip(resp)
        if peer and not ipaddress.ip_address(peer.split("%")[0]).is_global:
            resp.close()
            return None
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                return None
            url = urljoin(url, location)
            continue
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
            resp.close()
            return None
        body = _read_until(resp, deadline)
        resp.close()
        return url, body[:MAX_BYTES], dict(resp.headers)
    return None


def _page_text(html: bytes):
    """Title, meta description and readable body text, plus the soup for link finding."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    description = meta.get("content", "").strip() if meta else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    parts = [p for p in (f"Title: {title}" if title else "", f"Description: {description}" if description else "", body) if p]
    return " | ".join(parts)[:MAX_CHARS_PER_PAGE], soup


_ABOUT_PATH_RE = re.compile(r"/(about|about-us|about_us|company|who-we-are|our-story)/?$")  # path ends in it
_ABOUT_TEXTS = {"about", "about us", "company", "our company", "who we are", "our story"}
_TEAM_PATH_RE = re.compile(r"/(team|our-team|leadership|management|founders)/?$")
_TEAM_TEXTS = {"team", "our team", "leadership", "meet the team", "management", "founders"}


def _team_link(soup, base_url: str):
    """The site's own Team/Leadership page link, if the page has one."""
    base_host = urlparse(base_url).hostname
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        parsed = urlparse(url)
        if parsed.hostname == base_host and (
                _TEAM_PATH_RE.search(parsed.path.lower()) or a.get_text(" ", strip=True).lower() in _TEAM_TEXTS):
            return url
    return None


def _about_link(soup, base_url: str):
    """The site's own About page link, if the homepage has one. Prefers a link
    whose path is an About page, then one whose text is exactly "About"-like
    (so "learn about marketing" doesn't count)."""
    base_host = urlparse(base_url).hostname
    by_path = by_text = None
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        parsed = urlparse(url)
        if parsed.hostname != base_host:
            continue
        if by_path is None and _ABOUT_PATH_RE.search(parsed.path.lower()):
            by_path = url
        if by_text is None and a.get_text(" ", strip=True).lower() in _ABOUT_TEXTS:
            by_text = url
    return by_path or by_text


TEAM_MARKER = "[Team page "


def without_team_pages(text: str) -> str:
    """The site text minus Team/Leadership pages. Staff bios mention other
    companies' facts ("she founded X in 2012", "led a team of 40"), so they're
    used only to confirm people's names, never for company facts or figures."""
    return "\n".join(line for line in (text or "").split("\n") if not line.startswith(TEAM_MARKER))


def fetch_company_site(company_name: str = None, domain: str = None) -> dict:
    """Returns {"url": homepage, "pages": [urls read], "text": combined text,
    "home_html": raw homepage bytes, "home_headers": its HTTP headers}. Empty
    values (never an exception) if there's no domain or the site can't be read."""
    result = {"url": None, "pages": [], "text": "", "home_html": b"", "home_headers": {}}
    start = site_url(company_name, domain)
    if not start:
        return result
    try:
        deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
        home = _safe_get(start, deadline)
        if not home:
            return result
        home_url, home_html, home_headers = home
        result.update(url=home_url, home_html=home_html, home_headers=home_headers)
        text, soup = _page_text(home_html)
        chunks = [f"[Homepage {home_url}] {text}"]
        result["pages"].append(home_url)

        soup_links = None
        candidates = [_about_link(soup, home_url)] + [urljoin(home_url, p) for p in ABOUT_PATHS]
        for url in dict.fromkeys(c for c in candidates if c):
            if time.monotonic() > deadline - 1:
                break  # out of time: the homepage alone is enough
            page = _safe_get(url, deadline)
            if page:
                about_text, about_soup = _page_text(page[1])
                if about_text:
                    chunks.append(f"[About page {page[0]}] {about_text}")
                    result["pages"].append(page[0])
                    soup_links = about_soup
                break

        # Team/leadership page: names on it confirm leaders and contacts.
        team_candidates = [_team_link(soup, home_url)]
        if soup_links is not None:
            team_candidates.append(_team_link(soup_links, home_url))
        team_candidates += [urljoin(home_url, p) for p in TEAM_PATHS]
        for url in dict.fromkeys(c for c in team_candidates if c and c not in result["pages"]):
            if time.monotonic() > deadline - 1:
                break
            page = _safe_get(url, deadline)
            if page and page[0] in result["pages"]:
                continue  # redirected back to a page already read (often the homepage)
            if page:
                team_text, _ = _page_text(page[1])
                if team_text:
                    chunks.append(f"{TEAM_MARKER}{page[0]}] {team_text}")
                    result["pages"].append(page[0])
                break
        result["text"] = "\n".join(chunks)
    except Exception:
        # Website reading is an optional boost; never let it fail the report.
        result["text"] = ""
        return result
    return result
