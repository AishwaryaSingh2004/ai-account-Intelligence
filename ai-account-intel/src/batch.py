"""Batch mode: analyze a CSV of companies and rank them by intent score.

Each row runs through the same pipeline as a single analysis. Intent comes
from visitor behavior, so rows without behavior columns score 0 (Awareness);
the ranking is only meaningful when the CSV includes pages visited, visits
this week, etc."""
import csv
import io
import ipaddress
import time

from src import config

MAX_ROWS = config.BATCH_MAX_ROWS   # each row is a full multi-agent run (Gemini free-tier limits)
MAX_FILE_BYTES = 1_000_000

# Accepted header spellings -> field (case, spaces and dashes ignored).
_ALIASES = {
    "company_name": {"company_name", "company", "name", "account", "account_name", "organization"},
    "website": {"website", "domain", "url", "site", "company_website", "partial_domain"},
    "ip_address": {"ip_address", "ip", "visitor_ip"},
    "location": {"location", "city", "region"},
    "pages_visited": {"pages_visited", "pages", "visited_pages"},
    "time_on_site_seconds": {"time_on_site_seconds", "time_on_site", "seconds_on_site"},
    "visits_this_week": {"visits_this_week", "visits", "weekly_visits"},
    "referral_source": {"referral_source", "referral", "source", "utm_source"},
    "device": {"device"},
}
_HEADER = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}

TEMPLATE = (
    "company_name,website,ip_address,location,pages_visited,time_on_site_seconds,visits_this_week,referral_source,device\n"
    "Fello,fello.ai,,,/pricing;/demo;/case-studies,420,4,google_organic,desktop\n"
    "Gong,gong.io,,,/blog,60,1,linkedin,mobile\n"
    ",,17.253.144.10,,/pricing;/integrations,300,3,,desktop\n"
)


def _key(header: str) -> str:
    return (header or "").strip().lower().replace(" ", "_").replace("-", "_")


def _number(value: str) -> int:
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def parse_csv(data: bytes):
    """(rows, problems): rows are dicts ready for analyze_row(); problems are
    plain-English messages about the file or rows that were skipped."""
    if len(data) > MAX_FILE_BYTES:
        return [], [f"The file is larger than {MAX_FILE_BYTES // 1_000_000} MB."]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    columns = {h: _HEADER[_key(h)] for h in (reader.fieldnames or []) if _key(h) in _HEADER}
    if not {"company_name", "website", "ip_address"} & set(columns.values()):
        return [], ["The CSV needs a header row with at least one of: company_name, website or ip_address. "
                    "Download the template to see the format."]

    rows, problems = [], []
    for line, raw in enumerate(reader, start=2):
        row = {field: "" for field in _ALIASES}
        for header, field in columns.items():
            row[field] = (raw.get(header) or "").strip()
        if not any(row.values()):
            continue  # blank line
        if len(rows) >= MAX_ROWS:
            problems.append(f"Only the first {MAX_ROWS} companies are analyzed; the rest of the file was skipped.")
            break
        row["line"] = line
        rows.append(row)
    if not rows and not problems:
        problems.append("The CSV has no company rows.")
    return rows, problems


def pipeline_inputs(row: dict):
    """(kwargs for run_full_pipeline, None) or (None, reason the row can't run)."""
    name, website, ip = row["company_name"], row["website"], row["ip_address"]
    if ip:
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            if not (name or website):
                return None, f'"{ip}" isn\'t a valid IP address'
            ip = ""  # ignored, like the single-company form does
    if not name and website:
        from src.agents import normalize_domain
        name = normalize_domain(website) or website
    if not (name or ip or row["location"]):
        return None, "No company name, website or IP address"

    pages = [p.strip() for p in row["pages_visited"].replace("|", ";").split(";") if p.strip()]
    signals = {
        "pages_visited": pages,
        "time_on_site_seconds": _number(row["time_on_site_seconds"]),
        "visits_this_week": _number(row["visits_this_week"]),
        "referral_source": row["referral_source"] or None,
        "device": row["device"].lower() or None,
    }
    has_behavior = bool(pages or signals["time_on_site_seconds"] or signals["visits_this_week"]
                        or signals["referral_source"] or signals["device"])
    return {
        "company_name": name or None,
        "partial_domain": website or None,
        "visitor_signals": signals if has_behavior else None,
        "ip_address": ip or None,
        "location": row["location"] or None,
    }, None


