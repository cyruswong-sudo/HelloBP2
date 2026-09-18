# HelloBP2 — Credential SOP

How to hand BytePlus and Cloudflare credentials to an AI agent safely, when you
work across several accounts.

Three rules cover almost everything:

1. **Never paste a secret into a chat window.**
2. **One file per account**, and name it on every command.
3. **Check `whoami` before you write anything.**

---

## 1. Never paste a secret into chat

A pasted token doesn't just sit in the conversation. It is:

- written to a **plain-text transcript file** on your machine that stays there
  after the session ends
- **re-sent to the model on every turn** of that conversation
- **carried along** if the session is exported or shared

There is no unsend. Once a secret has been in a chat, the only fix is to
**revoke and reissue it**.

Instead: put the secret in a file yourself, and tell the agent *where it is*,
not *what it is*.

> "Use `~/.hellobp/acme.json`" — good.
> "Here's the token: `cfut_…`" — now you have to rotate it.

---

## 2. One file per account

Keep every account in its own file under `~/.hellobp/`.

### Add an account

Paste this into your terminal. It prompts for each value, **never echoes them to
the screen, and never writes them into your shell history.**

```bash
umask 077
mkdir -p ~/.hellobp
printf 'Account name (e.g. acme): ';               read -r name
printf 'BytePlus Access Key: ';                    read -rs ak; echo
printf 'BytePlus Secret Key: ';                    read -rs sk; echo
printf 'Cloudflare API Token (Enter to skip): ';   read -rs cf; echo
printf 'Region [ap-southeast-1]: ';                read -r region
: "${region:=ap-southeast-1}"
printf '{\n  "access_key": "%s",\n  "secret_key": "%s",\n  "region": "%s",\n  "cloudflare": {"api_token": "%s"}\n}\n' \
  "$ak" "$sk" "$region" "$cf" > ~/.hellobp/"$name".json
chmod 600 ~/.hellobp/"$name".json
unset ak sk cf
echo "saved ~/.hellobp/$name.json"
```

One file holds **both** the BytePlus keys and the Cloudflare token, so switching
accounts switches both together.

### Use an account

Put `--config` on every command:

```bash
bpctl whoami --config ~/.hellobp/acme.json
bpctl call cdn ListCdnDomains --all --config ~/.hellobp/acme.json
```

Or just tell your agent once: **"Use `~/.hellobp/acme.json` for this job."**

---

## 3. Check before you write

Run this before any command that creates or changes something:

```bash
bpctl whoami --config ~/.hellobp/acme.json
```

```
access key : AKAP****************0NzE   [/Users/you/.hellobp/acme.json]
region     : ap-southeast-1             [/Users/you/.hellobp/acme.json]
api token  : cfut****n052               [/Users/you/.hellobp/acme.json]
```

Two things to confirm:

- the **masked key** is the account you meant
- the **path in brackets** is your account file — not `~/.byteplus/config`

If the path is wrong, the command would run against the wrong customer.

**A read-only sanity check is even better** — the domain list tells you
instantly whose account you're on:

```bash
bpctl call cdn ListCdnDomains --all --config ~/.hellobp/acme.json
```

---

## 4. Keep the default account empty

Without `--config`, `bpctl` falls back to `~/.byteplus/config` and
`~/.cloudflare/config` — a "default account" that's easy to forget about.

If you work across several customers, **don't keep credentials there**:

```bash
mv ~/.byteplus/config ~/.hellobp/personal.json   # then always pass --config
```

---

## 5. Scope and expiry

Ask for the smallest credential that does the job.

| Credential | Scope it to | Expiry |
|---|---|---|
| Cloudflare API token | `Zone:DNS:Edit` on the **one zone** being migrated | end of the migration window |
| BytePlus AK/SK | a sub-account with CDN / Certificate / DNS / WAF only | rotate quarterly |

Create Cloudflare tokens at **dash.cloudflare.com/profile/api-tokens** using the
**"Edit zone DNS"** template, with Zone Resources set to that single zone.

An **expired token is harmless**; an unscoped, non-expiring one is the problem.

---

## 6. When the job is done

```bash
rm ~/.hellobp/acme.json
```

Then revoke the credential in the provider's console. Deleting your copy does
not disable the token — **only revoking does.**

---

## 7. If a secret leaks

A secret is leaked if it was pasted into chat, committed to git, put in a
ticket, screenshotted, or sent over IM.

1. **Revoke it first.** Don't investigate first — revoke, then investigate.
2. Issue a replacement and update the account file.
3. Tell whoever owns the account.

For chat leaks, transcripts live in `~/.claude/projects/<project>/*.jsonl` as
plain text. Deleting them lowers exposure but **does not** undo the leak —
revocation is what counts.

---

## Quick reference

| Task | Command |
|---|---|
| Add an account | the snippet in §2 |
| Check which account is active | `bpctl whoami --config ~/.hellobp/<name>.json` |
| Run a command | `bpctl <command> --config ~/.hellobp/<name>.json` |
| List accounts | `ls ~/.hellobp/` |
| Retire an account | `rm ~/.hellobp/<name>.json`, then revoke in the console |

**Rule of thumb:** tell the agent *where* the credential is, never *what* it is.
