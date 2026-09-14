---
name: hellobp2-migrate
description: >
  Migrate a Cloudflare zone to BytePlus: extract the zone, plan what maps to
  BytePlus CDN delivery and encryption policies, execute and validate, issue and
  deploy certificates, cut DNS over, and move IP allow/block rules to the WAF —
  reporting everything that cannot carry across. Use when the user wants to move
  a site, zone or workload off Cloudflare, asks what a Cloudflare feature maps to
  on BytePlus, or needs certificates issued or WAF rules migrated.
argument-hint: "[cloudflare zone domain]"
allowed-tools: Bash(python3 *) Bash(bpctl *) Read Write Edit Grep Glob WebFetch
---

# Cloudflare → BytePlus migration

There is no migration program. **You carry out the migration** with two
primitives from the `hellobp2` skill, and this skill is the procedure:

```bash
bpctl cf <method> <path>                  # Cloudflare API v4 — the token never touches the command line
bpctl call <service> <Action>             # any BytePlus API action, correctly signed
bpctl call <service> <ListAction> --all   # every page of a list
```

Each stage names the calls to make, the payloads to send, and the rules for
deciding what carries across. Load a stage's reference when you reach it.

## Before you start

1. `bpctl whoami` — BytePlus keys present, Cloudflare token present.
2. `bpctl probe --save` once per BytePlus account.
3. Create `migration/<zone>/`. Every stage writes its result there —
   `inventory.json`, `plan.json`, `run.json`, `cutover.json` — so each step can
   be reviewed, and a stopped migration can be resumed.

Nothing reaches BytePlus or Cloudflare without `--live`. Run every write stage as
a dry run first, show the user the exact payloads, and get a clear yes before
repeating it live.

## Stages

| # | Stage | Touches | Reference |
|---|---|---|---|
| 1 | Extract the zone | Cloudflare — reads only | [extract.md](references/extract.md) |
| 2 | Plan and report coverage | nothing — local files | [mapping.md](references/mapping.md) |
| 3 | Execute, then validate | BytePlus CDN — creates policies and domains | [execute.md](references/execute.md) |
| 4 | Certificates | Certificate Service, CDN, Cloudflare DNS | [ssl.md](references/ssl.md) |
| 5 | DNS cutover | Cloudflare DNS — moves real traffic | [cutover.md](references/cutover.md) |
| 6 | WAF IP rules | Cloudflare firewall, BytePlus WAF or CDN | [waf.md](references/waf.md) |

**Certificates come before cutover.** Pointing DNS at a domain without a working
certificate breaks HTTPS for every visitor. Stages 4 and 6 also stand alone —
run either without a full migration.

**Stop after stage 2** and walk the user through the coverage report. It is the
last point at which nothing has been created.

## Reporting

Every feature the zone uses goes in exactly one bucket, and you report all three:

- **Auto** — configured by stage 3.
- **Manual** — has a BytePlus equivalent, but needs a person or another stage.
- **Unsupported** — Cloudflare-only: Waiting Rooms, Spectrum, Zaraz, Page Shield,
  Cache Reserve, Network Error Logging, Access / Zero Trust, Custom Hostnames,
  DDoS L7 overrides, Snippets, and DNS record types BytePlus cannot host.

A migration that drops the manual and unsupported buckets looks successful and
isn't. **Never tell the user a zone is migrated because API calls succeeded** —
say what was created, what wasn't, and what is still theirs to do.

## Rules that override instinct

Each of these has cost a real debugging session. The stage references repeat
them where they apply.

| Rule | Why |
|---|---|
| Access rules are `{"Switch": true, "RuleType": "deny", "Ip": [...]}` | `blacklist` is rejected; `FilterType` / `Filters` don't exist and silently apply nothing |
| Never map a Cloudflare **allow** rule to `"RuleType": "allow"` | Cloudflare allow skips checks; a BytePlus allow list blocks everyone else — an outage |
| Encryption policy settings go inside `HTTPS` (`HTTP2` bool, lowercase `TlsVersion`, `ForcedRedirect`) | HelloBP v1's flat shape doesn't match the API |
| "Always use HTTPS" is `HTTPS.ForcedRedirect`, never `HttpForcedRedirect` | `HttpForcedRedirect` redirects HTTPS → HTTP |
| HTTP/2 off whenever WebSocket is on | they are incompatible |
| Redirects use `RedirectionRewrite` with exact paths; wildcards go to the Rules Engine | there is no `UrlRedirect` field |
| `AddTemplateDomain` — one `Domain` per call; `UpdateTemplateDomain` — a `Domains` array | the two actions differ |
| Bind the encryption policy with the certificate (`UpdateTemplateDomain`, HTTPS on) | BytePlus binds `CipherTemplateId` together with `CertId` |
| Publish templates with `LockTemplate` | what BytePlus's official Terraform provider does; locked templates are immutable |
| Read ruleset phases at `…/phases/<phase>/entrypoint` | without `/entrypoint` every phase looks empty |
| WAF `ListDomain` needs `Page`, `PageSize` and `Region` | without them an empty result means nothing |
| WAF `AclType` is `Block` / `Allow`; `HostAddType: 3`; `IpAddType: 2` or `3` | lowercase and v1's `1` values are wrong |
| Use `--all` on every list | lists stop at 10 rows without saying so |
| `BatchDeployCert` takes `Domain` as a comma string, at most 50 | not an array |
| Strip the query string from Logpush destinations | it contains credentials |

Every payload shape in the stage references was checked field by field against
BytePlus's official SDK models, SDK example payloads and Terraform provider.
The live API is still the final word — when it rejects a field, report the error
exactly rather than improvising another shape.

For signing errors, general BytePlus API gotchas and the CDN Rules Engine, see
the `hellobp2` skill's references.
