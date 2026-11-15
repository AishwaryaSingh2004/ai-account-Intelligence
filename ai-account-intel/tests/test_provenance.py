"""Every company fact says where it came from, and the website's own
statements beat the model's guesses."""
import copy

import pytest

import src.agents as A
from src import provenance as P, ui, wikidata
from src.report import build_pdf
from src.validate import validate_intel
from tests.conftest import FIXTURE, NO_SITE


@pytest.mark.parametrize("text,year", [("Founded in 2019 in Austin.", 2019), ("Established 1998. Founded in 2004 again", 1998),
                                       ("We launched in 2024", None), ("Founded by Ryan Young, a realtor.", None),
                                       ("founded in 2999", None)])
def test_founding_year_from_site(text, year):
    assert P.founded_on_site(text) == year


@pytest.mark.parametrize("text,count", [("We are 500+ employees", 500), ("a team of over 1,200 people", 1200),
                                        ("Fello empowers 20,000 agents", None), ("trusted by 5,000 teams", None)])
def test_headcount_from_site(text, count):
    assert P.employees_on_site(text) == count


def test_every_fact_gets_a_source():
    r = P.label_company_facts({"company": {"employee_count": 50, "founded_year": 2021, "headquarters": "Ohio, USA"}},
                              "Fello is based in Ohio. Founded in 2020.")
    assert r["company"]["founded_year"] == 2020  # the website beats the model
    assert r["company"]["fact_sources"] == {"employee_count": "AI estimate", "founded_year": "Company website",
                                            "headquarters": "Company website"}
    empty = P.label_company_facts({"company": {"employee_count": None, "headquarters": "Unknown"}}, "")
    assert empty["company"]["fact_sources"] == {}  # nothing to label


def test_wikidata_facts_are_labelled_with_their_year():
    r = wikidata.apply_to_research({"company": {}, "business_signals": [], "leadership": []},
                                   {"employee_count": 8000, "employees_as_of": 2025, "founded_year": 2006,
                                    "source": {"title": "W", "url": "https://w.org"}})
    r = P.label_company_facts(r, "")
    assert r["company"]["fact_sources"] == {"employee_count": "Wikidata, 2025", "founded_year": "Wikidata"}


def test_pipeline_labels_facts_and_separates_ai_signals(stubs, monkeypatch):
    monkeypatch.setattr(A, "run_researcher", lambda **kw: copy.deepcopy(
        {**{k: FIXTURE[k] for k in ("company", "tech_stack", "leadership")},
         "business_signals": ["AI thinks they are growing"],
         }) | {"company": dict(FIXTURE["company"], fact_sources={"employee_count": "Wikidata"})})  # model tries to self-label
    monkeypatch.setattr(A.website, "fetch_company_site", lambda n, d: dict(NO_SITE, text="Founded in 2009 in Chicago."))
    raw = A.run_full_pipeline(company_name="BrightPath Lending", partial_domain="brightpathlending.com",
                              ip_address="17.0.0.1")
    src = raw["company"]["fact_sources"]
    assert src == {"employee_count": "AI estimate", "founded_year": "Company website", "headquarters": "Company website"}
    assert raw["verified_signals"] == [] and raw["business_signals"] == ["AI thinks they are growing"]
    parsed, err = validate_intel(raw)
    html = ui.company_and_intent_html(parsed, "")
    assert "~200 employees" in html and "AI estimate" in html and "&#10003; Company website" in html
    assert 'class="ai-tag"' in ui.signals_html(parsed, [])
    assert build_pdf(parsed)[:5] == b"%PDF-"


def test_verified_signals_are_listed_first_without_ai_tag(stubs, monkeypatch):
    monkeypatch.setattr(A.hiring, "hiring_signals", lambda *a, **k: {
        "signals": ["Hiring: 3 open roles on Ashby (2 Sales)"], "tools": {}, "board_url": "https://jobs.ashbyhq.com/acme",
        "provider": "Ashby", "open_roles": 3})
    raw = A.run_full_pipeline(company_name="Acme")
    assert raw["business_signals"][0].startswith("Hiring:") and raw["verified_signals"] == [raw["business_signals"][0]]
    parsed, _ = validate_intel(raw)
    first_item = ui.signals_html(parsed, []).split("<li>")[1]
    assert "ai-tag" not in first_item


@pytest.mark.parametrize("claim,ok", [
    ("Used by 1,000+ WSJ RealTrends teams", True),          # stated on the site
    ("Maintains 99.999% platform uptime", False),            # not on the site
    ("Processed US$1.9tn in payments volume in 2025", False),
    ("Strong focus on AI agents", True),                     # no figures to check
    ("Operates in 5 regions", True)])                         # single digits aren't checked
def test_ai_figures_must_appear_on_the_website(claim, ok):
    site = "Trusted by 1000+ WSJ RealTrends teams and 20,000 agents. Founded 2021."
    assert P.figures_supported(claim, site) is ok


def test_pipeline_drops_ai_signals_with_unsupported_figures(stubs, monkeypatch):
    research = {**{k: copy.deepcopy(FIXTURE[k]) for k in ("company", "tech_stack", "leadership")},
                "business_signals": ["Serves 20,000 agents", "Maintains 99.999% uptime", "Growing fast"]}
    monkeypatch.setattr(A, "run_researcher", lambda **kw: copy.deepcopy(research))
    monkeypatch.setattr(A.website, "fetch_company_site", lambda n, d: dict(NO_SITE, text="Loved by 20,000 agents."))
    raw = A.run_full_pipeline(company_name="Acme", partial_domain="acme.com")
    assert raw["business_signals"] == ["Serves 20,000 agents", "Growing fast"]
