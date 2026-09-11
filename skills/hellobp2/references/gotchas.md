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

**Configuration lives in policies, not on domains.** A domain gets its behaviour
from an attached *service template* (delivery policy) and optionally a *cipher
template* (encryption policy). The flow is create → release → attach:

```
CreateServiceTemplate  →  ReleaseTemplate  →  AddTemplateDomain
CreateCipherTemplate   →  LockTemplate     →  AddCipherDomain
```

**Released policies are immutable.** To change one: `DuplicateTemplate`, edit the
copy, release it, re-attach the domains. There is no in-place edit. Plan for this
— it means "change one cache TTL" is a four-call operation.

**A domain must have HTTPS enabled before a cipher template can attach.** The
error if you skip it does not say so.

**Omit `OriginHost` to get "Same as Domain Name".** Sending `""` is a different
setting and is not equivalent. This applies both in `CreateServiceTemplate` and
inside `OriginLines[]`.

> **Reads and writes are asymmetric here, which is the trap.** A domain set to
> "Same as Domain Name" comes *back* from `DescribeCdnConfig` as
> `"OriginHost": ""` — verified against a live domain. So round-tripping a
> config (read it, edit one field, write it back) silently changes this setting,
> because the `""` you read is not the `""` you may send. **Strip `OriginHost`
> from any payload you built from a `Describe` response**, unless you are
> deliberately setting a real origin host.

**HTTP/2 and WebSocket are mutually exclusive.** WebSocket needs an HTTP/1.1
Upgrade; HTTP/2 multiplexing breaks it. Set `HTTP2.Switch: "off"` whenever
WebSocket is on, or connections fail in a way that looks like an origin problem.

**Access rules must not carry `Switch`.** On `IpAccessRule`, `UaAccessRule`, and
`RefererAccessRule`, including `Switch` with *any* value returns:

```
InvalidParameter.IpAccessRule.RuleType: Unsupported IP Access Rule type
```

The message blames `RuleType`; the actual cause is the presence of `Switch`. Send
only `FilterType` (`blacklist` or `whitelist`) and `Filters`.

**Use `FilterType`, not `RuleType`.** `RuleType` is not a field on these objects
despite what the error above implies.

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

**Free DV issuance is a three-step DNS-01 flow**: `CertificateAddFreeInstance`
→ `CertificateGetDcvParam` (returns the TXT record to publish) →
`CertificateGetInstance` (poll; `certificate_exist: 1` means issued).

## WAF

**Authenticating against WAF does not mean the account has WAF.** Provisioning
and signing are unrelated: an account with CDN domains and certificates but no
WAF deployment still returns `✓ waf ... authenticated` from `bpctl probe`. Check
before doing any WAF work:

```bash
bpctl call waf ListDomain
```

`"Data": null` on a 200 means **no domains are onboarded to WAF** — there is
nothing to attach a rule to, and `CreateAclRule` will fail no matter how correct
the payload is. A corroborating signal: on such an account most management
actions do not exist at all — `ListInstance`, `DescribeInstance`,
`ListWafDomain`, `GetInstanceStatus` and `DescribeInstanceSpec` all return
`404 InvalidActionOrVersion`, leaving only `ListDomain` and `ListAclRule`.

When you see this, **say so and stop.** Tell the user WAF is not enabled on this
account and that the WAF portion of their work cannot proceed here. Do not retry
with different payloads, do not guess at enum values to make an error go away,
and never present a dry run as though the rule were created. If the WAF half of
a Cloudflare migration matters, it has to run against the account that actually
holds the WAF deployment.

**Two hosts exist.** The docs specify `waf.byteplusapi.com`; HelloBP v1 used
`open.byteplusapi.com` successfully. Both resolve. Run `bpctl probe waf --save`
to determine which one your account signs for, rather than assuming.

**Four enum shapes are disputed between the docs and v1's working code.** Do not
trust either source — read them back from the account with
`bpctl probe waf --params`, which inspects real rules via `ListAclRule`:

| Field | Docs | v1 used | Status |
|---|---|---|---|
| `AclType` | `Allow` / `Block` | `allow` / `deny` | **RESOLVED — docs are right** |
| `HostAddType` | `2` = group, `3` = multiple domains | `1` | unresolved |
| `IpAddType` | `2` = group, `3` = manual, `4` = geographic | `1` / `2` | unresolved |

**`AclType` is settled: capitalised `Allow` / `Block`, as a string.** This is
testable without any existing rule, because `ListAclRule` *requires* `AclType`
as a parameter and validates it:

```bash
bpctl call waf ListAclRule -p AclType=Block   # 200 OK
bpctl call waf ListAclRule -p AclType=block   # 400 InvalidParameter. AclType
bpctl call waf ListAclRule -p AclType=1       # 400 ... Type error
```

Lowercase is rejected outright, so **v1's `allow` / `deny` cannot ever have
worked** — anything in v1 that sent them was failing, whatever the surrounding
code reported. Integers are rejected with a type error, confirming a string enum.

The other two remain unresolved: they can only be read back off an existing
rule, and `ListAclRule` returns an empty set on an account with none. The API's
own response is the authority. Create one rule by hand in the console and
re-probe — guessing here produces rules that appear to succeed but match nothing.

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
