"""Unit tests for verity_core.technical_intel (whois/dns mocked, offline)."""
from datetime import datetime

from verity_core import technical_intel as ti


class _Whois:
    def __init__(self, creation_date):
        self.creation_date = creation_date
        self.registrar = "R"
        self.expiration_date = None
        self.last_updated = None
        self.name_servers = ["NS1"]


def test_creation_date_scalar(monkeypatch):
    monkeypatch.setattr(ti.whois, "whois", lambda d: _Whois(datetime(2020, 1, 2)))
    assert ti.get_domain_creation_date("x.test") == "2020-01-02"


def test_creation_date_list(monkeypatch):
    monkeypatch.setattr(
        ti.whois, "whois", lambda d: _Whois([datetime(2021, 6, 7), datetime(2022, 1, 1)])
    )
    assert ti.get_domain_creation_date("x.test") == "2021-06-07"


def test_creation_date_missing_and_error(monkeypatch):
    monkeypatch.setattr(ti.whois, "whois", lambda d: _Whois(None))
    assert ti.get_domain_creation_date("x.test") == "N/A"

    def boom(d):
        raise RuntimeError("whois down")

    monkeypatch.setattr(ti.whois, "whois", boom)
    assert ti.get_domain_creation_date("x.test") == "N/A"


def test_has_mx_records(monkeypatch):
    monkeypatch.setattr(ti.dns.resolver, "resolve", lambda d, rt: ["10 mail.test."])
    assert ti.has_mx_records("x.test") == "Yes"

    def boom(d, rt):
        raise RuntimeError("no dns")

    monkeypatch.setattr(ti.dns.resolver, "resolve", boom)
    assert ti.has_mx_records("x.test") == "No"


def test_get_technical_intel_shape(monkeypatch):
    monkeypatch.setattr(ti.whois, "whois", lambda d: _Whois(datetime(2020, 1, 2)))
    monkeypatch.setattr(ti.dns.resolver, "resolve", lambda d, rt: [])
    intel = ti.get_technical_intel("https://www.examplenews.test/article")
    assert intel["domain"] == "examplenews.test"
    assert set(intel.keys()) == {"domain", "whois_info", "dns_records"}
    assert set(intel["dns_records"].keys()) == {"A", "AAAA", "MX", "NS", "TXT"}
