"""
cloudflare.py — a thin Cloudflare API v4 caller.

Cloudflare needs no request signing: it takes a plain bearer token, so this
adds none of the machinery `signer.py` exists for. It is here for the other
three things bpctl provides, which matter just as much:

1. **The token never reaches argv.** It is resolved from the environment or a
   file, so it does not land in a shell history, a process list, or an agent
   transcript. A token pasted into a `curl` command leaks into all three.
2. **Writes are simulated by default.** Publishing a DNS record is a real
   change to a real zone; it should be as hard to do by accident as a BytePlus
   write is.
3. **One output shape.** Callers branch on `ok` regardless of which API they
   just talked to.

The read/write split here is cleaner than the BytePlus one: HTTP already tells
us. GET and HEAD are reads; everything else is a write.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API_BASE = "https://api.cloudflare.com/client/v4"
USER_AGENT = "bpctl/2.0 (+HelloBP 2.0)"

READ_METHODS = ("GET", "HEAD")


class CloudflareError(RuntimeError):
    """A Cloudflare API call failed. Carries the structured error when there is one."""

    def __init__(self, message, *, status=None, code=None, hint=None, body=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.hint = hint
        self.body = body

    def __str__(self) -> str:
        parts = [super().__str__().strip()]
        if self.code:
            parts.append(f"(code {self.code})")
        if self.hint:
            parts.append("\n  hint: " + self.hint)
        return " ".join(parts).strip()


def is_write_method(method: str) -> bool:
    return method.upper() not in READ_METHODS


# Cloudflare's error codes are numerous but a handful account for most of what
# goes wrong in practice, and each has a specific fix worth naming.
_HINTS = {
    10000: "Token is missing, malformed, or lacks permission for this route. "
           "Check the token's zone scope and permissions.",
    9109: "Token lacks permission for this zone. DNS writes need Zone:DNS:Edit "
          "on the specific zone, not just Zone:Read.",
    7003: "No route for that path — check the zone id and the path spelling.",
    81044: "Record already exists with that name and content.",
    81057: "A record with this name and content already exists in this zone.",
    1004: "DNS validation failed — check the record type, name and content.",
}


def _hint_for(errors: list) -> str | None:
    for err in errors or []:
        if isinstance(err, dict) and err.get("code") in _HINTS:
            return _HINTS[err["code"]]
    return None


@dataclass
class CFResponse:
    ok: bool
    method: str
    path: str
    status: int | None
    body: dict
    dry_run: bool = False
    request: dict = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "service": "cloudflare",
            "action": f"{self.method} {self.path}",
            "status": self.status,
            "dry_run": self.dry_run,
            "elapsed_ms": self.elapsed_ms,
            "request": self.request,
            "response": self.body,
        }


class CloudflareClient:
    """
    Args:
        credentials: a ``config.CloudflareCredentials``.
        live:        when False (default), non-GET requests are simulated.
        timeout:     per-request timeout in seconds.
    """

    def __init__(self, credentials, *, live: bool = False, timeout: int = 20):
        self.credentials = credentials
        self.live = live
        self.timeout = timeout

    def call(self, method: str, path: str, *, params: dict | None = None,
             body: dict | None = None) -> CFResponse:
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path

        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{API_BASE}{path}" + (f"?{query}" if query else "")

        request_summary = {
            "method": method,
            "url": url,
            "body": body,
        }

        if is_write_method(method) and not self.live:
            return CFResponse(
                ok=True,
                method=method,
                path=path,
                status=None,
                dry_run=True,
                request=request_summary,
                body={
                    "dry_run": True,
                    "note": (
                        f"{method} is a write and live mode is off. Nothing was sent. "
                        "Re-run with --live to execute."
                    ),
                },
            )

        self.credentials.require()

        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("Authorization", f"Bearer {self.credentials.api_token}")
        req.add_header("User-Agent", USER_AGENT)
        if payload is not None:
            req.add_header("Content-Type", "application/json")

        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise CloudflareError(
                f"cloudflare: could not reach {API_BASE} — {exc.reason}",
                hint="Check network access and that api.cloudflare.com resolves.",
            ) from exc

        elapsed_ms = int((time.time() - started) * 1000)

        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise CloudflareError(
                f"cloudflare: {method} {path} returned non-JSON (HTTP {status})",
                status=status,
                body={"raw": raw[:500]},
            )

        # Cloudflare reports failure in the envelope's `success` field, which can
        # be false on an HTTP 200. Never treat the status alone as the verdict.
        if not parsed.get("success", False):
            errors = parsed.get("errors") or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            message = first.get("message") or f"HTTP {status}"
            raise CloudflareError(
                f"cloudflare: {method} {path} failed — {message}",
                status=status,
                code=first.get("code"),
                hint=_hint_for(errors),
                body=parsed,
            )

        return CFResponse(
            ok=True,
            method=method,
            path=path,
            status=status,
            body=parsed,
            request=request_summary,
            elapsed_ms=elapsed_ms,
        )
