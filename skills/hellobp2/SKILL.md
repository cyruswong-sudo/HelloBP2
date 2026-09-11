---
name: hellobp2
description: >
  Work with BytePlus CDN, WAF, DNS, and SSL certificates through their OpenAPIs,
  and migrate workloads from Cloudflare to BytePlus. Use when the user wants to
  inspect or change BytePlus CDN domains, delivery/encryption policies, WAF
  rules, DNS zones or records, or certificates; when they hit a BytePlus API
  signing or authentication error; or when they want to move a Cloudflare zone
  to BytePlus. Also use when the user names bpctl.
argument-hint: "[domain, zone, or task]"
allowed-tools: Bash(python3 *) Bash(bpctl *) Read Write Edit Grep Glob WebFetch
---

# HelloBP 2.0 — BytePlus CDN, WAF & DNS

BytePlus OpenAPIs cannot be called with a plain bearer token. Every request
needs an HMAC-SHA256 signature whose credential scope changes per product, and
a wrong region, a stale clock, or a trailing newline in a key all fail the same
way: `401 SignatureDoesNotMatch`, with no clue which one it was.

`bpctl` — bundled with this skill at `scripts/bpctl.py` — owns that problem so
you never hand-roll a signature. Its signing is verified byte-for-byte against
the official BytePlus SDK.

## Run it

Stdlib-only Python 3.9+. Nothing to install.

If the installer has run, `bpctl` is already on `PATH`:

```bash
bpctl --version
```

**Otherwise every command below is written as `scripts/bpctl.py`, relative to
this skill's directory.** Resolve it to an absolute path once and reuse it, since
your working directory is usually the user's project, not the skill:

```bash
BPCTL="$(command -v bpctl || find -L ~/.claude "$PWD" -path '*/hellobp2/scripts/bpctl.py' -print -quit 2>/dev/null)"
[ -n "$BPCTL" ] && python3 "$BPCTL" --version || echo "bpctl not found — run install.sh, or use the direct path"
```

If that prints `bpctl 2.1.0`, you are ready. If you already know where the
skill lives, just use the direct path.

> `find` needs `-L` because the installer symlinks the skill into
> `~/.claude/skills/`, and `-path` must name `hellobp2` specifically — the
> `hellobp2-migrate` skill has no `scripts/` directory, so a looser pattern can
> resolve to a file that does not exist.

## Credentials

Read from the environment first, then `~/.byteplus/config` (the same file the
official SDK reads), then `./bpctl.json`.

```bash
export BYTEPLUS_ACCESS_KEY=...
export BYTEPLUS_SECRET_KEY=...
export BYTEPLUS_REGION=singapore     # optional; singapore is the default
```

Never print, echo, or write these values. `bpctl whoami` shows masked values and
where each came from — use it instead of inspecting the environment yourself.

## Writes are off until you arm them

Reads run against the live account. **Writes are simulated** unless `--live` is
passed. An action is treated as a write unless its name starts with `Describe`,
`List`, `Query`, `Get`, or `Check` — unknown actions are assumed to write.

```bash
python3 scripts/bpctl.py call cdn ListCdnDomains                  # real call
python3 scripts/bpctl.py call cdn AddCdnDomain --body '{...}'     # simulated
python3 scripts/bpctl.py call cdn AddCdnDomain --body '{...}' --live   # real
```

Confirm with the user before the first `--live` call of a session, and state
exactly what will be created or changed. Do not add `--live` to a command just
because a dry run reported `"dry_run": true` — that is the tool working.

## First run against a new account

```bash
python3 scripts/bpctl.py probe --save
```

Read-only. It finds the host and signing variant each service accepts, reads
back the real shape of the disputed WAF enum fields, and writes `verified.json`,
which every later command uses. Run it once per account. If a service fails to
authenticate, the probe output names the causes it ruled in or out.

## Calling any action

The generic caller reaches **every** BytePlus CDN, WAF, DNS, and Certificate
action, including ones not listed anywhere in this skill:

```bash
# Payload as JSON
python3 scripts/bpctl.py call waf CreateAclRule --body '{"Name":"block-abuse","AclType":"Block"}'

# Or field by field; values that look like JSON are parsed
python3 scripts/bpctl.py call dns ListRecords -p ZID=12345 -p PageSize=100

# Or from a file, for large payloads
python3 scripts/bpctl.py call cdn UpdateCdnConfig --body-file /tmp/config.json

# Every page of a List action — lists stop at 10 rows without saying so
python3 scripts/bpctl.py call cdn ListCdnDomains --all
```

Output is always JSON on stdout — on success and on failure. Branch on `ok`.
Failures carry `error`, `code`, `status`, `request_id`, and usually a `hint`
naming the next thing to try. HTTP method and API version are chosen for you.

Use `bpctl call` rather than writing your own HTTP client, and rather than
guessing at an action's existence — if you are unsure an action exists, call it
and read the error.

## Cloudflare

`bpctl cf` calls the Cloudflare API v4. There is no signing — Cloudflare takes a
plain bearer token — so this exists for the other guarantees: **the token is
resolved from a file or the environment and never reaches `argv`**, a shell
history, or a transcript, and writes are simulated unless armed.

