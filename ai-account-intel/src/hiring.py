"""Hiring signals from a company's public job board (Greenhouse, Lever or
Ashby, all free and keyless). Open roles show where a company is growing
("hiring 4 SDRs") and the tools its teams use ("experience with Salesforce").

Finding the right board: a job-board link on the company's own website is
trusted; otherwise likely board names are tried, and accepted only if the
board's company name matches (a guessed "acme" board could belong to a
different Acme). Best-effort: any failure means "no hiring data".

Only fixed API hosts are called, with a sanitized board name, so this can't
be pointed at arbitrary URLs. Job text is never put into a prompt verbatim;
only short, derived summaries are."""
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src import config, website

TIMEOUT_SECONDS = config.HTTP_TIMEOUT_SECONDS
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

# Board links that may appear on a company's website.
_BOARD_LINKS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)")),
    ("rippling", re.compile(r"api\.rippling\.com/platform/api/ats/v\d/board/([a-z0-9_-]+)")),
    ("rippling", re.compile(r"ats\.rippling\.com/([a-z0-9_-]+)/jobs")),
]
_NOT_TOKENS = {"embed", "js", "jobs", "job_board", "api", "v1"}
_CAREERS_TEXT = {"careers", "jobs", "join us", "we're hiring", "we’re hiring", "open roles", "open positions", "work with us", "join our team"}
_CAREERS_PATH = re.compile(r"/(careers?|jobs|join-us|work-with-us|open-roles)/?$")

# Teams, from job titles (first match wins).
_TEAMS = [
    ("Sales", re.compile(r"\b(sdr|bdr|sales|account executive|\bae\b|account manager|business development|revenue|partnerships?)\b")),
    ("Marketing", re.compile(r"\b(marketing|growth|demand gen|brand|content|seo|communications|pr)\b")),
    ("Customer Success", re.compile(r"\b(customer success|support|onboarding|implementation|solutions? (consultant|engineer)|customer experience)\b")),
    ("Engineering", re.compile(r"\b(engineer|engineering|developer|devops|sre|architect|software|security|infrastructure|ml|machine learning|data scientist)\b")),
    ("Product & Design", re.compile(r"\b(product manager|product|design|designer|ux|ui|research)\b")),
    ("Data", re.compile(r"\b(data|analytics|analyst|bi)\b")),
    ("Operations & G&A", re.compile(r"\b(operations|ops|finance|accounting|legal|people|recruit|talent|hr|admin|office)\b")),
]
_SDR = re.compile(r"\b(sdr|bdr|sales development|business development rep)")
_AE = re.compile(r"\b(account executive|\bae\b)")
_LEADER = re.compile(r"\b(vp|vice president|head of|director|chief|cmo|cro|cto|cfo|coo|ceo|general manager)\b")

