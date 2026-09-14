# Stage 2 — Build the migration plan

No API calls. Read `inventory.json`, write `migration/<zone>/plan.json`, and
tell the user what will and will not carry across **before** anything is
created.

## 1. Group hostnames by origin

Every A, AAAA and CNAME record becomes a CDN domain. Records that point at the
**same content** share one delivery policy; different content means a separate
policy.

1. Take every A/AAAA/CNAME record with a name and content. Put the apex first.
2. Group by `content`. Skip a name you have already placed.
3. Per group:
   - `template_title` = `policy-<first domain>` with every character outside
     `[A-Za-z0-9_-]` replaced by `-`
   - `instance_type` = `ip` for A/AAAA, `domain` for CNAME
   - `cf_worker` = true when content starts with `100:` — Cloudflare's internal
     range for Workers and Pages routes. That group has **no real origin**: use
     `127.0.0.1` as a placeholder and tell the user it must be replaced.

## 2. Classify DNS records

| Types | Treatment |
|---|---|
| A, AAAA, CNAME, TXT, MX | carried in the plan |
| SRV, CAA, NS, PTR and other types BytePlus DNS supports | manual — recreate them |
| HTTPS, SVCB, DS, DNSKEY, TLSA, SSHFP, LOC, NAPTR, CERT, URI, SMIMEA | **no BytePlus equivalent** — warn loudly |

Dropping an `HTTPS` or `SVCB` record silently disables ECH and ALPN hints for
that hostname. Raise it before cutover, not after.

## 3. Map settings

Read a setting from `zone_settings.by_id`. `on`, `true`, `1` and `yes` are
true.

### Encryption policy — one per zone

All TLS settings sit inside one `HTTPS` object. Full payload in `execute.md`.

| Cloudflare | BytePlus `CreateCipherTemplate` |
|---|---|
| (always) | `HTTPS.TlsVersion: ["tlsv1.1","tlsv1.2","tlsv1.3"]` — lowercase, deliberately wide |
| `websockets` on | `HTTPS.HTTP2: false` — WebSocket needs an HTTP/1.1 Upgrade |
| `websockets` off | `HTTPS.HTTP2: true` |
| `http3` on | `Quic: {"Switch": true}` |
| `always_use_https` on | `HTTPS.ForcedRedirect: {"EnableForcedRedirect": true, "StatusCode": "301"}` |
| `security_header.strict_transport_security.enabled` | `HTTPS.Hsts: {"Switch": true, "Ttl": max_age, "Subdomain": "include" or "exclude"}` |
| (always) | `HTTPS.OCSP: true` |

TLS, HTTP/2, HTTP/3, HSTS and forced redirect belong **only** in the encryption
policy — never in the delivery policy. **Don't use `HttpForcedRedirect`** for
"Always Use HTTPS": it redirects HTTPS → HTTP.

### Delivery policy — one per origin group

| Cloudflare | BytePlus `CreateServiceTemplate` |
|---|---|
| record content | `Origin[0].OriginAction.OriginLines[0].Address`, `InstanceType`, `OriginType: "primary"`, `HttpPort: "80"`, `HttpsPort: "443"`, `Weight: "100"` |
| `ssl` mode `full` / `strict` | `OriginProtocol: "https"`, otherwise `"http"` |
| (always) | leave out `OriginHost` — the origin Host header is then the domain name |
| `ipv6` | `IPv6: {"Switch": true/false}` — a boolean, not a string |
| `websockets` | `Websocket: {"Switch": true/false}` |
| `brotli` on | `Compression: {"Switch": true, "CompressionRules": [{"CompressionAction": {"CompressionType": ["gzip","br"], "CompressionFormat": "default", "CompressionTarget": "*"}}]}` |

### Header transforms

`http_response_headers_transform` and `http_request_transform` rules carry
`action_parameters.headers` as `{Name: {"operation": "set"|"remove", "value"}}`.

| Operation | BytePlus instance |
|---|---|
| set | `{"Action": "set", "Key": name, "ValueType": "constant", "Value": value}` |
| remove | `{"Action": "delete", "Key": name}` |

Collected into `ResponseHeader: [{"ResponseHeaderAction": {"ResponseHeaderInstances": […]}}]`
and the equivalent `RequestHeader` shape.

### Redirects

