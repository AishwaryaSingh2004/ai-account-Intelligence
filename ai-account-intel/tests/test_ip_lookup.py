"""Who owns a visitor's IP (free RDAP registration lookup) and how that
identifies the company, or explains why it can't."""
import threading
import time

import pytest

import src.agents as A
from src import iplookup as IP
from src.validate import validate_intel


def rdap(entities, objects, asn="714", asn_desc="APPLE-ENGINEERING - Apple Inc., US", net="APPLE-WWNET", country="US"):
    """The shape ipwhois returns (trimmed from real responses)."""
    return {"asn": asn, "asn_description": asn_desc, "asn_country_code": country, "entities": entities,
            "objects": {h: {"contact": {"name": n, "kind": k}, "roles": r} for h, (n, k, r) in objects.items()},
            "network": {"name": net, "cidr": "17.0.0.0/8", "country": None,
                        "links": ["https://rdap.arin.net/registry/ip/17.0.0.0", "https://whois.arin.net/rest/net/x"]}}


APPLE = rdap(["APPLEC-1-Z"], {"APPLEC-1-Z": ("Apple Inc.", "org", ["registrant"])})
COMCAST = rdap(["C05332894"], {"C05332894": ("Comcast IP Services, L.L.C.", "org", ["registrant"])},
               "7922", "COMCAST-7922 - Comcast Cable Communications, LLC, US", "BAYAREA-CPE-45")
AWS = rdap(["AT-88-Z"], {"AT-88-Z": ("Amazon Technologies Inc.", "org", ["registrant"])},
           "14618", "AMAZON-AES - Amazon.com, Inc., US", "AT-88-Z")
META_RIPE = rdap(["meta-mnt", "NE1880-RIPE", "ORG-FIL7-RIPE"],
                 {"meta-mnt": ("meta-mnt", "individual", ["registrant"]),
                  "NE1880-RIPE": ("Network Engineering", "group", ["administrative", "technical"]),
                  "ORG-FIL7-RIPE": ("Meta Platforms Ireland Limited", "org", ["registrant"])},
                 "32934", "FACEBOOK - Facebook, Inc., US", "IE-FACEBOOK-20140612", "IE")
CLOUDFLARE = rdap(["CTC15-AP"], {"CTC15-AP": ("Cloudflare Technical Contact", "group", ["technical"])},
                  "13335", "CLOUDFLARENET - Cloudflare, Inc., US", "CLOUDFLARE_103_21_244_0")
GOOGLE = rdap(["GOGL"], {"GOGL": ("Google LLC", "org", ["registrant"])}, "15169", "GOOGLE - Google LLC, US", "GOGL")


@pytest.fixture
def registry(monkeypatch):
    """fake RDAP: registry({ip: result}); returns the list of looked-up IPs."""
    looked_up = []

    def install(answers):
        class Fake:
            def __init__(self, ip, timeout=None):
                looked_up.append(ip); self.ip = ip

            def lookup_rdap(self, **kw):
                answer = answers[self.ip]
                if isinstance(answer, BaseException):
                    raise answer
                return answer() if callable(answer) else answer
        monkeypatch.setattr(IP, "IPWhois", Fake)
        return looked_up
    return install


@pytest.mark.parametrize("result,owner,kind,company", [
    (APPLE, "Apple Inc.", "organization", "Apple Inc."),
    (COMCAST, "Comcast IP Services, L.L.C.", "isp", None),
    (AWS, "Amazon Technologies Inc.", "hosting", None),
    (META_RIPE, "Meta Platforms Ireland Limited", "organization", "Meta Platforms Ireland Limited"),  # skips maintainer handles
    (CLOUDFLARE, "Cloudflare, Inc.", "hosting", None),   # no registrant: falls back to the ASN holder
    (GOOGLE, "Google LLC", "organization", None),        # also a public cloud: not treated as a visit from Google
])
def test_owner_and_kind(registry, result, owner, kind, company):
    registry({"17.253.144.10": result})
    info = IP.lookup("17.253.144.10")
    assert (info["owner"], info["kind"], IP.company_name(info)) == (owner, kind, company)
    assert info["source"]["url"].startswith("https://")


def test_isp_and_hosting_are_explained_not_guessed(registry):
    registry({"73.15.20.1": COMCAST, "34.201.112.55": AWS})
    assert "internet provider" in IP.describe(IP.lookup("73.15.20.1"))
    assert "VPN, proxy or automated" in IP.describe(IP.lookup("34.201.112.55"))