# Tools worth knowing about when selling to a company, and the tech-stack
# field each one fills (None = mentioned in the signal only).
_TOOLS = {
    "Salesforce": "crm", "HubSpot": "crm", "Microsoft Dynamics": "crm", "Pipedrive": "crm", "Zoho CRM": "crm",
    "Marketo": "marketing_automation", "Pardot": "marketing_automation", "Eloqua": "marketing_automation",
    "Braze": "marketing_automation", "Iterable": "marketing_automation", "Klaviyo": "marketing_automation",
    "Customer.io": "marketing_automation", "Mailchimp": "marketing_automation",
    "Google Analytics": "analytics", "Amplitude": "analytics", "Mixpanel": "analytics", "Segment": "analytics",
    "Heap": "analytics", "Looker": "analytics", "Tableau": "analytics", "Power BI": "analytics",
    "Snowflake": None, "Databricks": None, "BigQuery": None, "Redshift": None, "dbt": None,
    "AWS": None, "GCP": None, "Azure": None, "Kubernetes": None, "Terraform": None,
    "Python": None, "Go": None, "Java": None, "TypeScript": None, "React": None, "Node.js": None,
    "Outreach": None, "Salesloft": None, "Gong": None, "ZoomInfo": None, "Apollo": None, "Clari": None,
    "LinkedIn Sales Navigator": None, "6sense": None, "Zendesk": None, "Intercom": None, "Jira": None, "NetSuite": None,
}
_TOOL_RE = {
    name: re.compile(
        r"(?<![\w.])" + {
            "Go": r"golang",
            "React": r"react\.?js|react native",
            "Apollo": r"apollo\.io",
            "Outreach": r"outreach\.io|outreach\b(?=[^.]{0,40}(?:salesloft|crm|sequenc))",
            "Segment": r"segment(?:\.io| \(twilio\))|twilio segment",
            "Heap": r"heap analytics|\bheap\b(?=[^.]{0,30}(?:amplitude|mixpanel|analytics))",
            "Microsoft Dynamics": r"(?:microsoft )?dynamics 365|ms dynamics",
            "GCP": r"\bgcp\b|google cloud",
        }.get(name, re.escape(name.lower())) + r"(?![\w])",
    )
    for name in _TOOLS
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc",
             "technologies", "technology", "tech", "labs", "hq", "ai", "io", "app", "software", "group", "careers", "jobs"}


def _core_name(s) -> str:
    words = re.findall(r"[a-z0-9]+", str(s or "").lower())
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return "".join(words)


def _names_match(company: str, other: str) -> bool:
    """Same company, ignoring legal/branding suffixes: "Stripe" = "Stripe, Inc.",
    but "Acme" != "Acme Brick Company" (a different business)."""
    a, b = _core_name(company), _core_name(other)
    return bool(a and b and a == b)


def _get_json(url: str):
    try:
        r = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": website.USER_AGENT})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _strip_html(text) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True) if text else ""


# ---- Providers: each returns (board_company_name or None, [job dicts]) or None.
def _greenhouse(token):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if not data or "jobs" not in data:
        return None
    meta = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}") or {}
    jobs = [{
        "title": j.get("title", ""),
        "team": ", ".join(d.get("name", "") for d in j.get("departments") or []),
        "location": (j.get("location") or {}).get("name", ""),
        "text": _strip_html(_strip_html(j.get("content"))),  # content is HTML-escaped HTML
    } for j in data["jobs"]]
    names = {meta.get("name")} | {j.get("company_name") for j in data["jobs"][:5]}
    return next((n for n in names if n), None), jobs, f"https://job-boards.greenhouse.io/{token}"


def _lever(token):
    data = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(data, list):
        return None
    jobs = [{
        "title": p.get("text", ""),
        "team": (p.get("categories") or {}).get("team", "") or (p.get("categories") or {}).get("department", ""),
        "location": (p.get("categories") or {}).get("location", ""),
        "text": " ".join(filter(None, [p.get("descriptionPlain"), p.get("additionalPlain"),
                                       " ".join(_strip_html(l.get("content")) for l in p.get("lists") or [])])),
    } for p in data]
    return None, jobs, f"https://jobs.lever.co/{token}"


def _ashby(token):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if not data or "jobs" not in data:
        return None
    jobs = [{
        "title": j.get("title", ""),
        "team": j.get("department") or j.get("team") or "",
        "location": j.get("location", ""),
        "text": j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml")),
    } for j in data["jobs"] if j.get("isListed", True)]
    return None, jobs, f"https://jobs.ashbyhq.com/{token}"