def _label(row: dict) -> str:
    return row["company_name"] or row["website"] or row["ip_address"] or row["location"] or f"Row {row['line']}"


def _first_contact(parsed):
    people = getattr(parsed, "contacts", None) or []
    if not people:
        return None
    c = people[0]
    return f"{c.name} ({c.title})" if c.name else f"{c.title} (find on LinkedIn)"


def _blank_result(row: dict) -> dict:
    return {"Input": _label(row), "CSV line": row["line"], "Company": None, "Intent score": None,
            "Stage": None, "Likely persona": None, "Industry": None, "Employees": None, "Employees source": None,
            "Contact first": None, "Recommended action": None, "First next step": None, "Website": row["website"] or None, "Error": None}


def analyze_row(row: dict, run_pipeline, validate, model=None, describe_error=None) -> dict:
    """One result line. Never raises: failures become a row with an error."""
    result = _blank_result(row)
    inputs, problem = pipeline_inputs(row)
    if problem:
        result["Error"] = problem
        return result
    try:
        parsed, error = validate(run_pipeline(**inputs, model=model))
    except Exception as e:
        message = describe_error(e) if describe_error else str(e)
        result["Error"] = (message or type(e).__name__)[:300]
        result["_fatal"] = is_fatal(e)
        return result
    if error:
        result["Error"] = "The model's answer didn't match the expected format"
        return result
    result.update({
        "Company": parsed.company.name,
        "Intent score": parsed.intent.score,
        "Stage": parsed.intent.stage,
        "Likely persona": parsed.persona.likely_role,
        "Industry": parsed.company.industry,
        "Employees": parsed.company.employee_count,
        "Employees source": (parsed.company.fact_sources or {}).get("employee_count"),
        "Contact first": _first_contact(parsed),
        "Recommended action": parsed.recommended_action.summary,
        "First next step": (parsed.recommended_action.next_steps or [None])[0],
        "Website": parsed.company.domain or row["website"] or None,
    })
    return result


def is_fatal(e: Exception) -> bool:
    """Errors every later row would hit too: a missing or invalid API key, or
    the daily Gemini quota used up (it resets the next day, not in a minute)."""
    text = str(e)
    return any(k in text for k in ("API key", "API_KEY", "PERMISSION_DENIED", "PerDay", "per day"))


def run_batch(rows, run_pipeline, validate, model=None, on_progress=None, delay_seconds=0,
              sleep=time.sleep, describe_error=None):
    """Analyzes every row in order; on_progress(done, total, results) after each."""
    results, stopped = [], None
    for i, row in enumerate(rows):
        if stopped:
            result = dict(_blank_result(row), Error=f"Not analyzed: {stopped}")
        else:
            if i and delay_seconds:
                sleep(delay_seconds)  # stay under the Gemini free-tier requests-per-minute cap
            result = analyze_row(row, run_pipeline, validate, model, describe_error)
            if result.pop("_fatal", False):
                stopped = result["Error"]
        results.append(result)
        if on_progress:
            on_progress(i + 1, len(rows), list(results))
    return results


def ranked(results):
    """Highest intent first; rows that failed go last. Adds a Rank column."""
    ok = sorted((r for r in results if r["Error"] is None), key=lambda r: (-(r["Intent score"] or 0), r["CSV line"]))
    failed = sorted((r for r in results if r["Error"] is not None), key=lambda r: r["CSV line"])
    out = []
    for i, r in enumerate(ok + failed, start=1):
        out.append({"Rank": i if r["Error"] is None else None, **r})
    return out


def to_csv(rows) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        # Spreadsheet apps run cells starting with = + - @ as formulas.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8-sig")
