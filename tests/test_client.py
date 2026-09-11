"""
Client behaviour: read/write classification, dry-run, and error interpretation.

The read-vs-write split is a safety boundary, not a convenience: misclassifying
a write as a read means it executes against a live account with no `--live`
flag and no confirmation. These tests exist to keep that boundary honest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "hellobp2" / "scripts"))

from bpctl.client import BytePlusError, Client, is_write_action  # noqa: E402
from bpctl.config import Credentials, mask, resolve_live  # noqa: E402
from bpctl.services import SERVICES, resolve  # noqa: E402

CERT = SERVICES["certificate"]
CDN = SERVICES["cdn"]


# ── Read vs write ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "action",
    ["DescribeTemplates", "ListCdnDomains", "QueryZone", "GetAreaBlockInfo", "CheckCdnDomain"],
)
def test_read_verbs_are_reads(action):
    assert not is_write_action(action)


@pytest.mark.parametrize(
    "action",
    [
        "AddCdnDomain",
        "CreateServiceTemplate",
        "UpdateCdnConfig",
        "DeleteCdnDomain",
        "BatchDeployCert",
        "SubmitRefreshTask",
        "ReleaseTemplate",
        "LockTemplate",
        "StopCdnDomain",
    ],
)
def test_write_verbs_are_writes(action):
    assert is_write_action(action)


def test_unknown_verbs_default_to_write():
    """Fail safe: an unrecognised action must never be executed unarmed."""
    assert is_write_action("FrobnicateTheThing")
    assert is_write_action("")


def test_certificate_product_prefix_does_not_hide_the_verb():
    """
    Regression: every Certificate Service action starts with "Certificate",
    which hid the Get/List verb and made reads look like writes. Found by
    calling CertificateGetInstance and getting a dry-run instead of a result.
    """
    assert not is_write_action("CertificateGetInstance", CERT)
    assert not is_write_action("CertificateGetDcvParam", CERT)
    assert is_write_action("CertificateAddFreeInstance", CERT)
    # Without the spec the prefix cannot be stripped, so it stays a write.
    assert is_write_action("CertificateGetInstance")
    # The prefix alone is not a verb and must not be stripped into nothing.
    assert is_write_action("Certificate", CERT)
    # A service with no prefix is unaffected.
    assert not is_write_action("DescribeTemplates", CDN)


# ── Dry-run ──────────────────────────────────────────────────────────────────

def _creds():
    return Credentials(access_key="AKLTtest", secret_key="secret", region="singapore")


def test_dry_run_simulates_writes_without_credentials_or_network():
    client = Client(Credentials(), live=False)
    response = client.call("cdn", "AddCdnDomain", {"Domain": "a.example.com"})
    assert response.dry_run is True
    assert response.ok is True
    assert response.status is None
    assert response.request["body"] == {"Domain": "a.example.com"}


def test_dry_run_response_does_not_duplicate_payload_for_get_actions():
    client = Client(Credentials(), live=False)
    # A GET write is unusual but the shape must still be honest: the payload
    # belongs in the query, not repeated as a body.
    response = client.call("dns", "CreateRecord", {"ZID": 1}, method="GET")
    assert response.request["body"] is None
    assert response.request["query"]["ZID"] == 1


def test_live_mode_requires_credentials():
    client = Client(Credentials(), live=True)
    with pytest.raises(SystemExit, match="missing BytePlus"):
        client.call("cdn", "AddCdnDomain", {"Domain": "a.example.com"})


def test_get_actions_reject_nested_values():
    """A dict cannot survive a query string; fail loudly rather than sign junk."""
    client = Client(_creds(), live=True)
    with pytest.raises(ValueError, match="nested value"):
        client.call("dns", "ListZones", {"Filter": {"nested": True}})


# ── Error interpretation ─────────────────────────────────────────────────────

def test_invalid_access_key_hint_does_not_blame_the_signature():
    """
    Regression: an unrecognised key was reported as a signing failure, sending
    the caller to check clock skew and regions for no reason.
    """
    client = Client(_creds())
    hint = client._hint_for(401, "InvalidAccessKey", {}, CDN, None)
    assert "does not recognise this access key" in hint
    assert "clock" not in hint.lower()


def test_signature_hint_lists_the_real_causes():
    client = Client(_creds())
    hint = client._hint_for(401, "SignatureDoesNotMatch", {}, SERVICES["waf"], None)
    assert "region" in hint
    assert "whitespace" in hint
    # WAF has an alternate host, so the hint should mention probing for it.
    assert "probe" in hint


def test_clock_skew_is_measured_from_the_server_date_header():
    client = Client(_creds())
    assert client._clock_skew({}) is None
    assert client._clock_skew({"Date": "not a date"}) is None
    skew = client._clock_skew({"Date": "Wed, 09 Sep 2026 00:00:00 GMT"})
    assert skew is not None


def test_403_is_reported_as_authorisation_not_signing():
    client = Client(_creds())
    hint = client._hint_for(403, "", {}, CDN, None)
    assert "not authorised" in hint
    assert "Signature" not in hint


def test_unknown_action_points_at_the_catalog():
    client = Client(_creds())
    hint = client._hint_for(404, "InvalidActionOrVersion", {}, CDN, None)
    assert "catalog" in hint and CDN.version in hint


def test_error_str_includes_request_id_and_hint():
    error = BytePlusError("boom", status=401, code="X", request_id="RID123", hint="try Y")
    rendered = str(error)
    assert "RID123" in rendered and "try Y" in rendered


# ── Credentials ──────────────────────────────────────────────────────────────

def test_mask_never_reveals_a_short_secret():
    assert mask("abcd") == "****"
    assert mask("") == "(not set)"
    assert mask(None) == "(not set)"
    masked = mask("AKLTabcdefghijklmnop")
    assert "AKLT" in masked and "mnop" in masked
    assert "efghijkl" not in masked


def test_live_mode_is_off_unless_clearly_affirmative(monkeypatch):
    monkeypatch.delenv("BPCTL_LIVE", raising=False)
    assert resolve_live(False) is False
    assert resolve_live(True) is True
    for value in ("false", "0", "no", "", "off", "maybe"):
        monkeypatch.setenv("BPCTL_LIVE", value)
        assert resolve_live(False) is False, value
    for value in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("BPCTL_LIVE", value)
        assert resolve_live(False) is True, value


def test_unknown_service_names_are_rejected_with_a_useful_message():
    with pytest.raises(KeyError, match="cdn"):
        resolve("cnd")


# ── --all pagination ─────────────────────────────────────────────────────────

from bpctl.cli import main  # noqa: E402
from bpctl.client import list_all  # noqa: E402


class _CappedList:
    """A List* endpoint that honours PageNum but caps PageSize, like several BytePlus APIs."""

    def __init__(self, total, cap):
        self.total, self.cap, self.calls = total, cap, 0

    def call(self, service, action, payload):
        self.calls += 1
        size = min(payload["PageSize"], self.cap)
        start = (payload["PageNum"] - 1) * size
        rows = [{"Domain": f"d{i}.example.com"} for i in range(start, min(start + size, self.total))]

        class R:
            body = {"Result": {"Data": rows, "Total": self.total}}
            dry_run = False

        return R()


def test_list_all_follows_total_when_the_api_caps_page_size():
    fake = _CappedList(total=25, cap=10)
    listing = list_all(fake, "cdn", "ListCdnDomains", page_size=100)
    assert listing["count"] == 25
    assert listing["complete"] is True
    assert fake.calls == 3


def test_list_all_treats_null_data_as_empty():
    class Empty:
        def call(self, *args, **kwargs):
            class R:
                body = {"Result": {"Data": None}}
                dry_run = False
            return R()

    listing = list_all(Empty(), "waf", "ListDomain")
    assert listing["count"] == 0 and listing["complete"] is True


def test_call_all_refuses_write_actions(capsys):
    assert main(["call", "cdn", "AddCdnDomain", "--all"]) == 1
    assert "only applies to read actions" in capsys.readouterr().out
