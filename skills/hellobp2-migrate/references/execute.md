# Stage 3 — Execute, then validate

Creates real resources. Run the whole sequence as a dry run first, show the user
every payload, and only then repeat it with `--live` after they agree.

Keep payloads in files and send them with `--body-file` — they are too large to
pass inline, and a file is what the user reviewed:

```bash
bpctl call cdn CreateServiceTemplate --body-file migration/example.com/payloads/policy-example-com.json
```

Record every response in `migration/<zone>/run.json`.

> **Where these shapes come from.** Every payload below was checked field by
> field against BytePlus's official Go SDK CDN model
> (`byteplus-sdk-golang/service/cdn/model.go`), the SDK's own example payloads,
> the official Terraform provider, and the veCDN API reference, which shares the
> schema. HelloBP v1's payloads disagreed with those sources in several places;
> where they did, the official shape is used here.

## Order

```
1. CreateCipherTemplate   → Result.TemplateId  (cipher id)
2. LockTemplate           { TemplateId: cipher id }
   for each origin group:
3.   CreateServiceTemplate → Result.TemplateId  (policy id)
4.   LockTemplate          { TemplateId: policy id }
5.   AddTemplateDomain     one call per domain, HTTPS off
```

The encryption policy is **created** here but **bound** to domains in the
certificate stage — BytePlus binds `CipherTemplateId` together with a `CertId`
and `HTTPSSwitch: "on"`.

**Locked templates are immutable.** Changing one later means
`DuplicateTemplate`, edit, lock, re-attach. Get the payloads right before step 2
and step 4.

> **`LockTemplate` vs `ReleaseTemplate`.** Both actions exist. The official
> Terraform provider publishes delivery and encryption policies with
> `LockTemplate` and waits for status `locked` before binding domains; HelloBP v1
> used `ReleaseTemplate` for delivery policies. Use `LockTemplate`. If
> `AddTemplateDomain` then rejects the template as unpublished, call
> `ReleaseTemplate` with the same `TemplateId` and retry — and record which one
> the account needed.

## Payloads

### 1. CreateCipherTemplate

Everything TLS-related lives inside one `HTTPS` object:

```json
{
  "Title": "encryption-example-com",
  "Message": "Encryption policy migrated from Cloudflare — example.com",
  "HTTPS": {
    "HTTP2": true,
    "OCSP": true,
    "TlsVersion": ["tlsv1.1", "tlsv1.2", "tlsv1.3"],
    "ForcedRedirect": {"EnableForcedRedirect": true, "StatusCode": "301"},
    "Hsts": {"Switch": true, "Ttl": 31536000, "Subdomain": "include"}
  },
  "Quic": {"Switch": false}
}
```

| Field | Rule |
|---|---|
| `HTTPS.HTTP2` | a **boolean**. `false` whenever the zone has WebSocket on — they're incompatible |
| `HTTPS.TlsVersion` | **lowercase**: `tlsv1.0` … `tlsv1.3`. Use `tlsv1.1`–`tlsv1.3` |
| `HTTPS.ForcedRedirect` | HTTP → **HTTPS**. Include only when Cloudflare "Always Use HTTPS" is on. `StatusCode` is a string: `301`, `302`, `303`, `307`, `308` |
| `HTTPS.Hsts` | `Ttl` in seconds; `Subdomain` is `include` or `exclude`. Omit when the zone has no HSTS |
| `HTTPS.OCSP` | a boolean |
| `Quic.Switch` | HTTP/3 — `true` when Cloudflare `http3` is on |

> **Never use `HttpForcedRedirect` for "Always Use HTTPS".** Despite the name, it
> redirects **HTTPS → HTTP**. The HTTP → HTTPS redirect is `HTTPS.ForcedRedirect`.

### 2. LockTemplate

```json
{"TemplateId": "<cipher id>"}
```

### 3. CreateServiceTemplate

```json
{
  "Title": "policy-example-com",
  "Message": "Migrated from Cloudflare — example.com",
  "Origin": [{"OriginAction": {"OriginLines": [
    {"Address": "192.0.2.10", "InstanceType": "ip", "OriginType": "primary",
     "HttpPort": "80", "HttpsPort": "443", "Weight": "100"}
  ]}}],
  "OriginProtocol": "https",
  "IPv6": {"Switch": true},
  "Websocket": {"Switch": true},
  "Compression": {"Switch": true, "CompressionRules": [{"CompressionAction": {
    "CompressionType": ["gzip", "br"], "CompressionFormat": "default", "CompressionTarget": "*"}}]},
  "ResponseHeader": [{"ResponseHeaderAction": {"ResponseHeaderInstances": [
    {"Action": "set", "Key": "X-Frame-Options", "ValueType": "constant", "Value": "DENY"}]}}],
  "RedirectionRewrite": {"Switch": true, "RedirectionRule": [{"RedirectionAction": {
    "RedirectCode": "301", "SourcePath": "/old-page", "TargetHost": "example.com",
    "TargetPath": "/new-page", "TargetProtocol": "https"}}]},
  "OriginRewrite": {"Switch": true, "OriginRewriteRule": [{"OriginRewriteAction": {
    "RewriteType": "rewrite_path", "SourcePath": "^/api/(.*)$", "TargetPath": "/v2/$1"}}]},
  "IpAccessRule": {"Switch": true, "RuleType": "deny", "Ip": ["192.0.2.1", "198.51.100.0/24"]},
  "UaAccessRule": {"Switch": true, "RuleType": "deny", "UserAgent": ["*badbot*"], "IgnoreCase": true},
  "RefererAccessRule": {"Switch": true, "RuleType": "deny", "Referers": ["spam.example.net"], "AllowEmpty": false}
}
```

