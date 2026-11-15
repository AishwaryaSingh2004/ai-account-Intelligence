"""The Streamlit app end to end (with the pipeline stubbed), the UI's HTML
safety, and the PDF report."""
import copy
import inspect
import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src
from src import ui
from src.report import build_pdf
from src.validate import validate_intel
from tests.conftest import FIXTURE


@pytest.fixture
def app(monkeypatch):
    """Runs app.py with the pipeline replaced; returns (start, pipeline_calls)."""
    calls = []

    def fake_pipeline(**kw):
        calls.append(kw)
        kw["on_step"]("research")
        return copy.deepcopy(FIXTURE)

    def start():
        # Mark the code as already loaded so the app doesn't reload over the stub.
        src._loaded_version = max(f.stat().st_mtime_ns for f in Path(src.__file__).parent.glob("*.py"))
        monkeypatch.setattr(sys.modules["src.agents"], "run_full_pipeline", fake_pipeline)
        at = AppTest.from_file("app.py", default_timeout=30)
        at.run()
        return at

    return start, calls


def field(at, label):
    return next(t for t in at.text_input if t.label == label)


def run_button(at):
    return next(b for b in at.button if "Run the agents" in b.label)


def test_app_starts_without_errors(app):
    start, _ = app
    assert not start().exception


def test_empty_form_shows_an_error(app):
    start, calls = app
    at = start(); run_button(at).click().run()
    assert not calls and any("Enter a company name or website" in e.value for e in at.error)


def test_website_alone_is_enough(app):
    start, calls = app
    at = start(); field(at, "Website or domain").input("https://www.fello.ai/"); run_button(at).click().run()
    assert calls[-1]["company_name"] == "fello.ai"


def test_invalid_ip(app):
    start, calls = app
    at = start(); field(at, "IP address").input("999.1.1"); run_button(at).click().run()
    assert not calls and any("isn't a valid IP" in e.value for e in at.error)
    at = start(); field(at, "Company name").input("Fello"); field(at, "IP address").input("bad"); run_button(at).click().run()
    assert calls[-1]["ip_address"] is None and any("ignored" in w.value for w in at.warning)


def test_results_render_and_empty_question_hint(app):
    start, _ = app
    at = start(); field(at, "Company name").input("Fello"); run_button(at).click().run()
    assert not at.exception and "parsed" in at.session_state
    assert sum("PDF" in b.proto.label for b in at.get("download_button")) == 1
    next(b for b in at.button if b.label == "Ask AI").click().run()
    assert any("Type a question first" in w.value for w in at.warning)


def test_changed_code_is_reloaded_unchanged_code_is_not():
    sys.modules["src.agents"].run_full_pipeline = lambda **kw: None  # a stale in-memory version
    src._loaded_version = "older"
    at = AppTest.from_file("app.py", default_timeout=30); at.run()
    assert "on_step" in inspect.signature(sys.modules["src.agents"].run_full_pipeline).parameters
    sys.modules["src.agents"]._probe = 1
    at.run()
    assert getattr(sys.modules["src.agents"], "_probe", None) == 1


def test_model_text_is_escaped_and_bad_links_dropped(fixture_data):
    parsed, _ = validate_intel(fixture_data)
    html = ui.signals_html(parsed, []) + ui.news_html(parsed)
    assert "<script>" not in html and "javascript:" not in html


def test_pdf_handles_unicode_and_long_urls(fixture_data):
    fixture_data["company"]["name"] = "Zürich — 株式会社 / “Test”"
    fixture_data["sources"] = [{"title": "long", "url": "https://e.com/" + "a" * 400}]
    parsed, err = validate_intel(fixture_data)
    assert err is None and build_pdf(parsed)[:5] == b"%PDF-"


def test_sample_data_files_are_valid_json():
    for name in ("sample_companies.json", "sample_visitor_signals.json"):
        assert json.loads(Path("src/data", name).read_text())


# ---- Batch CSV section --------------------------------------------------------
def batch_run(at, csv_text):
    at.file_uploader[0].set_value(("accounts.csv", csv_text.encode(), "text/csv"))
    at.run()
    next(b for b in at.button if "Analyze all" in b.label).click().run()
    return at


def test_batch_upload_runs_and_ranks(app, monkeypatch):
    start, calls = app
    start()  # loads the app code once
    scores = {"Low": 2.0, "High": 9.0}

    def pipeline(**kw):
        calls.append(kw)
        data = copy.deepcopy(FIXTURE)
        data["company"]["name"] = kw["company_name"]
        data["intent"]["score"] = scores[kw["company_name"]]
        return data
    monkeypatch.setattr(sys.modules["src.agents"], "run_full_pipeline", pipeline)
    monkeypatch.setattr(sys.modules["src.config"], "BATCH_DELAY_SECONDS", 0)
    at = AppTest.from_file("app.py", default_timeout=30); at.run()
    batch_run(at, "company_name,pages_visited\nLow,/blog\nHigh,/pricing;/demo\n")
    assert not at.exception
    results = at.session_state["batch_results"]
    assert [r["Company"] for r in sys.modules["src.batch"].ranked(results)] == ["High", "Low"]
    assert calls[-1]["visitor_signals"]["pages_visited"] == ["/pricing", "/demo"]
    assert any("ranked" in b.proto.label for b in at.get("download_button"))


