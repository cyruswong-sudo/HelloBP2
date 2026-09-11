# HelloBP 2.0

An **agent-agnostic** toolkit for working with **BytePlus CDN, WAF, DNS, and
certificates** through their OpenAPIs, and for migrating workloads from
Cloudflare.

Works with Claude Code, Codex, Cursor, Windsurf, Copilot, Cline, Gemini CLI —
or any agent that reads `AGENTS.md`. The core is a plain CLI, so it also works
with no agent at all.

HelloBP 2.0 is the successor to HelloBP v1.6, which was a Flask web app you
clicked through. This one has no server and no UI — you talk to your agent, and
it drives the APIs.

## Setup

**These are terminal commands.** Run them in a shell, not in your agent's chat
window — your agent cannot clone a repo it has not got yet.

### 1. Get it and install

```bash
git clone https://github.com/cyruswong-sudo/HelloBP2.git
cd HelloBP2
./install.sh
```

Needs Python 3.9+ and nothing else. The installer puts `bpctl` on your PATH and
writes instructions for whichever agents it finds — Claude Code, Codex, Cursor,
Windsurf, Copilot, Cline, Gemini CLI. Anything else reads `AGENTS.md`, which is
self-contained and always installed.

Expected output ends with `Verify:` and a couple of commands.

### 2. Check it landed

```bash
bpctl --version
```

Expected: `bpctl 2.1.0`

If you get `command not found`, `~/.local/bin` is not on your PATH — the
installer printed the exact line to fix it. Or skip PATH entirely and use
`python3 skills/hellobp2/scripts/bpctl.py` anywhere you would use `bpctl`.

### 3. Add your BytePlus credentials

Get an Access Key and Secret Key from the BytePlus Console under
**Access Control → API Access Key**, then:

```bash
mkdir -p ~/.byteplus && chmod 700 ~/.byteplus
printf '{"ak":"YOUR_ACCESS_KEY","sk":"YOUR_SECRET_KEY","region":"singapore"}\n' > ~/.byteplus/config
chmod 600 ~/.byteplus/config
```

Use `ap-southeast-3` instead of `singapore` for Indonesia, Philippines, Vietnam
or Taiwan accounts. Environment variables (`BYTEPLUS_ACCESS_KEY` /
`BYTEPLUS_SECRET_KEY`) work too and take priority.

```bash
bpctl whoami
```

Both keys should show masked, with `~/.byteplus/config` named as the source.

### 4. Verify against your account

```bash
bpctl probe --save
```

Read-only. All four services should report `authenticated`.

### 5. Start a new agent session

Instructions are loaded at startup, so **restart your agent** — an already-open
session will not see them.

## Then just ask

There are no numbered steps to follow from here. Say what you want:

> "What CDN domains do I have on BytePlus?"
>
> "Which of my domains are missing a certificate?"
>
> "Block these IP ranges on the WAF."
>
> "What does Cloudflare's rate limiting map to on BytePlus?"
>
> "Migrate example.com from Cloudflare to BytePlus."

For a migration, also put a Cloudflare API token in `~/.cloudflare/config` as
`{"api_token": "..."}` — see [GETTING-STARTED.md §3](GETTING-STARTED.md#3-credentials).

Or drive it yourself — everything the agent can do, you can:

```bash
bpctl call cdn ListCdnDomains
bpctl call dns ListZones -p PageSize=10
bpctl cf get /zones                      # Cloudflare, same output shape
```

`bpctl cf` calls the Cloudflare API v4 with a bearer token from
`$CLOUDFLARE_API_TOKEN` or `~/.cloudflare/config`. It exists so the token never
reaches a shell history or an agent transcript, and so writes are simulated
until you pass `--live`.

Full manual: **[GETTING-STARTED.md](GETTING-STARTED.md)**. Test plan:
**[TESTING.md](TESTING.md)**.

## Installer options

```bash
./install.sh --list        # what it would detect, changes nothing
./install.sh --dry-run     # every action it would take, changes nothing
./install.sh --uninstall   # remove everything it installed
```

Everything it creates symlinks back to **the clone you ran it from**, so
`git pull` in that clone is the only update step.

> If you keep more than one checkout, the symlinks keep pointing at whichever
> one you last installed from — edits to the other copy will appear to do
> nothing. `./install.sh --list` does not show this; check with
> `ls -l $(command -v bpctl)`, and re-run `./install.sh` from the clone you
> actually want.

## What's in it

| | |
|---|---|
| ✅ | Request signing for all four BytePlus services, verified byte-for-byte against the official SDK |
| ✅ | `bpctl call` — every CDN, WAF, DNS and Certificate action; `--all` reads every page |
| ✅ | `bpctl cf` — the Cloudflare API, with the token kept off the command line |
| ✅ | Dry-run by default for every write, structured JSON errors, `bpctl probe` verification |
| ✅ | **Migration playbooks** — extract, plan, execute, validate, certificates, DNS cutover, WAF IP rules |
| ⬜ | Action catalogs |

Everything v1's web UI did, your agent now does by following the playbooks in
`skills/hellobp2-migrate/`. There is deliberately no migration program: the
skill *is* the procedure, and the agent carries it out with `bpctl`.

## Safety model

Reads run against the live account. Writes are **simulated** unless `--live` is
passed. An action counts as a read only if its name starts with `Describe`,
`List`, `Query`, `Get`, or `Check` — anything unrecognised is treated as a write.

```bash
bpctl call cdn AddCdnDomain --body '{...}'          # simulated
bpctl call cdn AddCdnDomain --body '{...}' --live   # real
```

## Layout

```
HelloBP2/
├── install.sh                 agent-agnostic installer
├── AGENTS.md                  portable instructions — any agent
├── .claude-plugin/plugin.json
├── skills/
│   ├── hellobp2/              the skill — self-contained
│   │   ├── SKILL.md
│   │   ├── references/        auth · gotchas · rule-engine
│   │   └── scripts/
│   │       ├── bpctl.py       entry point
│   │       └── bpctl/         signer · services · config · client · cloudflare · probe · cli
│   └── hellobp2-migrate/      the migration procedure — no code
│       ├── SKILL.md           stage order, reporting, overriding rules
│       └── references/        extract · mapping · execute · ssl · cutover · waf
└── tests/                     77 tests, incl. signature parity with the SDK
```

## Tests

```bash
python3 -m pytest tests/ -q
```

The parity tests compare `bpctl`'s `Authorization` header against
`byteplus_sdk.auth.SignerV4` for the same inputs and a frozen clock. They skip
automatically when the SDK is absent; to run them:

```bash
python3 -m venv /tmp/sdkvenv
/tmp/sdkvenv/bin/pip install byteplus-sdk pycryptodome pytz pytest
/tmp/sdkvenv/bin/python -m pytest tests/ -q
```

## Install as a Claude Code plugin

An alternative to `install.sh` for Claude Code users only. This repo carries
`.claude-plugin/marketplace.json`, so the skill installs from a local checkout
or straight from the Git remote:

```
/plugin marketplace add https://github.com/cyruswong-sudo/HelloBP2
```

`/plugin` is an interactive terminal dialog — it is unavailable in the Claude
desktop app's Code tab, so run it from a `claude` terminal session. `install.sh`
has no such restriction.
