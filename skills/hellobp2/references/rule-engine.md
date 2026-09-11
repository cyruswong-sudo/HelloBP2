# BytePlus CDN Rules Engine

A third policy type alongside delivery and encryption policies, created with
`CreateRuleEngineTemplate`. It is the only place in BytePlus CDN where behaviour
can be **conditional**, which makes it the natural target for Cloudflare's
ruleset engine — Transform Rules, Cache Rules, Redirect Rules, Origin Rules, and
header modification.

HelloBP v1 did not use it at all, which is why v1 flattened every Cloudflare rule
into unconditional policy fields and lost the conditions.

## When to use it

Use the **delivery policy** for settings that apply to the whole domain
unconditionally — one origin, one cache TTL, compression on.

Use the **Rules Engine** when behaviour depends on the request: a cache TTL that
differs by path, a header added only for one User-Agent, a redirect for a
specific query string. Reaching for it unconditionally adds a third policy per
domain for no benefit.

## Grammar

A rule is an `if` block: a **condition** tree plus a list of **actions**.

### Conditions

| Object | Matches |
|---|---|
| `always` | Everything — unconditional action |
| `client_ip` | Client IP address |
| `client_area` | Client geographic region |
| `request_header` | A named request header |
| `http_referer` | `Referer` |
| `http_ua` | `User-Agent` |
| `http_origin` | `Origin` |
| `http_method` | `GET`, `POST`, … |
| `protocol` | `http` / `https` |
| `url` | Full URL |
| `path` | Path only |
| `path_and_param` | Path plus query string |
| `query_string` | Query string only |
| `response_header` | A named response header |
| `http_code` | Response status code |
| `request_time` | Time of day / day of week |

Combine with `Connective`: `and` / `or`.

### Operators

`equal`, `not_equal`, `match`, `not_match`, `prefix_match`, `prefix_not_match`,
`suffix_match`, `suffix_not_match`, `exist`, `not_exist`, `regex_match`,
`regex_not_match`, `belong_to`, `not_belong_to`

### Actions

| Action | Effect |
|---|---|
| `deny_access` / `allow_access` | Block or permit |
| `redirect_protocol` | HTTP ↔ HTTPS |
| `redirect_request` | 301/302 to a new URL |
| `request_header` | Modify the request header |
| `response_header` | Modify the client response header |
| `origin_request_header` | Modify the origin request header |
| `origin_response_header` | Modify the origin response header |
| `origin_host` | Override the origin Host |
| `cache_time` | Set edge cache TTL |
| `cache_key` | Set the cache key |
| `compression` | Control compression |
| `video_drag` | Video seeking |
| `origin_range` | Range origin fetch |
| `origin_follow` | Follow origin redirects |
| `origin_tcp_timeout` | TCP connect timeout |
| `origin_http_timeout` | HTTP read timeout |
| `download_speed_limit` | Throttle transfer rate |

Header actions take `set`, `delete`, or `append`.

## Mapping from Cloudflare

| Cloudflare | Rules Engine equivalent |
|---|---|
| `http.request.uri.path` | `path` |
| `http.request.full_uri` | `url` |
| `http.request.method` | `http_method` |
| `http.user_agent` | `http_ua` |
| `http.referer` | `http_referer` |
| `ip.src` | `client_ip` |
| `ip.geoip.country` | `client_area` |
| `http.request.headers[...]` | `request_header` |
| `contains` | `match` |
| `matches` (regex) | `regex_match` |
| `starts_with` / `ends_with` | `prefix_match` / `suffix_match` |
| `in {...}` | `belong_to` |
| Redirect Rules | `redirect_request` |
| Transform Rules (headers) | `request_header` / `response_header` |
| Cache Rules (TTL) | `cache_time` |
| Cache Rules (cache key) | `cache_key` |
| Origin Rules | `origin_host` |

**No equivalent**, and must be reported rather than approximated: Cloudflare
Workers and Snippets (port to Edge Functions by hand), rate limiting (use WAF
`CreateCCRule` instead), and bot-score conditions (`cf.bot_management.*`).

## Payload shape

The `Rule` field of `CreateRuleEngineTemplate` is an **encoded string**, not a
JSON object. BytePlus publishes a helper that builds it:

```bash
pip install cdn_rule_engine_sdk
```

```python
from cdn_rule_engine_sdk.rule_engine.Rule import Rule, Condition, Action
from cdn_rule_engine_sdk.rule_engine.Const import Const

rule = Rule()
rule.desc = "Cache /static/* for 7 days"
rule.if_block.condition = Condition({
    "IsGroup": False,
    "Connective": Const.ConnectiveAnd,
    "Condition": {
        "Object": Const.ConditionHTTPPath,
        "Operator": Const.OperatorPrefixMatch,
        "Value": ["/static/"],
    },
})
rule.if_block.actions.append(Action({
    "Action": Const.ActionCacheTime,
    "Groups": [{
        "Dimension": Const.ActionCacheTime,
        "GroupParameters": [{"Parameters": [
            {"Name": "ttl", "Values": ["604800"]},
            {"Name": "ttl_unit", "Values": ["sec"]},
            {"Name": "cache_policy", "Values": [Const.CacheDefault]},
        ]}],
    }],
}))

payload = {"Project": "default", "Title": "static-cache", "Rule": rule.encode_to_string()}
```

Then submit the encoded result:

```bash
bpctl call cdn CreateRuleEngineTemplate --body-file /tmp/rule.json
```

`cdn_rule_engine_sdk` is the one place bpctl's zero-dependency rule does not
hold — the encoding is not documented independently of the SDK. Treat it as an
optional extra, needed only for Rules Engine work.