def _rippling(token):
    base = f"https://api.rippling.com/platform/api/ats/v2/board/{token}/jobs"
    first = _get_json(f"{base}?page=0")
    if not first or "items" not in first:
        return None
    items = list(first["items"])
    for page in range(1, min(int(first.get("totalPages") or 1), 5)):
        more = _get_json(f"{base}?page={page}")
        items += (more or {}).get("items") or []
    # The list has no descriptions; read them for the first 20 roles (in parallel).
    with ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(lambda j: _get_json(f"{base}/{j.get('id')}") or {}, items[:20]))
    details += [{}] * (len(items) - len(details))
    jobs = [{
        "title": j.get("name", ""),
        "team": (j.get("department") or {}).get("name", ""),
        "location": "; ".join(l.get("name", "") for l in j.get("locations") or []),
        "text": " ".join(_strip_html(v) for v in ((d.get("description") or {}).values() if isinstance(d.get("description"), dict) else [d.get("description")]) if v),
    } for j, d in zip(items, details)]
    name = next(((d.get("board") or {}).get("companyName") for d in details if (d.get("board") or {}).get("companyName")), None)
    return name, jobs, f"https://ats.rippling.com/{token}/jobs"


_PROVIDERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "rippling": _rippling}
_PROVIDER_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "rippling": "Rippling"}
# Boards guessed from the company name (Rippling board names are custom, so
# Rippling is only used when the company's website links to it).
_GUESSABLE = ("greenhouse", "lever", "ashby")


def _boards_in(html) -> list:
    text = (html.decode("utf-8", "ignore") if isinstance(html, bytes) else html or "").lower()
    found = []
    for provider, rx in _BOARD_LINKS:
        for token in rx.findall(text):
            if token not in _NOT_TOKENS and _TOKEN_RE.match(token) and (provider, token) not in found:
                found.append((provider, token))
    return found


def _careers_page(home_html, home_url):
    """The company's own Careers/Jobs page, if the homepage links to one."""
    if not home_html or not home_url:
        return None
    soup = BeautifulSoup(home_html, "html.parser")
    for a in soup.find_all("a", href=True):
        url = urljoin(home_url, a["href"])
        if urlparse(url).scheme not in ("http", "https"):
            continue
        if a.get_text(" ", strip=True).lower() in _CAREERS_TEXT or _CAREERS_PATH.search(urlparse(url).path.lower()):
            try:
                return website._safe_get(url)  # same public-address-only fetcher
            except Exception:
                return None
    return None


def _guess_tokens(company_name, domain) -> list:
    tokens = []
    root = (website._host_of(domain) or website._host_of(company_name) or "").split(".")[0]
    name = str(company_name or "")
    for t in (root, _norm(name), re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")):
        if t and _TOKEN_RE.match(t) and t not in tokens:
            tokens.append(t)
    return tokens[:3]


def _verified_by_text(company, jobs) -> bool:
    """Lever/Ashby boards don't state the company name, so a guessed board is
    accepted only if its job posts mention the company."""
    target = str(company or "").lower().strip()
    if len(_norm(target)) < 3:
        return False
    hits = sum(1 for j in jobs[:10] if target in (j["text"] or "").lower())
    return hits >= min(2, len(jobs[:10]))


def find_jobs(company_name: str = None, domain: str = None, home_html=None, home_url: str = None):
    """Returns {"provider", "board_url", "jobs"} for the company's job board,
    or None if no board could be found and confirmed."""
    company = company_name if not website._host_of(company_name) else (website._host_of(company_name) or "").split(".")[0]

    # 1) Board links on the company's own site (homepage, then Careers page): trusted.
    candidates = _boards_in(home_html)
    if not candidates:
        careers = _careers_page(home_html, home_url)
        if careers:
            candidates = _boards_in(careers[1])
    for provider, token in candidates[:3]:
        result = _PROVIDERS[provider](token)
        if result and result[1]:
            return {"provider": _PROVIDER_NAMES[provider], "board_url": result[2], "jobs": result[1]}

    # 2) Guessed board names: only accepted when the board confirms the company.
    guesses = [(p, t) for t in _guess_tokens(company_name, domain) for p in _GUESSABLE]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda pt: (pt, _PROVIDERS[pt[0]](pt[1])), guesses))
    confirmed = []
    for (provider, token), result in results:
        if not result or not result[1]:
            continue
        board_name, jobs, url = result
        if _names_match(company, board_name) if board_name else _verified_by_text(company, jobs):
            confirmed.append({"provider": _PROVIDER_NAMES[provider], "board_url": url, "jobs": jobs})
    # Companies sometimes leave an old, near-empty board behind on another
    # platform: the one with the most open roles is the live one.
    return max(confirmed, key=lambda b: len(b["jobs"])) if confirmed else None


