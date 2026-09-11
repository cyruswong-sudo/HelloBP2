# HelloBP 2.0 — BytePlus CDN, WAF, DNS & Certificates

> Portable agent instructions. This file is the vendor-neutral entry point: any
> coding agent that reads `AGENTS.md` gets the full capability from this file
> alone. Claude Code users get the same content as a skill; the behaviour is
> identical.

## What this gives you

BytePlus OpenAPIs cannot be called with a bearer token. Every request needs an
HMAC-SHA256 signature whose credential scope changes per product. A wrong
region, a clock more than ~5 minutes off UTC, or a trailing newline in a pasted
key all fail identically — `401 SignatureDoesNotMatch` — with no clue which.

`bpctl` owns that problem. Its signing is verified byte-for-byte against the
official BytePlus SDK. **Never hand-roll a BytePlus signature; never write your
own HTTP client for these APIs.**

## Running it

Stdlib-only Python 3.9+. Nothing to install.

If `bpctl` is on `PATH`:

```bash
bpctl --version
```

Otherwise call the script directly — it needs no installation:

```bash
python3 <repo>/skills/hellobp2/scripts/bpctl.py --version
```

Expected: `bpctl 2.1.0`. Resolve the path once and reuse it; your working
directory is usually the user's project, not this repo.

## Credentials

Resolved in this order, first hit wins per field:

```
explicit flag → BYTEPLUS_* env var → ~/.byteplus/config → ./bpctl.json
```

```bash
export BYTEPLUS_ACCESS_KEY=...
export BYTEPLUS_SECRET_KEY=...
export BYTEPLUS_REGION=singapore   # optional; singapore is the default
```

`~/.byteplus/config` is the same file the official SDK reads, so an
SDK-configured machine works with no extra steps.

**Never print, echo, log, or write these values.** Use `bpctl whoami`, which
masks them and reports where each came from, instead of inspecting the
environment yourself.

## Writes are off until armed

Reads run live. **Writes are simulated unless `--live` is passed.** An action
counts as a read only if its name starts with `Describe`, `List`, `Query`,
`Get`, or `Check` — anything unrecognised is treated as a write, which is the
fail-safe direction.

```bash
bpctl call cdn ListCdnDomains                        # real
bpctl call cdn AddCdnDomain --body '{...}'           # simulated
bpctl call cdn AddCdnDomain --body '{...}' --live    # real
```

Confirm with the user before the first `--live` call of a session and state
exactly what will be created or changed. **Do not add `--live` merely because a
dry run reported `"dry_run": true`** — that is the tool working as designed.

`BPCTL_LIVE=1` also arms writes, but only clearly affirmative values (`1`,
`true`, `yes`, `on`) count.

## First run against a new account

```bash
bpctl probe --save
```

Read-only — every call it makes is a List/Describe/Get. It finds the host and
signing variant each service accepts, reads back the real shape of the disputed
WAF enum fields, and writes `~/.byteplus/verified.json`, which later commands
consult. Run once per account. On failure the output names the causes it ruled
in and out.

> `AclType` resolves on any account. `HostAddType` and `IpAddType` can only be
> read off an existing WAF ACL rule — on an account with none they stay
> unresolved, and the migration playbook uses the documented values and says so.

## Calling any action

The generic caller reaches **every** BytePlus CDN, WAF, DNS, and Certificate
action, including ones documented nowhere here:

```bash
bpctl call waf CreateAclRule --body '{"Name":"block-abuse","AclType":"Block"}'
bpctl call dns ListRecords -p ZID=12345 -p PageSize=100
bpctl call cdn UpdateCdnConfig --body-file /tmp/config.json
```

Output is always JSON on stdout, on success and on failure. **Branch on `ok`.**
The live payload is under `response`; failures carry `error`, `code`, `status`,
`request_id`, and usually a `hint` naming the next thing to try. HTTP method and
API version are chosen for you.

If unsure whether an action exists, call it and read the error rather than
guessing.

| Flag | Use |
|---|---|
| `--body '{"K":"V"}'` | Full JSON payload |
| `-p Key=Value` | One field, repeatable; JSON-looking values are parsed |
| `--body-file path.json` | Large payloads |
| `--method GET\|POST` | Override the chosen method |
| `--host <host>` | Override the API host |
| `--live` | Actually execute a write |
| `--all` | Read every page of a List action — lists stop at 10 rows otherwise |

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

