"""
Signature parity against the official BytePlus SDK.

This is the load-bearing test of Phase 1. bpctl reimplements BytePlus request
signing so it can stay dependency-free, and a reimplementation is only worth
anything if it is provably identical to the reference. Here we drive both
implementations with the same inputs and the same frozen clock and require the
resulting ``Authorization`` headers to match exactly.

The official SDK (``byteplus_sdk.auth.SignerV4``) is an optional import: it
pulls in pycryptodome and pytz, which bpctl deliberately does not depend on.
When it is absent the parity tests skip and the self-consistency tests below
still run.

    /tmp/sdkvenv/bin/python -m pytest tests/ -v
    python3 -m pytest tests/ -v          # parity tests skip, rest run
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "hellobp2" / "scripts"))

from bpctl import signer  # noqa: E402
from bpctl.services import SERVICES, method_for, resolve  # noqa: E402

try:
    from byteplus_sdk.Credentials import Credentials as SdkCredentials
    from byteplus_sdk.auth.SignerV4 import SignerV4
    from byteplus_sdk.base.Request import Request as SdkRequest

    HAVE_SDK = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_SDK = False

needs_sdk = pytest.mark.skipif(not HAVE_SDK, reason="official byteplus-sdk not installed")

FROZEN = datetime.datetime(2026, 9, 9, 12, 34, 56, tzinfo=datetime.timezone.utc)
AK = "AKLTexampleaccesskeyid00000"
SK = "U2VjcmV0S2V5RXhhbXBsZVZhbHVlMDAwMDAwMDA="


def _sdk_authorization(*, method, host, service, region, query, body, content_type):
    """Sign with the official SDK, pinning its clock to FROZEN."""
    request = SdkRequest()
    request.set_method(method)
    request.set_path("/")
    request.set_host(host)
    request.set_query(dict(query))
    request.set_body(body.decode("utf-8") if body else "")
    request.headers = {}
    if content_type:
        request.headers["Content-Type"] = content_type

    original = SignerV4.get_current_format_date
    SignerV4.get_current_format_date = staticmethod(lambda: FROZEN.strftime("%Y%m%dT%H%M%SZ"))
    try:
        credentials = SdkCredentials(AK, SK, service, region)
        SignerV4.sign(request, credentials)
    finally:
        SignerV4.get_current_format_date = original
    return request.headers["Authorization"], request.headers


def _ours(*, method, host, service, region, query, body, content_type):
    return signer.sign(
        method=method,
        host=host,
        service=service,
        region=region,
        access_key=AK,
        secret_key=SK,
        query=query,
        body=body,
        content_type=content_type,
        now=FROZEN,
    )


CASES = [
    pytest.param(
        "POST",
        "cdn.byteplusapi.com",
        "CDN",
        "singapore",
        {"Action": "DescribeTemplates", "Version": "2021-03-01"},
        json.dumps({"PageSize": 1, "PageNum": 1}, separators=(",", ":")).encode(),
        "application/json",
        id="cdn-post-json",
    ),
    pytest.param(
        "POST",
        "waf.byteplusapi.com",
        "waf",
        "singapore",
        {"Action": "ListDomain", "Version": "2023-12-25"},
        json.dumps({"PageNum": 1, "PageSize": 1}, separators=(",", ":")).encode(),
        "application/json",
        id="waf-post-json",
    ),
    pytest.param(
        "GET",
        "dns.byteplusapi.com",
        "DNS",
        "singapore",
        {"Action": "ListZones", "Version": "2018-08-01", "PageSize": "1", "PageNumber": "1"},
        b"",
        None,
        id="dns-get-empty-body",
    ),
    pytest.param(
        "GET",
        "open.byteplusapi.com",
        "certificate_service",
        "singapore",
        {
            "Action": "CertificateGetInstance",
            "Version": "2021-06-01",
            "instance_id": "cert-abc123",
        },
        b"",
        None,
        id="certificate-get",
    ),
    pytest.param(
        "POST",
        "cdn.byteplusapi.com",
        "CDN",
        "ap-southeast-3",
        {"Action": "UpdateCdnConfig", "Version": "2021-03-01"},
        json.dumps(
            {"Domain": "www.example.com", "Origin": [{"OriginAction": {"OriginLines": []}}]},
            separators=(",", ":"),
        ).encode(),
        "application/json",
        id="cdn-alternate-region-nested-body",
    ),
    pytest.param(
        "GET",
        "dns.byteplusapi.com",
        "DNS",
        "singapore",
        # Values needing percent-encoding exercise norm_query on both sides.
        {"Action": "QueryRecord", "Version": "2018-08-01", "Host": "a b/c+d~e", "ZID": "42"},
        b"",
        None,
        id="dns-query-encoding",
    ),
]


@needs_sdk
@pytest.mark.parametrize("method,host,service,region,query,body,content_type", CASES)
def test_authorization_matches_official_sdk(method, host, service, region, query, body, content_type):
    """The whole point: our Authorization header must equal the SDK's, exactly."""
    expected, _ = _sdk_authorization(
        method=method,
        host=host,
        service=service,
        region=region,
        query=query,
        body=body,
        content_type=content_type,
    )
    actual = _ours(
        method=method,
        host=host,
        service=service,
        region=region,
        query=query,
        body=body,
        content_type=content_type,
    )
    assert actual.headers["Authorization"] == expected


