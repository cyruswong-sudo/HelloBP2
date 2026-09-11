# Stage 6 — WAF IP allow and block rules

Move Cloudflare IP rules to BytePlus. This stage also stands on its own.

## 1. Check WAF exists on the account

```bash
bpctl call waf ListDomain -p PageNum=1 -p PageSize=100
```

**`"Data": null` means WAF is not enabled** — there's nothing to attach a rule
to, and every `CreateAclRule` will fail however correct the payload is.
Authenticating against WAF does not mean the account has it.

When WAF isn't enabled: **say so and stop the WAF strategy.** Don't retry with
different payloads, don't guess enum values, and never present a dry run as a
created rule. Offer the CDN strategy (section 5) for block lists, or ask the
user to enable WAF and onboard the domains.

## 2. Collect the IP rules

| Source | Call |
|---|---|
| IP access rules — zone | `bpctl cf get /zones/<zone_id>/firewall/access_rules/rules -q per_page=1000` (paginate) |
| IP access rules — account | `bpctl cf get /accounts/<account_id>/firewall/access_rules/rules -q per_page=1000` |
| Legacy firewall rules | `bpctl cf get /zones/<zone_id>/firewall/rules` |
| Custom firewall rules | `bpctl cf get /zones/<zone_id>/rulesets/phases/http_request_firewall_custom/entrypoint` (404 = none) |

### What carries across

**IP access rules** (`configuration.target`, `configuration.value`, `mode`):

| Condition | Result |
|---|---|
| target `ip`, `ip_range` or `ip6`, mode `block` | **Block** |
| target `ip`, `ip_range` or `ip6`, mode `whitelist` | **Allow** |
| mode `challenge`, `js_challenge`, `managed_challenge` | skip — no WAF ACL equivalent |
| target `country` or `asn` | skip — needs a geo or ASN rule, not an IP list |

**Firewall expressions** — migrate a rule only when it is a **pure IP match**:
`ip.src eq <ip>` or `ip.src in {…}` and nothing else.

| Expression | Result |
|---|---|
| pure IP match, action `block` | **Block** |
| pure IP match, action `allow` | **Allow** |
| negated (`not ip.src …`, `ip.src ne …`) | skip — as a plain list it would do the opposite |
| IP combined with another field (`… and http.request.uri.path …`) | skip — a bare IP list would broaden the rule; recreate as a WAF custom rule |
| `challenge` actions | skip |
| `ip.src in $named_list` | skip — resolve the list's contents first |

Normalise every value to CIDR: a single IPv4 address becomes `/32`, a single
IPv6 address `/128`. Drop duplicates. **An address that appears in both Block
and Allow is a conflict** — list it and ask the user which wins.

Show the user the counts, the skipped rules with reasons, and the conflicts.
Offer to write `migration/<zone>/block-ips.csv`, `allow-ips.csv` and
`all-ips.csv` (columns `ip, cidr, action, source, note`) for review.

## 3. Pick a strategy

| | WAF (preferred) | CDN (fallback) |
|---|---|---|
| Needs | WAF enabled with the domains onboarded | nothing extra |
| Block lists | ✓ | ✓ |
| Allow lists | ✓ | **✗ never** |
| Calls | a few, whatever the domain count | one per domain |

## 4. WAF strategy

For each action (`Block`, then `Allow`), split the CIDRs into groups of at most
500 and create one IP group per chunk:

```bash
bpctl call waf AddIpGroup --body '{"AddType":"ip","Name":"cf-block-1","IpList":["192.0.2.1/32","198.51.100.0/24"]}'
```

`Result.IpGroupId` is an integer. Then one ACL rule per action, covering every
domain at once:

```bash
bpctl call waf CreateAclRule --body '{
  "Name": "cf-migration-block",
  "AclType": "Block",
  "Enable": 1,
  "Url": "*",
  "HostAddType": 3,
  "HostList": ["example.com", "www.example.com"],
  "IpAddType": 2,
  "IpGroupId": [101, 102],
  "Description": "Migrated from Cloudflare"
}'
```

- **`AclType` is capitalised: `Block` or `Allow`.** The API rejects lowercase,
  so HelloBP v1's `deny` / `allow` never worked.
- **`Enable` is the integer `1`**, not `true`. `IpGroupId` holds integers.
- **`HostAddType` and `IpAddType` are not yet confirmed against a real rule.**
  The documented values are `HostAddType` 3 = multiple domain names and
  `IpAddType` 2 = IP group (3 = manual list). v1 used 1 and 1/2. If
  `~/.byteplus/verified.json` has them under `waf_parameters.observed` —
  written by `bpctl probe --save` on an account with an existing rule — use
  those. Otherwise use the documented values and **tell the user they're
  unverified**. If the API rejects them, report the error rather than cycling
  through other numbers.

Pause about 0.3 seconds between live calls.

## 5. CDN strategy — block lists only

One `UpdateCdnConfig` per domain, with the **whole** block list in a single
call — a second call replaces the first:

```bash
bpctl call cdn UpdateCdnConfig --body '{
  "Domain": "www.example.com",
  "IpAccessRule": {"FilterType": "blacklist", "Filters": ["192.0.2.1/32", "198.51.100.0/24"]}
}'
```

- **No `Switch` field.** HelloBP v1's CDN fallback sent one, which the API
  rejects.
- **Never `"FilterType": "whitelist"` from Cloudflare allow rules.** A
  Cloudflare allow rule skips other checks for those visitors; a CDN whitelist
  blocks everyone else. Report allow entries as not applied and point to the
  WAF strategy.
- Above roughly 500 entries, warn that BytePlus may cap the list size and try
  one domain first.

## 6. Confirm

```bash
bpctl call waf ListAclRule -p AclType=Block
bpctl call waf ListAclRule -p AclType=Allow
```

Report what was created — groups, rules, the domains they cover — and every
rule that was skipped, with its reason.
