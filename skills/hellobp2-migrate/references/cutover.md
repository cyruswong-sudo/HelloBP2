# Stage 5 — Cut DNS over

Switching DNS moves real traffic. Do it last, after the certificate stage, and
only with the user's explicit go-ahead for each zone.

## 1. Check every hostname is ready

```bash
bpctl call cdn ListCdnDomains --all
```

For each hostname in the plan's `cdn_domains`, record the BytePlus `Cname` and
`Status`:

| Condition | Ready? |
|---|---|
| domain missing from the listing | no — the execute stage hasn't run live |
| no `Cname` yet | no — BytePlus hasn't assigned one |
| `Status` is not `online` | no — wait for deployment |
| `online` with a `Cname` | yes |

Also confirm, before offering to switch anything:

- **A certificate covers the hostname** and is bound to it — see `ssl.md`.
  Cutting over without one breaks HTTPS for every visitor.
- **No placeholder origins.** A Worker group still pointing at `127.0.0.1`
  will serve errors.
- **The listing is complete** (`complete: true`).

Write `migration/<zone>/cutover.json` with one entry per hostname: current
records, BytePlus CNAME, status, ready, and the blocker when not ready.

## 2. Give the user the table

| Hostname | Now | Change to | Ready |
|---|---|---|---|
| `www.example.com` | CNAME `origin.example.net` (proxied) | CNAME `www.example.com.<byteplus-cname>` | ✓ |
| `example.com` | A `192.0.2.10` (proxied) | CNAME `example.com.<byteplus-cname>` | ✓ |

**Apex records.** An apex can't hold a plain CNAME. Cloudflare flattens apex
CNAMEs automatically; other DNS providers need an ALIAS or ANAME record.

If DNS isn't hosted on Cloudflare, stop at this table — the user makes the
change at their provider.

## 3. Apply it in Cloudflare DNS

Only with a Cloudflare token that has **DNS:Edit**, and only after the user
agrees. Everything below is a dry run until `--live`.

**First, save the current records** — you need them to roll back:

```bash
bpctl cf get /zones/<zone_id>/dns_records -q name=www.example.com
```

Write the A/AAAA/CNAME records for that name into `cutover.json` under
`previous`, with their ids.

**If the name has exactly one CNAME**, update it in place:

```bash
bpctl cf patch /zones/<zone_id>/dns_records/<record_id> \
  --body '{"type":"CNAME","name":"www.example.com","content":"<byteplus cname>","proxied":false,"ttl":300}'
```

**If the name has A or AAAA records**, they must go before a CNAME can exist:

```bash
bpctl cf delete /zones/<zone_id>/dns_records/<record_id>      # once per A/AAAA record
bpctl cf post   /zones/<zone_id>/dns_records \
  --body '{"type":"CNAME","name":"example.com","content":"<byteplus cname>","proxied":false,"ttl":300}'
```

If the create fails after a delete, **that hostname now has no record**.
Restore the entries from `previous` immediately, then report what happened.

Rules for every record you write:

- **`"proxied": false`** — DNS-only. Proxying through Cloudflare in front of
  BytePlus defeats the migration.
- **`"ttl": 300`** — short, so a rollback propagates quickly. Raise it after
  the migration is confirmed.
- One hostname at a time; confirm each before moving on.

## 4. Confirm it worked

```bash
dig +short CNAME www.example.com
curl -sI https://www.example.com
```

The CNAME should be the BytePlus value, and the response should come from
BytePlus with a valid certificate.

## Rolling back

Delete the CNAME and recreate each record in `previous` with its original type,
content, TTL and proxy flag. With a 300-second TTL, most resolvers follow within
minutes.
