#!/bin/sh
# HelloBP 2.0 installer — works with any coding agent.
#
# Installs two things:
#   1. `bpctl` on PATH — a plain CLI, usable by any agent that can run a shell.
#   2. Agent context files — so the agent knows the CLI exists and how to use it.
#
# Everything is a symlink back into this checkout, so `git pull` updates the
# install with no reinstall step. Re-running is safe and idempotent.
#
# Usage:
#   ./install.sh                       # auto-detect agents, user scope
#   ./install.sh --scope project       # install into ./ instead of $HOME
#   ./install.sh --agent claude,cursor # only these
#   ./install.sh --list                # show what would be detected
#   ./install.sh --dry-run             # print actions, change nothing
#   ./install.sh --uninstall           # remove what this script installed

set -eu

REPO="$(cd "$(dirname "$0")" && pwd)"
SKILLS="$REPO/skills"
BPCTL_SRC="$SKILLS/hellobp2/scripts/bpctl.py"
AGENTS_SRC="$REPO/AGENTS.md"

DRY_RUN=0
SCOPE=user
AGENTS_WANTED=auto
UNINSTALL=0
LIST_ONLY=0
BIN_DIR="${BPCTL_BIN_DIR:-$HOME/.local/bin}"

MARK_BEGIN="<!-- BEGIN HelloBP 2.0 -->"
MARK_END="<!-- END HelloBP 2.0 -->"

RED=''; GRN=''; YLW=''; DIM=''; RST=''
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
    RED=$(printf '\033[31m'); GRN=$(printf '\033[32m')
    YLW=$(printf '\033[33m'); DIM=$(printf '\033[2m'); RST=$(printf '\033[0m')
fi

say()  { printf '%s\n' "$*"; }
ok()   { [ "$DRY_RUN" = 1 ] && return 0; printf '  %s✓%s %s\n' "$GRN" "$RST" "$*"; }
skip() { printf '  %s·%s %s%s%s\n' "$DIM" "$RST" "$DIM" "$*" "$RST"; }
warn() { printf '  %s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%serror:%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '  %swould:%s %s\n' "$DIM" "$RST" "$*"
    else
        "$@"
    fi
}

# Print the leading comment block only — it stops at the first line that is not
# a comment, so the banner cannot drift out of sync with the code below it.
usage() {
    awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --scope)     SCOPE="${2:?--scope needs user|project}"; shift 2 ;;
        --scope=*)   SCOPE="${1#*=}"; shift ;;
        --agent)     AGENTS_WANTED="${2:?--agent needs a list}"; shift 2 ;;
        --agent=*)   AGENTS_WANTED="${1#*=}"; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --list)      LIST_ONLY=1; shift ;;
        -h|--help)   usage ;;
        *)           die "unknown option: $1 (try --help)" ;;
    esac
done

case "$SCOPE" in
    user|project) ;;
    *) die "--scope must be 'user' or 'project', got '$SCOPE'" ;;
esac

# ---------------------------------------------------------------- preflight

preflight() {
    command -v python3 >/dev/null 2>&1 || die "python3 not found. HelloBP needs Python 3.9+."
    python3 - <<'PY' || die "Python 3.9+ required. Found: $(python3 --version 2>&1)"
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
    [ -f "$BPCTL_SRC" ] || die "bpctl not found at $BPCTL_SRC — is this checkout complete?"
}

# ------------------------------------------------------- agent target table
#
# Each agent maps to a destination that its own convention already reads.
# "skill" targets get a directory symlink; "doc" targets get a marked block
# appended to a markdown file we do not otherwise own.

target_for() {
    _agent="$1"; _kind=""; _path=""
    if [ "$SCOPE" = user ]; then _base="$HOME"; else _base="$PWD"; fi

    case "$_agent" in
        claude)
            _kind=skill
            if [ "$SCOPE" = user ]; then _path="$HOME/.claude/skills"
            else _path="$PWD/.claude/skills"; fi ;;
        agents)   _kind=doc;  _path="$_base/AGENTS.md" ;;
        gemini)   _kind=doc;  _path="$_base/GEMINI.md" ;;
        cursor)   _kind=doc;  _path="$_base/.cursor/rules/hellobp2.md" ;;
        windsurf) _kind=doc;  _path="$_base/.windsurf/rules/hellobp2.md" ;;
        copilot)  _kind=doc;  _path="$_base/.github/copilot-instructions.md" ;;
        cline)    _kind=doc;  _path="$_base/.clinerules/hellobp2.md" ;;
        codex)    _kind=doc;  _path="$_base/.codex/AGENTS.md" ;;
        *) return 1 ;;
    esac
    printf '%s\t%s\n' "$_kind" "$_path"
}

