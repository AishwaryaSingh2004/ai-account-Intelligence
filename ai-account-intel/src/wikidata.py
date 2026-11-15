"""Verified company facts from Wikidata (the structured data behind
Wikipedia): headquarters, founding year, current CEO, employee count,
stock listing and revenue. Free, no key. Only well-known companies have an
entry, so most small companies simply get nothing.

Picking the right entity matters ("Ramp" is also a road feature, "Stripe" a
beetle): with a known website, only an entity whose official website
matches is used; without one, only an exact name match that is clearly a
company. Best-effort: any failure means "no Wikidata facts"."""
import re
from datetime import date

import requests

from src import config

API = "https://www.wikidata.org/w/api.php"
TIMEOUT_SECONDS = config.HTTP_TIMEOUT_SECONDS
# Wikimedia asks API clients to identify themselves.
HEADERS = {"User-Agent": config.USER_AGENT}
COMPANY_PROPS = ("P169", "P159", "P1128", "P452", "P414")  # CEO, HQ, employees, industry, stock exchange
USD = "Q4917"
BUILDINGS = {"Q41176", "Q11303", "Q1021645", "Q18142", "Q1081138", "Q15206070"}  # building, skyscraper, office building, high-rise, campus, corporate HQ
_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc",
             "technologies", "technology", "tech", "labs", "hq", "ai", "io", "app", "software", "group", "holdings"}


