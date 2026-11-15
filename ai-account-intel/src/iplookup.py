"""Who actually owns an IP address, from the free public RDAP registration
records of the regional internet registries (via the ipwhois library; no
sign-up or API key).

This is the real version of "reverse IP lookup": a visitor whose IP block is
registered to a company (e.g. 17.0.0.0/8 -> Apple Inc.) is identified as that
company. Most visitors, though, come through an ISP or mobile carrier (the
registrant is Comcast, Airtel, ...) or a cloud/VPN host (AWS, Cloudflare, ...);
those can't name the company, and the result says so instead of guessing."""
import ipaddress
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from ipwhois import IPWhois

from src import config

TIMEOUT_SECONDS = config.IP_LOOKUP_TIMEOUT_SECONDS   # whole lookup; the report goes ahead without it after this
_cache = {}            # ip -> result (registrations rarely change)
_cache_lock = threading.Lock()

# Who the registrant is decides what the IP can tell us.
_HOSTING = re.compile(
    r"\b(amazon|aws|azure|digitalocean|digital ocean|linode|akamai|ovh|hetzner|cloudflare|fastly|"
    r"vultr|choopa|leaseweb|contabo|scaleway|m247|datacamp|hostinger|godaddy|rackspace|"
    r"alibaba cloud|aliyun|tencent cloud|oracle cloud|google cloud|cloud|hosting|host|"
    r"data ?cent(er|re)s?|colo(cation)?|servers?|vps|cdn|vpn|proxy)\b", re.I)
_ISP = re.compile(
    r"\b(comcast|verizon|at&t|charter|spectrum|cox|centurylink|lumen|frontier|windstream|"
    r"t-mobile|sprint|telecom\w*|telekom|telefonica|telia|vodafone|orange|airtel|bharti|jio|"
    r"reliance|bsnl|act fibernet|hathway|cable|broadband|communications|wireless|mobile|"
    r"cellular|telstra|optus|virgin media|british telecommunications|rogers|bell canada|shaw|"
    r"telus|ntt|kddi|softbank|china unicom|china mobile|isp|dsl|fiber|fibre|internet service)\b", re.I)
# Companies whose own network also carries their public cloud.
_ALSO_CLOUD = re.compile(r"\b(google|microsoft|oracle|ibm|alibaba|tencent)\b", re.I)


def _owner(result: dict) -> str | None:
    """The organization the IP block is registered to: the registrant of the
    most specific network (ISPs reassign blocks to business customers, so this
    can be the customer itself), else the network's ASN holder."""
    objects = result.get("objects") or {}
    names = []
    for handle in result.get("entities") or []:
        obj = objects.get(handle) or {}
        name = ((obj.get("contact") or {}).get("name") or "").strip()
        if "registrant" in (obj.get("roles") or []) and name and not re.search(r"-mnt$", name, re.I):
            org = (obj.get("contact") or {}).get("kind") == "org"
            names.append((not org, name))  # organizations first
    if names:
        return sorted(names, key=lambda n: n[0])[0][1]  # stable: registry order among equals
    # e.g. "CLOUDFLARENET - Cloudflare, Inc., US" -> "Cloudflare, Inc."
    desc = result.get("asn_description") or ""
    name = desc.split(" - ", 1)[-1]
    return re.sub(r",\s*[A-Z]{2}$", "", name).strip() or None


def _kind(owner: str, result: dict) -> str:
    text = " ".join([owner or "", result.get("asn_description") or "", (result.get("network") or {}).get("name") or ""])
    if _HOSTING.search(text):
        return "hosting"
    if _ISP.search(text) or re.search(r"\bAT&T\b|\bATT-", text):
        return "isp"
    return "organization"


def _lookup(ip: str) -> dict:
    result = IPWhois(ip, timeout=5).lookup_rdap(depth=0, rate_limit_timeout=0, retry_count=1)
    network = result.get("network") or {}
    owner = _owner(result)
    if not owner:
        raise ValueError("no registrant")
    kind = _kind(owner, result)
    links = [l for l in network.get("links") or [] if str(l).startswith("https://")]
    return {
        "ip": ip,
        "owner": owner,
        "kind": kind,  # organization | isp | hosting
        "may_be_cloud": kind == "organization" and bool(_ALSO_CLOUD.search(owner)),
        "network": network.get("name"),
        "cidr": network.get("cidr"),
        "asn": result.get("asn"),
        "asn_name": result.get("asn_description"),
        "country": network.get("country") or result.get("asn_country_code"),
        "source": {"title": f"IP registration (RDAP): {ip}", "url": links[0] if links else f"https://rdap.org/ip/{ip}"},
    }


def lookup(ip: str, timeout: float = None) -> dict | None:
    """The IP's registered owner, or None for private/reserved addresses,
    failures and lookups slower than the time limit. Never raises."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if not addr.is_global:
        return None  # private, loopback, reserved: nothing is registered
    ip = str(addr)
    with _cache_lock:
        if ip in _cache:
            return dict(_cache[ip])
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        found = pool.submit(_lookup, ip).result(timeout=timeout or TIMEOUT_SECONDS)
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[ip] = found
    return dict(found)


_KIND_TEXT = {
    "organization": "the organization's own network, so the visitor most likely works there",
    "isp": "an internet provider / mobile carrier, so the visitor's company isn't identifiable from it",
    "hosting": "a cloud / hosting provider, so this is likely a VPN, proxy or automated (bot) visit",
}


def describe(info: dict) -> str:
    """One line for the report's signals."""
    text = f"Visitor IP {info['ip']} is registered to {info['owner']}"
    if info.get("asn"):
        text += f" (AS{info['asn']}"
        text += f", {info['country']})" if info.get("country") else ")"
    note = _KIND_TEXT[info["kind"]]
    if info.get("may_be_cloud"):
        note += " (it also runs a public cloud, so this may be cloud traffic)"
    return f"{text}: {note}"


def company_name(info: dict | None) -> str | None:
    """The company an IP identifies, only when its block is registered to a
    regular organization (not an ISP, host or cloud)."""
    if not info or info["kind"] != "organization" or info.get("may_be_cloud"):
        return None
    return info["owner"]
