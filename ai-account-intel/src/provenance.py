"""Where each company fact in the report came from.

Every headline fact (employees, founding year, headquarters) is tagged with
its source: Wikidata (with the year a headcount was reported), the company's
own website, or "AI estimate" when only the model supplied it. Facts the
website states outright ("founded in 2019", "a team of 120") replace the
model's guesses."""
import re
from datetime import date

AI = "AI estimate"
SITE = "Company website"
FIELDS = ("employee_count", "founded_year", "headquarters")

# "Founded in 2019", "established 1998". Not "launched"/"started": those are
# often about a product, not the company.
_FOUNDED = re.compile(r"\b(?:founded|established|incorporated)\b[^.!?\n]{0,30}?\b(1[89]\d\d|20\d\d)\b", re.I)
# "500+ employees", "a team of 120", "over 2,000 team members".
_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d{1,6})"
_EMPLOYEES = re.compile(
    rf"\b{_NUMBER}\s*\+?\s*(?:full[- ]time\s+)?(?:employees|team members|teammates|staff members)\b"
    rf"|\bteam of\s+(?:over\s+|more than\s+)?{_NUMBER}\b", re.I)


def founded_on_site(text: str):
    years = [int(m.group(1)) for m in _FOUNDED.finditer(text or "")]
    years = [y for y in years if 1800 <= y <= date.today().year]
    return min(years) if years else None  # the earliest is the company's founding


def employees_on_site(text: str):
    for m in _EMPLOYEES.finditer(text or ""):
        n = int((m.group(1) or m.group(2)).replace(",", ""))
        if 1 <= n < 5_000_000:
            return n
    return None


def _hq_on_site(hq: str, text: str) -> bool:
    city = (hq or "").split(",")[0].strip().lower()
    return len(city) > 2 and city not in ("unknown", "n/a") and re.search(rf"\b{re.escape(city)}\b", (text or "").lower()) is not None


def label_company_facts(research: dict, website_text: str = "") -> dict:
    """Fills company["fact_sources"]. Run after Wikidata has applied (and
    tagged) its facts, so those keep their Wikidata label."""
    company = research.setdefault("company", {})
    sources = company.setdefault("fact_sources", {})

    if "founded_year" not in sources:
        year = founded_on_site(website_text)
        if year:
            company["founded_year"], sources["founded_year"] = year, SITE
    if "employee_count" not in sources:
        count = employees_on_site(website_text)
        if count:
            company["employee_count"], sources["employee_count"] = count, SITE
    if "headquarters" not in sources and _hq_on_site(company.get("headquarters"), website_text):
        sources["headquarters"] = SITE

    for field in FIELDS:
        value = company.get(field)
        if value in (None, "", 0) or str(value).strip().lower() in ("unknown", "n/a"):
            sources.pop(field, None)
        elif field not in sources:
            sources[field] = AI
    return research


_FIGURE = re.compile(r"\d+(?:[.,]\d+)*")


def _figures(text: str) -> set:
    """Numbers in a text, normalized: "1,000+" -> "1000", "$1.9tn" -> "1.9"."""
    return {m.group(0).replace(",", "").rstrip(".") for m in _FIGURE.finditer(text or "")}


def figures_supported(claim: str, evidence: str) -> bool:
    """True if every number in the claim also appears in the evidence (single
    digits are too common to check). A claim without numbers passes."""
    wanted = {n for n in _figures(claim) if len(n.replace(".", "")) > 1}
    return wanted <= _figures(evidence)