```bash
export CLOUDFLARE_API_TOKEN=...          # or ~/.cloudflare/config as {"api_token": "..."}

bpctl cf get /zones                                   # real
bpctl cf get /zones/<zone_id>/dns_records -q per_page=100
bpctl cf post /zones/<zone_id>/dns_records \
    --body '{"type":"TXT","name":"_acme-challenge.example.com","content":"...","ttl":60}'
```

**Never paste the token into a `curl` command.** That is the leak `bpctl cf`
exists to prevent — use it, or tell the user to put the token in the file.

The read/write split is the HTTP method: `GET` and `HEAD` are reads, everything
else is a write and needs `--live`. Output is the same envelope as `bpctl call`,
so branch on `ok` either way.

Token scoping, least privilege first:

| Task | Permission |
|---|---|
| Publish DCV validation records | `Zone:DNS:Edit` on the specific zones |
| Read zone config for a migration | `Zone:Read` + `Zone Settings:Read` |
| Read firewall / rate limits | `Zone:Firewall Services:Read` |

Cloudflare reports failure in the envelope's `success` field, **which can be
`false` on an HTTP 200** — the same trap as BytePlus. `bpctl cf` raises on it;
if you ever call the API another way, do not branch on HTTP status alone.

## Services

| Service | Host | Signing name | Version |
|---|---|---|---|
| `cdn` | `cdn.byteplusapi.com` | `CDN` | 2021-03-01 |
| `waf` | `waf.byteplusapi.com` | `waf` | 2023-12-25 |
| `dns` | `dns.byteplusapi.com` | `DNS` | 2018-08-01 |
| `certificate` | `open.byteplusapi.com` | `certificate_service` | 2021-06-01 |

`python3 scripts/bpctl.py services` prints this with per-service notes.

## Things that will waste your time if you don't know them

These are load-bearing. Read [references/gotchas.md](references/gotchas.md)
before writing any config payload.

- **CDN delivery config lives in *policies*, not on the domain.** Create a
  service template → release it → attach domains. A released policy is
  immutable: to change it, `DuplicateTemplate`, edit, release, re-attach.
- **List actions stop at 10 rows without saying so.** Use `--all`, or check
  `len(Data)` against `Result.Total`, before calling any list complete.
- **Authenticating against WAF does not mean the account has WAF.** An account
  with no WAF deployment still probes as `authenticated`. Check with
  `bpctl call waf ListDomain` — `"Data": null` means no domains are onboarded and
  `CreateAclRule` cannot succeed. Say WAF is not enabled and stop; do not retry
  or guess enum values.
- **Access rules must omit `Switch`.** Including it on `IpAccessRule`,
  `UaAccessRule`, or `RefererAccessRule` returns a misleading
  `InvalidParameter.IpAccessRule.RuleType`. Set only `FilterType`
  (`blacklist`/`whitelist`) and `Filters`.
- **Omit `OriginHost` entirely** to get "Same as Domain Name". Sending `""` is
  not the same thing.
- **HTTP/2 must be off when WebSocket is on** — they are incompatible.
- **The Certificate Service has no list endpoint.** Every `CertificateList*`
  name returns 404. Enumerate certs via the *CDN* service's `ListCdnCertInfo`.
- **`BatchDeployCert` takes `Domain` as a comma-joined string**, not an array,
  max 50 per call.
- **Conditional logic belongs in the Rules Engine**, not in flat policy fields.
  See [references/rule-engine.md](references/rule-engine.md).

## References

| Topic | File |
|---|---|
| Signing, regions, and decoding a 401 | [references/auth.md](references/auth.md) |
| Errors and their actual causes | [references/gotchas.md](references/gotchas.md) |
| CDN Rules Engine conditions and actions | [references/rule-engine.md](references/rule-engine.md) |

When a payload's exact field names are unclear, fetch the official docs rather
than guessing — each service's `doc` URL is in `bpctl services --json`.

## Migrating from Cloudflare

Use the **`hellobp2-migrate`** skill. It is the complete procedure — extract the
zone, plan and report coverage, execute and validate, certificates, DNS cutover,
and WAF IP rules — carried out with `bpctl cf` and `bpctl call`. Its certificate
and WAF stages also work on their own, outside a migration.

Never claim a zone is migrated because some API calls succeeded: say what was
created, what wasn't, and what is left to do.

**Cloudflare features with no BytePlus equivalent**, which must be reported
rather than silently dropped: Waiting Rooms, Spectrum, Zaraz, Page Shield, Cache
Reserve, Network Error Logging, Access/Zero Trust, Custom Hostnames (SSL for
SaaS), DDoS L7 overrides. **BytePlus DNS also does not support** `HTTPS`/`SVCB`,
`DS`/`DNSKEY`, `TLSA`, `SSHFP`, `LOC`, `NAPTR`, `CERT`, `URI`, or `SMIMEA`
records — dropping an `HTTPS` record silently breaks ECH and ALPN hints, so flag
these loudly.
