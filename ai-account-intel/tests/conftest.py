"""Shared test setup. All tests run offline: network calls are replaced with
fakes, so no API key or internet connection is needed."""
import copy
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # app.py and run_tests.py use paths relative to the project

FIXTURE = {
    "company": {"name": "BrightPath Lending", "domain": "brightpathlending.com", "industry": "Mortgage Lending",
                "employee_count": 200, "headquarters": "Chicago, USA", "founded_year": 2009,
                "description": "An established mortgage lender focused on residential loans."},
    "tech_stack": {"crm": "Salesforce", "marketing_automation": "HubSpot", "website_platform": "WordPress", "analytics": None},
    "business_signals": ["High reliance on digital lead generation"],
    "leadership": [{"name": "Jane Doe", "title": "VP of Lending"}],
    "persona": {"likely_role": "Decision Maker", "confidence": 0.85},
    "intent": {"score": 8.2, "stage": "Evaluation"},
    "ai_summary": "BrightPath Lending is evaluating tools to improve conversion.",
    "recommended_action": {"summary": "Reach out this week.", "next_steps": ["Contact the VP of Lending"]},
    "risks": {k: {"level": "Low", "explanation": "Inferred baseline."}
              for k in ["employee_turnover", "funding_slowdown", "negative_news", "declining_hiring", "low_buying_intent"]},
    "recent_news": [{"headline": "Expands <script>x</script>", "summary": "Two branches.", "date": "2026-06"}],
    "sources": [{"title": "site", "url": "https://brightpathlending.com"}, {"title": "evil", "url": "javascript:alert(1)"}],
    "confidence_notes": "Company data is model-inferred.",
}


@pytest.fixture
def fixture_data():
    return copy.deepcopy(FIXTURE)


@pytest.fixture
def no_sleep(monkeypatch):
    """Retry waits become instant (only inside the agents module)."""
    import src.agents as agents
    monkeypatch.setattr(agents, "time", types.SimpleNamespace(sleep=lambda s: None, time=agents.time.time))


def gemini_response(text, finish="STOP"):
    return types.SimpleNamespace(text=text, candidates=[types.SimpleNamespace(
        finish_reason=types.SimpleNamespace(name=finish), grounding_metadata=None)])


@pytest.fixture
def fake_gemini(monkeypatch):
    """Scripted Gemini: fake_gemini({model: [response or exception, ...]})
    installs a client; the returned list records which models were called."""
    import src.agents as agents
    calls = []

    def install(script):
        calls.clear()

        def generate_content(model, contents, config):
            calls.append(model)
            queue = script.get(model) or [AssertionError(f"unexpected call to {model}")]
            step = queue.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

        monkeypatch.setattr(agents, "_client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
        return calls

    return install


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Safety net: any test that accidentally reaches the real network fails."""
    import requests

    def blocked(*a, **k):
        raise AssertionError("test tried to use the real network")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    # Gemini's SDK uses httpx, not requests: block real Gemini clients too, so
    # tests can never spend API quota (fake_gemini installs a fake instead).
    from google import genai
    import src.agents as agents
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(agents, "_client", None)
    # IP registration lookups (ipwhois) use their own HTTP and DNS clients.
    import src.iplookup as iplookup
    monkeypatch.setattr(iplookup, "IPWhois", blocked)
    monkeypatch.setattr(iplookup, "_cache", {})


# ---- Pipeline with every agent stubbed (shared by pipeline and IP tests) -----
RESEARCH = {k: FIXTURE[k] for k in ("company", "tech_stack", "business_signals", "leadership")}
SCORE = {"persona": {"likely_role": "Decision Maker", "confidence": 0.8}, "intent": {"score": 8.0, "stage": "Evaluation"}}
STRATEGY = {"ai_summary": "s", "recommended_action": {"summary": "s", "next_steps": []}, "confidence_notes": "n"}
NO_SITE = {"url": None, "pages": [], "text": "", "home_html": b"", "home_headers": {}}


@pytest.fixture
def stubs(monkeypatch):
    """Agents return fixed data; returns a dict recording what was called."""
    import src.agents as A
    from src import hiring, iplookup, website, wikidata
    seen = {"fetch": [], "researcher": None}
    monkeypatch.setattr(website, "fetch_company_site", lambda n, d: seen["fetch"].append((n, d)) or dict(NO_SITE))
    monkeypatch.setattr(A, "run_researcher", lambda **kw: seen.__setitem__("researcher", kw) or copy.deepcopy(RESEARCH))
    monkeypatch.setattr(A, "run_scorer", lambda s, m=None: copy.deepcopy(SCORE))
    monkeypatch.setattr(A, "run_strategist", lambda *a, **k: copy.deepcopy(STRATEGY))
    monkeypatch.setattr(A, "run_risk_analyst", lambda *a, **k: copy.deepcopy(FIXTURE["risks"]))
    monkeypatch.setattr(A, "_news_or_unavailable", lambda c, m=None: ({"recent_news": [], "sources": []}, False))
    monkeypatch.setattr(hiring, "hiring_signals", lambda *a, **k: None)
    monkeypatch.setattr(wikidata, "facts", lambda *a, **k: None)
    monkeypatch.setattr(iplookup, "lookup", lambda ip, timeout=None: None)
    return seen
