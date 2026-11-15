"""Detects a company's real tech stack from its homepage code (HTML + HTTP
headers), Wappalyzer-style: each tool leaves fingerprints such as the
domains its scripts load from or markers in the page. Matches replace the
AI's guesses for the matching tech-stack fields.

Built in instead of using python-Wappalyzer (breaks on current Python,
fingerprints frozen in 2020) or webtech (downloads its database at runtime
and fetches URLs itself, bypassing the safe fetcher in website.py).

Limits: only tools visible in the homepage code are found. Back-office
systems (e.g. a CRM with no web forms) and tags loaded later by a tag
manager can't be seen, so "not detected" doesn't mean "not used"."""
import re

from bs4 import BeautifulSoup, Comment, NavigableString

# (name, category, html patterns, header patterns). Patterns are regexes
# matched against the lowercased homepage HTML, and against lowercased
# "header: value" lines (only the headers in _HEADERS_SCANNED).
FINGERPRINTS = [
    # --- CRM ---
    ("Salesforce", "crm", [r"\.force\.com", r"salesforce\.com/servlet/servlet\.webtolead", r"salesforceliveagent", r"embeddedservice_bootstrap"], []),
    # HubSpot's marketing tools run on the HubSpot CRM, so they imply it.
    ("HubSpot CRM", "crm", [r"hs-scripts\.com", r"hsforms\.(net|com)", r"hbspt\.forms\.create", r"hs-analytics\.net", r"hs-banner\.com", r"hsleadflows\.net", r"cp\.hubspot\.com", r"hscollectedforms\.net", r"js\.usemessages\.com"], []),
    ("Zoho CRM", "crm", [r"crm\.zoho\.", r"zohopublic\.com", r"zcampaign\.zoho\."], []),
    ("Pipedrive", "crm", [r"pipedrivewebforms", r"leadbooster-chat\.pipedrive"], []),
    ("Freshsales", "crm", [r"fw-cdn\.com", r"freshsales\.io"], []),
    ("Microsoft Dynamics 365", "crm", [r"\.dynamics\.com", r"mktdplp\d*cdn\.azureedge\.net", r"msdynmkt"], []),
    # --- Marketing automation / email ---
    ("HubSpot Marketing Hub", "marketing_automation", [r"hs-scripts\.com", r"hsforms\.(net|com)", r"hbspt\.forms\.create", r"hs-analytics\.net", r"hs-banner\.com", r"hsleadflows\.net", r"cp\.hubspot\.com", r"hscollectedforms\.net", r"js\.usemessages\.com"], []),
    ("Marketo", "marketing_automation", [r"munchkin", r"mktoforms", r"marketo\.com", r"mktdns\.com", r"mkto-"], []),
    ("Salesforce Account Engagement (Pardot)", "marketing_automation", [r"pi\.pardot\.com", r"go\.pardot\.com", r"piaid\s*=", r"pardot\.com/pd\.js"], []),
    ("Oracle Eloqua", "marketing_automation", [r"elqcfg", r"\.en25\.com", r"eloqua\.com/"], []),
    ("Mailchimp", "marketing_automation", [r"chimpstatic\.com", r"list-manage\.com"], []),
    ("Klaviyo", "marketing_automation", [r"static\.klaviyo\.com", r"klaviyo\.com/onsite"], []),
    ("ActiveCampaign", "marketing_automation", [r"activehosted\.com", r"trackcmp\.net"], []),
    ("Brevo", "marketing_automation", [r"sibforms\.com", r"sibautomation\.com", r"sendinblue\.com", r"brevo\.com/js"], []),
    ("Customer.io", "marketing_automation", [r"assets\.customer\.io", r"track\.customer\.io"], []),
    ("Braze", "marketing_automation", [r"js\.appboycdn\.com", r"sdk\.iad-\d+\.braze\.com", r"braze\.com/web-sdk"], []),
    ("Iterable", "marketing_automation", [r"js\.iterable\.com"], []),
    ("Kit (ConvertKit)", "marketing_automation", [r"convertkit\.com", r"ck\.page"], []),
    # --- Website platform / CMS / framework ---
    ("HubSpot CMS", "website_platform", [r"hs-sites\.com", r"hubspotusercontent", r"/hs/hsstatic/", r"hs-fs/hubfs"], [r"^x-hs-cache-config:", r"^x-hs-content-id:"]),
    ("WordPress", "website_platform", [r"/wp-content/", r"/wp-includes/", r'name="generator" content="wordpress'], [r"^x-powered-by: wp engine", r"^link: .*wp-json"]),
    ("Webflow", "website_platform", [r"data-wf-page", r"webflow\.js", r"website-files\.com", r"assets-global\.website-files"], []),
    ("Framer", "website_platform", [r"framerusercontent\.com", r"framer\.com/m/", r'name="generator" content="framer'], [r"^server: framer"]),
    ("Wix", "website_platform", [r"static\.wixstatic\.com", r"wix-code", r'name="generator" content="wix'], [r"^x-wix-"]),
    ("Squarespace", "website_platform", [r"static1\.squarespace\.com", r"squarespace-cdn", r'name="generator" content="squarespace'], []),
    ("Shopify", "website_platform", [r"cdn\.shopify\.com", r"shopify\.theme", r"myshopify\.com"], [r"^x-shopid:", r"^x-shopify-"]),
    ("Drupal", "website_platform", [r"drupal-settings-json", r"/sites/default/files/", r'name="generator" content="drupal'], [r"^x-drupal-", r"^x-generator: drupal"]),
    ("Ghost", "website_platform", [r'name="generator" content="ghost', r"ghost\.io/"], []),
    ("Adobe Experience Manager", "website_platform", [r"/etc\.clientlibs/", r"/content/dam/"], []),
    ("Sitecore", "website_platform", [r"/-/media/", r"/sitecore/", r"sc_itemid"], []),
    ("Contentful", "website_platform", [r"images\.ctfassets\.net", r"ctfassets\.net"], []),
    ("Sanity", "website_platform", [r"cdn\.sanity\.io"], []),
    ("Next.js", "website_platform", [r"__next_data__", r"/_next/static/"], [r"^x-powered-by: next\.js"]),
    ("Nuxt", "website_platform", [r"__nuxt", r"/_nuxt/"], []),
    ("Gatsby", "website_platform", [r"___gatsby", r'name="generator" content="gatsby'], []),
    ("Unbounce", "website_platform", [r"unbounce\.com", r"ubembed"], []),
    # --- Analytics ---
    ("Google Analytics", "analytics", [r"google-analytics\.com/(analytics|ga)\.js", r"googletagmanager\.com/gtag/js\?id=(g|ua)-", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"](g|ua)-"], []),
    ("Segment", "analytics", [r"cdn\.segment\.com", r"segment\.io/analytics"], []),
    ("Mixpanel", "analytics", [r"cdn\.mxpnl\.com", r"mixpanel\.init"], []),
    ("Amplitude", "analytics", [r"cdn\.amplitude\.com", r"amplitude\.getinstance", r"amplitude\.init"], []),
    ("Heap", "analytics", [r"cdn\.heapanalytics\.com", r"heap\.load\("], []),
    ("Hotjar", "analytics", [r"static\.hotjar\.com"], []),
    ("FullStory", "analytics", [r"fullstory\.com/s/fs\.js", r"edge\.fullstory\.com"], []),
    ("PostHog", "analytics", [r"posthog\.init", r"\.posthog\.com/static"], []),
    ("Microsoft Clarity", "analytics", [r"clarity\.ms/tag"], []),
    ("Adobe Analytics", "analytics", [r"\.omtrdc\.net", r"s_code\.js", r"appmeasurement"], []),
    ("Matomo", "analytics", [r"matomo\.js", r"piwik\.js", r"_paq\.push"], []),
    ("Plausible", "analytics", [r"plausible\.io/js"], []),
    ("Pendo", "analytics", [r"cdn\.pendo\.io", r"pendo\.initialize"], []),
    ("HubSpot Analytics", "analytics", [r"hs-analytics\.net"], []),
    ("Vercel Analytics", "analytics", [r"/_vercel/insights", r"va\.vercel-scripts\.com"], []),
    # --- Other sales-relevant tools (reported as signals) ---
    ("Google Tag Manager", "tag manager", [r"googletagmanager\.com/gtm\.js", r"googletagmanager\.com/ns\.html"], []),
    ("Intercom", "live chat", [r"widget\.intercom\.io", r"intercomcdn\.com", r"intercomsettings"], []),
    ("Drift", "live chat", [r"js\.driftt\.com", r"drift\.load"], []),
    ("Zendesk", "live chat", [r"static\.zdassets\.com", r"zopim"], []),
    ("Qualified", "live chat", [r"js\.qualified\.com"], []),
    ("Crisp", "live chat", [r"client\.crisp\.chat"], []),
    ("Tawk.to", "live chat", [r"embed\.tawk\.to"], []),
    ("LiveChat", "live chat", [r"cdn\.livechatinc\.com"], []),
    ("6sense", "intent / ABM", [r"j\.6sc\.co", r"\.6sense\.com/"], []),
    ("Demandbase", "intent / ABM", [r"tag\.demandbase\.com", r"api\.company-target\.com"], []),
    ("ZoomInfo WebSights", "intent / ABM", [r"ws\.zoominfo\.com", r"zi-scripts"], []),
    ("Clearbit", "intent / ABM", [r"tag\.clearbitscripts\.com", r"x\.clearbitjs\.com"], []),
    ("Leadfeeder", "intent / ABM", [r"lftracker", r"sc\.lfeeder\.com"], []),
    ("RB2B", "intent / ABM", [r"rb2b\.com/", r"reb2b\.js"], []),
    ("LinkedIn Insight Tag", "ad pixels", [r"snap\.licdn\.com", r"_linkedin_partner_id"], []),
    ("Meta Pixel", "ad pixels", [r"connect\.facebook\.net/[^\"']*/fbevents\.js", r"fbq\(\s*['\"]init"], []),
    ("Google Ads", "ad pixels", [r"googleadservices\.com", r"googletagmanager\.com/gtag/js\?id=aw-", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]aw-"], []),
    ("TikTok Pixel", "ad pixels", [r"analytics\.tiktok\.com"], []),
    ("Reddit Pixel", "ad pixels", [r"redditstatic\.com/ads"], []),
    ("X (Twitter) Pixel", "ad pixels", [r"static\.ads-twitter\.com"], []),
    ("Optimizely", "A/B testing", [r"cdn\.optimizely\.com"], []),
    ("VWO", "A/B testing", [r"visualwebsiteoptimizer\.com", r"_vwo_code"], []),
    ("Calendly", "scheduling", [r"assets\.calendly\.com", r"calendly\.com/[a-z0-9_-]+"], []),
    ("Chili Piper", "scheduling", [r"chilipiper\.com"], []),
    ("Stripe", "payments", [r"js\.stripe\.com"], []),
    ("OneTrust", "consent", [r"cdn\.cookielaw\.org", r"otsdkstub", r"optanon"], []),
    ("Cookiebot", "consent", [r"consent\.cookiebot\.com"], []),
    ("Cloudflare", "hosting / CDN", [], [r"^server: cloudflare", r"^cf-ray:"]),
    ("Vercel", "hosting / CDN", [], [r"^server: vercel", r"^x-vercel-id:"]),
    ("Netlify", "hosting / CDN", [], [r"^server: netlify", r"^x-nf-request-id:"]),
    ("Amazon CloudFront", "hosting / CDN", [], [r"^x-amz-cf-id:", r"^via: .*cloudfront"]),
    ("Fastly", "hosting / CDN", [], [r"^x-served-by: cache-", r"^x-fastly-"]),
    ("Akamai", "hosting / CDN", [], [r"^server: akamai", r"^x-akamai-"]),
]

TECH_FIELDS = ("crm", "marketing_automation", "website_platform", "analytics")

# Only these headers are scanned. Content-Security-Policy is deliberately
# excluded: it lists domains a site *allows*, not tools it actually runs.
_HEADERS_SCANNED = {"server", "x-powered-by", "x-generator", "via", "link", "cf-ray", "x-served-by"}

_COMPILED = [
    (name, cat, [re.compile(p) for p in html_pats], [re.compile(p) for p in header_pats])
    for name, cat, html_pats, header_pats in FINGERPRINTS
]


def _header_lines(headers) -> list:
    lines = []
    for key, value in (headers or {}).items():
        k = str(key).lower()
        if k in _HEADERS_SCANNED or k.startswith(("x-hs-", "x-wix-", "x-shopify", "x-shopid", "x-drupal",
                                                 "x-vercel", "x-nf-", "x-amz-cf", "x-fastly", "x-akamai")):
            lines.append(f"{k}: {str(value).lower()}")
    return lines


def _code_only(html) -> str:
    """The page's markup and scripts without its visible prose, so a blog post
    that merely mentions "6sense" or "Eloqua" doesn't count as using it."""
    soup = BeautifulSoup(html or b"", "html.parser")
    for node in soup.find_all(string=True):
        if isinstance(node, Comment) or (isinstance(node, NavigableString)
                                         and node.parent.name not in ("script", "style")):
            node.extract()
    return str(soup).lower()


def detect(html, headers=None) -> dict:
    """Returns {"fields": {field: [tool, ...]}, "other": {category: [tool, ...]},
    "all": [tool, ...]}. Empty lists when nothing is found."""
    text = _code_only(html)
    lines = _header_lines(headers)
    fields = {f: [] for f in TECH_FIELDS}
    other = {}
    found = []
    for name, cat, html_pats, header_pats in _COMPILED:
        hit = any(p.search(text) for p in html_pats) or any(p.search(line) for p in header_pats for line in lines)
        if not hit:
            continue
        found.append(name)
        if cat in fields:
            fields[cat].append(name)
        else:
            other.setdefault(cat, []).append(name)

    # A CMS beats the framework it's built on (show "Webflow", not "Webflow, Next.js"),
    # unless the framework is all we found.
    frameworks = {"Next.js", "Nuxt", "Gatsby"}
    platforms = fields["website_platform"]
    if any(p not in frameworks for p in platforms):
        fields["website_platform"] = [p for p in platforms if p not in frameworks] + \
                                     [p for p in platforms if p in frameworks][:1]
    return {"fields": fields, "other": other, "all": found}


def apply_to_research(research: dict, detection: dict) -> dict:
    """Replaces the AI's tech-stack guesses with what the website's code
    actually shows. Fields with no detection keep the AI's value, labelled as
    a guess. Other sales-relevant tools are added as one business signal."""
    if not detection or not detection.get("all"):
        return research
    tech = research.setdefault("tech_stack", {})
    found = [n.lower() for n in detection["all"]]
    for field in TECH_FIELDS:
        names = detection["fields"].get(field) or []
        guess = tech.get(field)
        if names:
            tech[field] = ", ".join(names[:3])
        elif guess and any(n in guess.lower() or guess.lower() in n for n in found):
            tech[field] = None  # the AI put a tool we detected under the wrong field
        elif guess:
            tech[field] = f"{guess} (AI guess)"  # not visible in the website's code
    if detection.get("other"):
        parts = [f"{', '.join(names)} ({cat})" for cat, names in detection["other"].items()]
        research.setdefault("business_signals", []).append("Website also runs: " + "; ".join(parts))
    return research


def label_unverified(research: dict) -> dict:
    """Marks every tech-stack value that wasn't confirmed by the website's code
    or by job posts as "(AI guess)", so verified and guessed values are always
    told apart, whether or not a website was scanned."""
    tech = research.get("tech_stack") or {}
    for field in TECH_FIELDS:
        value = tech.get(field)
        if value and not str(value).endswith(("(AI guess)", "(from job posts)")):
            tech[field] = f"{value} (AI guess)"
    return research
