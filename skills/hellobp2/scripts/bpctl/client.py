"""
client.py — the BytePlus transport.

One class talks to every BytePlus service. It owns four things that are easy to
get wrong and expensive to debug:

* **Serialisation.** The body hash in the Authorization header must match the
  bytes on the wire exactly. The body is serialised once, here, and the same
  ``bytes`` object is both hashed and sent. Nothing re-encodes it downstream.
* **Dry-run.** Reads are allowed to hit the live API; writes are simulated
  unless live mode is on. HelloBP v1 blocked reads too, which made exploring an
  account impossible without arming the tool for writes — exactly backwards.
* **Errors.** BytePlus reports failures two different ways: a non-2xx status,
  and HTTP 200 with an error buried in ``ResponseMetadata.Error``. Both are
  raised as ``BytePlusError`` so a caller cannot mistake a failure for success.
* **Diagnosis.** A 401 from a signing mismatch is indistinguishable from a 401
  from a clock-skew or a wrong region unless you check. We check.

Zero third-party dependencies: stdlib ``urllib`` only.
"""

from __future__ import annotations

import email.utils
import datetime
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import signer
from .services import ServiceSpec, method_for, resolve

# Action name prefixes that only read. Anything not matching one of these is
# treated as a write and therefore blocked in dry-run mode. Defaulting unknown
# actions to "write" is the fail-safe direction: the cost of wrongly simulating
# a read is a confusing message, the cost of wrongly executing a write is real.
READ_PREFIXES = (
    "Describe",
    "List",
    "Query",
    "Get",
    "Check",
)

USER_AGENT = "bpctl/2.0 (+HelloBP V2)"


class BytePlusError(RuntimeError):
    """A BytePlus API call failed. Carries the structured error when there is one."""

    def __init__(self, message, *, status=None, code=None, request_id=None, hint=None, body=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.hint = hint
        self.body = body

    def __str__(self) -> str:
        # BytePlus messages sometimes carry trailing whitespace; strip each part
        # so the rendered error does not end in a stray space.
        parts = [super().__str__().strip()]
        if self.request_id:
            parts.append(f"(RequestId {self.request_id})")
        if self.hint:
            parts.append("\n  hint: " + self.hint)
        return " ".join(parts).strip()


def is_write_action(action: str, spec: ServiceSpec | None = None) -> bool:
    """
    True unless the action name clearly identifies a read-only operation.

    Some products prefix every action with the product name — the Certificate
    Service's ``CertificateGetInstance`` is a read, but the ``Get`` verb is
    hidden behind ``Certificate``. Strip the service's known prefix first.
    """
    name = action
    if spec and spec.action_prefix and name.startswith(spec.action_prefix):
        stripped = name[len(spec.action_prefix):]
        # Only strip when a verb remains, so a bare "Certificate" action name
        # is still treated as a write.
        if stripped:
            name = stripped
    return not name.startswith(READ_PREFIXES)


@dataclass
class Response:
    ok: bool
    action: str
    service: str
    status: int | None
    body: dict
    dry_run: bool = False
    request: dict = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "service": self.service,
            "action": self.action,
            "status": self.status,
            "dry_run": self.dry_run,
            "elapsed_ms": self.elapsed_ms,
            "request": self.request,
            "response": self.body,
        }