Include only the blocks the plan calls for.

**Access rules** — `Switch`, `RuleType` and the list are all required:

| Block | List field | `RuleType` |
|---|---|---|
| `IpAccessRule` | `Ip` | `deny` (blocklist) or `allow` (allowlist) |
| `UaAccessRule` | `UserAgent` | `deny` or `allow` |
| `RefererAccessRule` | `Referers` | `deny` or `allow` |

> **HelloBP v1 got this wrong, and its "fix" made rules silently do nothing.** v1
> first sent `"RuleType": "blacklist"`, which the API rejects with
> `Unsupported IP Access Rule type` — `blacklist` isn't a valid value. v1 then
> renamed the fields to `FilterType` / `Filters` and dropped `Switch`. Neither
> field exists, so the request "succeeded" and **no rule was applied**. The valid
> values are `deny` and `allow`.

Each block holds **one** list, so a domain can't carry both a deny and an allow
list for the same block. Cloudflare **allow** rules are never mapped here — see
the mapping stage.

**Redirects** — `RedirectionRewrite` matches an **exact** `SourcePath`. There are
no wildcards or match operators:

| Cloudflare redirect | BytePlus |
|---|---|
| exact path, e.g. `example.com/old-page` | `RedirectionRewrite` rule |
| wildcard or prefix, e.g. `example.com/old/*` | **not** `RedirectionRewrite` — build a Rules Engine policy with a `redirect_request` action (see the `hellobp2` skill's `rule-engine.md`), or report it as manual |

`RedirectCode` is a string (`301`, `302`, `303`, `307`, `308`). `TargetProtocol` is
`followclient`, `http` or `https`. To carry the query string, add
`"TargetQueryComponents": {"Action": "include", "Value": "*"}`.

**Origin rewrites** — `OriginRewrite` is an **object** holding a rule list, not a
list itself. `SourcePath` is a regular expression; `TargetPath` can use `$1`, `$2`
for captured groups. `RewriteType` is `rewrite_path` (path only) or
`rewrite_url` (path and query string).

**OriginHost** — leave it out. Absent, the origin Host header is the domain name,
which is what a Cloudflare migration wants. BytePlus's own SDK examples send
`"OriginHost": ""` for the same result, so an empty string from a
`DescribeCdnConfig` response is safe to send back. Any **non-empty** value
switches to a fixed origin hostname.

For a Cloudflare Worker origin, use `"Address": "127.0.0.1"` and tell the user
that origin must be replaced before cutover.

### 4. LockTemplate

```json
{"TemplateId": "<policy id>"}
```

### 5. AddTemplateDomain — once per domain

```json
{
  "Domain": "example.com",
  "ServiceTemplateId": "<policy id>",
  "ServiceRegion": "outside_chinese_mainland",
  "HTTPSSwitch": "off"
}
```

- **`Domain` is a single domain name.** Make one call per domain. HelloBP v1 sent
  a comma-joined list; BytePlus's SDK model and example both take one name.
- `ServiceRegion`: `outside_chinese_mainland`, `global` or `chinese_mainland`.
  Use `outside_chinese_mainland` unless the user needs mainland China delivery,
  which requires an ICP filing.
- HTTPS stays **off** here. The certificate stage turns it on and binds the
  encryption policy in the same call.

## When a step fails

- A failed create, lock or add **skips the rest of that origin group only**.
  Carry on with the other groups — one bad origin must not block the zone.
- A failed `AddTemplateDomain` for one domain doesn't stop the other domains in
  the group.
- A failed `CreateCipherTemplate` doesn't stop the delivery policies. Report
  that the encryption policy is missing — the certificate stage needs it.
- Report every failure with its `error` and `hint` from bpctl's output.

## Always manual

Tell the user these need the console, whatever the zone looks like:

- **Signed URL authentication** — Cloudflare doesn't export token-auth config.
  Console → CDN → Domain Management → Access Control.
- **Custom error pages** — Cloudflare doesn't export the HTML. Console → CDN →
  Domain Management → Advanced → Custom Error Pages.

## Validate

Validate from the **account**, not from your own log of what you sent:

```bash
bpctl call cdn ListCdnDomains --all
bpctl call cdn DescribeCdnConfig -p Domain=example.com
```

| Check | Pass when |
|---|---|
| Every plan domain exists | each `cdn_domains` entry appears in the listing |
| Delivery policy attached | the domain's `ServiceTemplateId` is the id created for its group |
| Deployed | `Status` is `online` and `ConfigStatus` is `FullDeployed` |
| Nothing silently truncated | the listing's `complete` is `true` |
| Rules took effect | `DescribeCdnConfig` shows `IpAccessRule.Switch: true` with the expected `Ip` list — a rule you sent but don't see was ignored |
| Encryption policy created and locked | steps 1–2 succeeded |
| Every group created, locked and added | steps 3–5 succeeded for each domain |
| Origin known | no group still on the `127.0.0.1` placeholder |

The encryption-policy binding is checked in the certificate stage, where it
happens.

Then report, alongside the checklist: any dry-run steps (nothing was created),
every manual item from the plan's coverage, and every warning. A validation that
passes with manual items outstanding is not a finished migration — say so.
