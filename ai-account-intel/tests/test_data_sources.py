"""Tech detection, job-board hiring signals, Wikidata facts and free news."""
import types

import pytest

from src import hiring as H, news as N, techstack as T, wikidata as WD


# ---- Tech stack from the website's code -------------------------------------
def test_mentions_in_page_text_are_not_detections():
    assert T.detect(b"<p>We compared 6sense, Eloqua, Sitecore and WordPress.</p>")["all"] == []


def test_real_fingerprints_are_detected():
    html = (b'<script src="https://js.hs-scripts.com/1.js"></script><script src="https://j.6sc.co/6si.min.js"></script>'
            b'<link href="/wp-content/x.css"><script>gtag("config", "G-ABC");</script>'
            b'<script src="https://www.googletagmanager.com/gtag/js?id=G-ABC"></script>')
    d = T.detect(html, {"Server": "cloudflare", "Content-Security-Policy": "script-src https://static.hotjar.com"})
    assert d["fields"]["crm"] == ["HubSpot CRM"] and "WordPress" in d["fields"]["website_platform"]
    assert "Google Analytics" in d["fields"]["analytics"] and d["other"]["intent / ABM"] == ["6sense"]
    assert "Hotjar" not in d["all"]  # only *allowed* in the CSP header, not used


def test_detection_replaces_guesses_and_drops_misfiled_tools():
    d = T.detect(b'<script src="https://cdn.optimizely.com/js/1.js"></script><link href="/wp-content/a.css">')
    r = T.apply_to_research({"tech_stack": {"crm": "Salesforce", "marketing_automation": None,
                                            "website_platform": "Drupal", "analytics": "Optimizely"},
                             "business_signals": []}, d)
    assert r["tech_stack"] == {"crm": "Salesforce (AI guess)", "marketing_automation": None,
                               "website_platform": "WordPress", "analytics": None}


def test_unverified_values_are_always_labelled():
    r = T.label_unverified({"tech_stack": {"crm": "Salesforce", "marketing_automation": "HubSpot (AI guess)",
                                           "website_platform": None, "analytics": "Looker (from job posts)"}})
    assert r["tech_stack"]["crm"] == "Salesforce (AI guess)" and r["tech_stack"]["analytics"] == "Looker (from job posts)"


# ---- Hiring signals ----------------------------------------------------------
def test_board_links_found_on_website():
    html = b'<a href="https://boards.greenhouse.io/acmeco">Jobs</a><a href="https://jobs.lever.co/other-co/1">x</a>'
    assert H._boards_in(html) == [("greenhouse", "acmeco"), ("lever", "other-co")]


@pytest.mark.parametrize("a,b,same", [("Stripe", "Stripe, Inc.", True), ("Notion", "Notion Labs", True),
                                      ("Acme", "Acme Brick Company", False), ("X", "Xerox", False)])
def test_guessed_board_needs_exact_company_name(a, b, same):
    assert H._names_match(a, b) is same


@pytest.mark.parametrize("text,tool,hit", [(" react quickly ", "React", False), (" react.js ", "React", True),
                                           (" go above and beyond ", "Go", False), (" golang ", "Go", True),
                                           (" javascript ", "Java", False), (" community outreach ", "Outreach", False)])
def test_common_words_are_not_tools(text, tool, hit):
    assert bool(H._TOOL_RE[tool].search(text)) is hit


def test_hiring_summary_and_evidence():
    found = {"provider": "Greenhouse", "board_url": "https://job-boards.greenhouse.io/acme", "jobs": [
        {"title": "Sales Development Representative", "team": "", "location": "Austin", "text": "Salesforce, Outreach.io"},
        {"title": "Account Executive", "team": "", "location": "Austin", "text": "Salesforce"},
        {"title": "VP of Marketing", "team": "", "location": "Remote", "text": "Marketo"},
        {"title": "Senior Software Engineer", "team": "", "location": "Remote", "text": "Python, AWS"}]}
    s = H.summarize(found)
    assert s["signals"][0] == "Hiring: 4 open roles on Greenhouse (2 Sales, 1 Marketing, 1 Engineering)"
    assert "Sales team growth: hiring 1 SDR/BDR and 1 Account Executive" in s["signals"]
    r = H.apply_to_research({"tech_stack": {"crm": "Pipedrive", "marketing_automation": None, "analytics": None},
                             "business_signals": []}, s, unlabelled_values_are_guesses=True)
    assert r["tech_stack"]["crm"] == "Salesforce (from job posts)"
    assert r["hiring_source"]["url"] == "https://job-boards.greenhouse.io/acme"


