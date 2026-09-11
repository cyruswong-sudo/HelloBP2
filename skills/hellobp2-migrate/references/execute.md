# Stage 3 — Execute, then validate

Creates real resources. Run the whole sequence as a dry run first, show the user
every payload, and only then repeat it with `--live` after they agree.

Keep payloads in files and send them with `--body-file` — they are too large to
pass inline, and a file is what the user reviewed:

```bash
bpctl call cdn CreateServiceTemplate --body-file migration/example.com/payloads/policy-example-com.json
```

Record every response in `migration/<zone>/run.json`.

## Order

```
1. CreateCipherTemplate   → Result.TemplateId  (cipher id)
2. LockTemplate           { TemplateId: cipher id }
   for each origin group:
3.   CreateServiceTemplate → Result.TemplateId  (policy id)
4.   ReleaseTemplate       { TemplateId: policy id }
5.   AddTemplateDomain     binds the delivery AND encryption policy in one call
```

Get the encryption policy right first — its id goes into every
`AddTemplateDomain`. **Released and locked templates are immutable**: changing
one later means `DuplicateTemplate`, edit, release, re-attach.

## Payloads

### 1. CreateCipherTemplate

```json
{
  "Title": "encryption-example-com",
  "Message": "Encryption policy migrated from Cloudflare — example.com",
  "HTTP2": {"Switch": "on"},
  "HTTP3": {"Switch": "off"},
  "TLSVersions": ["TLSv1.1", "TLSv1.2", "TLSv1.3"],
  "ForceRedirect": {"Switch": "on", "RedirectType": "http"},
  "HSTS": {"Switch": "on", "Age": 31536000, "IncludeSubDomain": "on"},
  "OCSPStapling": {"Switch": "on"}
}
```

`HTTP2.Switch` is `"off"` whenever the zone has WebSocket on. Omit `HTTP3`,
`ForceRedirect` and `HSTS` when the zone doesn't use them.

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
  "UrlRedirect": [{"Condition": {"ConditionRule": [
    {"Object": "path", "Operator": "match_prefix", "Type": "url", "Value": "/old/"}]},
    "RedirectAction": {"RedirectType": "301", "RedirectUrl": "https://example.com/new/"}}],
  "IpAccessRule": {"FilterType": "blacklist", "Filters": ["192.0.2.1", "198.51.100.0/24"]}
}
```

Include only the blocks the plan calls for. Three rules are not optional:

- **No `OriginHost` key at all.** Absent means "Same as Domain Name". Sending
  `""` selects "Specified Hostname". If you built a payload from a
  `DescribeCdnConfig` response, strip `OriginHost` — the API *returns* `""`
  for "Same as Domain Name", but you must not *send* it.
- **No `Switch` inside `IpAccessRule`, `UaAccessRule` or `RefererAccessRule`.**
- **No `Https` block.** TLS settings belong to the encryption policy.

For a Cloudflare Worker origin, use `"Address": "127.0.0.1"` and tell the user
that origin must be replaced before cutover.

### 4. ReleaseTemplate

```json
{"TemplateId": "<policy id>"}
```

### 5. AddTemplateDomain

```json
{
  "Domain": "example.com,api.example.com",
  "ServiceTemplateId": "<policy id>",
  "CipherTemplateId": "<cipher id>",
  "ServiceRegion": "outside_chinese_mainland",
  "HTTPSSwitch": "off"
}
```

`Domain` is a **comma-joined string**, not an array. HTTPS stays off until a
certificate is deployed in the certificate stage.

If the API rejects `CipherTemplateId` because HTTPS is off, add the domains
without it, deploy the certificate, then attach the encryption policy.

## When a step fails

- A failed create, release or add **skips the rest of that origin group only**.
  Carry on with the other groups — one bad origin must not block the zone.
- A failed `CreateCipherTemplate` doesn't stop the delivery policies; add the
  domains without `CipherTemplateId` and report that the encryption policy is
  missing.
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
```

| Check | Pass when |
|---|---|
| Every plan domain exists | each `cdn_domains` entry appears in the listing |
| Delivery policy attached | the domain's `ServiceTemplateId` is the id created for its group |
| Encryption policy attached | `CipherTemplateId` is the cipher id |
| Deployed | `Status` is `online` and `ConfigStatus` is `FullDeployed` |
| Nothing silently truncated | the listing's `complete` is `true` |
| Encryption policy created and locked | steps 1–2 succeeded |
| Every group created, released and added | steps 3–5 succeeded for each group |
| Origin known | no group still on the `127.0.0.1` placeholder |

Then report, alongside the checklist: any dry-run steps (nothing was created),
every manual item from the plan's coverage, and every warning. A validation that
passes with manual items outstanding is not a finished migration — say so.
