# AI Account Intelligence & Enrichment System

Built for the **Fello AI Builder Hackathon**. Turns a company name, an anonymous website visitor's IP/behavior, or both, into structured, sales-ready account intelligence — company profile, tech stack, leadership, likely persona, intent score, live news, and a recommended next action — in a single Streamlit app powered by a multi-agent Gemini pipeline.

**Stack:** Python · Google Gemini API (`google-genai`) with Google Search grounding · Pydantic · Streamlit · fpdf2

## Contents

- [The problem](#the-problem)
- [How it works](#how-it-works)
- [Features](#features)
- [Example output](#example-output)
- [Coverage against the brief](#coverage-against-the-brief)
- [Project structure](#project-structure)
- [Setup](#setup)
- [How to use it](#how-to-use-it)
- [Testing](#testing)
- [Design decisions](#design-decisions)
- [Known limitations / what's next](#known-limitations--whats-next)

## The problem

Sales and marketing teams have two data problems: anonymous website visitors give almost no actionable signal, and incomplete company data makes it hard to know whom to prioritize. This system takes a bare company name, raw anonymous visitor activity (IP + behavior), or both, and turns it into something a salesperson can act on immediately.

## How it works

Rather than one large prompt, the work is split across narrow, single-purpose agents. Each returns JSON that the next one builds on, and the combined result is validated against a Pydantic schema before it reaches the UI.

```
Input: company name and/or IP + location, plus optional visitor behavior
        │
        ▼
 [Researcher]     → company profile, tech stack, business signals, leadership
        │
        ▼
 [Scorer]         → likely persona + intent score (0–10) and stage from behavior
        │
        ├──► [Strategist]     → plain-English summary, recommended sales action, confidence notes
        ├──► [Risk Analyst]   → five account-risk ratings (Low / Medium / High)
        └──► [News & Sources] → real recent news + verification links via live Google Search
        │
        ▼
 Validated JSON (Pydantic)  →  Streamlit dashboard  →  PDF report
                                     │
                                     ▼
                            [Q&A agent] answers follow-up questions
                            grounded in the generated account data
```

All agents live in [`ai-account-intel/src/agents.py`](ai-account-intel/src/agents.py).

## Features

- **Company identification** from a name (most reliable) or, with no name at all, from an IP address and/or location. A free RDAP registry lookup (ipwhois, no sign-up) shows who really owns the IP: if the block is registered to a company (e.g. Apple's 17.0.0.0/8), the visitor is identified as that company and gets the full report; if it belongs to an internet provider, mobile carrier or cloud/VPN host, the report says so instead of guessing a company.
- **Batch CSV mode** — upload a CSV of up to 25 companies (with optional visitor behavior per row), get them back ranked by intent score with stage, persona and recommended action, and download the ranked list as a CSV. Rows that fail are listed last with the reason instead of stopping the run.
- **Who to contact** — up to three contacts, best first: the verified leader who heads the visitor's team (or the founder/CEO at small companies), the visitor's own likely role as a warm contact, and an executive sponsor at small and mid-size companies. Names come only from verified leaders (Wikidata or the company's own website, including its Team/Leadership page); when no name is public the contact is a role with a one-click LinkedIn search. The recommended action opens with the first contact. Set `DEFAULT_CONTACT_ROLE` to the role that usually buys your product.
- **Profile enrichment** — domain, industry, size, HQ, founding year, tech stack (CRM, marketing automation, website platform, analytics), leadership, and business signals.
- **Persona and intent scoring** from browsing behavior (pages visited, visits this week, time on site, referral source, device). Hover the *Intent* heading to see which signals drove the score.
- **Grounded in real, free data** (no paid APIs, no keys) instead of model memory alone:
  - **Company website** — the homepage and About page are read and given to the Researcher as its most reliable source.
  - **Real tech stack** — ~80 sales/marketing tools (CRMs, marketing automation, CMS, analytics, chat, ad pixels, intent/ABM tools) detected from the homepage's code. Detected values replace the AI's guesses; anything unconfirmed is labeled "(AI guess)".
  - **Hiring signals** — open roles from the company's public job board (Greenhouse, Lever, Ashby or Rippling): roles per team, SDR/AE hiring, leadership hires, locations, and tools named in job posts.
  - **Verified facts** — HQ, founding year, current CEO, headcount (dated), stock listing and revenue from Wikidata, linked to Wikipedia.
  - **Recent news** — Gemini with Google Search grounding when available, otherwise free Google News RSS, filtered for relevance (company named in the headline, business events first, duplicates removed).
- **Recommended sales action** with concrete next steps and a `confidence_notes` field on every result.
- **AI Research Agent** — ask free-form questions ("Who should I contact first?", "What's the likelihood they'll buy?") answered from the account data, with one-click example questions.
- **Download PDF** — one-click account report, including the risk analysis (employee turnover, funding slowdown, negative news, declining hiring, low buying intent).

## Example output

Given **BrightPath Lending** plus visitor behavior (pages `/pricing`, `/ai-sales-agent`, `/case-studies`; 3 visits this week; 222 seconds on site):

```
Company:          BrightPath Lending
Website/Domain:   brightpathlending.com
Industry:         Mortgage Lending
Company Size:     ~200 employees
Headquarters:     Chicago, USA

Technology Stack: CRM: Salesforce · Marketing Automation: HubSpot
                  Website Platform: WordPress · Analytics: Google Analytics

Leadership:       Jane Doe — VP of Lending
                  John Smith — President & CEO

Business Signals: High reliance on digital lead generation
                  Likely active in residential refinancing

Likely Persona:   Decision Maker (85% confidence)
Intent Score:     8.2 / 10
Intent Stage:     Evaluation

AI Summary:       BrightPath Lending is an established mortgage lender
                  currently evaluating tools to improve lead-to-loan
                  conversion, based on strong recent engagement with
                  pricing and case study content.

Recommended Action:
  - Reach out to VP of Lending via LinkedIn with a relevant case study
  - Monitor for continued pricing-page engagement as a buying signal
  - Reference mortgage-specific ROI data in first outreach
```

## Coverage against the brief

| Brief requirement | Where it lives |
| --- | --- |
| Company identification (name, or IP/location) | Researcher agent |
| Company profile enrichment | Researcher agent |
| Technology stack detection | Researcher agent |
| Business signals | Researcher agent |
| Leadership discovery | Researcher agent |
| Persona inference | Scorer agent |
| Intent scoring | Scorer agent |
| Key signals observed | Derived directly from the visitor inputs, shown in the UI |
| AI account intelligence summary | Strategist agent |
| Recommended sales action | Strategist agent |
| Multi-agent workflow *(optional extension)* | Researcher → Scorer → Strategist / Risk Analyst / News |
| Data confidence scoring *(optional extension)* | `confidence_notes` on every result |
| Beyond the brief | Live search-grounded news & sources, AI Q&A agent, risk analysis, PDF export |

## Project structure

```
ai-account-intel/
├── app.py                 # Streamlit frontend (single page)
├── requirements.txt
├── .env.example           # copy to .env and add your key
├── run_tests.py           # runs the live pipeline against all sample data (uses the API)
├── requirements-dev.txt   # adds pytest for the automated tests
├── tests/                 # offline automated test suite (no API key needed)
├── .streamlit/config.toml
└── src/
    ├── config.py          # all settings, read from .env or Streamlit secrets
    ├── schema.py          # locked Pydantic output schema
    ├── validate.py        # schema validation helper
    ├── agents.py          # the agent pipeline + Q&A agent
    ├── website.py         # safe reading of the company's website
    ├── techstack.py       # tech-stack detection from the website's code
    ├── hiring.py          # hiring signals from public job boards
    ├── wikidata.py        # verified company facts from Wikidata
    ├── news.py            # free news fallback (Google News RSS)
    ├── iplookup.py        # who owns an IP address (free RDAP lookup)
    ├── batch.py           # batch CSV mode: analyze many companies, rank by intent
    ├── report.py          # PDF report generation
    ├── ui.py              # page theme (CSS) and HTML for each results card
    └── data/
        ├── sample_companies.json
        └── sample_visitor_signals.json
```

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/AishwaryaSingh2004/ai-account-intelligence.git
cd ai-account-intelligence/ai-account-intel
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com) (no credit card required) and put it in `.env`:

```
GEMINI_API_KEY=your-key-here
```

Everything else in `.env` is optional with sensible defaults: the models and fallbacks, retries and timeouts, how long optional lookups may run, how many news items to show, the batch size limit, and how the app identifies itself to the sites it reads (`CONTACT_URL` / `USER_AGENT`). `.env.example` lists them all. Nothing is hardcoded except safety limits for reading websites (allowed ports, redirects, page size), which stay fixed on purpose. When deployed on Streamlit Community Cloud, the same keys are read from Streamlit secrets instead.

Run it:

```bash
streamlit run app.py
```

The repo also includes a Dev Container config, so it runs in GitHub Codespaces with no local setup.

## How to use it

Provide at least one way to identify a company:

- **Company name** (most reliable), e.g. `BrightPath Lending`, optionally with a **website or domain** to sharpen identification. A website on its own (e.g. `fello.ai`) also works.
- **IP address and/or location** with no company name, to identify an anonymous visitor from who owns the IP (RDAP registration lookup). IP addresses are validated (IPv4 or IPv6) before anything is sent to the model.
- **Visitor behavior** (optional, collapsible) — pages visited, time on site, visits this week, referral source, device. This moves the intent score and persona from "no signal" to a real inference.

Click **Analyze**, then explore the results, ask the AI Research Agent follow-up questions, or download the PDF report.

## Testing

**Automated test suite (offline).** About 170 tests cover the agents' retry and fallback logic, the pipeline (including that slow or broken data sources can't hold up or break a report), website safety (blocked internal addresses, DNS rebinding, slow-dripping sites, zip bombs), tech detection, hiring, Wikidata, news relevance, IP ownership lookup, batch CSV parsing and ranking, the app's input handling, HTML escaping and the PDF. Every network call is faked, so the suite needs no API key and runs in seconds:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

GitHub Actions runs it on every push and pull request (`.github/workflows/tests.yml`), so a change that breaks something, or brings back a fixed bug, is caught before it ships. When fixing a bug, add a test that reproduces it.

**Live checks** use the sample data in `src/data/` and the real API:

- **Via the UI** — copy any entry from `sample_companies.json` or `sample_visitor_signals.json` into the app.
- **Automated** — run every sample through the full pipeline:

  ```bash
  python run_tests.py
  ```

  This prints `OK` / `SCHEMA ERROR` / `CRASHED` for each golden-path and deliberately messy case (misspelled names, empty fields, garbled input, fabricated companies) and for high-, medium- and low-intent visitor profiles, so the handling of messy real-world input is verifiable in one run.

## Design decisions

- **Narrow agents over one big prompt.** Each agent has one job, which makes outputs more consistent, failures easier to debug, and any step replaceable (e.g. swapping the Researcher for real enrichment APIs) without touching the rest.
- **Schema-validated output.** Every result is parsed into Pydantic models with enums and range constraints (intent 0–10, confidence 0–1, stage ∈ Awareness/Evaluation/Decision), so malformed model output fails cleanly instead of corrupting the UI.
- **Calibrated scoring.** The Scorer prompt includes explicit score bands and a worked example — without them, the model confused the 0–10 intent scale with the 0–1 confidence scale.
- **Consistent intent.** The Scorer runs at temperature 0 on normalized input, and the funnel stage is derived from the score with fixed cutoffs (under 4 Awareness, 4–8.9 Evaluation, 9+ Decision), so the same behavior always gets the same score and stage. With no behavior at all, the result is a fixed 0 / Awareness with no model call.
- **No placeholder data.** Model placeholders like "N/A" or "unknown" are turned into blanks, and the Researcher returns no leadership rather than inventing names for companies it doesn't know.
- **Gemini as a simulated enrichment engine.** In production, the Researcher would call real enrichment APIs (Clearbit, Apollo, BuiltWith) and a commercial reverse-IP service (the free RDAP lookup here only names companies that register their own IP blocks; most office and home traffic goes through ISPs). Using Gemini for that step was a deliberate choice to ship a working end-to-end system inside the hackathon window.
- **Anonymous visitors get a role, not a name.** The Scorer infers a likely persona (e.g. "Decision Maker") but never attaches a name to a visitor. Named individuals appear only under Leadership, as suggested outreach targets.
- **IP-only identification is explicitly low-confidence.** With no company name, the system returns a clearly labeled inference instead of inventing a specific company, and says so in `confidence_notes`.
- **Structured output on every agent.** Each agent passes its Pydantic model to Gemini as the response schema and validates the answer, so even the lighter fallback models return exactly the expected structure. Output tokens never drop below 8,192, since thinking models spend part of that budget on reasoning.
- **Resilient to outages and the free tier.** A rate-limited (429), overloaded (5xx) or retired (404) model switches straight to the next model in a fallback chain (`GEMINI_FALLBACK_MODEL` + `GEMINI_EXTRA_FALLBACK_MODELS`); cut-off or mis-shaped answers are retried; users see plain-English errors. The search-grounded news step has its own quota, so it degrades to a "news unavailable" note rather than failing the run, and malformed news items are dropped individually.
- **Parallel where possible.** News runs alongside the Scorer and the Risk Analyst alongside the Strategist, which roughly halves the wait.
- **Safe redeploys.** Streamlit keeps imported modules in memory across a deploy, so `app.py` reloads the project's own modules whenever their files change. Streamlit is pinned to the version the custom UI was tested on.
- **Search grounding workaround.** Gemini's JSON response mode can't be combined with the Google Search tool, so the news agent asks for raw JSON in the prompt, extracts the JSON object from the reply, validates each item, and merges in the URLs from the grounding metadata.

## Known limitations / what's next

- Small companies have no Wikidata entry and may have no readable website or public job board; their profile then relies more on the model (unconfirmed tech values are labeled "(AI guess)"). Only tools visible in the homepage code can be detected, and Wikidata covers the CEO but not other executives.
- Every company fact shows its source: Wikidata (with the year for headcounts), the company's own website, or "AI estimate · not verified". Most private companies publish no headcount, so their size is usually an AI estimate. Business signals written by the model carry an "AI" tag, and any figure in them ("$1.9tn", "99.999%") must appear on the company's website or the signal is dropped.
- A leader is shown only if they are the CEO on Wikidata or their name appears on the company's own website. Executives remembered by the model alone are never shown, since they are often out of date, so some reports list only the CEO or no one.
- Wikidata facts are matched only through the company's official website (given by the user, or found by the Researcher), never by name alone, so a common name like "Mercury" can't pull in a different company's facts. Companies whose Wikidata entry lists no website get no Wikidata facts.
- Job boards follow the same rule: with only a name, the board is looked up after the official website is found, preferring the board linked from that site.
- Team/Leadership pages are read only to confirm people's names; staff bios are never used for company facts (founding year, headcount) or to back up figures.
- A company with no official website and no Wikidata entry is treated as unconfirmed: no guessed headquarters, no contacts, and the recommended action is to confirm the company first.
- Sources can disagree when something just changed (e.g. a CEO change reported in today's news but not yet in Wikidata).
- Gemini's free tier has a real requests-per-minute ceiling. Retry/backoff and a fallback model handle it, but it's worth knowing before a live demo.
- Next steps: wire real enrichment and commercial reverse-IP APIs behind the same agent interface, persist visitor history for tracking intent over time, and add a CRM integration for one-click outreach from the Recommended Action section.