@needs_sdk
@pytest.mark.parametrize("method,host,service,region,query,body,content_type", CASES)
def test_content_hash_matches_official_sdk(method, host, service, region, query, body, content_type):
    _, sdk_headers = _sdk_authorization(
        method=method,
        host=host,
        service=service,
        region=region,
        query=query,
        body=body,
        content_type=content_type,
    )
    actual = _ours(
        method=method,
        host=host,
        service=service,
        region=region,
        query=query,
        body=body,
        content_type=content_type,
    )
    assert actual.headers["X-Content-Sha256"] == sdk_headers["X-Content-Sha256"]


# ── Self-consistency checks that run without the SDK ─────────────────────────

def test_signed_headers_track_which_headers_are_present():
    """Host is signed only when we choose to send it — the v1 'mystery'."""
    without = _ours(
        method="POST",
        host="cdn.byteplusapi.com",
        service="CDN",
        region="singapore",
        query={"Action": "X", "Version": "1"},
        body=b"{}",
        content_type="application/json",
    )
    assert without.signed_headers == "content-type;x-content-sha256;x-date"
    assert "Host" not in without.headers

    with_host = signer.sign(
        method="POST",
        host="cdn.byteplusapi.com",
        service="CDN",
        region="singapore",
        access_key=AK,
        secret_key=SK,
        query={"Action": "X", "Version": "1"},
        body=b"{}",
        content_type="application/json",
        sign_host=True,
        now=FROZEN,
    )
    assert with_host.signed_headers == "content-type;host;x-content-sha256;x-date"
    assert with_host.headers["Authorization"] != without.headers["Authorization"]


def test_norm_query_sorts_and_encodes():
    assert signer.norm_query({"b": "2", "a": "1"}) == "a=1&b=2"
    assert signer.norm_query({"k": "a b"}) == "k=a%20b"
    assert signer.norm_query({"k": "a/b"}) == "k=a%2Fb"
    assert signer.norm_query({"k": "~-_."}) == "k=~-_."
    # Repeated keys expand in list order.
    assert signer.norm_query({"k": ["x", "y"]}) == "k=x&k=y"
    # Non-string values are coerced rather than raising.
    assert signer.norm_query({"n": 7}) == "n=7"


def test_credential_scope_uses_service_and_region():
    result = _ours(
        method="POST",
        host="waf.byteplusapi.com",
        service="waf",
        region="ap-southeast-3",
        query={"Action": "X", "Version": "1"},
        body=b"{}",
        content_type="application/json",
    )
    assert result.credential_scope == "20260909/ap-southeast-3/waf/request"


def test_body_hash_is_over_exact_bytes():
    """A different body must change the signature, or the hash is not binding."""
    base = dict(
        method="POST",
        host="cdn.byteplusapi.com",
        service="CDN",
        region="singapore",
        query={"Action": "X", "Version": "1"},
        content_type="application/json",
    )
    a = _ours(body=b'{"a":1}', **base)
    b = _ours(body=b'{"a":2}', **base)
    assert a.headers["Authorization"] != b.headers["Authorization"]


def test_missing_credentials_are_rejected_before_any_network_call():
    with pytest.raises(ValueError, match="access_key and secret_key"):
        signer.sign(
            method="POST",
            host="cdn.byteplusapi.com",
            service="CDN",
            region="singapore",
            access_key="",
            secret_key="",
            query={},
        )


# ── Service registry ─────────────────────────────────────────────────────────

def test_every_service_has_a_distinct_signing_identity():
    identities = {(s.host, s.signing_name, s.version) for s in SERVICES.values()}
    assert len(identities) == len(SERVICES)


def test_dns_reads_are_get_and_writes_are_post():
    dns = SERVICES["dns"]
    assert method_for(dns, "ListZones") == "GET"
    assert method_for(dns, "QueryRecord") == "GET"
    assert method_for(dns, "CreateRecord") == "POST"
    assert method_for(dns, "UpdateRecord") == "POST"


def test_certificate_get_actions_match_the_documented_two():
    cert = SERVICES["certificate"]
    assert method_for(cert, "CertificateGetDcvParam") == "GET"
    assert method_for(cert, "CertificateGetInstance") == "GET"
    assert method_for(cert, "CertificateAddFreeInstance") == "POST"


def test_service_aliases_resolve():
    assert resolve("cert").key == "certificate"
    assert resolve("CDN").key == "cdn"
    with pytest.raises(KeyError):
        resolve("nope")
