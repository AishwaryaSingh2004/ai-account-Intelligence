from src import contacts as C

CO = {"name": "Acme", "domain": "acme.com", "employee_count": 300}
SALES_VISITOR = {"likely_role": "VP of Sales evaluating tools", "confidence": 0.7}
LEADERS = [{"name": "Jane Doe", "title": "CEO"}, {"name": "Raj Rao", "title": "Chief Revenue Officer"},
           {"name": "Li Wu", "title": "CTO"}]


def test_department_head_matching_the_visitor_comes_first():
    out = C.pick_contacts(LEADERS, SALES_VISITOR, CO)
    assert out[0]["name"] == "Raj Rao" and out[0]["source"] == "Verified leader"
    assert out[1]["name"] is None and "Sales" in out[1]["title"]       # the visitor as warm contact
    assert out[2]["name"] == "Jane Doe"                                 # exec sponsor at 300 people
    assert all(c["linkedin"].startswith(C.LINKEDIN_SEARCH) for c in out)


def test_names_only_come_from_verified_leaders():
    out = C.pick_contacts([], SALES_VISITOR, CO)
    assert out and all(c["name"] is None for c in out)
    assert out[0]["title"] == "Head of Sales" and "Acme" in out[0]["linkedin"]


def test_small_company_goes_to_the_founder():
    out = C.pick_contacts([{"name": "Ann Lee", "title": "Founder & CEO"}],
                          {"likely_role": "Marketing Manager", "confidence": 0.6}, {"name": "Tiny", "domain": "tiny.io", "employee_count": 20})
    assert out[0]["name"] == "Ann Lee"


def test_large_company_skips_ceo_sponsor():
    out = C.pick_contacts(LEADERS, SALES_VISITOR, {"name": "Big", "domain": "big.com", "employee_count": 50000})
    assert "Jane Doe" not in [c["name"] for c in out]


def test_low_confidence_visitor_is_not_listed():
    out = C.pick_contacts(LEADERS, {"likely_role": "VP of Sales", "confidence": 0.2}, CO)
    assert [c["name"] for c in out] == ["Raj Rao", "Jane Doe"]


def test_no_behavior_and_no_leaders_suggests_default_role():
    out = C.pick_contacts([], {"likely_role": "Unknown (no visitor behavior provided)", "confidence": 0}, CO)
    assert len(out) == 1 and out[0]["title"] == C.config.DEFAULT_CONTACT_ROLE


def test_unidentified_visitor_has_no_contacts():
    assert C.pick_contacts(LEADERS, SALES_VISITOR, {"name": "Unidentified Visitor (IP-based inference)"}) == []


def test_department_of():
    assert C.department_of("Head of Growth") == "marketing"
    assert C.department_of("Chief Technology Officer") == "engineering"
    assert C.department_of("Barista") is None


def test_generic_persona_label_is_not_a_contact():
    out = C.pick_contacts([], {"likely_role": "Decision Maker", "confidence": 0.9}, CO)
    assert [c["title"] for c in out] == [C.config.DEFAULT_CONTACT_ROLE]


def test_visitor_role_not_repeated_when_same_as_decision_maker():
    out = C.pick_contacts([], {"likely_role": "Head of Sales", "confidence": 0.9}, CO)
    assert [c["title"] for c in out] == ["Head of Sales"]



def test_site_ceo_newer_than_wikidata_is_not_doubled():
    """Wikidata's CEO is replaced by a newer CEO named on the company's own
    website; the leader check must not bring the old one back."""
    from src import wikidata
    from src.agents import keep_supported_leaders
    site = "Our team: Jane New, CEO."
    r = {"company": {}, "leadership": [{"name": "Jane New", "title": "CEO"}]}
    r = wikidata.apply_to_research(r, {"ceo": "Old Boss"}, site)
    r = keep_supported_leaders(r, "Old Boss", site)
    assert r["leadership"] == [{"name": "Jane New", "title": "CEO"}]


def test_revops_is_not_read_as_sales():
    assert C.department_of("Revenue Operations Manager") == "operations"
    assert C.department_of("Chief Operating Officer") == "operations"
    assert C.department_of("Chief Revenue Officer") == "sales"


def test_ceo_preferred_over_cofounder():
    leaders = [{"name": "A Cofounder", "title": "Co-founder"}, {"name": "B Boss", "title": "CEO"}]
    out = C.pick_contacts(leaders, {}, {"name": "Tiny", "domain": "tiny.io", "employee_count": 10})
    assert out[0]["name"] == "B Boss"


def test_missing_confidence_does_not_crash():
    out = C.pick_contacts(LEADERS, {"likely_role": "VP of Sales", "confidence": None}, CO)
    assert out[0]["name"] == "Raj Rao"


def test_executive_visitor_goes_to_ceo_not_head_of_sales():
    out = C.pick_contacts([], {"likely_role": "Founder / CEO", "confidence": 0.8}, CO)
    assert [c["title"] for c in out] == ["Founder / CEO"]
    out = C.pick_contacts(LEADERS, {"likely_role": "Founder / CEO", "confidence": 0.8}, CO)
    assert out[0]["name"] == "Jane Doe"


def test_no_contact_is_ever_listed_twice():
    for persona in ({"likely_role": "Head of Sales", "confidence": 0.9}, {"likely_role": "CEO", "confidence": 0.9}, {}):
        for size in (10, 300, 50000, None):
            out = C.pick_contacts(LEADERS, persona, {"name": "X", "domain": "x.com", "employee_count": size})
            keys = [(c["name"] or c["title"]).lower() for c in out]
            assert len(keys) == len(set(keys)) and 1 <= len(out) <= 3


def test_blank_names_and_titles_are_ignored():
    out = C.pick_contacts([{"name": " ", "title": "CEO"}, {"name": "X", "title": ""}], {}, CO)
    assert all(c["name"] is None for c in out)


def test_team_page_bios_never_become_company_facts():
    from src import website, provenance
    text = ("[Homepage https://acme.com/] Acme builds widgets.\n"
            f"{website.TEAM_MARKER}https://acme.com/team] Jane founded Zeta in 2009 and led a team of 40 engineers.")
    company = website.without_team_pages(text)
    assert "Jane" not in company and "Acme builds" in company
    assert provenance.founded_on_site(company) is None and provenance.employees_on_site(company) is None


def test_unconfirmed_company_has_no_contacts():
    assert C.pick_contacts(LEADERS, SALES_VISITOR, {"name": "Xyzzq Widgets LLC", "domain": None}) == []


def test_large_company_without_behavior_goes_to_buying_team_not_ceo():
    out = C.pick_contacts([{"name": "Pat C", "title": "CEO"}], {}, {"name": "Big", "domain": "big.com", "employee_count": 8000})
    assert [c["title"] for c in out] == [C.config.DEFAULT_CONTACT_ROLE]
