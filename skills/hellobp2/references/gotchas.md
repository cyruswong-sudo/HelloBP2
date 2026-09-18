# BytePlus API gotchas

Behaviours that are not in the docs, or that the docs state in a way that
doesn't survive contact with the API. Most of these cost HelloBP v1 a debugging
session each; the commit history is the receipt.

## CDN

**List endpoints default to `PageSize: 10` and truncate silently.** A
`ListCdnDomains` call with no `PageSize` returns 10 rows on an account with 12
domains — no error, no flag. The only tell is `Result.Total`, which reports the
true count alongside the short `Data` array.

**Always pass an explicit `PageSize`, and compare `len(Data)` against `Total`
before reporting a list as complete.** Reporting "you have 10 domains" when the
account has 12 is the easiest wrong answer to give confidently, and it silently
scopes down anything built from that list — a cert deployment, a migration
inventory, a bulk config change.

> **Payload shapes below were checked against BytePlus's official SDK model**
> (`byteplus-sdk-golang/service/cdn/model.go`), its example payloads, and the
> official Terraform provider. HelloBP v1 got several of them wrong; where it
> did, the correction is called out.

**Configuration lives in policies, not on domains.** A domain gets its behaviour
from an attached *service template* (delivery policy) and optionally a *cipher
template* (encryption policy):

```
CreateServiceTemplate → LockTemplate → AddTemplateDomain       (one Domain per call)
CreateCipherTemplate  → LockTemplate → UpdateTemplateDomain    (Domains[], CertId, CipherTemplateId, HTTPSSwitch: on)
```

`LockTemplate` is what the official Terraform provider uses to publish both kinds.
`ReleaseTemplate` also exists (v1 used it); fall back to it only if binding a
locked template is refused. There is no `AddCipherDomain` action.

**List templates with `DescribeTemplates`**, filtered by `Type` (`service` or
`cipher`). There is no `ListServiceTemplate` or `ListCipherTemplate` — both
return `404 InvalidActionOrVersion`. Results come back under `Result.Templates`
with `Result.TotalCount`, and each carries `TemplateId`, `Title`, `Status`
(`locked` once published) and the number of bound domains. `DescribeTemplateDomains`
lists the domains on one template.

**Locked policies are immutable.** To change one: `DuplicateTemplate`, edit the
copy, lock it, re-attach the domains. There is no in-place edit — "change one
cache TTL" is a four-call operation.

**The encryption policy binds together with a certificate.** Official examples
send `CipherTemplateId` alongside `CertId` and `HTTPSSwitch: "on"`. Add domains
with HTTPS off, then bind both at certificate time.

**`AddTemplateDomain.Domain` is one domain; `UpdateTemplateDomain.Domains` is an
array.** Different actions, different field names. v1 sent a comma-joined list
to `AddTemplateDomain`.

**Encryption policy fields nest inside `HTTPS`:**

```json
{"HTTPS": {"HTTP2": true, "OCSP": true, "TlsVersion": ["tlsv1.2", "tlsv1.3"],
           "ForcedRedirect": {"EnableForcedRedirect": true, "StatusCode": "301"},
           "Hsts": {"Switch": true, "Ttl": 31536000, "Subdomain": "include"}},
 "Quic": {"Switch": false}}
```

`HTTP2` and `OCSP` are booleans, `TlsVersion` values are lowercase. v1 sent a
flat shape — `HTTP2.Switch`, `TLSVersions`, `ForceRedirect`, `HSTS.Age`,
`OCSPStapling` — none of which are fields.

**`HttpForcedRedirect` is the opposite of what it sounds like.** It redirects
**HTTPS → HTTP**. "Always use HTTPS" is `HTTPS.ForcedRedirect`.

**HTTP/2 and WebSocket are mutually exclusive.** WebSocket needs an HTTP/1.1
Upgrade; HTTP/2 multiplexing breaks it. Set `HTTPS.HTTP2: false` whenever
WebSocket is on, or connections fail in a way that looks like an origin problem.

**`OriginHost`: leave it out, or send `""`.** Both give "Same as Domain Name" —
BytePlus's own SDK examples send `""`, and `DescribeCdnConfig` returns `""` for
it, so a config read back and resent is safe. Any non-empty value fixes the
origin hostname.

