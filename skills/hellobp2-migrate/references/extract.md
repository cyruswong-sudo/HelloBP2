# Stage 1 — Extract the Cloudflare zone

Everything in this stage is a read. Write the result to
`migration/<zone>/inventory.json`; every later stage works from that file.

## 1. Check the token

```bash
bpctl cf get /user/tokens/verify
bpctl cf get /accounts/<account_id>/tokens/verify     # account-scoped tokens
```

`result.status` should be `active`. A 401 or 403 here means the token itself is
wrong — stop and say so, because nothing below will work.

Permissions for a full extraction: **Zone:Read, Zone Settings:Read, DNS:Read,
Page Rules:Read, Firewall Services:Read, Zone WAF:Read**. Add **DNS:Edit** only
for publishing certificate validation records or cutting DNS over.

## 2. Find the zone id

```bash
bpctl cf get /zones -q name=example.com
```

Use `result[0].id` — 32 hex characters.

## 3. Read each section

Read every section **independently**: one permission error must not stop the
rest. Record the failure and carry on.

Cloudflare paginates with `-q per_page=<n> -q page=<n>`. Keep reading while
`result_info.page < result_info.total_pages`.

| Section | Call | Keep |
|---|---|---|
| Zone | `GET /zones/<id>` | id, name, status, plan.name, name_servers, original_name_servers, paused |
| DNS records | `GET /zones/<id>/dns_records` — paginate, `per_page=100` | type, name, content, ttl, proxied, priority |
| SSL mode | `GET /zones/<id>/settings/ssl` | `result.value` |
| Zone settings | `GET /zones/<id>/settings -q per_page=200` | build `by_id` as `{id: value}` for every item |
| Page rules | `GET /zones/<id>/pagerules` | as returned |
| Legacy firewall rules | `GET /zones/<id>/firewall/rules` | as returned |
| Rulesets | `GET /zones/<id>/rulesets` | entries with `kind == "zone"` only |
| Ruleset phases | `GET /zones/<id>/rulesets/phases/<phase>/entrypoint` | the ruleset, keyed by phase |
| Workers routes | `GET /zones/<id>/workers/routes` | pattern, script |
| Load balancers | `GET /zones/<id>/load_balancers` | name, default_pools, fallback_pool, steering_policy, proxied, ttl |
| Custom hostnames | `GET /zones/<id>/custom_hostnames` — paginate | hostname, status, ssl.status |
| Certificate packs | `GET /zones/<id>/ssl/certificate_packs -q status=all` | type, status, hosts, first certificate's issuer and expires_on |
| Universal SSL | `GET /zones/<id>/ssl/universal/settings` | as returned |
| Managed headers | `GET /zones/<id>/managed_headers` | ids of enabled request and response headers |
| Healthchecks | `GET /zones/<id>/healthchecks` | name, address, type, path, interval |
| Waiting rooms | `GET /zones/<id>/waiting_rooms` | name, host, path |
| Bot management | `GET /zones/<id>/bot_management` | fight_mode, enable_js, score_threshold, sbfm_* |
| DNSSEC | `GET /zones/<id>/dnssec` | status, ds, algorithm |
| Cache Reserve | `GET /zones/<id>/cache/cache_reserve` | value |
| Snippets | `GET /zones/<id>/snippets` | name, enabled |
| Logpush jobs | `GET /zones/<id>/logpush/jobs` | name, dataset, destination **with the query string removed** |
| Page Shield | `GET /zones/<id>/page_shield/settings` | enabled |
| Spectrum apps | `GET /zones/<id>/spectrum/apps` | protocol, dns.name, origin_port |
| Zaraz | `GET /zones/<id>/zaraz/v1/config` | tool names and enabled flags |
| Access apps | `GET /zones/<id>/access/apps` | name, domain, type |
| Argo | `GET /zones/<id>/argo/smart_routing` and `/argo/tiered_caching` | `result.value` |

Network Error Logging is an ordinary zone setting — read it from `by_id.nel`
rather than making a separate call.

### Ruleset phases

Read these, each at `/rulesets/phases/<phase>/entrypoint`:

`http_request_dynamic_redirect` · `http_request_transform` ·
`http_response_headers_transform` · `http_request_firewall_custom` ·
`http_request_cache_settings` · `http_request_origin` ·
`http_request_late_transform` · `http_config_settings` · `http_ratelimit` ·
`http_custom_errors` · `http_request_sanitize` ·
`http_response_firewall_managed` · `http_request_sbfm` ·
`http_log_custom_fields` · `http_request_snippets` ·
`http_response_compression` · `http_request_firewall_managed` · `ddos_l7`

> **The path ends in `/entrypoint`.** HelloBP v1 left it off, which makes every
> phase look empty. A **404 on an entrypoint means the phase has no rules** —
> a normal answer, not an error.

## 4. Sort the failures

| Response | Meaning | Record as |
|---|---|---|
| 404 on a ruleset phase | no rules in that phase | note |
| 403 or 404 on Argo | no Argo subscription | note |
| 400, 403 or 404 on an optional product | not in use, or not on this plan | note |
| anything else | a real failure | error, with the permission that fixes it |

Optional products: Workers routes, waiting rooms, healthchecks, custom
hostnames, certificate packs, universal SSL, load balancers, managed headers,
bot management, DNSSEC, Cache Reserve, snippets, Logpush, Page Shield, Spectrum,
Zaraz, Access.

Pair each error with its fix: DNS → DNS:Read · SSL mode or settings → Zone
Settings:Read · page rules → Page Rules:Read · rulesets → Zone WAF:Read ·
firewall rules → Firewall Services:Read · Logpush → Logs:Read.

**Report errors before planning.** A plan built from an inventory that is
missing DNS records or firewall rules is wrong in ways the user cannot see.

## 5. Never keep secrets

Logpush destinations carry credentials in their query string
(`s3://bucket/path?access-key-id=…&secret-access-key=…`). Drop everything from
`?` onward before writing anything to disk or into the conversation.

## Inventory file

Write `inventory.json` with these top-level keys, so the mapping stage can find
what it needs:

`zone` · `dns_records` · `ssl_mode` · `zone_settings` (`{"by_id": {…}}`) ·
`page_rules` · `firewall_rules` · `rulesets_by_phase` (`{phase: ruleset}`) ·
`argo` · `workers_routes` · `load_balancers` · `custom_hostnames` ·
`ssl_certificate_packs` · `ssl_universal` · `managed_headers` · `healthchecks` ·
`waiting_rooms` · `bot_management` · `dnssec` · `cache_reserve` · `snippets` ·
`logpush_jobs` · `page_shield` · `nel` · `spectrum_apps` · `zaraz_tools` ·
`access_apps` · `errors` · `notes`

## Rules-only export

When the user only wants the zone's rules (v1's "Download CF Rules"), write a
single JSON document with these sections:

| Section | Source |
|---|---|
| `custom_rules` | `http_request_firewall_custom` phase |
| `firewall_rules` | `GET /zones/<id>/firewall/rules` |
| `rate_limits` | `GET /zones/<id>/rate_limits` — paginate |
| `page_rules` | `GET /zones/<id>/pagerules` |
| `transform_rules` | `http_request_transform` phase |
| `redirect_rules` | `http_request_dynamic_redirect` phase |
| `managed_rules` | `http_request_firewall_managed` phase |

Put `{"error": "<message>"}` in any section that failed, rather than leaving it
out — a missing key reads as "no rules".