def test_batch_errors_are_readable_and_bad_key_stops_the_run(app, monkeypatch):
    start, calls = app

    def pipeline(**kw):
        calls.append(kw)
        raise ValueError("GEMINI_API_KEY is not set")
    monkeypatch.setattr(sys.modules["src.config"], "BATCH_DELAY_SECONDS", 0)
    start()  # loads the app code once
    monkeypatch.setattr(sys.modules["src.agents"], "run_full_pipeline", pipeline)
    at = AppTest.from_file("app.py", default_timeout=30); at.run()
    batch_run(at, "company_name\nA\nB\nC\n")
    assert not at.exception and len(calls) == 1  # stopped after the first key error
    errors = [r["Error"] for r in at.session_state["batch_results"]]
    assert errors[0].startswith("Gemini API key problem") and all(e.startswith("Not analyzed") for e in errors[1:])


def test_bad_csv_shows_a_warning_and_no_run_button(app):
    start, calls = app
    at = start()
    at.file_uploader[0].set_value(("x.csv", b"foo,bar\n1,2\n", "text/csv")); at.run()
    assert any("needs a header row" in w.value for w in at.warning)
    assert next(b for b in at.button if "Analyze all" in b.label).disabled and not calls


# ---- Code reloading after a deploy / file change -------------------------------
def _reload_order():
    """The app's own ordering function, taken from app.py."""
    import ast as _ast
    source = Path("app.py").read_text()
    fn = next(n for n in _ast.parse(source).body
              if isinstance(n, _ast.FunctionDef) and n.name == "_src_modules_in_dependency_order")
    ns = {"src": src, "Path": Path, "ast": _ast}
    exec(compile(_ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
    return ns["_src_modules_in_dependency_order"]()


def test_every_src_module_is_reloaded_dependencies_first():
    order = _reload_order()
    assert set(order) == {f.stem for f in Path("src").glob("*.py")} - {"__init__"}  # nothing left out
    for module, deps in {"agents": ["provenance", "iplookup", "wikidata", "news", "schema", "config"],
                         "validate": ["schema"], "batch": ["agents"]}.items():
        for dep in deps:
            if dep in order and module in order:
                assert order.index(dep) < order.index(module), (dep, module)


def test_stale_module_is_reloaded():
    """Regression: a module missing from a hand-written reload list kept its old
    code, so the app failed with "module 'src.provenance' has no attribute
    'figures_supported'"."""
    stale = sys.modules["src.provenance"]
    del stale.figures_supported  # as if loaded before the function was added
    src._loaded_version = "older"
    at = AppTest.from_file("app.py", default_timeout=30); at.run()
    assert not at.exception and hasattr(sys.modules["src.provenance"], "figures_supported")


def test_who_to_contact_renders_in_app_pdf_and_batch(app, fixture_data):
    contacts = [{"name": "Raj Rao", "title": "Chief Revenue Officer", "why": "Leads sales.", "source": "Verified leader",
                 "linkedin": "https://www.linkedin.com/search/results/people/?keywords=Raj+Rao+Acme"},
                {"name": None, "title": "Head of Sales <b>", "why": "Likely visitor.", "source": "Role to find",
                 "linkedin": "javascript:alert(1)"}]
    fixture_data["contacts"] = contacts
    parsed, err = validate_intel(fixture_data)
    assert err is None
    html = ui.contacts_html(parsed)
    assert "Raj Rao" in html and "Contact first" in html and "linkedin.com/search" in html
    assert "Head of Sales &lt;b&gt;" in html                         # model text is escaped
    assert "javascript:" not in html                                   # unsafe link dropped
    assert build_pdf(parsed)[:5] == b"%PDF-"
    from src import batch
    rows, _ = batch.parse_csv(b"company_name\nAcme\n")
    out = batch.analyze_row(rows[0], lambda **k: copy.deepcopy(fixture_data), validate_intel)
    assert out["Contact first"] == "Raj Rao (Chief Revenue Officer)"
    fixture_data["contacts"] = []
    assert "No confirmed company yet" in ui.contacts_html(validate_intel(fixture_data)[0])

    start, _ = app
    at = start(); field(at, "Company name").input("Fello"); run_button(at).click().run()
    assert not at.exception and any("Who to contact" in str(getattr(e, "proto", "")) for e in at.main)
