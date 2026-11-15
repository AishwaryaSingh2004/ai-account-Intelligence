"""How run_full_pipeline combines the agents and the optional data sources,
including that slow or broken extras can never hold up or break a report."""
import copy
import threading
import time

import src.agents as A
from src import hiring, news, website, wikidata
from src.validate import validate_intel
from tests.conftest import NO_SITE, RESEARCH, STRATEGY


def test_report_is_valid_and_steps_run_in_order(stubs):
    steps = []
    raw = A.run_full_pipeline(company_name="Acme", on_step=steps.append)
    assert validate_intel(raw)[1] is None
    assert steps == ["research", "score", "strategy", "risk", "news"]


def test_website_text_reaches_researcher(stubs, monkeypatch):
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: dict(NO_SITE, text="SITE TEXT"))
    A.run_full_pipeline(company_name="Fello", partial_domain="fello.ai")
    assert stubs["researcher"]["website_text"] == "SITE TEXT"


def test_ip_only_skips_company_lookups(stubs, monkeypatch):
    called = []
    for mod, fn in ((hiring, "hiring_signals"), (wikidata, "facts")):
        monkeypatch.setattr(mod, fn, lambda *a, **k: called.append(fn))
    A.run_full_pipeline(ip_address="34.201.112.55")
    assert stubs["fetch"] == [] and called == []


