"""Who to contact: turns the verified leaders, the visitor's likely role and the
company's size into a short ranked list of people (or roles) to reach out to.

Deterministic on purpose: names only ever come from the verified leadership
list (Wikidata or the company's own website), never from the model. When no
verified person fits, the contact is a role ("Head of Sales") with a LinkedIn
search link so the rep can find the real person in one click."""
import re
from urllib.parse import quote_plus

from src import config

LINKEDIN_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="

# Department -> (words that identify it in a title, its name in a sentence, the
# decision-maker role to look for). Checked in this order, so the more specific
# "revenue operations" wins over "revenue" (sales).
DEPARTMENTS = {
    "operations": (r"rev ?ops|revenue operations|sales operations|operations|operating|\bcoo\b",
                   "operations", "Head of Revenue Operations"),
    "sales": (r"sales|revenue|\bcro\b|business development|account exec|\bsdr\b|\bbdr\b|go[- ]to[- ]market|\bgtm\b|commercial",
              "sales", "Head of Sales"),
    "marketing": (r"marketing|\bcmo\b|growth|demand gen|brand", "marketing", "Head of Marketing"),
    "customer": (r"customer success|customer experience|support|\bcx\b", "customer success", "Head of Customer Success"),
    "product": (r"product|\bcpo\b", "product", "Head of Product"),
    "engineering": (r"engineer|\bcto\b|technology|technical|developer|devops|architect|\bit\b|platform|data",
                    "engineering", "CTO / VP Engineering"),
    "finance": (r"finance|\bcfo\b|procurement|controller|accounting", "finance", "CFO / Head of Finance"),
    "people": (r"\bhr\b|human resources|people|talent|recruit", "people / HR", "Head of People"),
}
EXECUTIVE = r"\bceo\b|chief executive|founder|president|owner|managing director"
CEO = r"\bceo\b|chief executive"
SENIOR = r"chief|\bc[a-z]o\b|\bvp\b|vice president|head|director|founder|president|\blead\b"
FOUNDER_CEO = "Founder / CEO"

SMALL_COMPANY = 50     # at or below this, the founder/CEO usually decides
MID_COMPANY = 500      # at or below this, the founder/CEO is still worth copying in
MIN_VISITOR_CONFIDENCE = 0.4


def _matches(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, (text or "").lower()))


def department_of(role: str):
    """The department a role or title belongs to ("VP of Sales" -> "sales")."""
    for dept, (pattern, _, _) in DEPARTMENTS.items():
        if _matches(pattern, role):
            return dept
    return None


def linkedin_search(*words) -> str:
    return LINKEDIN_SEARCH + quote_plus(" ".join(w for w in words if w))


def _known_role(persona_role: str) -> bool:
    role = (persona_role or "").strip().lower()
    return bool(role) and not role.startswith("unknown")


def pick_contacts(leadership: list, persona: dict, company: dict) -> list:
    """Up to three contacts, best first. Each is {"name" (None when only the
    role is known), "title", "why", "source", "linkedin"}."""
    company_name = ((company or {}).get("name") or "").strip()
    if not company_name or company_name.lower().startswith("unidentified") or not (company or {}).get("domain"):
        # No confirmed company (no website found): there is nobody to contact yet.
        return []
    size = (company or {}).get("employee_count")
    small = isinstance(size, int) and size <= SMALL_COMPANY
    large = isinstance(size, int) and size > MID_COMPANY
    persona = persona or {}
    role = (persona.get("likely_role") or "").strip() if _known_role(persona.get("likely_role")) else ""
    confidence = persona.get("confidence") or 0
    dept = department_of(role)
    visitor_is_exec = bool(role) and dept is None and _matches(EXECUTIVE, role)

    leaders = [p for p in leadership or [] if (p.get("name") or "").strip() and (p.get("title") or "").strip()]
    # The CEO first among executives, so a co-founder isn't picked over them.
    executives = sorted((p for p in leaders if _matches(EXECUTIVE, p["title"])),
                        key=lambda p: not _matches(CEO, p["title"]))
    in_dept = [p for p in leaders if dept and department_of(p["title"]) == dept and _matches(SENIOR, p["title"])]

    contacts = []
    taken = lambda title: title.lower() in {c["title"].lower() for c in contacts}

    def add_person(p, why):
        if p["name"] not in {c["name"] for c in contacts}:
            contacts.append({"name": p["name"], "title": p["title"], "why": why,
                             "source": "Verified leader", "linkedin": linkedin_search(p["name"], company_name)})

    def add_role(title, why):
        if not taken(title):
            contacts.append({"name": None, "title": title, "why": why,
                             "source": "Role to find", "linkedin": linkedin_search(title, company_name)})

    # 1. The decision maker for what the visitor was looking at.
    if in_dept:
        add_person(in_dept[0], f"Leads {DEPARTMENTS[dept][1]}, the team the visitor most likely belongs to, "
                               "so they own this decision.")
    elif executives and (small or visitor_is_exec):
        add_person(executives[0], f"At about {size:,} employees, the founder/CEO usually makes buying decisions directly."
                   if small else "The visitor looks like a senior executive; this is the company's verified top leader.")
    elif dept:
        add_role(DEPARTMENTS[dept][2], f"Decision maker for the {DEPARTMENTS[dept][1]} team the visitor most likely "
                                       "belongs to; no verified name in public data, so find them on LinkedIn.")
    elif executives and not large:
        add_person(executives[0], "The company's verified top leader; the visitor's behavior doesn't point to one team.")
    else:
        # At a large company the CEO doesn't pick tools: go to the team that buys.
        fallback = FOUNDER_CEO if (small or visitor_is_exec) else config.DEFAULT_CONTACT_ROLE
        add_role(fallback, "No verified leader matches the visitor's team; start with the person who usually "
                           "owns this purchase, and find them on LinkedIn.")

    # 2. The visitor themselves: the likely champion who is already interested.
    #    Only a real job title ("Marketing Manager"), not a label ("Decision Maker").
    if (dept or visitor_is_exec) and confidence >= MIN_VISITOR_CONFIDENCE:
        add_role(role, "Most likely the person who visited; reach out as a warm contact, not cold.")

    # 3. Executive sponsor for small and mid-size companies.
    if executives and (size is None or size <= MID_COMPANY):
        add_person(executives[0], "Executive sponsor: at a company this size the CEO often signs off on new tools.")
    return contacts[:3]