Sources:
- page rules with an action `id == "forwarding_url"`: source is
  `targets[0].constraint.value`, target `value.url`, code `value.status_code`
- `http_request_dynamic_redirect` rules with `action == "redirect"`: target
  `action_parameters.from_value.target_url.value`, code
  `from_value.status_code`, default 301

BytePlus's `RedirectionRewrite` matches an **exact path only** — no wildcards,
no prefixes, no operators. Split redirects by their source:

| Source path | Treatment |
|---|---|
| no `*`, e.g. `example.com/old-page` | **auto** — a `RedirectionRewrite` rule |
| contains `*`, e.g. `example.com/old/*` | **manual** — Rules Engine `redirect_request` (see `rule-engine.md` in the `hellobp2` skill) |
| dynamic redirect with an `expression` target (`target_url.expression`) | **manual** — Rules Engine |

For an auto redirect, split the target URL into protocol, host and path:

```json
{"RedirectionAction": {"RedirectCode": "301", "SourcePath": "/old-page",
  "TargetProtocol": "https", "TargetHost": "example.com", "TargetPath": "/new-page",
  "TargetQueryComponents": {"Action": "include", "Value": "*"}}}
```

Collected into `RedirectionRewrite: {"Switch": true, "RedirectionRule": [ … ]}`.
`RedirectCode` is a string. Use `"TargetProtocol": "followclient"` when the
Cloudflare target has no scheme. Include `TargetQueryComponents` only when the
Cloudflare rule preserves the query string (`preserve_query_string: true`).

### URI rewrites

`http_request_transform` rules with `action == "rewrite"` and
`action_parameters.uri.path`:

| Cloudflare path rewrite | BytePlus `OriginRewriteAction` |
|---|---|
| `type: static`, a fixed new path, rule expression `http.request.uri.path eq "/a"` | `RewriteType: "rewrite_path"`, `SourcePath: "^/a$"`, `TargetPath: "/b"` |
| `type: static` with any other expression | **manual** — the match can't be expressed as one regex safely |
| `type: expression` (dynamic, e.g. `regex_replace(...)`) | **manual** — Cloudflare functions don't translate to a capture-group rewrite automatically |

`SourcePath` is a regular expression, so escape regex characters in the literal
path. Collected into `OriginRewrite: {"Switch": true, "OriginRewriteRule": [{"OriginRewriteAction": {…}}]}`
— an **object**, not a list.

### Access rules

Parse legacy `firewall_rules` (`filter.expression`) and
`http_request_firewall_custom` rules (`expression`):

| Expression | Field |
|---|---|
| `ip.src eq 192.0.2.1`, `ip.src in {192.0.2.1 198.51.100.0/24}` | IP |
| `ip.geoip.country eq "DE"`, `in {"DE" "FR"}` | geo |
| `http.user_agent eq "x"` / `contains "x"` | User-Agent (`contains` → `*x*`) |
| `http.referer eq "x"` / `contains "x"` | Referer (`contains` → `*x*`) |

Actions `block`, `challenge`, `js_challenge` and `managed_challenge` are
**deny**; `allow` is **allow**.

| Bucket | BytePlus |
|---|---|
| deny IP | `IpAccessRule: {"Switch": true, "RuleType": "deny", "Ip": […]}` |
| deny User-Agent | `UaAccessRule: {"Switch": true, "RuleType": "deny", "UserAgent": […], "IgnoreCase": true}` |
| deny Referer | `RefererAccessRule: {"Switch": true, "RuleType": "deny", "Referers": […], "AllowEmpty": false}` |
| **allow** | **not mapped** — see below |
| geo | **manual** — BytePlus uses its own region codes, not ISO country codes |