@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "192.168.1.5", "192.0.2.1", "::1", "not-an-ip", None])
def test_private_reserved_and_invalid_ips_are_not_looked_up(registry, ip):
    looked_up = registry({})
    assert IP.lookup(ip) is None and looked_up == []


def test_failures_and_slow_lookups_return_none(registry):
    gate = threading.Event()
    registry({"8.8.4.4": RuntimeError("registry down"), "1.1.1.1": lambda: gate.wait(30) and APPLE})
    assert IP.lookup("8.8.4.4") is None
    start = time.monotonic()
    assert IP.lookup("1.1.1.1", timeout=0.3) is None and time.monotonic() - start < 2
    gate.set()


def test_results_are_cached(registry):
    looked_up = registry({"17.253.144.10": APPLE})
    IP.lookup("17.253.144.10")["owner"] = "changed"  # callers get a copy
    assert IP.lookup("17.253.144.10")["owner"] == "Apple Inc." and looked_up == ["17.253.144.10"]


# ---- In the pipeline ---------------------------------------------------------
def test_ip_owned_by_a_company_gets_full_company_research(stubs, monkeypatch):
    monkeypatch.setattr(IP, "lookup", lambda ip, timeout=None:
                        dict(ip=ip, owner="Apple Inc.", kind="organization", may_be_cloud=False, asn="714",
                             country="US", source={"title": "IP registration (RDAP): 17.253.144.10", "url": "https://rdap.arin.net/x"}))
    raw = A.run_full_pipeline(ip_address="17.253.144.10")
    assert stubs["researcher"]["company_name"] == "Apple Inc." and stubs["fetch"][0] == ("Apple Inc.", None)
    assert raw["business_signals"][0].startswith("Visitor IP 17.253.144.10 is registered to Apple Inc.")
    assert any(s["url"] == "https://rdap.arin.net/x" for s in raw["sources"])
    assert validate_intel(raw)[1] is None


def test_isp_ip_stays_unidentified_but_researcher_sees_the_record(stubs, monkeypatch):
    record = dict(ip="73.15.20.1", owner="Comcast IP Services, L.L.C.", kind="isp", may_be_cloud=False,
                  asn="7922", country="US", source={"title": "IP registration (RDAP): 73.15.20.1", "url": "https://rdap.arin.net/y"})
    monkeypatch.setattr(IP, "lookup", lambda ip, timeout=None: dict(record))
    raw = A.run_full_pipeline(ip_address="73.15.20.1")
    assert stubs["researcher"]["company_name"] is None and stubs["researcher"]["ip_record"]["owner"].startswith("Comcast")
    assert stubs["fetch"] == [] and "internet provider" in raw["business_signals"][0]


def test_named_company_with_ip_adds_owner_signal_without_waiting(stubs, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(A, "SIDE_LOOKUP_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(IP, "lookup", lambda ip, timeout=None: gate.wait(30) and None)
    start = time.monotonic()
    raw = A.run_full_pipeline(company_name="Acme", ip_address="73.15.20.1")
    gate.set()
    assert time.monotonic() - start < 5 and stubs["researcher"]["company_name"] == "Acme"
    assert validate_intel(raw)[1] is None


def test_researcher_prompt_includes_the_record(monkeypatch):
    prompts = []
    monkeypatch.setattr(A, "call_agent", lambda s, u, o, m=None, temperature=None: prompts.append(u) or {})
    A.run_researcher(ip_address="73.15.20.1", ip_record={"owner": "Comcast IP Services, L.L.C.", "kind": "isp",
                                                         "network": "BAYAREA-CPE-45", "asn": "7922", "country": "US"})
    assert "Verified IP registration (RDAP): registered to Comcast" in prompts[0] and "owner type: isp" in prompts[0]


def test_cloud_visitor_persona_is_not_overconfident(stubs, monkeypatch):
    record = dict(ip="34.201.112.55", owner="Amazon Technologies Inc.", kind="hosting", may_be_cloud=False,
                  asn="14618", country="US", source={"title": "IP registration (RDAP): 34.201.112.55", "url": "https://rdap.arin.net/z"})
    monkeypatch.setattr(IP, "lookup", lambda ip, timeout=None: dict(record))
    raw = A.run_full_pipeline(ip_address="34.201.112.55", visitor_signals={"pages_visited": ["/pricing"]})
    assert raw["persona"]["confidence"] <= 0.3 and raw["intent"]["score"] == 8.0  # behavior still scores
