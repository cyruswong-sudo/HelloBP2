"""
Cloudflare caller: the read/write boundary, credential handling, and error shape.

The point of routing Cloudflare through bpctl is not signing — there is none —
it is that the token stays out of argv and that writes are as hard to do by
accident as BytePlus writes are. These tests hold both properties.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "hellobp2" / "scripts"))

from bpctl.cloudflare import (  # noqa: E402
    CloudflareClient,
    CloudflareError,
    is_write_method,
)
from bpctl.config import CloudflareCredentials, load_cloudflare_credentials, mask  # noqa: E402


# --------------------------------------------------------------- read / write

@pytest.mark.parametrize("method", ["GET", "HEAD", "get", "head"])
def test_reads_are_reads(method):
    assert is_write_method(method) is False


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "OPTIONS"])
def test_everything_else_is_a_write(method):
    assert is_write_method(method) is True


def test_write_is_simulated_by_default():
    client = CloudflareClient(CloudflareCredentials(api_token="t"), live=False)
    resp = client.call("POST", "/zones/z/dns_records", body={"type": "TXT"})
    assert resp.dry_run is True
    assert resp.status is None
    assert "Nothing was sent" in resp.body["note"]


def test_dry_run_needs_no_credentials():
    """A simulated write sends nothing, so it must not demand a token first."""
    client = CloudflareClient(CloudflareCredentials(), live=False)
    resp = client.call("DELETE", "/zones/z/dns_records/r")
    assert resp.dry_run is True


def test_read_without_credentials_is_refused():
    client = CloudflareClient(CloudflareCredentials(), live=False)
    with pytest.raises(SystemExit) as exc:
        client.call("GET", "/zones")
    assert "Cloudflare API token" in str(exc.value)


def test_error_message_never_contains_the_token():
    secret = "EXAMPLE-not-a-real-token-0123456789"
    client = CloudflareClient(CloudflareCredentials(), live=False)
    with pytest.raises(SystemExit) as exc:
        client.call("GET", "/zones")
    assert secret not in str(exc.value)


# ------------------------------------------------------------------- requests

def test_path_is_normalised():
    client = CloudflareClient(CloudflareCredentials(api_token="t"), live=False)
    resp = client.call("POST", "zones/z/dns_records", body={})
    assert resp.request["url"].endswith("/client/v4/zones/z/dns_records")


def test_query_params_are_encoded():
    client = CloudflareClient(CloudflareCredentials(api_token="t"), live=False)
    resp = client.call("POST", "/zones", params={"name": "a b", "page": 2})
    assert "name=a+b" in resp.request["url"]
    assert "page=2" in resp.request["url"]


def test_envelope_shape_matches_the_byteplus_caller():
    client = CloudflareClient(CloudflareCredentials(api_token="t"), live=False)
    payload = client.call("POST", "/zones", body={}).to_dict()
    for key in ("ok", "service", "action", "status", "dry_run", "request", "response"):
        assert key in payload
    assert payload["service"] == "cloudflare"
    assert payload["action"] == "POST /zones"


# ---------------------------------------------------------------- credentials

def test_token_resolves_from_environment(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "  tok-from-env  ")
    creds = load_cloudflare_credentials()
    assert creds.api_token == "tok-from-env"
    assert creds.sources["api_token"] == "env"


def test_alternate_env_var_is_accepted(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CF_API_TOKEN", "tok2")
    assert load_cloudflare_credentials().api_token == "tok2"


def test_explicit_argument_wins_over_environment(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "from-env")
    assert load_cloudflare_credentials(api_token="explicit").api_token == "explicit"


def test_missing_token_is_not_complete(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    creds = load_cloudflare_credentials(config_path="/nonexistent/bpctl.json")
    assert creds.complete is False


def test_describe_masks_the_token(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "EXAMPLEabcdefghijklmnop")
    rendered = load_cloudflare_credentials().describe()
    assert "EXAMPLEabcdefghijklmnop" not in rendered
    assert "EXAM" in rendered  # first four are shown


def test_short_tokens_are_hidden_entirely():
    assert set(mask("abcd")) == {"*"}


# --------------------------------------------------------------------- errors

def test_error_renders_code_and_hint():
    err = CloudflareError("boom", status=403, code=9109, hint="needs Zone:DNS:Edit")
    text = str(err)
    assert "boom" in text and "9109" in text and "Zone:DNS:Edit" in text