def summarize(found) -> dict:
    """Turns raw job posts into short sales signals and tool mentions."""
    jobs = found["jobs"]
    titles = [j["title"].lower() for j in jobs]
    teams = Counter()
    for t in titles:
        teams[next((name for name, rx in _TEAMS if rx.search(t)), "Other")] += 1
    sdrs = sum(1 for t in titles if _SDR.search(t))
    aes = sum(1 for t in titles if _AE.search(t))
    leaders = [j["title"].strip()[:80] for j in jobs if _LEADER.search(j["title"].lower())]
    places = Counter(loc.strip() for j in jobs for loc in re.split(r"[;|]", j["location"] or "") if loc.strip())
    tools = Counter()
    for j in jobs:
        blob = " " + (j["title"] + " " + (j["text"] or "")).lower() + " "
        for name, rx in _TOOL_RE.items():
            if rx.search(blob):
                tools[name] += 1

    n = len(jobs)
    team_text = ", ".join(f"{c} {name}" for name, c in teams.most_common() if name != "Other")
    signals = [f"Hiring: {n} open role{'s' if n != 1 else ''} on {found['provider']}" + (f" ({team_text})" if team_text else "")]
    if sdrs or aes:
        parts = ([f"{sdrs} SDR/BDR{'s' if sdrs != 1 else ''}"] if sdrs else []) + ([f"{aes} Account Executive{'s' if aes != 1 else ''}"] if aes else [])
        signals.append("Sales team growth: hiring " + " and ".join(parts))
    if leaders:
        signals.append("Leadership hiring: " + "; ".join(dict.fromkeys(leaders[:3])) + (f" (+{len(leaders) - 3} more)" if len(leaders) > 3 else ""))
    if places:
        signals.append("Hiring locations: " + ", ".join(p for p, _ in places.most_common(4)))
    if tools:
        signals.append("Job posts mention: " + ", ".join(f"{name} ({c})" for name, c in tools.most_common(8)))
    return {"signals": signals, "tools": dict(tools), "board_url": found["board_url"], "provider": found["provider"], "open_roles": n}


def apply_to_research(research: dict, summary, unlabelled_values_are_guesses: bool = False) -> dict:
    """Adds hiring signals and the job board as a source; uses tools named in
    job posts for tech-stack fields that are still empty or only AI guesses.
    When the website's code wasn't scanned, every tech value is an AI guess
    even though it isn't labelled as one."""
    if not summary:
        return research
    research.setdefault("business_signals", []).extend(summary["signals"])
    tech = research.setdefault("tech_stack", {})
    for field in ("crm", "marketing_automation", "analytics"):
        current = tech.get(field)
        if current and not str(current).endswith("(AI guess)") and not unlabelled_values_are_guesses:
            continue  # detected in the website's code: keep it
        named = [t for t, c in sorted(summary["tools"].items(), key=lambda kv: -kv[1]) if _TOOLS.get(t) == field]
        if named:
            tech[field] = f"{named[0]} (from job posts)"  # the most-mentioned one
    research["hiring_source"] = {"title": f"Open roles on {summary['provider']}", "url": summary["board_url"]}
    return research


def hiring_signals(company_name: str = None, domain: str = None, home_html=None, home_url: str = None):
    """Best-effort wrapper: a summary dict, or None (never raises)."""
    try:
        found = find_jobs(company_name, domain, home_html, home_url)
        return summarize(found) if found and found["jobs"] else None
    except Exception:
        return None
