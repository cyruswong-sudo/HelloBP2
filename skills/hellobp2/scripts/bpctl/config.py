"""
config.py — credential and mode resolution.

Design rules, in priority order:

1. **Secrets never appear in output.** Every rendering path here masks. An
   agent driving bpctl will happily echo whatever it is given, so the tool must
   make leaking hard rather than trusting the caller.
2. **Environment variables come first.** Agents run in ephemeral shells; a file
   on disk is the fallback, not the interface.
3. **Writes are opt-in.** Dry-run is the default in every code path. Live mode
   requires an explicit flag or environment variable, never a config-file
   setting that someone forgot they turned on.

Resolution order (first hit wins, per field):

    explicit argument  →  BYTEPLUS_* env var  →  ~/.byteplus/config  →  ./bpctl.json

``~/.byteplus/config`` is the path the official BytePlus SDK already reads, so
an environment set up for the SDK works here with no extra steps.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .services import DEFAULT_REGION

SDK_CONFIG_PATH = Path.home() / ".byteplus" / "config"
CF_CONFIG_PATH = Path.home() / ".cloudflare" / "config"
LOCAL_CONFIG_NAME = "bpctl.json"

# The official SDK reads BYTEPLUS_ACCESSKEY / BYTEPLUS_SECRETKEY. The
# underscored spellings are what people actually type. Accept both.
_ACCESS_KEY_VARS = ("BYTEPLUS_ACCESS_KEY", "BYTEPLUS_ACCESSKEY")
_SECRET_KEY_VARS = ("BYTEPLUS_SECRET_KEY", "BYTEPLUS_SECRETKEY")
_TOKEN_VARS = ("BYTEPLUS_SESSION_TOKEN", "BYTEPLUS_SECURITY_TOKEN")
# Cloudflare uses a plain bearer token, so there is no signing to get wrong —
# but the token still must never reach argv, a shell history, or a transcript.
# Resolving it from a file is the whole point of routing CF through bpctl.
_CF_TOKEN_VARS = ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN")


def _first_env(names: tuple) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _load_json(path: Path) -> dict:
    try:
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        # A malformed or unreadable config file must not take the tool down;
        # the caller gets a clear "credentials missing" error instead.
        pass
    return {}


def mask(secret: str | None) -> str:
    """Render a secret safely. Short values are hidden entirely, never hinted at."""
    if not secret:
        return "(not set)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


@dataclass
class Credentials:
    access_key: str = ""
    secret_key: str = ""
    region: str = DEFAULT_REGION
    session_token: str | None = None
    # Where each value came from, for `bpctl whoami`. Diagnosing "wrong
    # account" is otherwise guesswork when several sources are in play.
    sources: dict = None

    def __post_init__(self):
        if self.sources is None:
            self.sources = {}

    @property
    def complete(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def require(self) -> None:
        if self.complete:
            return
        missing = []
        if not self.access_key:
            missing.append("access key")
        if not self.secret_key:
            missing.append("secret key")
        raise SystemExit(
            f"bpctl: missing BytePlus {' and '.join(missing)}.\n"
            "\n"
            "Set them in the environment:\n"
            "    export BYTEPLUS_ACCESS_KEY=...\n"
            "    export BYTEPLUS_SECRET_KEY=...\n"
            "\n"
            f"or write {SDK_CONFIG_PATH} as {{\"ak\": \"...\", \"sk\": \"...\"}} "
            "(the path the official BytePlus SDK also reads)."
        )

    def describe(self) -> str:
        lines = [
            f"  access key : {mask(self.access_key)}   [{self.sources.get('access_key', '-')}]",
            f"  secret key : {mask(self.secret_key)}   [{self.sources.get('secret_key', '-')}]",
            f"  region     : {self.region}   [{self.sources.get('region', 'default')}]",
        ]
        if self.session_token:
            lines.append(
                f"  sts token  : {mask(self.session_token)}   "
                f"[{self.sources.get('session_token', '-')}]"
            )
        return "\n".join(lines)


def load_credentials(
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
    config_path: str | None = None,
) -> Credentials:
    """
    Resolve credentials from arguments, environment, and config files.

    Values are resolved per-field, so an access key from the environment can be
    paired with a region from a config file. Whitespace is stripped from every
    credential field — a trailing newline from a copy-paste is otherwise an
    unexplainable 401.
    """
    creds = Credentials()

    explicit = {
        "access_key": (access_key or "").strip() or None,
        "secret_key": (secret_key or "").strip() or None,
        "region": (region or "").strip() or None,
    }

    env = {
        "access_key": _first_env(_ACCESS_KEY_VARS),
        "secret_key": _first_env(_SECRET_KEY_VARS),
        "region": _first_env(("BYTEPLUS_REGION",)),
        "session_token": _first_env(_TOKEN_VARS),
    }

    sdk_file = _load_json(SDK_CONFIG_PATH)
    from_sdk = {
        "access_key": (sdk_file.get("ak") or sdk_file.get("access_key") or "").strip() or None,
        "secret_key": (sdk_file.get("sk") or sdk_file.get("secret_key") or "").strip() or None,
        "region": (sdk_file.get("region") or "").strip() or None,
    }

    local_path = Path(config_path) if config_path else Path.cwd() / LOCAL_CONFIG_NAME
    local_file = _load_json(local_path)
    # Accept both a flat shape and HelloBP v1's nested {"byteplus": {...}} shape,
    # so an existing v1 config.json can be pointed at with --config.
    nested = local_file.get("byteplus") if isinstance(local_file.get("byteplus"), dict) else {}
    from_local = {
        "access_key": (
            local_file.get("access_key") or nested.get("access_key") or ""
        ).strip() or None,
        "secret_key": (
            local_file.get("secret_key") or nested.get("secret_key") or ""
        ).strip() or None,
        "region": (local_file.get("region") or nested.get("region") or "").strip() or None,
    }

    layers = [
        ("argument", explicit),
        ("env", env),
        (str(SDK_CONFIG_PATH), from_sdk),
        (str(local_path), from_local),
    ]

    for field_name in ("access_key", "secret_key", "region", "session_token"):
        for origin, layer in layers:
            value = layer.get(field_name)
            if value:
                setattr(creds, field_name, value)
                creds.sources[field_name] = origin
                break

    if not creds.region:
        creds.region = DEFAULT_REGION
        creds.sources["region"] = "default"

    return creds


@dataclass
class CloudflareCredentials:
    api_token: str = ""
    sources: dict = None

    def __post_init__(self):
        if self.sources is None:
            self.sources = {}

    @property
    def complete(self) -> bool:
        return bool(self.api_token)

    def require(self) -> None:
        if self.complete:
            return
        raise SystemExit(
            "bpctl: missing Cloudflare API token.\n"
            "\n"
            "Set it in the environment:\n"
            "    export CLOUDFLARE_API_TOKEN=...\n"
            "\n"
            f"or write {CF_CONFIG_PATH} as {{\"api_token\": \"...\"}}.\n"
            "\n"
            "Create one at https://dash.cloudflare.com/profile/api-tokens.\n"
            "Scope it to the zones you need: Zone:DNS:Edit is enough to publish\n"
            "DCV validation records; Zone:Read plus Zone Settings:Read covers\n"
            "migration extraction."
        )

    def describe(self) -> str:
        return f"  api token  : {mask(self.api_token)}   [{self.sources.get('api_token', '-')}]"


def load_cloudflare_credentials(*, api_token: str | None = None, config_path: str | None = None):
    """Resolve a Cloudflare token: explicit arg -> env -> ~/.cloudflare/config -> ./bpctl.json."""
    local_name = Path(config_path) if config_path else Path.cwd() / LOCAL_CONFIG_NAME
    cf_file = _load_json(CF_CONFIG_PATH)
    local_file = _load_json(local_name)
    nested = local_file.get("cloudflare") if isinstance(local_file.get("cloudflare"), dict) else {}

    candidates = [
        ((api_token or "").strip() or None, "argument"),
        (_first_env(_CF_TOKEN_VARS), "env"),
        ((cf_file.get("api_token") or cf_file.get("token") or "").strip() or None, str(CF_CONFIG_PATH)),
        ((nested.get("api_token") or "").strip() or None, str(local_name)),
    ]

    creds = CloudflareCredentials()
    for value, origin in candidates:
        if value:
            creds.api_token = value
            creds.sources["api_token"] = origin
            break
    return creds


def resolve_live(flag: bool) -> bool:
    """
    Decide whether writes actually go to BytePlus.

    Live mode is deliberately awkward to enable by accident: an explicit
    ``--live`` flag, or ``BPCTL_LIVE`` set to a clearly affirmative value.
    Anything else — including the string "false" or an empty variable — is
    dry-run.
    """
    if flag:
        return True
    return os.environ.get("BPCTL_LIVE", "").strip().lower() in ("1", "true", "yes", "on")
