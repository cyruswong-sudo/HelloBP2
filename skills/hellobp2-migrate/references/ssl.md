# Stage 4 — Certificates

Issue free DV certificates, validate them through DNS, and deploy them to CDN
domains. This stage also stands on its own: use it whenever the user needs
certificates, migration or not.

Two API facts shape everything here:

- **The Certificate Service has no list endpoint.** Every `CertificateList*`
  name returns 404. Enumerate certificates through the **CDN** service.
- **`BatchDeployCert` takes `Domain` as a comma-joined string**, 50 names at
  most per call — not an array.

## 1. Know what already exists

```bash
bpctl call cdn ListCdnCertInfo --all --page-size 100
```

`PageSize` above 100 is rejected. For each certificate, report `CertId`, name
(`Desc` or `CertName`), `ExpireTime` and `Status`. `ExpireTime` is epoch
seconds; flag every certificate that has **expired** or expires within 30 days.

To see where one certificate is deployed:

```bash
bpctl call cdn ListCdnCertInfo -p CertId=<cert_id>
```

`ConfiguredDomain` is a comma-separated string of the domains it's bound to.

And which domains still need one:

```bash
bpctl call cdn ListCdnDomains --all
```

A domain with `HTTPS: false` serves plain HTTP. A domain whose certificate has
expired is **worse off**: it still advertises HTTPS, so browsers show a full
warning page instead of falling back quietly.

## 2. Decide what to order

A certificate covers a hostname when its name matches exactly, or when it is a
wildcard one label up: `*.example.com` covers `www.example.com` but **not**
`example.com` and **not** `a.b.example.com`.

One wildcard often replaces many single-name certificates — one order, one
validation, one renewal to track. Suggest it when several hostnames share a
parent domain.

## 3. Request

```bash
bpctl call certificate CertificateAddFreeInstance --body '{
  "plan": "lets_encrypt_standard_dv",
  "ssl": {
    "common_name": "*.example.com",
    "order_validation_type": "dns_txt",
    "san": ["*.example.com"],
    "certificate": {"csr": "", "private_key": "", "key_type": "rsa"}
  }
}'
```

`Result` is the certificate instance id.

## 4. Get the validation record

```bash
bpctl call certificate CertificateGetDcvParam -p instance_id=<cert_id>
```

`Result.domains_to_be_validated[]` holds `validation_domain` (where the TXT
record goes) and `value` (its content).

## 5. Publish it

**DNS on Cloudflare** — needs a token with DNS:Edit:

```bash
bpctl cf post /zones/<zone_id>/dns_records \
  --body '{"type":"TXT","name":"<validation_domain>","content":"<value>","ttl":60}'
```

Cloudflare error code **81057** or **81058** means the record already exists —
treat it as success.

**DNS elsewhere** — give the user the exact name and value to add at their
provider, then wait for them to confirm.

If the domain has **no working DNS delegation at all**, stop: it can't be
validated, and it can't serve traffic either. Tell the user.

## 6. Wait for issuance

```bash
bpctl call certificate CertificateGetInstance -p instance_id=<cert_id>
```

Issued when `Result.content[0].certificate_exist` is `1`, or its `status` is
`issued` / `active`. Check every 30–60 seconds; DNS propagation usually takes a
few minutes. Give up after about 15 minutes and report the certificate as
pending rather than looping forever.

## 7. Deploy

Deploy only to domains the certificate covers and that aren't already bound to
it. Batch at 50:

```bash
bpctl call cdn BatchDeployCert --body '{
  "CertId": "<cert_id>",
  "Domain": "www.example.com,api.example.com,static.example.com"
}'
```

Per-domain results may come back in `Result.DeployResult` (or `DomainResults`,
`Results`): a `Status` or `Code` of `0` / `"success"` is success; anything else
carries a message. When there's no per-domain breakdown, the call succeeded for
the whole batch.

Then confirm from the account, not from the deploy response:

```bash
bpctl call cdn ListCdnCertInfo -p CertId=<cert_id>
```

### If a domain still serves HTTP

Domains added with `HTTPSSwitch: "off"` may need HTTPS switched on explicitly
with the certificate:

```bash
bpctl call cdn UpdateTemplateDomain --body '{
  "Domain": "www.example.com",
  "ServiceTemplateId": "<policy id>",
  "CipherTemplateId": "<cipher id>",
  "CertId": "<cert_id>",
  "HTTPSSwitch": "on",
  "ServiceRegion": "outside_chinese_mainland"
}'
```

## Renewal

Free DV certificates last **90 days** and nothing here renews them. Tell the
user the expiry date and that a renewal has to be scheduled — a batch of
certificates ordered together will all expire in the same week.