def test_hiring_never_raises(monkeypatch):
    monkeypatch.setattr(H, "find_jobs", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert H.hiring_signals("Acme") is None


# ---- Wikidata facts ----------------------------------------------------------
def test_wikidata_site_and_name_matching():
    assert WD._same_site("https://www.gong.io/", "gong.io") and not WD._same_site("https://rampnetwork.com", "ramp.com")
    assert WD._core_name("Gong.io") == "gong" and WD._core_name("Apple Inc.") == "apple"


def test_wikidata_facts_applied_carefully():
    facts = {"headquarters": "Cupertino, United States", "founded_year": 1976, "ceo": "John Ternus",
             "employee_count": 164000, "employees_as_of": 2019, "listing": "Nasdaq: AAPL",
             "source": {"title": "Wikipedia: Apple Inc.", "url": "https://en.wikipedia.org/wiki/Apple_Inc."}}
    r = WD.apply_to_research({"company": {"employee_count": 160000}, "business_signals": [],
                              "leadership": [{"name": "Tim Cook", "title": "CEO"}, {"name": "Kevan Parekh", "title": "CFO"}]},
                             dict(facts))
    assert r["company"]["headquarters"] == "Cupertino, United States" and r["company"]["employee_count"] == 160000  # stale count ignored
    assert r["leadership"] == [{"name": "John Ternus", "title": "CEO"}, {"name": "Kevan Parekh", "title": "CFO"}]
    site = WD.apply_to_research({"company": {}, "business_signals": [], "leadership": [{"name": "Jane Roe", "title": "CEO"}]},
                                dict(facts, ceo="Old Boss"), "Meet Jane Roe, our CEO")
    assert site["leadership"] == [{"name": "Jane Roe", "title": "CEO"}]  # the company's own site wins


def test_wikidata_never_raises(monkeypatch):
    monkeypatch.setattr(WD, "_get", lambda params: (_ for _ in ()).throw(RuntimeError("down")))
    assert WD.facts("Stripe", "stripe.com") is None


# ---- Free news ---------------------------------------------------------------
FEED = b"""<rss><channel>
<item><title>Barclays Opens Office as Houthis Ramp Up Attacks - Bloomberg</title><link>https://n/1</link><pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate><source>Bloomberg</source></item>
<item><title>Elder Gong Calls for Moral Compass - Church News</title><link>https://n/2</link><source>Church News</source></item>
<item><title>Ramp raises $1.85B as it expands - SiliconANGLE</title><link>https://n/3</link><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate><source>SiliconANGLE</source></item>
<item><title>Ramp reportedly hits $60bn valuation in $1.85bn round - FinTech Futures</title><link>https://n/4</link><pubDate>Thu, 15 Oct 2026 09:00:00 GMT</pubDate><source>FinTech Futures</source></item>
<item><title>Ramp Inc. Stock Price, News, Quote &amp; History - Yahoo</title><link>https://n/5</link><source>Yahoo</source></item>
<item><title>Ramp's guide to expenses - Ramp</title><link>http://insecure/6</link><source>Ramp</source></item>
<item><title>Ramp Enriches its Platform - - Enterprise Times</title><link>https://n/7</link><pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate><source>Enterprise Times</source></item>
</channel></rss>"""


def test_news_relevance_dedup_and_cleanup(monkeypatch):
    monkeypatch.setattr(N, "requests", types.SimpleNamespace(get=lambda *a, **k: types.SimpleNamespace(status_code=200, content=FEED)))
    headlines = [n["headline"] for n in N.free_news("Ramp", "Fintech")["recent_news"]]
    assert headlines == ["Ramp raises $1.85B as it expands", "Ramp Enriches its Platform"]
    assert N.free_news("Gong", "Revenue")["recent_news"] == []  # "Elder Gong" is a person


@pytest.mark.parametrize("a,b,same", [
    ("Ramp's $1.85 Billion Round Sets A $60 Billion Benchmark", "Ramp hits $60bn valuation in $1.85bn round", True),
    ("Ramp raises $500M", "Ramp raises $1.85bn", False),
    ("HubSpot cuts nearly 660 jobs as AI reshapes its business", "HubSpot cuts 660 jobs", True),
    ("Gong appoints a new Chief R&D Officer", "Gong Launches Mission Callisto", False)])
def test_same_story_detection(a, b, same):
    assert N._overlaps(a, b) is same


def test_news_query_is_safe_and_contextual():
    assert N.build_query('Ben "Ice Cream"', None) == '"Ben Ice Cream" when:1y'
    assert N.build_query("Ramp", "Financial Services / Fintech") == '"Ramp" ("financial services" OR fintech) when:1y'


def test_news_never_raises(monkeypatch):
    monkeypatch.setattr(N, "requests", types.SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))))
    assert N.free_news("Ramp") is None


@pytest.mark.parametrize("full,short", [("Apple Inc.", "Apple"), ("Comcast IP Services, L.L.C.", "Comcast IP Services"),
                                        ("Stripe, Inc", "Stripe"), ("Infosys Pvt. Ltd.", "Infosys"),
                                        ("Acme Brick Company", "Acme Brick Company"), ("Inc.", "Inc.")])
def test_news_searches_the_brand_not_the_legal_name(full, short):
    assert N.short_name(full) == short