# An agent is "present" if its config root already exists — evidence the user
# actually uses it. AGENTS.md is the portable default and is always installed.
detected() {
    _found=""
    if [ "$SCOPE" = user ]; then _base="$HOME"; else _base="$PWD"; fi
    [ -d "$HOME/.claude" ]      && _found="$_found claude"
    [ -d "$_base/.cursor" ]     && _found="$_found cursor"
    [ -d "$_base/.windsurf" ]   && _found="$_found windsurf"
    [ -d "$_base/.github" ]     && _found="$_found copilot"
    [ -d "$_base/.clinerules" ] && _found="$_found cline"
    [ -d "$_base/.codex" ] || [ -d "$HOME/.codex" ] && _found="$_found codex"
    [ -f "$_base/GEMINI.md" ] || [ -d "$HOME/.gemini" ] && _found="$_found gemini"
    printf '%s agents\n' "$_found"
}

resolve_agents() {
    if [ "$AGENTS_WANTED" = auto ]; then
        detected
    elif [ "$AGENTS_WANTED" = all ]; then
        echo "claude agents gemini cursor windsurf copilot cline codex"
    else
        printf '%s\n' "$AGENTS_WANTED" | tr ',' ' '
    fi
}

# ------------------------------------------------------------------ install

install_bin() {
    say ""
    say "bpctl on PATH"
    if [ "$DRY_RUN" != 1 ]; then mkdir -p "$BIN_DIR"; fi
    run ln -sfn "$BPCTL_SRC" "$BIN_DIR/bpctl"
    if [ "$DRY_RUN" != 1 ]; then chmod +x "$BPCTL_SRC" 2>/dev/null || true; fi
    ok "$BIN_DIR/bpctl"

    case ":${PATH}:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not on your PATH. Add it:"
           say  "        echo 'export PATH=\"\$PATH:$BIN_DIR\"' >> ~/.zshrc && exec zsh" ;;
    esac
}

install_skill() {
    _dir="$1"
    if [ "$DRY_RUN" != 1 ]; then mkdir -p "$_dir"; fi
    for _s in hellobp2 hellobp2-migrate; do
        run ln -sfn "$SKILLS/$_s" "$_dir/$_s"
        ok "$_dir/$_s"
    done
}

