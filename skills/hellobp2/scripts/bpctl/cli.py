"""
cli.py — the bpctl command line.

This is the surface an agent drives, so two properties matter more than they
would in a human-only tool:

* **Machine-readable by default.** ``bpctl call`` writes JSON to stdout, on
  success and on failure alike, so an agent can branch on ``.ok`` instead of
  parsing prose. Human-facing commands (``whoami``, ``services``, ``probe``)
  print tables, and take ``--json`` when a caller wants structure.
* **Failures are still JSON.** An error that arrives as an exception trace is
  an error an agent cannot reason about. Every failure path emits
  ``{"ok": false, "error": ..., "hint": ...}`` and exits non-zero.

Commands:

    bpctl whoami                     which account and mode are in effect
    bpctl services                   the service registry
    bpctl probe [service ...]        verify signing against a live account
    bpctl call <service> <Action>    invoke any BytePlus API action (--all reads every page)
    bpctl cf <method> <path>         call the Cloudflare API
    bpctl catalog <service>          list known actions for a service

There are deliberately no workflow commands. Migrations, certificates and WAF
moves are procedures an agent carries out with `call` and `cf` — the steps live
in the hellobp2-migrate skill's references, not in code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import probe as probe_mod
from .client import BytePlusError, Client, is_write_action, list_all
from .cloudflare import CloudflareClient, CloudflareError
from .config import load_cloudflare_credentials, load_credentials, resolve_live
from .services import SERVICES, resolve, service_table

VERSION = "2.1.0"

# The skill directory: .../skills/hellobp2/, two levels above this module
# (bpctl/ -> scripts/ -> hellobp2/). Reference files are resolved from here.
SKILL_ROOT = Path(__file__).resolve().parent.parent.parent

# Where `bpctl probe --save` writes. Probe results are per-account, not per
# checkout, and a skill installed as a plugin may live on a read-only path —
# so this goes beside the credentials, not inside the skill.
VERIFIED_PATH = Path.home() / ".byteplus" / probe_mod.VERIFIED_FILENAME


def _emit(payload: dict, *, code: int = 0) -> int:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return code


def _fail(message: str, *, hint: str | None = None, **extra) -> int:
    return _emit({"ok": False, "error": message, "hint": hint, **extra}, code=1)


def _credentials(args):
    return load_credentials(
        access_key=getattr(args, "access_key", None),
        secret_key=getattr(args, "secret_key", None),
        region=getattr(args, "region", None),
        config_path=getattr(args, "config", None),
    )


def _client(args, credentials):
    verified = probe_mod.load(VERIFIED_PATH)
    # Hosts pinned by a previous `bpctl probe --save`. A per-call `--host` is
    # passed straight to Client.call and takes precedence over these.
    overrides = probe_mod.host_overrides_from(verified)

    # A probe that verified a Host-signing variant is respected; otherwise the
    # documented default (Host not signed) applies.
    sign_host = False
    for entry in (verified.get("services") or {}).values():
        if entry.get("ok") and entry.get("sign_host"):
            sign_host = True
            break

    return Client(
        credentials,
        live=resolve_live(getattr(args, "live", False)),
        timeout=getattr(args, "timeout", 30),
        host_overrides=overrides,
        sign_host=sign_host,
    )


def _parse_payload(args) -> dict:
    """Build the request payload from --body, --body-file, and repeated -p."""
    payload = {}

    if getattr(args, "body_file", None):
        try:
            raw = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"bpctl: cannot read --body-file {args.body_file}: {exc}")
        payload.update(_decode_json(raw, "--body-file"))

    if getattr(args, "body", None):
        payload.update(_decode_json(args.body, "--body"))

    for item in getattr(args, "param", None) or []:
        if "=" not in item:
            raise SystemExit(f"bpctl: -p expects Key=Value, got {item!r}")
        key, _, value = item.partition("=")
        payload[key] = _coerce(value)

    return payload


def _decode_json(raw: str, label: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"bpctl: {label} is not valid JSON — {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"bpctl: {label} must be a JSON object, got {type(data).__name__}")
    return data


def _coerce(value: str):
    """Let -p carry JSON so nested values do not require --body."""
    stripped = value.strip()
    if stripped[:1] in "[{" or stripped in ("true", "false", "null"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    if stripped.lstrip("-").isdigit():
        return int(stripped)
    return value


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_whoami(args) -> int:
    credentials = _credentials(args)
    live = resolve_live(args.live)
    verified = probe_mod.load(VERIFIED_PATH)

    if args.json:
        return _emit(
            {
                "ok": True,
                "version": VERSION,
                "live": live,
                "region": credentials.region,
                "credentials_present": credentials.complete,
                "cloudflare_token_present": load_cloudflare_credentials(
                    config_path=args.config
                ).complete,
                "sources": credentials.sources,
                "verified": bool(verified),
            }
        )

    print(f"bpctl {VERSION}")
    print()
    print("Credentials")
    print(credentials.describe())
    cf = load_cloudflare_credentials(config_path=args.config)
    print()
    print("Cloudflare")
    print(cf.describe())
    print()
    print("Mode")
    if live:
        print("  ⚠️  LIVE — write actions will really execute")
    else:
        print("  dry-run — reads run live, writes are simulated (use --live to arm)")
    print()
    print("Verified facts")
    if verified:
        ok = [k for k, v in (verified.get("services") or {}).items() if v.get("ok")]
        print(f"  {VERIFIED_PATH.name}: {len(ok)}/{len(SERVICES)} services verified ({', '.join(sorted(ok)) or 'none'})")
    else:
        print("  none yet — run `bpctl probe --save` to verify signing against this account")
    return 0


def cmd_services(args) -> int:
    if args.json:
        return _emit(
            {
                "ok": True,
                "services": {
                    key: {
                        "host": spec.host,
                        "signing_name": spec.signing_name,
                        "version": spec.version,
                        "alt_hosts": list(spec.alt_hosts),
                        "get_actions": sorted(spec.get_actions),
                        "doc": spec.doc,
                        "notes": spec.notes,
                    }
                    for key, spec in SERVICES.items()
                },
            }
        )

    print(service_table())
    print()
    for spec in SERVICES.values():
        if spec.notes:
            print(f"{spec.key}: {spec.notes}")
            print()
    return 0


def cmd_probe(args) -> int:
    credentials = _credentials(args)
    credentials.require()

    keys = args.service or list(SERVICES)
    for key in keys:
        resolve(key)  # validate before making network calls

    report = probe_mod.run(
        credentials,
        services=[resolve(k).key for k in keys],
        check_params=args.params,
        timeout=args.timeout,
    )

    if args.save:
        probe_mod.save(report, VERIFIED_PATH)
        report["saved_to"] = str(VERIFIED_PATH)

    if args.json:
        return _emit({"ok": True, **report})

    print(f"Probing {len(keys)} service(s) in region {credentials.region!r}, read-only.\n")
    failures = 0
    for key, entry in report["services"].items():
        if entry["ok"]:
            variant = "Host signed" if entry["sign_host"] else "Host not signed"
            print(f"  ✓ {key:<12} {entry['host']}  ({variant})")
            print(f"      {entry['detail']}")
        else:
            failures += 1
            print(f"  ✗ {key:<12} {entry['detail']}")
            for attempt in entry["attempts"]:
                marker = "signed Host" if attempt["sign_host"] else "unsigned Host"
                print(
                    f"      tried {attempt['host']} ({marker}): "
                    f"{attempt.get('code') or attempt.get('status')}"
                )

    if "waf_parameters" in report:
        print("\nWAF parameter shapes (read back from the account)")
        observed = report["waf_parameters"].get("observed") or {}
        if observed:
            for key, value in observed.items():
                print(f"  {key} = {value!r}")
        print(f"  {report['waf_parameters']['note']}")

    print("\nDisputed facts")
    for entry in report["discrepancies"]:
        state = f"RESOLVED → {entry['resolved']}" if entry.get("resolved") else "still unresolved"
        print(f"  {entry['topic']}: {state}")
        if not entry.get("resolved"):
            print(f"      docs say {entry['documented']}; v1 used {entry['v1_used']}")
            print(f"      to settle: {entry['how_to_settle']}")

    if args.save:
        print(f"\nWritten to {VERIFIED_PATH}")
    else:
        print("\n(Not saved. Re-run with --save to pin these facts for other commands.)")

    return 1 if failures else 0


def cmd_call(args) -> int:
    credentials = _credentials(args)
    try:
        payload = _parse_payload(args)
    except SystemExit as exc:
        return _fail(str(exc))

    client = _client(args, credentials)

    if args.all:
        try:
            spec = resolve(args.service)
        except KeyError as exc:
            return _fail(str(exc).strip("'\""))
        if is_write_action(args.action, spec):
            return _fail(f"--all only applies to read actions; {args.action} is a write")
        try:
            listing = list_all(client, spec.key, args.action, payload, page_size=args.page_size)
        except BytePlusError as exc:
            return _fail(str(exc).split("\n")[0], hint=exc.hint, status=exc.status, code=exc.code, request_id=exc.request_id)
        return _emit({"ok": True, "service": spec.key, "action": args.action, **listing})

    try:
        response = client.call(
            args.service,
            args.action,
            payload,
            method=args.method,
            host=args.host,
        )
    except BytePlusError as exc:
        return _fail(
            str(exc).split("\n")[0],
            hint=exc.hint,
            status=exc.status,
            code=exc.code,
            request_id=exc.request_id,
            response=exc.body,
        )
    except (KeyError, ValueError) as exc:
        return _fail(str(exc))

    return _emit(response.to_dict())


def cmd_cf(args) -> int:
    credentials = load_cloudflare_credentials(
        api_token=args.api_token, config_path=args.config
    )
    try:
        payload = _parse_payload(args)
    except SystemExit as exc:
        return _fail(str(exc))

    params = {}
    for item in args.query or []:
        if "=" not in item:
            return _fail(f"--query expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        params[key] = value

    client = CloudflareClient(
        credentials, live=resolve_live(args.live), timeout=args.timeout or 20
    )

    try:
        response = client.call(args.method, args.path, params=params, body=payload)
    except CloudflareError as exc:
        return _fail(
            str(exc).split("\n")[0],
            hint=exc.hint,
            status=exc.status,
            code=exc.code,
            response=exc.body,
        )

    return _emit(response.to_dict())


def cmd_catalog(args) -> int:
    spec = resolve(args.service)
    catalog_path = SKILL_ROOT / "references" / "actions" / f"{spec.key}.json"
    if not catalog_path.is_file():
        return _fail(
            f"no action catalog for {spec.key} yet",
            hint=(
                "Action catalogs land in Phase 2. Until then, browse "
                f"{spec.doc} and call any action with `bpctl call {spec.key} <Action>`."
            ),
        )
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if args.json:
        return _emit({"ok": True, "service": spec.key, **data})

    for action in data.get("actions", []):
        print(f"{action['name']:<36} {action.get('summary', '')}")
    return 0


# ── Argument parsing ─────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bpctl",
        description="Call any BytePlus CDN, WAF, DNS, or Certificate API with correct signing.",
    )
    parser.add_argument("--version", action="version", version=f"bpctl {VERSION}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--access-key", help="BytePlus access key (else $BYTEPLUS_ACCESS_KEY)")
    common.add_argument("--secret-key", help="BytePlus secret key (else $BYTEPLUS_SECRET_KEY)")
    common.add_argument("--region", help="signing region (default: singapore)")
    common.add_argument("--config", help="path to a JSON credentials file")
    common.add_argument("--timeout", type=int, default=30, help="request timeout in seconds")
    common.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("whoami", parents=[common], help="show the active account and mode")
    p.add_argument("--live", action="store_true", help="report as if live mode were on")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("services", parents=[common], help="show the service registry")
    p.set_defaults(func=cmd_services)

    p = sub.add_parser(
        "probe",
        parents=[common],
        help="verify signing against a live account (read-only)",
    )
    p.add_argument("service", nargs="*", help="services to probe (default: all)")
    p.add_argument("--save", action="store_true", help="write verified.json")
    p.add_argument(
        "--params",
        action="store_true",
        default=True,
        help="also read back WAF enum shapes (default: on)",
    )
    p.add_argument("--no-params", dest="params", action="store_false")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("call", parents=[common], help="invoke any BytePlus API action")
    p.add_argument("service", help="cdn | waf | dns | certificate")
    p.add_argument("action", help="the API action name, e.g. ListCdnDomains")
    p.add_argument("--body", help="request payload as a JSON object")
    p.add_argument("--body-file", help="read the JSON payload from a file")
    p.add_argument(
        "-p",
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help="a single payload field; repeatable. Values that look like JSON are parsed.",
    )
    p.add_argument("--method", choices=["GET", "POST"], help="override the HTTP method")
    p.add_argument("--host", help="override the API host")
    p.add_argument("--live", action="store_true", help="actually execute write actions")
    p.add_argument("--all", action="store_true", help="read every page of a List* action and merge the rows")
    p.add_argument("--page-size", type=int, default=100, help="page size used with --all (default 100)")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser(
        "cf",
        parents=[common],
        help="call the Cloudflare API v4 (bearer token; GET/HEAD are reads)",
    )
    p.add_argument("method", help="HTTP method: get, post, put, patch, delete")
    p.add_argument("path", help="API path, e.g. /zones or /zones/<id>/dns_records")
    p.add_argument("--api-token", help="Cloudflare API token (else $CLOUDFLARE_API_TOKEN)")
    p.add_argument("--body", help="request payload as a JSON object")
    p.add_argument("--body-file", help="read the JSON payload from a file")
    p.add_argument(
        "-p", "--param", dest="param", action="append", metavar="KEY=VALUE",
        help="a single body field; repeatable. Values that look like JSON are parsed.",
    )
    p.add_argument(
        "-q", "--query", action="append", metavar="KEY=VALUE",
        help="a query-string parameter; repeatable",
    )
    p.add_argument("--live", action="store_true", help="actually execute writes")
    p.set_defaults(func=cmd_cf)

    p = sub.add_parser("catalog", parents=[common], help="list known actions for a service")
    p.add_argument("service")
    p.set_defaults(func=cmd_catalog)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            return _fail(exc.code)
        raise
    except KeyboardInterrupt:
        return _fail("interrupted")


if __name__ == "__main__":
    sys.exit(main())
