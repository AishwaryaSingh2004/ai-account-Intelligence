"""Batch CSV mode: parsing, running each row, ranking and the CSV export."""
import copy

import pytest

from src import batch as B
from src.validate import validate_intel
from tests.conftest import FIXTURE


def csv_bytes(text):
    return text.encode("utf-8")


def test_template_parses_and_flexible_headers_work():
    rows, problems = B.parse_csv(B.TEMPLATE.encode())
    assert problems == [] and [r["company_name"] for r in rows] == ["Fello", "Gong", ""]
    rows, _ = B.parse_csv(csv_bytes("﻿Company,Domain,Pages,Visits\nAcme,acme.com,/pricing | /demo,3\n"))
    assert rows[0]["company_name"] == "Acme" and rows[0]["website"] == "acme.com" and rows[0]["visits_this_week"] == "3"


@pytest.mark.parametrize("text,message", [("foo,bar\n1,2\n", "needs a header row"), ("company_name\n\n\n", "no company rows")])
def test_unusable_files_explain_why(text, message):
    rows, problems = B.parse_csv(csv_bytes(text))
    assert rows == [] and message in problems[0]


def test_row_limit_and_size_limit():
    rows, problems = B.parse_csv(csv_bytes("company\n" + "".join(f"C{i}\n" for i in range(40))))
    assert len(rows) == B.MAX_ROWS and "first 25" in problems[0]
    assert "larger than" in B.parse_csv(b"x" * (B.MAX_FILE_BYTES + 1))[1][0]


def row(**kw):
    base = {f: "" for f in B._ALIASES}
    base.update(kw, line=kw.pop("line", 2))
    return base


def test_pipeline_inputs():
    inputs, _ = B.pipeline_inputs(row(website="https://www.Fello.ai/", pages_visited="/pricing; /demo", visits_this_week="3.0",
                                      time_on_site_seconds="abc", device="Desktop"))
    assert inputs["company_name"] == "fello.ai" and inputs["visitor_signals"] == {
        "pages_visited": ["/pricing", "/demo"], "time_on_site_seconds": 0, "visits_this_week": 3,
        "referral_source": None, "device": "desktop"}
    assert B.pipeline_inputs(row(company_name="Acme"))[0]["visitor_signals"] is None  # no behavior -> scores 0
    assert B.pipeline_inputs(row(company_name="Acme", ip_address="bad"))[0]["ip_address"] is None  # ignored
    assert "isn't a valid IP" in B.pipeline_inputs(row(ip_address="bad"))[1]
    assert B.pipeline_inputs(row(referral_source="x"))[1] == "No company name, website or IP address"


def fake_pipeline(scores):
    """Returns the fixture with the score for each company; "boom" raises."""
    calls = []

    def run(**kw):
        calls.append(kw)
        name = kw["company_name"]
        if name == "boom":
            raise RuntimeError("Gemini overloaded")
        data = copy.deepcopy(FIXTURE)
        data["company"]["name"] = name
        data["intent"]["score"] = scores[name]
        return data
    return run, calls


def test_batch_runs_every_row_ranks_and_survives_failures():
    run, calls = fake_pipeline({"Low": 2.0, "High": 9.1, "Mid": 6.5})
    rows, _ = B.parse_csv(csv_bytes("company_name,ip_address\nLow,\nboom,\nHigh,\n,not-an-ip\nMid,\n"))
    progress, sleeps = [], []
    results = B.run_batch(rows, run, validate_intel, model="m", delay_seconds=3, sleep=sleeps.append,
                          on_progress=lambda done, total, res: progress.append((done, total, len(res))))
    assert progress[-1] == (5, 5, 5) and sleeps == [3, 3, 3, 3] and all(c["model"] == "m" for c in calls)
    ranked = B.ranked(results)
    assert [(r["Rank"], r["Input"]) for r in ranked] == [(1, "High"), (2, "Mid"), (3, "Low"), (None, "boom"), (None, "not-an-ip")]
    assert ranked[0]["Intent score"] == 9.1 and ranked[0]["Recommended action"] == "Reach out this week."
    assert "Employees source" in ranked[0]
    assert "overloaded" in ranked[3]["Error"]


def test_export_neutralizes_spreadsheet_formulas():
    out = B.to_csv([{"Rank": 1, "Company": "=HYPERLINK(\"http://evil\")", "Note": "fine"}]).decode("utf-8-sig")
    assert "'=HYPERLINK" in out and out.splitlines()[0] == "Rank,Company,Note"


@pytest.mark.parametrize("message,fatal", [
    ("API key not valid. Please pass a valid API key.", True),
    ("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", True),
    ("429 RESOURCE_EXHAUSTED GenerateRequestsPerMinutePerProjectPerModel", False),  # passes in a minute
    ("503 UNAVAILABLE", False)])
def test_only_problems_every_row_would_hit_stop_the_batch(message, fatal):
    assert B.is_fatal(RuntimeError(message)) is fatal