# Append a delimited block referencing AGENTS.md, rather than duplicating its
# body into half a dozen files that then drift apart.
install_doc() {
    _file="$1"
    _dir="$(dirname "$_file")"

    # A bare AGENTS.md at repo root in project scope IS the source — don't
    # make it point at itself.
    if [ "$_file" = "$AGENTS_SRC" ]; then
        skip "$_file (this is the source file)"
        return 0
    fi

    if [ -f "$_file" ] && grep -qF "$MARK_BEGIN" "$_file" 2>/dev/null; then
        if grep -qF "$AGENTS_SRC" "$_file" 2>/dev/null; then
            skip "$_file (already installed)"
            return 0
        fi
        # A block exists but was written from a different checkout, so it points
        # at a path that may no longer exist. Checking only for the marker would
        # leave it stale forever — strip it and write a fresh one below.
        if [ "$DRY_RUN" = 1 ]; then
            printf '  %swould:%s repoint HelloBP block in %s\n' "$DIM" "$RST" "$_file"
            return 0
        fi
        _tmp="$_file.hellobp.tmp"
        sed "/$MARK_BEGIN/,/$MARK_END/d" "$_file" > "$_tmp" && mv "$_tmp" "$_file"
    fi

    if [ "$DRY_RUN" = 1 ]; then
        printf '  %swould:%s append HelloBP block to %s\n' "$DIM" "$RST" "$_file"
        return 0
    fi

    mkdir -p "$_dir"
    [ -f "$_file" ] && printf '\n' >> "$_file"
    cat >> "$_file" <<EOF
$MARK_BEGIN
## HelloBP 2.0 — BytePlus CDN, WAF, DNS & certificates

This machine has \`bpctl\`, a signed CLI that reaches every BytePlus CDN, WAF,
DNS and Certificate API action. **Never hand-roll a BytePlus signature** — the
signing is subtle and \`bpctl\` is verified against the official SDK.

    bpctl whoami                      # masked account + mode
    bpctl probe --save                # verify signing (read-only, run once)
    bpctl call cdn ListCdnDomains     # any action, any service

Reads run live. **Writes are simulated unless \`--live\` is passed.** Confirm
with the user before the first \`--live\` call and say what will change.

Full instructions, gotchas and the Cloudflare migration map:
$AGENTS_SRC
$MARK_END
EOF
    ok "$_file"
}

# ---------------------------------------------------------------- uninstall

do_uninstall() {
    say ""
    say "Removing HelloBP 2.0"
    run rm -f "$BIN_DIR/bpctl" && ok "$BIN_DIR/bpctl"

    for _scope_base in "$HOME" "$PWD"; do
        for _s in hellobp2 hellobp2-migrate; do
            _p="$_scope_base/.claude/skills/$_s"
            if [ -L "$_p" ]; then run rm -f "$_p"; ok "$_p"; fi
        done
    done

    for _agent in agents gemini cursor windsurf copilot cline codex; do
        _t="$(target_for "$_agent")" || continue
        _path="$(printf '%s' "$_t" | cut -f2)"
        [ -f "$_path" ] || continue
        [ "$_path" = "$AGENTS_SRC" ] && continue
        grep -qF "$MARK_BEGIN" "$_path" 2>/dev/null || continue
        if [ "$DRY_RUN" = 1 ]; then
            printf '  %swould:%s strip HelloBP block from %s\n' "$DIM" "$RST" "$_path"
            continue
        fi
        _tmp="$_path.hellobp.tmp"
        sed "/$MARK_BEGIN/,/$MARK_END/d" "$_path" > "$_tmp" && mv "$_tmp" "$_path"
        # Remove the file entirely if the block was all it held.
        [ -s "$_path" ] || rm -f "$_path"
        ok "$_path"
    done

    say ""
    say "Done. The checkout at $REPO was not touched."
    exit 0
}

# --------------------------------------------------------------------- main

preflight

if [ "$LIST_ONLY" = 1 ]; then
    say "Scope: $SCOPE"
    say "Detected agents:$(detected)"
    say ""
    for _agent in $(resolve_agents); do
        _t="$(target_for "$_agent")" || { warn "unknown agent: $_agent"; continue; }
        printf '  %-10s %s\n' "$_agent" "$(printf '%s' "$_t" | cut -f2)"
    done
    exit 0
fi

[ "$UNINSTALL" = 1 ] && do_uninstall

say ""
say "HelloBP 2.0 — installing ($SCOPE scope)"
[ "$DRY_RUN" = 1 ] && say "${DIM}dry run — nothing will change${RST}"

install_bin

say ""
say "Agent context"
_any=0
for _agent in $(resolve_agents); do
    _t="$(target_for "$_agent")" || { warn "unknown agent: $_agent"; continue; }
    _kind="$(printf '%s' "$_t" | cut -f1)"
    _path="$(printf '%s' "$_t" | cut -f2)"
    _any=1
    case "$_kind" in
        skill) install_skill "$_path" ;;
        doc)   install_doc   "$_path" ;;
    esac
done
[ "$_any" = 0 ] && warn "no agents selected — try --agent all"

say ""
say "Verify:"
say "  ${DIM}bpctl --version${RST}          → bpctl 2.0.0-phase1"
say "  ${DIM}bpctl whoami${RST}             → masked credentials and mode"
say ""
say "Then set credentials and probe once:"
say "  ${DIM}export BYTEPLUS_ACCESS_KEY=...  BYTEPLUS_SECRET_KEY=...${RST}"
say "  ${DIM}bpctl probe --save${RST}"
say ""