def _get(params):
    try:
        r = requests.get(API, params={**params, "format": "json"}, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _core_name(s) -> str:
    words = re.findall(r"[a-z0-9]+", str(s or "").lower())
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return "".join(words)


def _host(url) -> str:
    h = re.sub(r"^[a-z]+://", "", str(url or "").strip().lower()).split("/")[0].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def _same_site(official, domain_host) -> bool:
    h = _host(official)
    return bool(h and domain_host and (h == domain_host or h.endswith("." + domain_host) or domain_host.endswith("." + h)))


def _candidates(name: str) -> list:
    """Entity ids from the name search plus a full-text search limited to
    entries that have a website and an industry (i.e. businesses)."""
    ids = []
    found = _get({"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 7}) or {}
    ids += [r["id"] for r in found.get("search", [])]
    text = _get({"action": "query", "list": "search", "srlimit": 10,
                 "srsearch": f"{name} haswbstatement:P856 haswbstatement:P452"}) or {}
    ids += [h["title"] for h in text.get("query", {}).get("search", [])]
    return list(dict.fromkeys(i for i in ids if re.fullmatch(r"Q\d+", i)))[:15]


def _entities(ids, props="claims|labels|aliases|sitelinks") -> dict:
    if not ids:
        return {}
    # "mul" = multilingual: newer entries (e.g. many people) only have that label.
    data = _get({"action": "wbgetentities", "ids": "|".join(ids), "props": props, "languages": "en|mul"}) or {}
    return data.get("entities", {})


def _label(entity):
    labels = (entity or {}).get("labels", {})
    return (labels.get("en") or labels.get("mul") or {}).get("value")


def _value(statement):
    return ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")


def _qual(statement, prop):
    vals = (statement.get("qualifiers") or {}).get(prop) or []
    return ((vals[0].get("datavalue") or {}).get("value")) if vals else None


def _year(time_value):
    m = re.match(r"[+-]?(\d{1,4})-", (time_value or {}).get("time", "") if isinstance(time_value, dict) else "")
    return int(m.group(1)) if m else None


def _current(statements):
    """Statements still in effect (no end date, not deprecated), best rank first,
    then the most recent start / point in time."""
    live = [s for s in statements or [] if s.get("rank") != "deprecated" and not _qual(s, "P582")]
    rank = {"preferred": 0, "normal": 1}
    return sorted(live, key=lambda s: (rank.get(s.get("rank"), 2),
                                       -(_year(_qual(s, "P580")) or _year(_qual(s, "P585")) or 0)))


def _is_company(entity) -> bool:
    return any(p in entity.get("claims", {}) for p in COMPANY_PROPS)


def _names(entity) -> set:
    names = {_label(entity) or ""}
    for lang in ("en", "mul"):
        names |= {a.get("value", "") for a in entity.get("aliases", {}).get(lang, [])}
    return {_core_name(n) for n in names if n}


def find_entity(company_name: str, domain: str = None):
    """The Wikidata entity for this company, or None if there's no confident match."""
    host = _host(domain) if domain else ""
    query = company_name or (host.split(".")[0] if host else "")
    if not query or len(_core_name(query)) < 2:
        return None
    entities = _entities(_candidates(query))
    if host:
        # Website known: the entity's official website must match it.
        matches = [e for e in entities.values()
                   if any(_same_site(_value(s), host) for s in e.get("claims", {}).get("P856", []))]
    else:
        # Name only: exact name (ignoring "Inc", ".io" etc.) and clearly a company.
        target = _core_name(company_name)
        matches = [e for e in entities.values() if _is_company(e) and target in _names(e)]
    # Several matches: the best-documented one (most Wikipedia language editions).
    return max(matches, key=lambda e: len(e.get("sitelinks", {}))) if matches else None


def facts(company_name: str = None, domain: str = None):
    """Returns verified facts for the company, or None. Never raises."""
    try:
        entity = find_entity(company_name, domain)
        if not entity:
            return None
        claims = entity.get("claims", {})
        hq = next(iter(_current(claims.get("P159"))), None)
        country = next(iter(_current(claims.get("P17"))), None)
        ceo = next(iter(_current(claims.get("P169"))), None)
        employees = sorted((s for s in claims.get("P1128", []) if s.get("rank") != "deprecated" and _value(s)),
                           key=lambda s: (_year(_qual(s, "P585")) or 0, s.get("rank") == "preferred"))
        revenue = sorted((s for s in claims.get("P2139", []) if isinstance(_value(s), dict)
                          and _value(s).get("unit", "").endswith("/" + USD)),
                         key=lambda s: _year(_qual(s, "P585")) or 0)
        listing = next(iter(_current(claims.get("P414"))), None)

        ids = [(_value(s) or {}).get("id") for s in (hq, country, ceo, listing) if s]
        linked = _entities([i for i in ids if i], "labels|claims")
        label = lambda s: _label(linked.get((_value(s) or {}).get("id"))) if s else None

        out = {"qid": entity["id"], "name": _label(entity)}
        hq_name, country_name = label(hq), label(country)
        if hq_name:
            # Some companies list a building (e.g. "Salesforce Tower"): add its city.
            hq_entity = linked.get((_value(hq) or {}).get("id"), {})
            kinds = {(_value(c) or {}).get("id") for c in hq_entity.get("claims", {}).get("P31", [])}
            if kinds & BUILDINGS:
                city = next(iter(_current(hq_entity.get("claims", {}).get("P131"))), None)
                city_name = _label(_entities([(_value(city) or {}).get("id")], "labels").get((_value(city) or {}).get("id"))) if city else None
                if city_name:
                    hq_name = f"{hq_name}, {city_name}"
            out["headquarters"] = hq_name if not country_name or country_name in hq_name else f"{hq_name}, {country_name}"
        founded = _year(_value(next(iter(_current(claims.get("P571"))), {}) or {}))
        if founded and 1600 < founded <= date.today().year:
            out["founded_year"] = founded
        if label(ceo):
            out["ceo"] = label(ceo)
        if employees:
            amount = int(float(_value(employees[-1])["amount"]))
            if amount > 0:
                out["employee_count"], out["employees_as_of"] = amount, _year(_qual(employees[-1], "P585"))
        if listing and label(listing):
            ticker = _qual(listing, "P249")
            out["listing"] = f"{label(listing)}: {ticker}" if ticker else label(listing)
        if revenue:
            out["revenue_usd"] = int(float(_value(revenue[-1])["amount"]))
            out["revenue_year"] = _year(_qual(revenue[-1], "P585"))
        wiki = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
        out["source"] = ({"title": f"Wikipedia: {wiki}", "url": "https://en.wikipedia.org/wiki/" + wiki.replace(" ", "_")}
                         if wiki else {"title": f"Wikidata: {out['name'] or entity['id']}", "url": f"https://www.wikidata.org/wiki/{entity['id']}"})
        return out if len(out) > 3 else None  # only qid/name/source: nothing useful
    except Exception:
        return None


def _money(n: int) -> str:
    for size, unit in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if n >= size:
            return f"${n / size:.1f} {unit}".replace(".0 ", " ")
    return f"${n:,}"


_CEO_TITLE = re.compile(r"\b(ceo|chief executive)\b", re.I)


def apply_to_research(research: dict, f, website_text: str = "") -> dict:
    """Overwrites the AI's HQ, founding year and employee count with Wikidata's,
    puts the current CEO first in leadership, and adds one facts signal.
    A CEO named on the company's own website is kept over Wikidata's (the
    site is usually more up to date)."""
    if not f:
        return research
    company = research.setdefault("company", {})
    sources = company.setdefault("fact_sources", {})
    for key in ("headquarters", "founded_year"):
        if f.get(key):
            company[key], sources[key] = f[key], "Wikidata"
    # Headcount changes fast: only replace the AI's figure with a recent one
    # (older figures still appear, dated, in the facts signal).
    as_of = f.get("employees_as_of")
    if f.get("employee_count") and (as_of is None or as_of >= date.today().year - 2):
        company["employee_count"] = f["employee_count"]
        sources["employee_count"] = f"Wikidata, {as_of}" if as_of else "Wikidata"

    if f.get("ceo"):
        leaders = research.setdefault("leadership", [])
        site = (website_text or "").lower()
        site_ceo = next((p for p in leaders if _CEO_TITLE.search(p.get("title", "")) and site
                         and p.get("name", "").lower() in site and _core_name(p["name"]) != _core_name(f["ceo"])), None)
        if not site_ceo:
            others = [p for p in leaders if _core_name(p.get("name")) != _core_name(f["ceo"])
                      and not _CEO_TITLE.search(p.get("title", ""))]
            research["leadership"] = [{"name": f["ceo"], "title": "CEO"}] + others

    parts = []
    if f.get("headquarters"):
        parts.append(f"HQ {f['headquarters']}")
    if f.get("founded_year"):
        parts.append(f"founded {f['founded_year']}")
    if f.get("employee_count"):
        parts.append(f"~{f['employee_count']:,} employees" + (f" (as of {f['employees_as_of']})" if f.get("employees_as_of") else ""))
    if f.get("ceo"):
        parts.append(f"CEO {f['ceo']}")
    if f.get("listing"):
        parts.append(f"listed on {f['listing']}")
    if f.get("revenue_usd"):
        parts.append(f"revenue {_money(f['revenue_usd'])}" + (f" ({f['revenue_year']})" if f.get("revenue_year") else ""))
    if parts:
        research.setdefault("business_signals", []).append("Verified facts (Wikidata): " + "; ".join(parts))
    if f.get("source"):
        research["wikidata_source"] = f["source"]
    return research
