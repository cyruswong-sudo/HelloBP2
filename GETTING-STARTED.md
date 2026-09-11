# HelloBP 2.0 — Manual

Drive **BytePlus CDN, WAF, DNS and certificates** from any coding agent, and
migrate workloads off Cloudflare.

This guide takes you from nothing to a validated install in about fifteen
minutes. Work down it in order; each section assumes the one before it passed.

| | |
|---|---|
| **Install** | [§2](#2-install) — one command, any agent |
| **Prove the code is sound** | [§4](#4-run-the-test-suite) — 77 tests, no credentials needed |
| **Prove your account works** | [§5](#5-validate-against-your-byteplus-account) — the validation ladder |
| **Actually use it** | [§6](#6-how-you-actually-use-it) — the part that surprises people |
| **When it breaks** | [§9](#9-troubleshooting) |

---

## 1. What this is

BytePlus OpenAPIs cannot be called with a bearer token. Every request needs an
HMAC-SHA256 signature whose credential scope changes per product. Get the region,
the clock, or a pasted key slightly wrong and every one of them fails
identically — `401 SignatureDoesNotMatch` — with no clue which.

HelloBP 2.0 packages that problem away. It ships two things:

- **`bpctl`** — a signed CLI reaching every BytePlus CDN, WAF, DNS and
  Certificate API action. Stdlib-only Python, no dependencies. Its signing is
  verified byte-for-byte against the official BytePlus SDK.
- **Agent instructions** — so your agent drives those APIs with the right
  context, gotchas and safety rails already loaded.

### It is not tied to one agent

The CLI is a plain executable. Anything that can run a shell can use it —
including you, with no agent at all. The instructions ship in whichever format
your agent already reads, and `AGENTS.md` is the portable fallback that needs no
adapter.

### How it relates to V1

V1 — the Flask web app, in its own separate repository — is **unchanged and still works**.
V2 lives in this separate repository and shares no code with it.

|  | V1 | V2 |
|---|---|---|
| Interface | Web UI, numbered sections | Your agent, or the CLI |
| Needs a server running | Yes | No |
| Works with | A browser | Any agent, or none |
| Cloudflare migration | Guided web sections | Your agent follows staged playbooks (§6) |

Use V1 for the guided web workflow. Use V2 when you want an agent or a script
doing the work.

### Status

**V2 covers everything V1 did.** Signing and the API callers are code. The
migration itself — extraction, planning, execution, validation, certificates,
cutover and WAF rules — is a set of playbooks your agent carries out. Action
catalogs are the one planned addition.

---

## 2. Install

### Prerequisites

- **Python 3.9+** — check with `python3 --version`
- **A BytePlus Access Key and Secret Key** — BytePlus Console → Access Control →
  API Access Key
- This repository checked out locally

Nothing to `pip install` for normal use.

### One command

From a fresh clone:

```bash
git clone https://github.com/cyruswong-sudo/HelloBP2.git
cd HelloBP2 && ./install.sh
```

Or, if you already have the repo, run `./install.sh` from its root.

It does exactly two things:

1. **Puts `bpctl` on your PATH** at `~/.local/bin/bpctl`.
2. **Writes agent context** so your agent knows the CLI exists — detecting which
   agents you actually have and installing only for those.

Everything it creates is a symlink or a delimited block pointing back at this
checkout, so **`git pull` is your only update step**. Re-running is idempotent.

### Where it installs

| Agent | Target |
|---|---|
| Claude Code | `~/.claude/skills/hellobp2` + `hellobp2-migrate` (symlinks) |
| Codex CLI | block in `~/.codex/AGENTS.md` |
| Cursor | block in `~/.cursor/rules/hellobp2.md` |
| Windsurf | block in `~/.windsurf/rules/hellobp2.md` |
| GitHub Copilot | block in `.github/copilot-instructions.md` |
| Cline | block in `~/.clinerules/hellobp2.md` |
| Gemini CLI | block in `GEMINI.md` |
| **Anything else** | `AGENTS.md` — always installed |

Adapters get a short block *pointing at* `AGENTS.md` rather than a copy of it,
so the seven files cannot drift apart. `AGENTS.md` itself is self-contained:
signing, credentials, the safety model, every gotcha and the Cloudflare mapping.

**If your agent is not listed, it is still supported** — most agents read
`AGENTS.md`, and that is installed unconditionally.

### Options

```bash
./install.sh --list                 # show what would be detected, change nothing
./install.sh --dry-run              # print every action, change nothing
./install.sh --scope project        # install into ./ instead of $HOME
./install.sh --agent claude,cursor  # only these
./install.sh --agent all            # every adapter, detected or not
./install.sh --uninstall            # remove everything it installed
```

Use `--scope project` for a repo where the whole team should pick up HelloBP,
then commit the generated files. `--uninstall` strips the marked blocks and
removes the symlinks; it never touches your checkout.

### Confirm it worked

```bash
bpctl --version
```

Expected: `bpctl 2.1.0`

If you get `command not found`, `~/.local/bin` is not on your PATH — the
installer prints the exact line to add. You can bypass PATH entirely at any
time; the script needs no installation:

```bash
python3 skills/hellobp2/scripts/bpctl.py --version
```

**Claude Code users:** start a **new** session, then run `/skills`. You should
see `hellobp2` and `hellobp2-migrate`. Skills are read at startup, so an
existing session will not show them.

> An alternative for Claude Code only: the repo root carries
> `.claude-plugin/marketplace.json`, so `/plugin marketplace add <REPO>` works.
> Note `/plugin` is an interactive terminal dialog — it is unavailable in the
> Claude desktop app's Code tab, so run it from a `claude` terminal.
> `install.sh` has no such restriction.

---

## 3. Credentials

Resolved in this order, first hit wins per field:

```
explicit flag  →  BYTEPLUS_* env var  →  ~/.byteplus/config  →  ./bpctl.json
```

```bash
export BYTEPLUS_ACCESS_KEY=...
export BYTEPLUS_SECRET_KEY=...
export BYTEPLUS_REGION=singapore   # optional; singapore is the default
```

`~/.byteplus/config` is the same file the official BytePlus SDK reads, so a
machine already set up for the SDK works with no extra steps.

**Region:** `singapore` is the default. Indonesia, Philippines, Vietnam and
Taiwan accounts need `BYTEPLUS_REGION=ap-southeast-3`.

### Cloudflare

Migrations also need a Cloudflare API token:

```bash
mkdir -p ~/.cloudflare && chmod 700 ~/.cloudflare
printf '{"api_token":"YOUR_TOKEN"}\n' > ~/.cloudflare/config && chmod 600 ~/.cloudflare/config
```

`CLOUDFLARE_API_TOKEN` in the environment works too. Extraction needs Zone:Read,
Zone Settings:Read, DNS:Read, Page Rules:Read, Firewall Services:Read and Zone
WAF:Read. Add DNS:Edit only to publish certificate validation records or cut DNS
over.

Credentials are never printed, echoed or logged. `bpctl whoami` shows masked
values and reports the source of each — use it instead of inspecting the
environment yourself.

---

## 4. Run the test suite

No credentials needed. This validates the code, not your account.

The signature-parity tests compare `bpctl` against the **official BytePlus SDK**,
which needs `pycryptodome` and `pytz`. Those are deliberately *not* bpctl
dependencies, so they go in a throwaway virtualenv:

```bash
python3 -m venv /tmp/sdkvenv
/tmp/sdkvenv/bin/pip install -q byteplus-sdk pycryptodome pytz pytest
```

Run:

```bash
/tmp/sdkvenv/bin/python -m pytest tests/ -v
```

**Expected: 77 passed.**

### Read the result carefully

The tests that matter are the twelve named:

- `test_authorization_matches_official_sdk[...]`
- `test_content_hash_matches_official_sdk[...]`

They drive `bpctl` and the official SDK with identical inputs and a frozen clock,
and require the resulting `Authorization` headers to match **byte for byte**
across all four services, two regions, GET and POST, nested bodies and
percent-encoding edge cases.

> **If you see `12 skipped`**, the SDK is missing from the interpreter you used —
> which means you have *not* tested the one thing worth testing. Redo the venv
> above. Your system `python3` will not have it.

---

## 5. Validate against your BytePlus account

Work down this ladder. **Stop at the first step that misbehaves.**

### 5a. Check what's loaded — no network

```bash
bpctl whoami
```

Keys shown masked, region shown, and the source of each value labelled (`env`, a
config file, or a default). Nothing is sent anywhere.

### 5b. Probe — read-only, the most important step

```bash
bpctl probe --save
```

This is the single most valuable command in V2. It:

- tries every candidate host and both signing variants for each service
- reads your actual WAF rules back to settle enum values where the BytePlus docs
  and HelloBP V1 disagree
- writes the answers to `~/.byteplus/verified.json`, which later commands read

Success looks like:

```
Probing 4 service(s) in region 'singapore', read-only.

  ✓ cdn          cdn.byteplusapi.com  (Host not signed)
  ✓ waf          waf.byteplusapi.com  (Host not signed)
  ✓ dns          dns.byteplusapi.com  (Host not signed)
  ✓ certificate  open.byteplusapi.com (Host not signed)

Disputed facts
  waf.host: RESOLVED -> waf.byteplusapi.com
  ...
```

Every call it makes is a List/Describe/Get. Nothing is created or modified.

> **`AclType` resolves on any account.** `HostAddType` and `IpAddType` can only be
> read off an existing WAF ACL rule, so on an account with none they stay
> unresolved — create one throwaway rule in the console and re-run to settle them.

**Share this output with the team.** Those resolutions are what the WAF half of
the migration depends on.

### 5c. Read real data

```bash
bpctl call cdn ListCdnDomains
bpctl call dns ListZones -p PageSize=10
bpctl call cdn ListCdnCertInfo
```

Output is always JSON on stdout, on success and on failure. **The live payload
is under `response`** — branch on `ok`.

### 5d. Confirm the safety rail holds

Run a **write** action without `--live`:

```bash
bpctl call cdn SubmitRefreshTask -p Type=file -p Urls=https://example.com/x.png
```

This must return `"dry_run": true` and send nothing.

> If it does anything else, **stop and report it**. The dry-run default is the
> guardrail that makes the rest safe to hand to an agent.

### 5e. One real write

Only after 5d passes. Choose something reversible — a cache purge on a test URL,
never a domain or a policy:

```bash
bpctl call cdn SubmitRefreshTask -p Type=file -p Urls=https://yourdomain.com/test.png --live
```

---

## 6. How you actually use it

This is the part that catches people, so it is worth stating plainly.

**V1 was a web app: you ran a server, opened a browser, and clicked through
numbered sections. The UI was the workflow.**

**V2 has nothing to open.** `SKILL.md` and `AGENTS.md` are not programs you run —
they are instructions for your *agent*. Once installed, the agent is the
interface. You describe what you want in plain language and it chooses the
`bpctl` calls.

So there is no "step 1 of 7" to follow. Instead:

> "What CDN domains do I have on BytePlus?"
>
> "Which of my domains are missing a certificate?"
>
> "Block these three IP ranges on the WAF."
>
> "What would it take to move `example.com` off Cloudflare?"

### Migrating a zone

Ask in plain language — *"Migrate example.com from Cloudflare to BytePlus"* —
with both credentials in place. The agent works through six stages and keeps each
stage's output in `migration/<zone>/` so you can review it:

| # | Stage | What you see |
|---|---|---|
| 1 | Extract | what the zone uses, and any token permission gaps |
| 2 | Plan | a coverage report: auto / manual / unsupported — **the agent stops here for your review** |
| 3 | Execute | dry-run payloads first; a live run only after you agree |
| 4 | Certificates | orders, DNS validation, deployment |
| 5 | Cutover | a hostname → CNAME table, applied only with your go-ahead |
| 6 | WAF | IP allow/block rules, with every skipped rule explained |

Certificates and WAF rules also work on their own — *"Issue a wildcard
certificate for example.com and deploy it"*, *"Move our Cloudflare IP block list
to the WAF."*

### Validate it as a skill, not just a CLI

The CLI working does not prove the *instructions* work. In a fresh session, ask a
question **without naming the tool**:

> "What CDN domains do I have on BytePlus?"

The agent should recognise the task, load the HelloBP context, find the script
and run `ListCdnDomains` — with no mention of `bpctl` from you.

If it does not trigger, that is a real finding: the `description` frontmatter
needs tuning. **Report it rather than working around it** — working around it
hides the bug.

### Driving it yourself

Everything the agent can do, you can do. The generic caller reaches every action
in all four services, including ones documented nowhere here:

```bash
bpctl call waf CreateAclRule --body '{"Name":"block-abuse","AclType":"Block"}'
bpctl call dns ListRecords -p ZID=12345 -p PageSize=100
bpctl call cdn UpdateCdnConfig --body-file /tmp/config.json
```

If you are unsure an action exists, call it and read the error rather than
guessing.

---

## 7. Safety model

**Reads run live. Writes are simulated unless you pass `--live`.**

An action counts as a read only if its name starts with `Describe`, `List`,
`Query`, `Get` or `Check`. Anything unrecognised is treated as a **write** — the
fail-safe direction.

```bash
bpctl call cdn AddCdnDomain --body '{...}'          # simulated
bpctl call cdn AddCdnDomain --body '{...}' --live   # real
```

`BPCTL_LIVE=1` also arms writes, but only clearly affirmative values (`1`,
`true`, `yes`, `on`) count. Anything else stays dry-run.

### For agents

Two rules, both load-bearing:

1. **Confirm with the user before the first `--live` call of a session**, and
   state exactly what will be created or changed.
2. **Do not add `--live` because a dry run reported `"dry_run": true`.** That is
   the tool working as designed, not an error to route around.

---

## 8. Command reference

```bash
bpctl whoami                        # active account and mode, masked
bpctl services                      # the four services and their signing details
bpctl probe [service ...] --save    # verify signing against a live account
bpctl call <service> <Action>       # invoke any BytePlus API action
bpctl call <service> <List…> --all  # every page of a list
bpctl cf <method> <path>            # the Cloudflare API, token kept off the command line
bpctl catalog <service>             # list known actions (not built yet)
```

Services: `cdn`, `waf`, `dns`, `certificate` (aliases: `cert`, `certs`).

| Service | Host | Signing name | Version |
|---|---|---|---|
| `cdn` | `cdn.byteplusapi.com` | `CDN` | 2021-03-01 |
| `waf` | `waf.byteplusapi.com` | `waf` | 2023-12-25 |
| `dns` | `dns.byteplusapi.com` | `DNS` | 2018-08-01 |
| `certificate` | `open.byteplusapi.com` | `certificate_service` | 2021-06-01 |

Payload options for `call`:

| Flag | Use |
|---|---|
| `--body '{"K":"V"}'` | Full JSON payload |
| `-p Key=Value` | One field, repeatable. JSON-looking values are parsed |
| `--body-file path.json` | Large payloads |
| `--method GET\|POST` | Override the chosen HTTP method |
| `--host <host>` | Override the API host |
| `--region <region>` | Override the signing region |
| `--timeout <seconds>` | Request timeout |
| `--all` | Read every page of a List action |
| `--live` | Actually execute a write |

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `bpctl: command not found` | `~/.local/bin` is not on your PATH. Add it, or call `python3 skills/hellobp2/scripts/bpctl.py` directly. |
| `401 SignatureDoesNotMatch` | Four usual causes: wrong region; system clock more than ~5 min off UTC; whitespace in a pasted key; wrong host. Run `bpctl probe` — it distinguishes them and says which it ruled out. |
| `401 InvalidAccessKey` | The key is not recognised at all. Confirm it is active and that both halves are from the same pair. |
| `403` | Signature was accepted; the **account** lacks permission for that action. Check the key's policy. |
| `404 InvalidActionOrVersion` | No such action at that API version. Check spelling against the product docs. |
| `12 skipped` in the test run | The official SDK is missing from that interpreter. Redo the venv in §4. |
| Skill does not appear | Start a **new** session — instructions are read at startup. Confirm `~/.claude/skills/hellobp2/SKILL.md` resolves. |
| Agent ignores the skill | The `description` frontmatter is not matching your phrasing. Report it; do not work around it. |
| `bpctl catalog` returns an error | Expected — action catalogs aren't built yet. Use `bpctl call` with any action name. |
| Indonesia / Philippines / Vietnam / Taiwan account | Use `BYTEPLUS_REGION=ap-southeast-3`. |

---

## 10. Known limitations

- **The migration is agent-run, not a program.** The `hellobp2-migrate`
  playbooks are the procedure and the agent follows them with `bpctl`. Review the
  stage 2 coverage report before anything is created.
- **No action catalogs yet.** `bpctl call` reaches everything regardless; you
  just need the action name from the BytePlus docs.
- **Two WAF enum values are unverified** — `HostAddType` and `IpAddType`. They
  can only be read off an existing ACL rule; until then the documented values
  are used and flagged. `AclType` is settled (`Block` / `Allow`). An account
  without WAF enabled can't create ACL rules at all — `bpctl call waf ListDomain`
  shows `"Data": null`.
- **BytePlus DNS does not support** `HTTPS`, `SVCB`, `DS`, `DNSKEY`, `TLSA`,
  `SSHFP`, `LOC`, `NAPTR`, `CERT`, `URI` or `SMIMEA` records. `HTTPS`/`SVCB`
  matters most — dropping one silently disables ECH and ALPN hints. Raise it
  before cutover.
- **Cloudflare features with no BytePlus equivalent**: Waiting Rooms, Spectrum,
  Zaraz, Page Shield, Cache Reserve, Network Error Logging, Access / Zero Trust,
  Custom Hostnames (SSL for SaaS), DDoS L7 overrides.

---

## 11. Reference documentation

Bundled with the skill at `skills/hellobp2/references/`:

| File | Contents |
|---|---|
| `auth.md` | The signing algorithm, regions, and how to decode a 401 |
| `gotchas.md` | Undocumented API behaviour, per service. **Read before writing any config payload** |
| `rule-engine.md` | CDN Rules Engine conditions, operators, actions, and the Cloudflare mapping |

Plus:

| File | Contents |
|---|---|
| `AGENTS.md` | Portable agent instructions — the vendor-neutral entry point |
| `skills/hellobp2-migrate/SKILL.md` | Migration stage order, reporting rules, rules that override instinct |
| `skills/hellobp2-migrate/references/` | One playbook per stage: `extract` · `mapping` · `execute` · `ssl` · `cutover` · `waf` |

When a payload's exact field names are unclear, fetch the official docs rather
than guessing — each service's `doc` URL is in `bpctl services --json`.
