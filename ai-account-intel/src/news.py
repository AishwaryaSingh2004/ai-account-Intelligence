"""Free recent news from Google News RSS (no key, no quota), used when
Gemini's search-grounded news isn't available.

Relevance: the query pairs the company name with words from its industry
("Ramp" alone returns road closures; "Ramp" + fintech returns the funding
round), only headlines that name the company are kept, and headlines about
business events (funding, launches, acquisitions, layoffs…) rank first.

GDELT, the other free option, isn't used: it allows one request per 5
seconds per IP, which a shared cloud server can't rely on.
Best-effort: any failure returns None."""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import requests

from src import config

TIMEOUT_SECONDS = config.HTTP_TIMEOUT_SECONDS
MAX_ITEMS = config.NEWS_MAX_ITEMS
HEADERS = {"User-Agent": config.USER_AGENT}

_GENERIC = {"technology", "technologies", "tech", "software", "services", "service", "development", "solutions",
            "company", "companies", "industry", "and", "the", "for", "products", "product", "platform", "platforms",
            "other", "information", "global", "systems", "provider", "inc", "management", "business"}
_PHRASES = re.compile(r"real estate|financial services|consumer electronics|health ?care|e-?commerce|cyber ?security|"
                      r"artificial intelligence|machine learning|human resources|supply chain|venture capital")
_EVENTS = re.compile(r"\b(raises?|raised|funding|round|series [a-f]|valuation|valued|ipo|acquires?|acquired|acquisition|"
                     r"merger|launch(es|ed)?|unveils?|announces?|partners?|partnership|layoffs?|lays off|cuts|hires?|"
                     r"appoints?|names|ceo|cfo|cto|revenue|arr|earnings|profit|expands?|expansion|opens|customers?|"
                     r"lawsuit|sues|sued|fined|breach|outage|deal|agreement|investment|invests?)\b", re.I)


# Evergreen pages that Google News also returns but aren't news.
_NOT_NEWS = re.compile(r"stock price|stock quote|quote & history|price target|company (overview|profile)|"
                       r"\breviews?\b|\balternatives\b|\bpricing\b|\bvs\.?\s|what is |how to |\bguide\b|"
                       r"\bstats\b|statistics|\bcareers\b|\bjobs at\b", re.I)
_PERSON_TITLES = "Elder|Mr|Mrs|Ms|Dr|Sen|Senator|President|Pastor|Bishop|Rev|Coach|Judge|Officer|Chef|Sister|Brother|Prof|Professor|Rep|Gov|Mayor"


def _context_terms(industry: str) -> list:
    text = (industry or "").lower()
    phrases = list(dict.fromkeys(m.group(0) for m in _PHRASES.finditer(text)))
    words = [w for w in re.findall(r"[a-z][a-z-]+", text)
             if len(w) > 2 and w not in _GENERIC and not any(w in p for p in phrases)]
    return list(dict.fromkeys(phrases + words))[:4]


def build_query(name: str, industry: str = None) -> str:
    name = re.sub(r'["“”]', "", name).strip()  # a quote inside the name would break the exact-phrase search
    terms = _context_terms(industry)
    context = " (" + " OR ".join(f'"{t}"' if " " in t else t for t in terms) + ")" if terms else ""
    return f'"{name}"{context} when:1y'


def _iso_date(pub) -> str:
    try:
        return parsedate_to_datetime(pub).date().isoformat()
    except Exception:
        return ""


_AMOUNT = re.compile(r"\$\s?\d[\d.,]*\s?(?:bn\b|mn\b|[bmk]\b|billion|million|thousand)?", re.I)


def _overlaps(a: str, b: str) -> bool:
    """Same story from two outlets: most words shared, or the same money
    amount ("$1.85B" in both)."""
    amounts = lambda t: {re.sub(r"(?<=[bm])n$", "", re.sub(r"[\s,]|illion", "", m.group(0).lower())) for m in _AMOUNT.finditer(t)}
    am_a, am_b = amounts(a), amounts(b)
    if am_a and am_b:
        return bool(am_a & am_b)  # same amount = same story; different amounts = different stories
    wa, wb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


# Headlines say "Apple", not "Apple Inc.", so legal suffixes are dropped.
_LEGAL_SUFFIX = re.compile(r"(?:,?\s+(?:inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|plc|gmbh|ag|"
                           r"s\.?a|pvt|private|co)\.?)+$", re.I)


def short_name(name: str) -> str:
    """"Apple Inc." -> "Apple", "Comcast IP Services, L.L.C." -> "Comcast IP Services"."""
    return _LEGAL_SUFFIX.sub("", name).strip() or name


def free_news(company_name: str, industry: str = None):
    """{"recent_news": [...], "sources": [...]} from Google News, or None if the
    lookup failed. An empty list (no relevant news) is a valid answer."""
    name = short_name((company_name or "").strip())
    if len(re.sub(r"\W", "", name)) < 2:
        return None
    try:
        url = f"https://news.google.com/rss/search?q={quote(build_query(name, industry))}&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
    except Exception:
        return None

    # Case-sensitive ("Ramp", not "ramp up production"), but all-caps headlines count.
    names_company = re.compile(rf"(?<![\w])(?:{re.escape(name)}|{re.escape(name.upper())})(?![\w])")
    # Headlines are Title Case, so also reject the name used as a verb ("Houthis
    # Ramp Up Attacks") or as a person's name ("Elder Gong", "Dr. Fello").
    not_company = re.compile(rf"(?:\b(?:{_PERSON_TITLES})\.?\s+(?:{re.escape(name)})\b)"
                             rf"|(?:\b{re.escape(name)}\s+(?:Up|Down|Out|Off)\b)")
    candidates = []
    for item in root.iter("item"):
        title, link = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()
        publisher = (item.findtext("source") or "").strip()
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        title = title.rstrip(" -–—|:").strip()  # some feeds leave a dangling separator
        if (not title or not names_company.search(title) or not_company.search(title)
                or _NOT_NEWS.search(title) or urlparse(link).scheme != "https"):
            continue
        candidates.append({"headline": title[:200], "date": _iso_date(item.findtext("pubDate")),
                           "publisher": publisher[:80], "url": link, "event": bool(_EVENTS.search(title))})

    # Business events first, newest first; drop repeats of the same story.
    candidates.sort(key=lambda c: (c["event"], c["date"]), reverse=True)
    picked = []
    for c in candidates:
        if not any(_overlaps(c["headline"], p["headline"]) for p in picked):
            picked.append(c)
        if len(picked) == MAX_ITEMS:
            break
    return {
        "recent_news": [{"headline": p["headline"],
                         "summary": f"Reported by {p['publisher']} (via Google News)." if p["publisher"] else "Via Google News.",
                         "date": p["date"]} for p in picked],
        "sources": [{"title": f"{p['publisher'] or 'News'}: {p['headline'][:70]}", "url": p["url"]} for p in picked],
    }