**Access rules need `Switch`, `RuleType` and a list:**

| Block | List field | `RuleType` |
|---|---|---|
| `IpAccessRule` | `Ip` | `deny` or `allow` |
| `UaAccessRule` | `UserAgent` (+ `IgnoreCase`, `AllowEmpty`) | `deny` or `allow` |
| `RefererAccessRule` | `Referers` (+ `AllowEmpty`) | `deny` or `allow` |

```json
{"IpAccessRule": {"Switch": true, "RuleType": "deny", "Ip": ["192.0.2.1", "198.51.100.0/24"]}}
```

This error:

```
InvalidParameter.IpAccessRule.RuleType: Unsupported IP Access Rule type
```

means the `RuleType` **value** is wrong — `blacklist` or `whitelist` instead of
`deny` or `allow`. HelloBP v1 hit it, then renamed the fields to `FilterType` /
`Filters` and dropped `Switch`. Those fields don't exist, so the API accepted the
request and applied **no rule** — a silent failure. After writing any access
rule, confirm it with `DescribeCdnConfig`.

`AllowEmpty` decides whether a request with no header counts as matching the
list. On a `deny` list, `true` blocks every request without a Referer.

**Redirects are `RedirectionRewrite`, not `UrlRedirect`.** `UrlRedirect` isn't a
field. Each `RedirectionAction` matches an **exact** `SourcePath` and sets
`TargetProtocol` (`followclient` / `http` / `https`), `TargetHost`, `TargetPath`
and `RedirectCode` (a string). No wildcards — prefix and pattern redirects belong
in the Rules Engine.

**`OriginRewrite` is an object, not a list:**
`{"Switch": true, "OriginRewriteRule": [{"OriginRewriteAction": {"RewriteType": "rewrite_path", "SourcePath": "^/api/(.*)$", "TargetPath": "/v2/$1"}}]}`.
`SourcePath` is a regex; `TargetPath` uses `$1`, `$2`.

**`AreaAccessRule` uses BytePlus region codes, not ISO country codes.** Fetch the
valid codes rather than assuming `US`/`GB` work everywhere.

**Errors can arrive with HTTP 200.** BytePlus sometimes returns success at the
HTTP layer with the real error in `ResponseMetadata.Error`. Never treat a 2xx as
success without checking. `bpctl` raises on both.

## Certificates

**There is no list endpoint.** Every candidate action name —
`CertificateListInstances`, `CertificateGetInstances`,
`CertificateDescribeInstances`, `CertificateListInstance` — returns
`404 InvalidActionOrVersion` on API version 2021-06-01.

To enumerate certificates, use the **CDN** service instead:

```bash
bpctl call cdn ListCdnCertInfo
bpctl call cdn ListCertInfo        # fallback
```

**`BatchDeployCert` takes `Domain` as a comma-joined string**, not an array, and
caps at 50 domains per call. Batch client-side.

**Certificate errors use numeric codes and Chinese messages**, unlike every
other service, and they arrive with **HTTP 200**. A missing certificate is
`{"Code": 3001, "Message": "未找到指定的证书"}` — "the specified certificate was
not found". Don't match on `Code` as a string, and translate the message before
showing it to the user.

**Free certificates are quota-limited, and the quota is invisible.** Every
`CertificateAddFreeInstance` on an exhausted account fails with
`3000: 免费证书配额不足`, whatever the plan. No quota-query action exists at
version 2021-06-01. Report it as an account limit and stop.

**`plan` is `<brand>_<standard|wildcard>_<dv|ov|ev>`** — `lets_encrypt_standard_dv`
for one hostname, `lets_encrypt_wildcard_dv` for `*.example.com`. A wildcard
`common_name` under a `standard` plan is rejected outright.

**Free DV issuance is a three-step DNS-01 flow**: `CertificateAddFreeInstance`
→ `CertificateGetDcvParam` (returns the TXT record to publish) →
`CertificateGetInstance` (poll; `certificate_exist: 1` means issued).

## WAF

