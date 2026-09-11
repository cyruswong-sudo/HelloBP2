# BytePlus API signing

`bpctl` handles all of this. Read this file when a request is being rejected and
you need to know *why*, or when you are extending `bpctl` itself.

## The algorithm

Every BytePlus service uses one HMAC-SHA256 scheme ("SignerV4"). Only three
values change per service: **host**, **signing name**, **API version**.

Canonical request, joined with `\n`:

```
METHOD
<percent-encoded path, "/" left literal>
<query: keys sorted, each key and value encoded with only -_.~ unreserved>
<canonical headers, each "name:value\n", names lowercased and sorted>
<signed header names, ";"-joined>
<hex sha256 of the body>
```

String to sign:

```
HMAC-SHA256
<X-Date: YYYYMMDDTHHMMSSZ>
<date>/<region>/<service>/request
<hex sha256 of the canonical request>
```

Signing key — four chained HMACs:

```
HMAC(HMAC(HMAC(HMAC(utf8(secret_key), date), region), service), "request")
```

Two details that break naive AWS SigV4 ports:

- The secret key is used **as-is** — the base64-looking string from the console,
  UTF-8 encoded. It is *not* base64-decoded, and there is no `"BytePlus"` prefix.
- The `service` value is case-sensitive and inconsistent across products: `CDN`
  and `DNS` are uppercase, `waf` and `certificate_service` are lowercase.

## Which headers get signed

A header is signed if it is `Content-Type`, `Content-Md5`, `Host`, or starts
with `X-`. The set is derived from the headers actually present, so:

- Send no `Host` header → `SignedHeaders=content-type;x-content-sha256;x-date`
- Send a `Host` header → `SignedHeaders=content-type;host;x-content-sha256;x-date`

**Both are valid.** The server verifies exactly the headers you declare. HelloBP
v1 concluded that "host causes SignatureDoesNotMatch" and hardcoded a fixed
header list; the real cause was sending `Host` without declaring it. `bpctl`
derives the set, matching the official SDK, so there is nothing to configure.
`bpctl probe` still tests both variants per account.

`X-Content-Sha256` must be the hash of the **exact bytes on the wire**. Serialise
the body once and hash that same object; re-encoding between hashing and sending
is the classic silent failure.

## GET vs POST

| Service | Reads | Writes |
|---|---|---|
| CDN | POST | POST |
| WAF | POST | POST |
| DNS | **GET** | POST |
| Certificate | **GET** (`CertificateGetDcvParam`, `CertificateGetInstance`) | POST |

GET actions put every parameter in the signed query string and sign an **empty**
body — the hash of zero bytes, not of `{}`. `bpctl` picks the method from the
action name; override with `--method` if an API disagrees.

## Regions

The region is part of the credential scope, so a mismatch fails as a signature
error rather than a helpful one.

| Region | Use for |
|---|---|
| `singapore` | Default for most BytePlus accounts |
| `ap-southeast-1` | Asia Pacific Region 1 |
| `ap-southeast-3` | Asia Pacific Region 2 — Indonesia, Philippines, Vietnam, Taiwan |

## Decoding a 401

`SignatureDoesNotMatch` has four common causes and the error names none of them.
In rough order of likelihood:

1. **Wrong region.** Check the account's home region against `BYTEPLUS_REGION`.
2. **Clock skew.** BytePlus rejects signatures more than ~5 minutes out. `bpctl`
   compares your clock to the server's `Date` header and says so in the hint.
3. **Whitespace in a credential.** A trailing newline from a copy-paste changes
   the derived key. `bpctl` strips whitespace on load.
4. **Wrong host.** WAF is served at both `waf.byteplusapi.com` and
   `open.byteplusapi.com`; only one may sign for a given account.

`bpctl probe --save` distinguishes all four using read-only calls, and pins the
answer in `verified.json`.

A `403` is different: the signature was accepted and the *account* lacks
permission. A `404` with `InvalidActionOrVersion` means the action name does not
exist at that API version — check spelling against the product docs.