class Client:
    """
    A signed BytePlus OpenAPI caller.

    Args:
        credentials: A ``config.Credentials``.
        live:        When False (default), write actions are simulated.
        timeout:     Per-request timeout in seconds.
        retries:     Retry attempts for 429 and 5xx responses.
        host_overrides: ``{service_key: host}``, used by ``bpctl probe`` and by
                     verified.json to pin a host that differs from the doc.
    """

    def __init__(
        self,
        credentials,
        *,
        live: bool = False,
        timeout: int = 30,
        retries: int = 2,
        host_overrides: dict | None = None,
        sign_host: bool = False,
    ):
        self.credentials = credentials
        self.live = live
        self.timeout = timeout
        self.retries = retries
        self.host_overrides = host_overrides or {}
        # Whether to send and sign an explicit Host header. Both variants are
        # valid (the server verifies exactly what you declare in SignedHeaders);
        # `bpctl probe` determines which one an account accepts.
        self.sign_host = sign_host
        self.last_signed: signer.SignedRequest | None = None

    # ── Public API ───────────────────────────────────────────────────────────

    def call(
        self,
        service: str,
        action: str,
        payload: dict | None = None,
        *,
        method: str | None = None,
        query: dict | None = None,
        host: str | None = None,
        sign_host: bool | None = None,
    ) -> Response:
        """
        Invoke one BytePlus action.

        ``payload`` becomes the JSON body for POST actions, or is merged into
        the signed query string for GET actions — the BytePlus convention, and
        the reason a caller should not have to think about method at all.
        """
        spec = resolve(service)
        http_method = (method or method_for(spec, action)).upper()
        target_host = host or self.host_overrides.get(spec.key) or spec.host
        payload = payload or {}

        params = {"Action": action, "Version": spec.version}
        params.update(query or {})

        if http_method == "GET":
            # GET actions carry everything in the signed query string. Nested
            # values cannot survive that, so reject them here rather than
            # producing a signature over a stringified dict.
            for key, value in payload.items():
                if isinstance(value, (dict, list)):
                    raise ValueError(
                        f"{action} is a GET action and cannot take a nested value for "
                        f"{key!r}; pass a scalar, or force --method POST if the API "
                        "actually accepts a body."
                    )
                params[key] = value
            body = b""
            content_type = None
        else:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            content_type = "application/json"

        if not self.live and is_write_action(action, spec):
            return Response(
                ok=True,
                action=action,
                service=spec.key,
                status=None,
                dry_run=True,
                request={
                    "method": http_method,
                    "host": target_host,
                    "query": params,
                    # For GET the payload is already merged into the query;
                    # repeating it here would misrepresent the request.
                    "body": None if http_method == "GET" else payload,
                },
                body={
                    "dry_run": True,
                    "note": (
                        f"{action} is a write action and live mode is off. "
                        "Nothing was sent. Re-run with --live to execute."
                    ),
                },
            )

        self.credentials.require()
        started = time.monotonic()

        signed = signer.sign(
            method=http_method,
            host=target_host,
            service=spec.signing_name,
            region=self.credentials.region,
            access_key=self.credentials.access_key,
            secret_key=self.credentials.secret_key,
            query=params,
            body=body,
            content_type=content_type,
            sign_host=self.sign_host if sign_host is None else sign_host,
            session_token=self.credentials.session_token,
        )
        self.last_signed = signed

        status, headers, raw = self._send(signed)
        elapsed = int((time.monotonic() - started) * 1000)

        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw.decode("utf-8", "replace")[:4000]}

        self._raise_for_error(status, headers, parsed, spec, action, signed)

        return Response(
            ok=True,
            action=action,
            service=spec.key,
            status=status,
            body=parsed,
            request={
                "method": http_method,
                "host": target_host,
                "query": params,
                "body": payload if http_method != "GET" else None,
            },
            elapsed_ms=elapsed,
        )

    # ── Transport ────────────────────────────────────────────────────────────

    def _send(self, signed: signer.SignedRequest):
        """Issue the request, retrying throttles and server errors with backoff."""
        attempt = 0
        while True:
            request = urllib.request.Request(
                signed.url,
                data=signed.body if signed.method != "GET" else None,
                method=signed.method,
            )
            for name, value in signed.headers.items():
                request.add_header(name, value)
            request.add_header("User-Agent", USER_AGENT)
            request.add_header("Accept", "application/json")

            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read()
                headers = dict(exc.headers or {})
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if retryable and attempt < self.retries:
                    attempt += 1
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                return exc.code, headers, body
            except urllib.error.URLError as exc:
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise BytePlusError(
                    f"could not reach {signed.url.split('?')[0]}: {exc.reason}",
                    hint="Check network access and that the host name is correct.",
                ) from exc

    # ── Error interpretation ─────────────────────────────────────────────────

    def _raise_for_error(self, status, headers, parsed, spec: ServiceSpec, action, signed):
        metadata = parsed.get("ResponseMetadata") or {}
        error = metadata.get("Error") or {}
        request_id = metadata.get("RequestId")
        code = error.get("Code") or ""
        message = error.get("Message") or ""

        # BytePlus returns 200 with an embedded error often enough that
        # checking status alone silently turns failures into successes.
        if 200 <= status < 300 and not code:
            return

        if not message:
            message = parsed.get("_raw") or f"HTTP {status} with no error body"

        raise BytePlusError(
            f"{spec.key}:{action} failed — {code + ': ' if code else ''}{message}",
            status=status,
            code=code,
            request_id=request_id,
            hint=self._hint_for(status, code, headers, spec, signed),
            body=parsed,
        )

    def _hint_for(self, status, code, headers, spec: ServiceSpec, signed):
        """Turn an opaque failure into the next thing to actually try."""
        # An unrecognised access key is not a signing problem, and sending the
        # caller off to check clock skew and regions wastes their time.
        if code in ("InvalidAccessKey", "InvalidAccessKeyId"):
            return (
                "BytePlus does not recognise this access key. Confirm it is an "
                "active key for the intended account, and that access key and "
                "secret key are from the same pair."
            )

        if status == 401 or "Signature" in (code or ""):
            skew = self._clock_skew(headers)
            causes = []
            if skew is not None and abs(skew) > 120:
                causes.append(
                    f"your clock is {skew:+.0f}s from the server — BytePlus rejects "
                    "signatures beyond ~5 minutes of skew. Fix the system clock first."
                )
            causes.append(
                f"region mismatch (signing for {self.credentials.region!r}; the region "
                "must match your account's home region)"
            )
            causes.append("whitespace in a pasted access key or secret key")
            if spec.alt_hosts:
                causes.append(
                    f"wrong host — this service is also served at "
                    f"{', '.join(spec.alt_hosts)}; run `bpctl probe {spec.key}`"
                )
            return "signature rejected. Likely causes: " + "; ".join(causes)

        if status == 403:
            return (
                f"authenticated but not authorised. Confirm the account has {spec.key.upper()} "
                "API access and that the access key's policy allows this action."
            )

        if status == 404 or code == "InvalidActionOrVersion":
            return (
                f"{spec.key} has no such action at version {spec.version}. "
                f"Check the action catalog (`bpctl catalog {spec.key}`) or {spec.doc}"
            )

        if status == 429:
            return "throttled after retries — reduce concurrency or add a delay between calls."

        return None

    @staticmethod
    def _clock_skew(headers) -> float | None:
        """Local clock minus server clock, in seconds, from the response Date header."""
        raw = headers.get("Date") or headers.get("date")
        if not raw:
            return None
        try:
            server = email.utils.parsedate_to_datetime(raw)
            if server.tzinfo is None:
                server = server.replace(tzinfo=datetime.timezone.utc)
            return (datetime.datetime.now(datetime.timezone.utc) - server).total_seconds()
        except (TypeError, ValueError):
            return None