**Authenticating against WAF does not mean the account has WAF.** Provisioning
and signing are unrelated: an account with no WAF deployment still returns
`✓ waf ... authenticated` from `bpctl probe`. Check before doing any WAF work:

```bash
bpctl call waf ListDomain -p Page=1 -p PageSize=100 -p Region=<waf region>
```

**`Page`, `PageSize` and `Region` are all required** (`Page`, not `PageNum`).
`Region` is the WAF instance's region from the BytePlus WAF console — the API
version is `2023-12-25` and it echoes the region back in `ResponseMetadata`.

> **Verified on a live account, 2026-09-18.** With all three parameters set and
> `Region` tried as `ap-southeast-1`, `ap-singapore-1` and `singapore`,
> `ListDomain` returned `Result.Data: null` every time, and `ListAclRule`
> returned `TotalCount: 0`. So an account really can sign against WAF while
> having nothing onboarded. Note the API accepts an unknown `Region` without
> complaint, so an empty result never *proves* the region was right — confirm it
> with the user before concluding WAF is off.

With all three set, an empty `Data` means no domains are onboarded to WAF in that
region, so `CreateAclRule` has nothing to attach to. **Say so and stop.** Confirm
the region with the user, don't retry with different payloads, don't guess enum
values, and never present a dry run as a created rule.

**Two hosts exist.** The docs specify `waf.byteplusapi.com`; HelloBP v1 used
`open.byteplusapi.com` successfully. Both resolve. Run `bpctl probe waf --save`
to determine which one your account signs for, rather than assuming.

**The `CreateAclRule` enums are settled.**

| Field | Value | Evidence |
|---|---|---|
| `AclType` | `Block` or `Allow` — capitalised string | Go SDK enum constants; the API rejects `block` / `deny` |
| `HostAddType` | `3` = a list of domains, sent in `HostList` | official Terraform docs: "HostList — required if HostAddType = 3" |
| `IpAddType` | `2` = IP groups in `IpGroupId`; `3` = IPs in `IpList` | official Terraform docs: "required if IpAddType = 2" / "= 3" |
| `Enable` | integer `1` | Go SDK model: `int32` |

HelloBP v1 used `allow` / `deny` and `1` for both add types — all wrong.
`Name`, `AclType`, `Enable`, `HostAddType`, `IpAddType` and `Url` are required;
the official example uses `"Url": "/"`.

`AclType` can also be checked on any account, because `ListAclRule` validates
it:

```bash
bpctl call waf ListAclRule -p AclType=Block   # 200 OK
bpctl call waf ListAclRule -p AclType=block   # 400 InvalidParameter. AclType
```

**`ListAclRule` returns rules under `Result.Rules`**, not `Result.Data`.

**`AccurateGroup` is a real expression engine**, not a formality. It supports 16
match objects, 30 operators (regex, IP-group membership, ASN, geographic, IP
classification), and AND/OR grouping. This is what makes Cloudflare custom
firewall rules translatable rather than reducible to flat IP lists.

Keyed objects need a concrete key: use `request.header.x-api-key`, never
`request.header.*`.

## DNS

**Reads are GET, writes are POST.** GET actions sign an empty body and carry all
parameters in the query string.

**Record types BytePlus does not support**: `HTTPS`, `SVCB`, `DS`, `DNSKEY`,
`TLSA`, `SSHFP`, `LOC`, `NAPTR`, `CERT`, `URI`, `SMIMEA`. Supported: `A`, `AAAA`,
`CNAME`, `Alias`, `MX`, `NS`, `TXT`, `SRV`, `CAA`, `PTR`.

This matters most for `HTTPS`/`SVCB`, which are increasingly common — dropping
one silently disables ECH and ALPN hints for that hostname. When migrating,
report unsupported records as failures, never skip them quietly.

**A zone is `ZID`, not a name.** Most record operations take the numeric zone ID
from `ListZones`, not the domain string.

## Cross-cutting

**Whitespace in credentials produces a 401, not a validation error.** Strip
before use; `bpctl` does.

**Clock skew beyond ~5 minutes produces a 401.** Check the system clock before
suspecting the keys.

**The signing region must match the account's home region.** Indonesia,
Philippines, Vietnam, and Taiwan accounts use `ap-southeast-3`.
