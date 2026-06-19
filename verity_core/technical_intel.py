"""WHOIS + DNS technical intelligence.

Two consumers, two shapes (Sprint 1 Phase 2 step 2):
  * Verity uses the rich `get_technical_intel` (full WHOIS + A/AAAA/MX/NS/TXT),
    moved verbatim from the Verity service.
  * PhishFinder uses the lightweight `get_domain_creation_date` /
    `has_mx_records`, mirroring its inline logic exactly (including the
    swallow-to-"N/A"/"No" behavior).
"""
from urllib.parse import urlparse

import whois
import dns.resolver


# --- Verity: rich technical intel (verbatim move) --------------------------
def get_technical_intel(url):
    """
    Performs WHOIS and DNS lookups on the URL's domain.
    """
    intel = {"domain": None, "whois_info": "N/A", "dns_records": {}}
    try:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        if not domain:
            return intel
        intel["domain"] = domain

        # WHOIS Lookup
        try:
            w = whois.whois(domain)
            intel["whois_info"] = {
                "registrar": w.registrar,
                "creation_date": str(w.creation_date),
                "expiration_date": str(w.expiration_date),
                "last_updated": str(w.last_updated),
                "name_servers": w.name_servers
            }
        except Exception as e:
            intel["whois_info"] = f"WHOIS lookup failed for {domain}: {e}"

        # DNS Lookup
        for record_type in ['A', 'AAAA', 'MX', 'NS', 'TXT']:
            try:
                answers = dns.resolver.resolve(domain, record_type)
                intel["dns_records"][record_type] = [rdata.to_text() for rdata in answers]
            except dns.resolver.NoAnswer:
                intel["dns_records"][record_type] = "No record found."
            except Exception:
                intel["dns_records"][record_type] = f"DNS lookup failed for {record_type} record."
    except Exception as e:
        print(f"TECHNICAL INTEL: Failed. Error: {e}")
    return intel


# --- PhishFinder: lightweight accessors (mirror inline behavior) -----------
def get_domain_creation_date(domain):
    """
    Returns the domain creation date as "%Y-%m-%d", or "N/A" if unavailable.
    Mirrors PhishFinder's inline logic: handles list/scalar creation_date and
    swallows any error to "N/A".
    """
    try:
        domain_info = whois.whois(domain)
        creation_date = (
            domain_info.creation_date[0]
            if isinstance(domain_info.creation_date, list)
            else domain_info.creation_date
        )
        if creation_date:
            return creation_date.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"⚠️ WHOIS lookup failed: {e}")
    return "N/A"


def has_mx_records(domain):
    """
    Returns "Yes" / "No" for MX presence. Mirrors PhishFinder's inline logic:
    any exception swallows to "No".
    """
    try:
        if dns.resolver.resolve(domain, 'MX'):
            return "Yes"
        return "No"
    except Exception:
        return "No"