`Switch`, `RuleType` and the list are all required. `RuleType` is `deny` or
`allow` — **not** `blacklist` / `whitelist`, which the API rejects. And there is
no `FilterType` or `Filters` field: a payload using them is accepted and applies
**nothing** (that was HelloBP v1's bug).

Only map an expression that is a pure match on one field. An expression that
combines fields (`ip.src … and http.request.uri.path …`) or negates one
(`not ip.src …`) is **manual** — as a plain list it would block far more, or the
opposite, of what Cloudflare blocked.

> **Never turn a Cloudflare allow rule into `"RuleType": "allow"`.** On
> Cloudflare, `allow` means "skip the other security checks for these
> visitors". On a BytePlus access rule, `allow` means "block everyone who isn't
> on this list". Translating one into the other turns an allow-list into an
> outage. Report allow rules and migrate IP allow-lists with the WAF stage
> instead.

## 4. Settings that need the console

These have BytePlus equivalents, but the API shape or valid range differs
enough that automating them breaks. Put each one the zone uses into
`plan.manual_console_config` with its exact instruction:

| Cloudflare setting | BytePlus console path | Instruction |
|---|---|---|
| `browser_cache_ttl` | CDN Features → Caching → Browser Caching | Caching Policy = Always Cache, TTL = the Cloudflare value |
| `cache_level`, `edge_cache_ttl` | CDN Features → Caching → Caching Rules | recreate the levels and TTLs |
| `always_online` | CDN Features → Caching → Serve Stale | enable Serve Stale |
| `hotlink_protection` | CDN Features → Access Control → Referer Restrictions | allow `*.<zone>`, AllowEmpty off |
| `true_client_ip_header` | CDN Features → Origin Fetch → Origin Request Headers | set `True-Client-IP` from the client-IP system variable |
| `proxy_read_timeout` | CDN Features → Origin Fetch → Request Time-out | set the HTTP timeout (BytePlus default 60s) |
| `minify` | CDN Features → Content Optimization → File Minification | enable for the minified types; cannot be on together with compression |
| `sort_query_string_for_cache` | CDN Features → Caching → Cache Key | include all query parameters |
| `tls_1_3` | Encryption Policy → TLS Versions | include or exclude TLSv1.3 to match |

## 5. Coverage — what you tell the user

List every feature the zone actually uses, in exactly one bucket. **Omitting a
feature is the one failure this skill must never have.**

**Auto** — the execute stage configures it:
delivery policies (one per origin group) · the encryption policy · header
transforms · exact-path URL redirects · static path rewrites · IP, User-Agent and
Referer **block** rules

**Manual** — has a BytePlus equivalent, needs a person or another stage:

| Feature | What to do |
|---|---|
| allow rules | WAF stage for IP allow-lists |
| geo rules | console, BytePlus region codes |
| console settings | `plan.manual_console_config` |
| DNS cutover | cutover stage |
| DNS types outside A/AAAA/CNAME/TXT/MX that BytePlus supports | recreate |
| rate limiting (`http_ratelimit`) | WAF CC rules — `CreateCCRule` |
| legacy firewall rules | WAF stage for pure IP lists; WAF `AccurateGroup` rules for expressions |
| wildcard redirects, dynamic redirects, dynamic rewrites | CDN Rules Engine |
| compound or negated firewall expressions | WAF custom rules (`AccurateGroup`) |
| origin rules, config rules | CDN Rules Engine |
| managed WAF rulesets | degrades to BytePlus vulnerability protection + system bots; per-rule overrides don't carry |
| load balancers | origin weights carry; active health checks don't |
| Workers routes | port to Edge Functions by hand |
| custom error rules | Custom Error Pages — Cloudflare doesn't export the HTML |
| Logpush jobs | CDN real-time log delivery; fields differ |
| healthchecks | no active health checks on BytePlus CDN |
| DNSSEC active | recreate the DS record at the registrar |

**Unsupported** — Cloudflare-only; report it, never drop it:
Waiting Rooms · Spectrum · Zaraz · Page Shield · Cache Reserve · Network Error
Logging · Access / Zero Trust apps · Custom Hostnames (SSL for SaaS) · DDoS L7
overrides · Snippets · custom log fields · DNS record types with no BytePlus
equivalent

## Plan file

`plan.json`:

```json
{
  "domain": "example.com",
  "zone_id": "<cloudflare zone id>",
  "ssl_mode": "full",
  "origin_groups": [
    {"template_title": "policy-example-com",
     "origin": {"host": "192.0.2.10", "type": "A", "instance_type": "ip", "cf_worker": false},
     "domains": ["example.com", "api.example.com"]}
  ],
  "cdn_domains": ["example.com", "api.example.com"],
  "dns_records": [],
  "cipher_payload": {"HTTPS": {}},
  "service_payloads": {"policy-example-com": {}},
  "manual_console_config": [],
  "coverage": {"auto": [], "manual": [], "unsupported": []},
  "warnings": []
}
```

Store the finished payloads in the plan, so the execute stage sends exactly
what the user reviewed.