Aliases: `cert`, `certs`. `bpctl services` prints this with per-service notes.

## Things that will waste your time if you don't know them

Load-bearing. Read `skills/hellobp2/references/gotchas.md` before writing any
config payload.

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
- **Omit `OriginHost` entirely** for "Same as Domain Name". Sending `""` is not
  the same thing.
- **HTTP/2 must be off when WebSocket is on** — they are incompatible.
- **The Certificate Service has no list endpoint.** Every `CertificateList*`
  name returns 404. Enumerate certs via the *CDN* service's `ListCdnCertInfo`.
- **`BatchDeployCert` takes `Domain` as a comma-joined string**, not an array,
  max 50 per call.
- **Conditional logic belongs in the Rules Engine**, not flat policy fields.

## Reference files

Bundled alongside this file:

| Topic | Path |
|---|---|
| Signing, regions, decoding a 401 | `skills/hellobp2/references/auth.md` |
| Errors and their actual causes | `skills/hellobp2/references/gotchas.md` |
| CDN Rules Engine grammar | `skills/hellobp2/references/rule-engine.md` |
| Migration — order, reporting, overriding rules | `skills/hellobp2-migrate/SKILL.md` |
| Stage 1: extract the Cloudflare zone | `skills/hellobp2-migrate/references/extract.md` |
| Stage 2: plan and coverage | `skills/hellobp2-migrate/references/mapping.md` |
| Stage 3: execute and validate | `skills/hellobp2-migrate/references/execute.md` |
| Stage 4: certificates | `skills/hellobp2-migrate/references/ssl.md` |
| Stage 5: DNS cutover | `skills/hellobp2-migrate/references/cutover.md` |
| Stage 6: WAF IP rules | `skills/hellobp2-migrate/references/waf.md` |

When a payload's exact field names are unclear, fetch the official docs rather
than guessing — each service's `doc` URL is in `bpctl services --json`.

## Migrating from Cloudflare

There is no migration program. **You carry the migration out** with `bpctl cf`
and `bpctl call`, following the playbooks in `skills/hellobp2-migrate/`:

| # | Stage | Playbook |
|---|---|---|
| 1 | Extract the zone — reads only | `references/extract.md` |
| 2 | Plan and report coverage | `references/mapping.md` |
| 3 | Execute, then validate | `references/execute.md` |
| 4 | Certificates | `references/ssl.md` |
| 5 | DNS cutover | `references/cutover.md` |
| 6 | WAF IP rules | `references/waf.md` |

Read `skills/hellobp2-migrate/SKILL.md` first: it holds the order, the reporting
rules and the rules that override instinct. Certificates come before cutover.
**Stop after stage 2** and review the coverage report with the user — nothing
has been created yet.

**Do not claim a Cloudflare zone has been migrated on the basis of a few
successful API calls.** Say what was created and what was not.

Cloudflare features with **no BytePlus equivalent**, which must be reported
rather than silently dropped: Waiting Rooms, Spectrum, Zaraz, Page Shield, Cache
Reserve, Network Error Logging, Access / Zero Trust, Custom Hostnames (SSL for
SaaS), DDoS L7 overrides.

**BytePlus DNS does not support** `HTTPS`, `SVCB`, `DS`, `DNSKEY`, `TLSA`,
`SSHFP`, `LOC`, `NAPTR`, `CERT`, `URI`, or `SMIMEA` records. `HTTPS`/`SVCB`
matters most — dropping one silently disables ECH and ALPN hints. Flag these
loudly before cutover.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401 SignatureDoesNotMatch` | Wrong region; clock >5 min off UTC; whitespace in a pasted key; wrong host. Run `bpctl probe` — it distinguishes them. |
| `401 InvalidAccessKey` | Key not recognised. Confirm it is active and that both halves are from the same pair. |
| `403` | Signature accepted; the **account** lacks permission. Check the key's policy. |
| `404 InvalidActionOrVersion` | No such action at that version. Check spelling against the product docs. |
| `bpctl catalog` errors | Expected — action catalogs aren't built yet. Use `bpctl call`. |
| Indonesia / Philippines / Vietnam / Taiwan account | Use `BYTEPLUS_REGION=ap-southeast-3`. |