# ── Pagination ───────────────────────────────────────────────────────────────
#
# List actions default to PageSize 10 and simply stop — no error, no flag. The
# only tell is Result.Total. Some endpoints also cap the page size below what
# was asked for, so paging follows Total when the API reports it and only falls
# back to "a short page is the last page" when it does not.

_ITEM_KEYS = (
    "Data", "DomainList", "CertInfo", "CertList", "CertInfoList", "Items",
    "List", "Zones", "Records", "Domains", "DomainInfos", "Rules",
)


def extract_items(result) -> tuple:
    """Return ``(rows, key)`` from a ``Result`` object, or ``([], None)``."""
    if not isinstance(result, dict):
        return [], None
    for key in _ITEM_KEYS:
        if isinstance(result.get(key), list):
            return result[key], key
    for key, value in result.items():
        if isinstance(value, list):
            return value, key
    return [], None


def _reported_total(result: dict):
    for key in ("Total", "TotalCount", "TotalNum"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def list_all(client, service, action, payload=None, *, page_size=100,
             page_param="PageNum", size_param="PageSize", max_pages=200) -> dict:
    """Read every page of a List* action. ``complete`` is False if Total was never reached."""
    items, key, total, pages, page = [], None, None, 0, 1
    while pages < max_pages:
        request = dict(payload or {})
        request[page_param] = page
        request[size_param] = page_size
        result = (client.call(service, action, request).body or {}).get("Result") or {}
        chunk, found = extract_items(result)
        key = key or found
        if isinstance(result, dict) and _reported_total(result) is not None:
            total = _reported_total(result)
        items.extend(chunk)
        pages += 1
        if not chunk:
            break
        if total is not None:
            if len(items) >= total:
                break
        elif len(chunk) < page_size:
            break
        page += 1
    return {
        "items": items,
        "count": len(items),
        "total": total if total is not None else len(items),
        "complete": total is None or len(items) >= total,
        "pages": pages,
        "items_key": key,
    }
