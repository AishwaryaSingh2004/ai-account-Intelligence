"""Settings come from .env / Streamlit secrets, not from values fixed in code,
and nothing personal or made-up is baked into the app."""
import importlib
import re
from pathlib import Path

from src import config


def test_data_source_settings_can_be_changed(monkeypatch):
    for key, value in {"HTTP_TIMEOUT_SECONDS": "3", "NEWS_MAX_ITEMS": "7", "BATCH_MAX_ROWS": "40",
                       "SIDE_LOOKUP_GRACE_SECONDS": "4", "CONTACT_URL": "https://example.org/contact"}.items():
        monkeypatch.setenv(key, value)
    cfg = importlib.reload(config)
    try:
        assert (cfg.HTTP_TIMEOUT_SECONDS, cfg.NEWS_MAX_ITEMS, cfg.BATCH_MAX_ROWS, cfg.SIDE_LOOKUP_GRACE_SECONDS) == (3, 7, 40, 4)
        assert "+https://example.org/contact" in cfg.USER_AGENT
        monkeypatch.setenv("USER_AGENT", "MyBot/2.0")
        assert importlib.reload(config).USER_AGENT == "MyBot/2.0"
    finally:
        for key in ("HTTP_TIMEOUT_SECONDS", "NEWS_MAX_ITEMS", "BATCH_MAX_ROWS", "SIDE_LOOKUP_GRACE_SECONDS", "CONTACT_URL", "USER_AGENT"):
            monkeypatch.delenv(key, raising=False)
        importlib.reload(config)


def test_default_user_agent_has_no_personal_details(monkeypatch):
    monkeypatch.delenv("CONTACT_URL", raising=False); monkeypatch.delenv("USER_AGENT", raising=False)
    agent = importlib.reload(config).USER_AGENT
    assert "github.com" not in agent and "@" not in agent and "AccountIntelBot" in agent


def test_no_personal_details_or_secrets_in_the_code():
    code = "\n".join(p.read_text() for p in [Path("app.py"), *Path("src").glob("*.py")])
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", code)            # Google API keys
    assert not re.search(r"[\w.+-]+@gmail\.com", code)                # personal email addresses
    assert "AishwaryaSingh2004" not in code and "aishwaryasingh20" not in code  # personal accounts


def test_hero_shows_no_made_up_results():
    from src import ui
    hero = ui.hero_html()
    assert not re.search(r"intent \d", hero)  # e.g. a fake "intent 8.2"
