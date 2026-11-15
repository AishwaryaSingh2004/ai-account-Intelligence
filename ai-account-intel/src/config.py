import os
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: str = None):
    """Reads a setting from environment variables first (local dev via .env),
    falling back to Streamlit secrets (when deployed). Returns `default` if
    found in neither, so the app never crashes on a missing optional setting."""
    value = os.environ.get(key)
    if value is not None:
        return value
    
    try:
        import streamlit as st
        return st.secrets.get(key, default)
    except Exception:
        return default


def _int_setting(key: str, default: int, minimum: int = 0) -> int:
    """A numeric setting; falls back to the default if it's missing or not a
    number, so a typo in .env or secrets can't stop the app from starting."""
    try:
        return max(minimum, int(_get_secret(key, default)))
    except (TypeError, ValueError):
        return default


GEMINI_API_KEY = _get_secret("GEMINI_API_KEY")
GEMINI_MODEL = _get_secret("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_FALLBACK_MODEL = _get_secret("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")
# Extra models tried in order if the main and fallback models are overloaded
# or retired (comma-separated).
GEMINI_EXTRA_FALLBACK_MODELS = str(_get_secret("GEMINI_EXTRA_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-flash-lite-latest") or "")
FALLBACK_MODELS = list(dict.fromkeys(
    m.strip() for m in [GEMINI_FALLBACK_MODEL, *GEMINI_EXTRA_FALLBACK_MODELS.split(",")] if m and m.strip()
))
MAX_RETRIES = _int_setting("MAX_RETRIES", 3, minimum=1)
# Per-request limit; a stalled Gemini call moves on to the next model after this.
REQUEST_TIMEOUT_SECONDS = _int_setting("REQUEST_TIMEOUT_SECONDS", 30, minimum=5)
RETRY_BASE_WAIT_SECONDS = _int_setting("RETRY_BASE_WAIT_SECONDS", 5)
BATCH_DELAY_SECONDS = _int_setting("BATCH_DELAY_SECONDS", 3)
# Never below agents.MIN_OUTPUT_TOKENS: thinking models spend part of this on reasoning.
MAX_OUTPUT_TOKENS = _int_setting("MAX_OUTPUT_TOKENS", 8192)


# ---- Outside data sources (website, job boards, Wikidata, news, IP registry) ----
# How this app identifies itself to the sites it reads. Set CONTACT_URL (e.g.
# your repo or a contact page) to add it, as Wikimedia asks automated clients to.
CONTACT_URL = str(_get_secret("CONTACT_URL", "") or "").strip()
USER_AGENT = str(_get_secret("USER_AGENT", "") or "").strip() or (
    "Mozilla/5.0 (compatible; AccountIntelBot/1.0; company research"
    + (f"; +{CONTACT_URL}" if CONTACT_URL else "") + ")")
HTTP_TIMEOUT_SECONDS = _int_setting("HTTP_TIMEOUT_SECONDS", 8, minimum=1)        # one request to a data source
WEBSITE_TIME_BUDGET_SECONDS = _int_setting("WEBSITE_TIME_BUDGET_SECONDS", 12, minimum=2)  # homepage + About page
IP_LOOKUP_TIMEOUT_SECONDS = _int_setting("IP_LOOKUP_TIMEOUT_SECONDS", 10, minimum=1)
# How long optional lookups may run past the AI work they overlap with before
# the report goes ahead without them.
SIDE_LOOKUP_GRACE_SECONDS = _int_setting("SIDE_LOOKUP_GRACE_SECONDS", 12, minimum=0)
NEWS_GRACE_SECONDS = _int_setting("NEWS_GRACE_SECONDS", 15, minimum=0)
NEWS_MAX_ITEMS = _int_setting("NEWS_MAX_ITEMS", 5, minimum=1)
BATCH_MAX_ROWS = _int_setting("BATCH_MAX_ROWS", 25, minimum=1)   # each row is a full multi-agent run
# Who to contact when there is no visitor behavior to point to a team: the role
# that usually buys what you sell. Change it to match your product.
DEFAULT_CONTACT_ROLE = str(_get_secret("DEFAULT_CONTACT_ROLE", "Head of Sales") or "").strip() or "Head of Sales"


def has_valid_key() -> bool:
    return bool(GEMINI_API_KEY)
