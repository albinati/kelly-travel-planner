#!/usr/bin/env bash
# Install Kelly for OpenClaw: clone/update repo, Poetry + MCP extra, register MCP server.
#
# One-liner (after this file is on your default branch):
#   curl -fsSL --proto '=https' --tlsv1.2 \
#     https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
#
# Private repo: set KELLY_REPO_URL (e.g. git@github.com:albinati/kelly-travel-planner.git) and ensure SSH works on the host.
#
# Env:
#   KELLY_INSTALL_DIR   Target directory (default: ~/kelly-travel-planner)
#   KELLY_REPO_URL      Git remote (default: https://github.com/albinati/kelly-travel-planner.git)
#   KELLY_GIT_BRANCH    Branch to checkout (default: main)
#   OPENCLAW_CONFIG_PATH  Override OpenClaw config path for JSON fallback
#   POETRY_HOME / PATH    Poetry must be on PATH, or it will be bootstrapped to ~/.local/bin
#
# Flags:
#   --dry-run       Print actions only
#   --no-register   Skip MCP registration (install Kelly only)
#   --dir PATH      Same as KELLY_INSTALL_DIR

set -euo pipefail

DRY_RUN=0
NO_REGISTER=0
INSTALL_DIR="${KELLY_INSTALL_DIR:-$HOME/kelly-travel-planner}"
REPO_URL="${KELLY_REPO_URL:-https://github.com/albinati/kelly-travel-planner.git}"
BRANCH="${KELLY_GIT_BRANCH:-main}"

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  echo ""
  echo "Usage: $0 [--dry-run] [--no-register] [--dir PATH]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-register) NO_REGISTER=1; shift ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "[dry-run] $*" >&2
  else
    "$@"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

ensure_poetry() {
  if command -v poetry >/dev/null 2>&1; then
    return 0
  fi
  echo "Poetry not found; installing to ~/.local/bin ..." >&2
  run curl -fsSL --proto '=https' --tlsv1.2 https://install.python-poetry.org | run python3 -
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v poetry >/dev/null 2>&1 || {
    echo "Poetry still not on PATH. Add ~/.local/bin to PATH and retry." >&2
    exit 1
  }
}

need_cmd git
need_cmd python3

py_minor="$(python3 -c 'import sys; print(sys.version_info.minor)')" || true
py_major="$(python3 -c 'import sys; print(sys.version_info.major)')" || true
if [[ "${py_major:-0}" -lt 3 ]] || [[ "${py_major:-3}" -eq 3 && "${py_minor:-0}" -lt 11 ]]; then
  echo "Python 3.11+ required; found $(python3 -V 2>&1)" >&2
  exit 1
fi

ensure_poetry

INSTALL_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$INSTALL_DIR")"

if [[ "$DRY_RUN" == 0 ]]; then
  mkdir -p "$(dirname "$INSTALL_DIR")"
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  echo "Cloning Kelly into $INSTALL_DIR ..." >&2
  run git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
else
  echo "Updating existing repo $INSTALL_DIR ..." >&2
  run git -C "$INSTALL_DIR" fetch origin "$BRANCH" --depth 1
  run git -C "$INSTALL_DIR" checkout "$BRANCH"
  run git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
fi

echo "Installing Python dependencies (MCP extra) ..." >&2
run bash -c "cd \"$INSTALL_DIR\" && poetry install --extras mcp --no-interaction"

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[dry-run] Would resolve kelly-mcp path via: cd \"$INSTALL_DIR\" && poetry env info -p" >&2
  echo "[dry-run] Would register MCP 'kelly' (openclaw mcp set, or merge ~/.openclaw/openclaw.json)" >&2
  exit 0
fi

MCP_BIN="$(cd "$INSTALL_DIR" && poetry env info -p)/bin/kelly-mcp"
if [[ "$DRY_RUN" == 0 && ! -x "$MCP_BIN" ]]; then
  echo "Expected kelly-mcp not executable at: $MCP_BIN" >&2
  exit 1
fi

if [[ ! -f "$INSTALL_DIR/config/kelly.md" ]]; then
  run cp "$INSTALL_DIR/config/kelly.example.md" "$INSTALL_DIR/config/kelly.md" || true
fi
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  run cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env" || true
  echo "Created $INSTALL_DIR/.env from example — add RAPIDAPI_KEY (and/or SERPAPI_API_KEY) and SEATS_AERO_API_KEY." >&2
fi

register_openclaw_cli() {
  command -v openclaw >/dev/null 2>&1 || return 1
  export MCP_BIN INSTALL_DIR
  MCP_JSON="$(python3 <<'PY'
import json, os
print(json.dumps({
  "command": os.environ["MCP_BIN"],
  "args": [],
  "cwd": os.environ["INSTALL_DIR"],
  "transport": "stdio",
}))
PY
)"
  echo "Registering MCP server 'kelly' via OpenClaw CLI ..." >&2
  run openclaw mcp set kelly "$MCP_JSON"
  run openclaw gateway restart || run openclaw gateway restart --force || true
  return 0
}

merge_openclaw_json() {
  local cfg="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"
  export MCP_BIN INSTALL_DIR cfg
  echo "Merging mcpServers.kelly into $cfg ..." >&2
  run python3 <<'PY'
import json, os
from pathlib import Path

cfg = Path(os.environ["cfg"])
entry = {
    "command": os.environ["MCP_BIN"],
    "args": [],
    "cwd": os.environ["INSTALL_DIR"],
    "transport": "stdio",
}
if cfg.exists():
    data = json.loads(cfg.read_text(encoding="utf-8"))
else:
    cfg.parent.mkdir(parents=True, exist_ok=True)
    data = {}
servers = data.setdefault("mcpServers", {})
servers["kelly"] = entry
if cfg.exists():
    cfg.rename(cfg.with_name(cfg.name + ".bak"))
cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print("Wrote", cfg)
PY
}

if [[ "$NO_REGISTER" == 1 ]]; then
  echo "Skipping MCP registration (--no-register)." >&2
  echo "kelly-mcp: $MCP_BIN" >&2
  echo "Add to OpenClaw manually, then: openclaw gateway restart" >&2
  exit 0
fi

if register_openclaw_cli; then
  echo "Done. Check: openclaw mcp list" >&2
else
  echo "openclaw CLI not found; writing ~/.openclaw/openclaw.json (set OPENCLAW_CONFIG_PATH if needed)." >&2
  merge_openclaw_json
  echo "Restart the OpenClaw gateway, then run: openclaw mcp list" >&2
fi

echo "kelly-mcp: $MCP_BIN" >&2
echo "Edit secrets: $INSTALL_DIR/.env and trips: $INSTALL_DIR/config/kelly.md" >&2