def test_slow_job_board_and_wikidata_do_not_hold_up_the_report(stubs, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(A, "SIDE_LOOKUP_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(hiring, "hiring_signals", lambda *a, **k: release.wait(30))
    monkeypatch.setattr(wikidata, "facts", lambda *a, **k: release.wait(30))
    start = time.monotonic()
    raw = A.run_full_pipeline(company_name="Acme")
    release.set()
    assert time.monotonic() - start < 5
    assert validate_intel(raw)[1] is None


def test_stuck_news_search_falls_back_to_free_news(stubs, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(A, "NEWS_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(A, "_news_or_unavailable", lambda c, m=None: release.wait(30))
    monkeypatch.setattr(news, "free_news", lambda name, industry=None: {
        "recent_news": [{"headline": "Acme raises $5M", "summary": "s", "date": "2026-01-01"}], "sources": []})
    start = time.monotonic()
    raw = A.run_full_pipeline(company_name="Acme")
    release.set()
    assert time.monotonic() - start < 5
    assert raw["recent_news"][0]["headline"] == "Acme raises $5M" and raw["news_unavailable"] is False


def test_crashing_extras_are_ignored(stubs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(hiring, "hiring_signals", boom)
    monkeypatch.setattr(wikidata, "facts", boom)
    assert validate_intel(A.run_full_pipeline(company_name="Acme"))[1] is None


def test_gemini_news_failure_uses_free_news(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(A, "run_news_and_sources", fail)
    monkeypatch.setattr(news, "free_news", lambda name, industry=None: {"recent_news": [], "sources": []})
    assert A._news_or_unavailable({"name": "Acme"}) == ({"recent_news": [], "sources": []}, False)
    monkeypatch.setattr(news, "free_news", lambda name, industry=None: None)
    assert A._news_or_unavailable({"name": "Acme"})[1] is True  # both failed: flagged


def test_all_sources_combine_into_a_valid_report(stubs, monkeypatch):
    monkeypatch.setattr(hiring, "hiring_signals", lambda *a, **k: {
        "signals": ["Hiring: 3 open roles on Ashby (2 Sales)"], "tools": {"Salesforce": 2},
        "board_url": "https://jobs.ashbyhq.com/acme", "provider": "Ashby", "open_roles": 3})
    monkeypatch.setattr(wikidata, "facts", lambda *a, **k: {
        "headquarters": "Austin, United States", "founded_year": 2015, "ceo": "Ada Lovelace",
        "source": {"title": "Wikipedia: Acme", "url": "https://en.wikipedia.org/wiki/Acme"}})
    raw = A.run_full_pipeline(company_name="Acme")
    assert validate_intel(raw)[1] is None
    assert raw["company"]["headquarters"] == "Austin, United States"
    assert raw["leadership"][0] == {"name": "Ada Lovelace", "title": "CEO"}
    assert raw["tech_stack"]["crm"] == "Salesforce (from job posts)"   # evidence beats the AI's unverified guess
    assert raw["tech_stack"]["marketing_automation"] == "HubSpot (AI guess)"  # unverified -> labelled
    assert {s["title"] for s in raw["sources"]} >= {"Wikipedia: Acme", "Open roles on Ashby"}


def test_unverifiable_leaders_are_never_shown(stubs, monkeypatch):
    """An obscure company: no Wikidata entry and the name isn't on its website,
    so a model-supplied person may be invented and is dropped."""
    raw = A.run_full_pipeline(company_name="BrightPath Lending")
    assert raw["leadership"] == []
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: dict(NO_SITE, text="Our team: Jane   Doe, VP of Lending"))
    assert A.run_full_pipeline(company_name="BrightPath Lending")["leadership"] == [{"name": "Jane Doe", "title": "VP of Lending"}]
    # Even for a well-known company, a remembered executive is not shown unless
    # Wikidata (current CEO) or the company's site confirms them.
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: dict(NO_SITE))
    monkeypatch.setattr(wikidata, "facts", lambda *a, **k: {"ceo": "Ada Lovelace", "founded_year": 2009,
                                                            "source": {"title": "W", "url": "https://w.org"}})
    assert A.run_full_pipeline(company_name="BrightPath Lending")["leadership"] == [{"name": "Ada Lovelace", "title": "CEO"}]


def test_wikidata_is_never_matched_by_name_alone(stubs, monkeypatch):
    """Regression: "Mercury" with no domain matched a different, 1945 London
    company. Wikidata is looked up by the website the Researcher found."""
    calls = []
    monkeypatch.setattr(wikidata, "facts", lambda name, domain=None: calls.append((name, domain)) or None)
    A.run_full_pipeline(company_name="Mercury")
    assert calls == [("Mercury", "brightpathlending.com")]  # the Researcher's domain (fixture)
    assert stubs["fetch"] == [("Mercury", None), ("BrightPath Lending", "brightpathlending.com")]  # site read late
    calls.clear()
    A.run_full_pipeline(company_name="Stripe", partial_domain="https://stripe.com/payments")
    assert calls == [("Stripe", "stripe.com")]  # domain known up front: one lookup, no late phase


def test_job_board_is_never_matched_by_name_alone(stubs, monkeypatch):
    """A bare name could match another company's job board: with only a name,
    the board is looked up once the Researcher has found the official website."""
    calls = []
    monkeypatch.setattr(hiring, "hiring_signals", lambda name, domain=None, *a: calls.append((name, domain)) or None)
    A.run_full_pipeline(company_name="Mercury")
    assert calls == [("BrightPath Lending", "brightpathlending.com")]
    calls.clear()
    A.run_full_pipeline(company_name="Stripe", partial_domain="stripe.com")
    assert calls == [("Stripe", "stripe.com")]


def test_late_website_read_detects_real_tech(stubs, monkeypatch):
    html = b'<script src="https://js.hs-scripts.com/1.js"></script>'
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: dict(NO_SITE, url="https://x.com/", home_html=html) if d else dict(NO_SITE))
    raw = A.run_full_pipeline(company_name="Hubspott")
    assert raw["tech_stack"]["crm"] == "HubSpot CRM"  # detected, not "(AI guess)"


def test_slow_late_lookups_do_not_hold_up_the_report(stubs, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(A, "SIDE_LOOKUP_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: release.wait(30) and dict(NO_SITE) if d else dict(NO_SITE))
    monkeypatch.setattr(wikidata, "facts", lambda *a, **k: release.wait(30))
    start = time.monotonic()
    raw = A.run_full_pipeline(company_name="Mercury")
    release.set()
    assert time.monotonic() - start < 5 and validate_intel(raw)[1] is None


def test_unconfirmed_company_gets_no_guessed_hq_and_no_contacts(stubs, monkeypatch):
    """A company with no website and no Wikidata entry can't be confirmed: no
    guessed location, nobody to contact, and the strategist is told so."""
    seen = {}
    research = copy.deepcopy(RESEARCH)
    research["company"].update(domain=None, headquarters="United States")
    monkeypatch.setattr(A, "run_researcher", lambda **kw: copy.deepcopy(research))
    monkeypatch.setattr(A, "run_strategist", lambda *a, **k: seen.update(k) or copy.deepcopy(STRATEGY))
    out = A.run_full_pipeline(company_name="Xyzzq Widgets Quantum LLC")
    assert out["company"]["headquarters"] == "Unknown" and "headquarters" not in out["company"]["fact_sources"]
    assert out["contacts"] == [] and seen["contacts"] == []
