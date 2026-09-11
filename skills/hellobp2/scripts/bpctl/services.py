"""
services.py — the BytePlus service registry.

Every BytePlus OpenAPI service shares one signing algorithm (see signer.py) and
differs only in three values: the host, the *service name* used in the
credential scope, and the API version. Those three values are the entire reason
HelloBP v1 needed four near-identical client modules. Here they are data.

Sources
-------
All values below are taken from official BytePlus documentation, not from
HelloBP v1's empirically-probed constants. Where v1 disagrees with the docs,
the doc value wins and the disagreement is recorded in ``KNOWN_DISCREPANCIES``
so ``bpctl probe`` can settle it against a live account rather than leaving it
as folklore.

  CDN   https://docs.byteplus.com/en/docs/byteplus-cdn/reference-send-api-requests
  WAF   https://docs.byteplus.com/en/docs/waf/common_request_parameters
  DNS   https://docs.byteplus.com/en/docs/byteplus-dns-suite/reference-calling-an-api-with-http-requests
  Cert  https://docs.byteplus.com/en/docs/certificate-center/
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_REGION = "singapore"

# BytePlus signing regions. The region is part of the credential scope, so a
# mismatch fails as 401 SignatureDoesNotMatch rather than a helpful error —
# this is the single most common cause of a rejected signature.
REGIONS = {
    "singapore": "Asia Pacific (Singapore) — default for most BytePlus accounts",
    "ap-southeast-1": "Asia Pacific Region 1",
    "ap-southeast-3": "Asia Pacific Region 2 — Indonesia, Philippines, Vietnam, Taiwan",
}


@dataclass(frozen=True)
class ServiceSpec:
    """How to reach and sign for one BytePlus product API."""

    key: str
    host: str
    # Case matters and is NOT consistent across BytePlus products. "CDN" is
    # uppercase; "waf" is lowercase; "DNS" is uppercase. Getting this wrong
    # produces a 401, never a descriptive error.
    signing_name: str
    version: str
    doc: str
    # Actions that must be issued as GET. Everything else defaults to POST.
    get_actions: frozenset = frozenset()
    # Prefixes treated as GET when not listed explicitly. Empty means "none";
    # we prefer explicit lists over guessing, because a wrong method is another
    # silent signature failure.
    get_prefixes: tuple = ()
    # Alternate hosts seen in the wild or in older integrations. `bpctl probe`
    # tests these when the primary host rejects a signature.
    alt_hosts: tuple = ()
    # Some products prefix every action name with the product itself
    # ("CertificateGetInstance"), which hides the Get/List/Describe verb that
    # read-vs-write detection keys off. Stripped before that check.
    action_prefix: str = ""
    notes: str = ""


SERVICES: dict[str, ServiceSpec] = {
    "cdn": ServiceSpec(
        key="cdn",
        host="cdn.byteplusapi.com",
        signing_name="CDN",
        version="2021-03-01",
        doc="https://docs.byteplus.com/en/docs/byteplus-cdn/reference-api-overview",
        notes=(
            "All CDN actions are POST to '/' with Action and Version in the "
            "query string and the payload as a JSON body."
        ),
    ),
    "waf": ServiceSpec(
        key="waf",
        host="waf.byteplusapi.com",
        signing_name="waf",
        version="2023-12-25",
        doc="https://docs.byteplus.com/en/docs/waf/common_request_parameters",
        alt_hosts=("open.byteplusapi.com",),
        notes=(
            "Docs specify waf.byteplusapi.com. HelloBP v1 used "
            "open.byteplusapi.com and worked; both hosts resolve. "
            "`bpctl probe waf` determines which one this account signs for."
        ),
    ),
    "dns": ServiceSpec(
        key="dns",
        host="dns.byteplusapi.com",
        signing_name="DNS",
        version="2018-08-01",
        doc="https://docs.byteplus.com/en/docs/byteplus-dns-suite/reference-calling-an-api-with-http-requests",
        get_actions=frozenset(
            {
                "ListZones",
                "QueryZone",
                "CheckZone",
                "ListRecords",
                "QueryRecord",
                "ListRecordSets",
                "ListLines",
                "ListCustomLines",
                "ListZoneStatistics",
                "ListDomainStatistics",
                "ListRecordDigestByLine",
                "QueryBackupSchedule",
                "ListUserZoneBackups",
            }
        ),
        notes=(
            "DNS Suite mixes GET (reads) and POST (writes). GET actions carry "
            "every parameter in the signed query string with an empty body."
        ),
    ),
    "certificate": ServiceSpec(
        key="certificate",
        host="open.byteplusapi.com",
        signing_name="certificate_service",
        version="2021-06-01",
        doc="https://docs.byteplus.com/en/docs/certificate-center/",
        get_actions=frozenset(
            {
                "CertificateGetDcvParam",
                "CertificateGetInstance",
            }
        ),
        action_prefix="Certificate",
        notes=(
            "The Certificate Service has no list endpoint — every "
            "CertificateList* / CertificateGetInstances / "
            "CertificateDescribeInstances action name returns 404 "
            "InvalidActionOrVersion. To enumerate certificates use the CDN "
            "service's ListCdnCertInfo or ListCertInfo instead."
        ),
    ),
}

# Convenience aliases so an agent can say "cert" or "certs" and be understood.
ALIASES = {
    "cert": "certificate",
    "certs": "certificate",
    "certificates": "certificate",
    "cdn": "cdn",
    "waf": "waf",
    "dns": "dns",
}


def resolve(name: str) -> ServiceSpec:
    """Look up a service by key or alias. Raises KeyError with a usable message."""
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    if key not in SERVICES:
        raise KeyError(
            f"unknown service {name!r}; expected one of: "
            + ", ".join(sorted(SERVICES))
        )
    return SERVICES[key]


def method_for(spec: ServiceSpec, action: str) -> str:
    """The HTTP method an action needs. POST unless the service says otherwise."""
    if action in spec.get_actions:
        return "GET"
    if spec.get_prefixes and action.startswith(spec.get_prefixes):
        return "GET"
    return "POST"


# ── Facts that HelloBP v1 and the official docs disagree about ───────────────
#
# v1 found its values by brute-force probing (see the "WAF debug: exhaustive
# API probe" commits) and they demonstrably worked, but they are undocumented
# and therefore unsafe to build on. `bpctl probe` resolves each of these
# against a live account and writes the answer to verified.json, which is what
# the rest of bpctl reads. Nothing here is treated as settled until it does.

@dataclass
class Discrepancy:
    topic: str
    documented: str
    v1_used: str
    how_to_settle: str
    resolved: str | None = None


KNOWN_DISCREPANCIES: list[Discrepancy] = [
    Discrepancy(
        topic="waf.host",
        documented="waf.byteplusapi.com",
        v1_used="open.byteplusapi.com",
        how_to_settle="Sign an identical ListDomain call against each host; a valid signature returns 200.",
    ),
    Discrepancy(
        topic="waf.CreateAclRule.AclType",
        documented="'Allow' / 'Block' (capitalised)",
        v1_used="'allow' / 'deny' (lowercase)",
        how_to_settle="Read an existing ACL rule via ListAclRule and inspect the AclType the API returns.",
    ),
    Discrepancy(
        topic="waf.CreateAclRule.HostAddType",
        documented="2 = domain group, 3 = multiple domain names",
        v1_used="1",
        how_to_settle="Read back an existing rule via ListAclRule; the returned HostAddType is authoritative.",
    ),
    Discrepancy(
        topic="waf.CreateAclRule.IpAddType",
        documented="2 = IP group, 3 = manual list, 4 = geographic",
        v1_used="1 = manual, 2 = group",
        how_to_settle="Read back an existing rule via ListAclRule; the returned IpAddType is authoritative.",
    ),
]


def service_table() -> str:
    """Human-readable registry dump, used by `bpctl services`."""
    rows = [("SERVICE", "HOST", "SIGNING NAME", "VERSION")]
    rows += [
        (s.key, s.host, s.signing_name, s.version)
        for s in sorted(SERVICES.values(), key=lambda s: s.key)
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)
