"""
probe.py — the truth-finding harness.

Phase 1 of HelloBP V2 refuses to build on folklore. Four facts about the
BytePlus WAF API are disputed between the official documentation and what
HelloBP v1 discovered by brute-force probing (see ``services.KNOWN_DISCREPANCIES``),
and a fifth — whether ``Host`` belongs in the signature — was never actually
settled, only worked around.

This module settles all of them against a live account using **read-only calls
only**, and writes the answers to ``verified.json``. Everything else in bpctl
reads that file. Until it exists, disputed values stay disputed and are labelled
as such rather than silently assumed.

Nothing here writes to BytePlus. Every action used is a List/Describe/Get.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .client import BytePlusError, Client
from .services import KNOWN_DISCREPANCIES, SERVICES, ServiceSpec

VERIFIED_FILENAME = "verified.json"

# A cheap, read-only call per service that proves the signature is accepted.
#
# `tolerate_errors` lists error conditions that still prove signing worked: for
# the Certificate Service there is no list endpoint at all, so the best
# available probe asks for an instance that does not exist. A "not found"
# answer is a *successful* signature; only an auth failure is a real failure.
PROBES = {
    "cdn": {
        "action": "DescribeTemplates",
        "payload": {"PageSize": 1, "PageNum": 1},
        "tolerate_errors": (),
    },
    "waf": {
        "action": "ListDomain",
        "payload": {"PageNum": 1, "PageSize": 1},
        "tolerate_errors": (),
    },
    "dns": {
        "action": "ListZones",
        "payload": {"PageSize": 1, "PageNumber": 1},
        "tolerate_errors": (),
    },
    "certificate": {
        "action": "CertificateGetInstance",
        "payload": {"instance_id": "bpctl-probe-does-not-exist"},
        "tolerate_errors": ("NotFound", "InvalidParameter", "ResourceNotFound"),
    },
}

# An auth failure means the signature itself was rejected. Anything else means
# the request was authenticated and the service simply disliked the arguments —
# which is all the probe needs to prove.
_AUTH_FAILURE_CODES = ("SignatureDoesNotMatch", "InvalidAccessKey", "Unauthorized", "AuthFailure")


def _is_auth_failure(exc: BytePlusError) -> bool:
    if exc.status == 401:
        return True
    return any(marker in (exc.code or "") for marker in _AUTH_FAILURE_CODES)


@dataclass
class ServiceProbe:
    service: str
    ok: bool
    host: str | None = None
    sign_host: bool | None = None
    action: str = ""
    status: int | None = None
    detail: str = ""
    attempts: list = field(default_factory=list)


def probe_service(credentials, spec: ServiceSpec, *, timeout: int = 20) -> ServiceProbe:
    """
    Find a host and signing variant that this account accepts for one service.

    Tries each candidate host, and for each host both signing variants (Host
    header excluded from the signature, then included). The first combination
    that authenticates wins. Read-only throughout.
    """
    probe = PROBES.get(spec.key)
    if not probe:
        return ServiceProbe(service=spec.key, ok=False, detail="no probe defined for this service")

    result = ServiceProbe(service=spec.key, ok=False, action=probe["action"])
    hosts = [spec.host, *spec.alt_hosts]

    for host in hosts:
        for sign_host in (False, True):
            attempt = {"host": host, "sign_host": sign_host}
            # `live=False` is safe here: it only gates writes, and every probe
            # action is read-only by construction.
            client = Client(credentials, live=False, timeout=timeout, retries=0)
            try:
                response = client.call(
                    spec.key,
                    probe["action"],
                    probe["payload"],
                    host=host,
                    sign_host=sign_host,
                )
                attempt.update(ok=True, status=response.status)
                result.attempts.append(attempt)
                result.ok = True
                result.host = host
                result.sign_host = sign_host
                result.status = response.status
                result.detail = "authenticated"
                return result
            except BytePlusError as exc:
                authenticated = not _is_auth_failure(exc) and (
                    not probe["tolerate_errors"]
                    or any(code in (exc.code or "") for code in probe["tolerate_errors"])
                    or exc.status not in (401, 403)
                )
                attempt.update(
                    ok=False,
                    status=exc.status,
                    code=exc.code,
                    message=str(exc).split("\n")[0],
                )
                result.attempts.append(attempt)

                if authenticated:
                    result.ok = True
                    result.host = host
                    result.sign_host = sign_host
                    result.status = exc.status
                    result.detail = (
                        f"authenticated (service returned {exc.code or exc.status}, "
                        "which proves the signature was accepted)"
                    )
                    return result

    last = result.attempts[-1] if result.attempts else {}
    result.detail = last.get("message", "all host and signing combinations were rejected")
    return result


def probe_waf_parameters(credentials, host: str, *, timeout: int = 20) -> dict:
    """
    Settle the disputed WAF enum values by reading rules back from the account.

    The API is the authority on its own shapes: whatever ``ListAclRule`` returns
    for ``AclType``, ``HostAddType`` and ``IpAddType`` is what those fields
    actually look like, regardless of what the docs or v1 assumed. Read-only.

    An account with no ACL rules yet cannot settle this — that is reported
    honestly rather than guessed at.
    """
    client = Client(credentials, live=False, timeout=timeout, retries=0, host_overrides={"waf": host})
    findings = {"source": "ListAclRule", "observed": {}, "note": ""}

    for acl_type in ("Block", "Allow", "block", "allow", "deny"):
        try:
            response = client.call("waf", "ListAclRule", {"AclType": acl_type, "Page": 1, "PageSize": 10})
        except BytePlusError as exc:
            findings.setdefault("rejected", {})[acl_type] = exc.code or str(exc.status)
            continue

        result = response.body.get("Result") or {}
        rules = result.get("Data") or result.get("data") or []
        findings.setdefault("accepted", []).append(acl_type)
        if rules:
            sample = rules[0]
            findings["observed"] = {
                key: sample.get(key)
                for key in ("AclType", "HostAddType", "IpAddType", "Action", "Enable")
                if key in sample
            }
            findings["note"] = (
                f"Read {len(rules)} existing rule(s); the values above are what the "
                "API itself returns and should be treated as authoritative."
            )
            break

    if not findings["observed"]:
        # No rules to read back — but ``ListAclRule`` *validates* AclType, so the
        # accept/reject split above already settles that one field on its own.
        # Only HostAddType and IpAddType genuinely need an existing rule, since
        # nothing validates them on a read.
        accepted = findings.get("accepted") or []
        rejected = findings.get("rejected") or {}
        if accepted and rejected:
            findings["observed"] = {"AclType": sorted(accepted)}
            findings["source"] = "ListAclRule parameter validation"
            findings["note"] = (
                f"No existing ACL rules, so HostAddType and IpAddType could not be "
                f"observed — create one rule in the BytePlus console and re-run "
                f"`bpctl probe waf --params` for those. AclType needed no rule: "
                f"ListAclRule validates it, accepting {sorted(accepted)} and "
                f"rejecting {sorted(rejected)}."
            )
        else:
            findings["note"] = (
                "No existing ACL rules found, so the enum shapes could not be observed. "
                "Create one rule by hand in the BytePlus console, then re-run "
                "`bpctl probe waf --params` to capture the exact field values."
            )
    return findings


def run(credentials, *, services=None, check_params: bool = True, timeout: int = 20) -> dict:
    """Probe every requested service and assemble the verified-facts document."""
    keys = services or list(SERVICES)
    report = {
        "region": credentials.region,
        "services": {},
        "discrepancies": [asdict(d) for d in KNOWN_DISCREPANCIES],
    }

    for key in keys:
        spec = SERVICES[key]
        result = probe_service(credentials, spec, timeout=timeout)
        report["services"][key] = asdict(result)

    waf = report["services"].get("waf")
    if check_params and waf and waf.get("ok"):
        report["waf_parameters"] = probe_waf_parameters(credentials, waf["host"], timeout=timeout)
        _settle(report)

    return report


def _settle(report: dict) -> None:
    """Fill in the `resolved` field of each discrepancy from what the probe saw."""
    waf = report["services"].get("waf") or {}
    observed = (report.get("waf_parameters") or {}).get("observed") or {}

    for entry in report["discrepancies"]:
        topic = entry["topic"]
        if topic == "waf.host" and waf.get("ok"):
            entry["resolved"] = waf["host"]
        elif topic.startswith("waf.CreateAclRule."):
            field_name = topic.rsplit(".", 1)[-1]
            if field_name in observed and observed[field_name] is not None:
                entry["resolved"] = repr(observed[field_name])


def save(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def host_overrides_from(report: dict) -> dict:
    """Extract `{service: host}` for the hosts the probe actually verified."""
    overrides = {}
    for key, entry in (report.get("services") or {}).items():
        if entry.get("ok") and entry.get("host"):
            overrides[key] = entry["host"]
    return overrides
