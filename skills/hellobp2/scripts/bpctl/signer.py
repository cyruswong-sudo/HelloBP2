"""
signer.py — BytePlus OpenAPI request signing (HMAC-SHA256, "SignerV4").

This is a faithful port of ``byteplus_sdk.auth.SignerV4`` from the official
BytePlus Python SDK (v1.0.53), with one deliberate difference: it is
zero-dependency (stdlib only) so ``bpctl`` can be dropped into any environment
an agent happens to be running in.

The algorithm is shared by every BytePlus service. Only three values change
per service — the host, the *service name* used in the credential scope, and
the API version. Those live in ``services.py``, never here.

Why this exists rather than reusing HelloBP v1's ``signer.py``
--------------------------------------------------------------
v1 hardcoded ``SignedHeaders=x-content-sha256;x-date`` and separately sent a
``Host`` header. That works — the server verifies exactly the headers you
*declare* — but it drifts from the SDK and made "should Host be signed?" look
like a mystery. It isn't. The SDK derives the signed-header set dynamically:

    any header named Content-Type, Content-Md5, Host, or starting with X-

So a header is signed if and only if it is present. Set ``Host`` and it gets
signed; omit it and it doesn't. Both produce a valid signature. We follow the
SDK and derive the set, so there is nothing to guess.

Canonical request (joined by "\\n"):

    METHOD
    normalised path
    normalised query
    <canonical headers, each "name:value\\n">
    <signed header names, ";"-joined>
    hex(sha256(body))

String to sign:

    HMAC-SHA256
    <x-date: YYYYMMDDTHHMMSSZ>
    <date>/<region>/<service>/request
    hex(sha256(canonical request))

Signing key:

    HMAC(HMAC(HMAC(HMAC(utf8(secret_key), date), region), service), "request")

Note the secret key is used **as-is** (the base64-looking string from the
console encoded to UTF-8), not base64-decoded, and there is no "BytePlus"
prefix as some AWS SigV4 ports assume.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
from dataclasses import dataclass, field
from urllib.parse import quote

ALGORITHM = "HMAC-SHA256"

# Headers the BytePlus signer includes in the signature, per SignerV4.
# A header is signed if it is one of these names or starts with "x-".
_ALWAYS_SIGNED = ("content-type", "content-md5", "host")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm_uri(path: str) -> str:
    """Percent-encode the path, keeping "/" literal. Mirrors Util.norm_uri."""
    return quote(path).replace("%2F", "/").replace("+", "%20")


def norm_query(params: dict) -> str:
    """
    Canonical query string: keys sorted, each key and value percent-encoded
    with only ``-_.~`` left unreserved. Mirrors Util.norm_query.

    List values expand to repeated ``key=value`` pairs, in list order, with the
    key's position fixed by the sort. Values are coerced to ``str`` so callers
    can pass ints without thinking about it.
    """
    parts = []
    for key in sorted(params.keys()):
        value = params[key]
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            parts.append(
                quote(str(key), safe="-_.~") + "=" + quote(str(item), safe="-_.~")
            )
    return "&".join(parts).replace("+", "%20")


def _is_signed_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in _ALWAYS_SIGNED or lowered.startswith("x-")


def _strip_default_port(host: str) -> str:
    """SignerV4 drops :80 / :443 from a Host header before signing it."""
    if ":" in host:
        name, _, port = host.rpartition(":")
        if port in ("80", "443"):
            return name
    return host


@dataclass
class SignedRequest:
    """Everything needed to put the request on the wire, and to explain it."""

    method: str
    url: str
    headers: dict
    body: bytes
    # Retained for diagnostics — `bpctl probe` prints these when a signature
    # is rejected, which is the only practical way to debug a 401.
    canonical_request: str = ""
    string_to_sign: str = ""
    signed_headers: str = ""
    credential_scope: str = ""
    debug: dict = field(default_factory=dict)


def sign(
    *,
    method: str,
    host: str,
    service: str,
    region: str,
    access_key: str,
    secret_key: str,
    query: dict | None = None,
    body: bytes = b"",
    path: str = "/",
    content_type: str | None = None,
    sign_host: bool = False,
    session_token: str | None = None,
    now: datetime.datetime | None = None,
) -> SignedRequest:
    """
    Sign a BytePlus OpenAPI request.

    Args:
        method:       HTTP method, e.g. "POST" or "GET".
        host:         API host, e.g. "cdn.byteplusapi.com".
        service:      Service name for the credential scope. Case-sensitive and
                      inconsistent across BytePlus products ("CDN" uppercase,
                      "waf" lowercase) — see services.py.
        region:       Signing region, e.g. "singapore".
        access_key:   Access Key ID.
        secret_key:   Secret Access Key, used as-is (not base64-decoded).
        query:        Query parameters, including Action and Version.
        body:         Request body, already serialised to the exact bytes that
                      will be sent. The hash must match the wire bytes, so the
                      caller owns serialisation.
        path:         Request path. Every BytePlus OpenAPI action lives at "/".
        content_type: Sent and signed when provided.
        sign_host:    Send and sign an explicit Host header. Off by default:
                      urllib sets Host itself, and leaving it unsigned matches
                      the request example in the BytePlus signing docs.
        session_token: STS token, sent as X-Security-Token when provided.
        now:          Override the clock, for tests.

    Returns:
        A SignedRequest carrying the URL, headers, body, and the intermediate
        signing strings for diagnostics.
    """
    if not access_key or not secret_key:
        raise ValueError("access_key and secret_key are both required to sign a request")

    method = method.upper()
    query = dict(query or {})
    now = now or datetime.datetime.now(datetime.timezone.utc)

    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = x_date[:8]
    body_hash = _sha256_hex(body)

    # ── Headers that participate in the signature ────────────────────────────
    headers = {
        "X-Date": x_date,
        "X-Content-Sha256": body_hash,
    }
    if content_type:
        headers["Content-Type"] = content_type
    if sign_host:
        headers["Host"] = host
    if session_token:
        headers["X-Security-Token"] = session_token

    signed = {}
    for name, value in headers.items():
        if _is_signed_header(name):
            lowered = name.lower()
            signed[lowered] = _strip_default_port(value) if lowered == "host" else value

    signed_header_names = ";".join(sorted(signed))
    canonical_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))

    canonical_request = "\n".join(
        [
            method,
            norm_uri(path),
            norm_query(query),
            canonical_headers,
            signed_header_names,
            body_hash,
        ]
    )

    credential_scope = f"{date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            x_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )

    # ── Derive the signing key and sign ──────────────────────────────────────
    k_date = _hmac(secret_key.encode("utf-8"), date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers["Authorization"] = (
        f"{ALGORITHM} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, "
        f"Signature={signature}"
    )

    url = f"https://{host}{path}"
    if query:
        url += "?" + norm_query(query)

    return SignedRequest(
        method=method,
        url=url,
        headers=headers,
        body=body,
        canonical_request=canonical_request,
        string_to_sign=string_to_sign,
        signed_headers=signed_header_names,
        credential_scope=credential_scope,
        debug={"x_date": x_date, "body_sha256": body_hash},
    )
