#!/usr/bin/env bash
# Install Kelly for OpenClaw as a shared, long-lived streamable-HTTP MCP server.
#
# One shared bearer-guarded process (systemd) serves OpenClaw (localhost) and,
# optionally, remote Claude Code (over `tailscale serve`) — one SQLite, one truth,
# no per-session "ghost" processes. See docs/HTTP-MCP-DEPLOY.md.
#
# One-liner (after this file is on your default branch):
#   curl -fsSL --proto '=https' --tlsv1.2 \
#     https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
#
# Private repo: set KELLY_REPO_URL (e.g. git@github.com:albinati/kelly-travel-planner.git) and ensure SSH works.
#
# Env:
#   KELLY_INSTALL_DIR     Target dir (default: ~/kelly-travel-planner)
#   KELLY_REPO_URL        Git remote (default: https://github.com/albinati/kelly-travel-planner.git)
#   KELLY_GIT_BRANCH      Branch (default: main)
#   KELLY_SERVICE_USER    systemd User= (default: $SUDO_USER or $USER)
#   KELLY_MCP_PORT        Bind port (default: 8765)
#   KELLY_MCP_HOST        Bind addr (default: 127.0.0.1)
#   OPENCLAW_CONFIG_PATH  Override OpenClaw config path for JSON fallback
#
# Flags:
#   --dry-run         Print actions only
#   --no-register     Install + service only; skip OpenClaw registration
#   --no-service      Install + token only; skip systemd (e.g. CLI-only host)
#   --tailscale-serve Front the loopback server with `tailscale serve` (remote access)
#   --browsers        Also run `patchright install chromium` (Eurostar scraping)
#   --dir PATH        Same as KELLY_INSTALL_DIR
#   --port N          Same as KELLY_MCP_PORT

set -euo pipefail

DRY_RUN=0; NO_REGISTER=0; NO_SERVICE=0; TS_SERVE=0; BROWSERS=0
INSTALL_DIR="${KELLY_INSTALL_DIR:-$HOME/kelly-travel-planner}"
REPO_URL="${KELLY_REPO_URL:-https://github.com/albinati/kelly-travel-planner.git}"
BRANCH="${KELLY_GIT_BRANCH:-main}"
SERVICE_USER="${KELLY_SERVICE_USER:-${SUDO_USER:-$USER}}"
PORT="${KELLY_MCP_PORT:-8765}"
HOST="${KELLY_MCP_HOST:-127.0.0.1}"

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; echo; echo "Usage: $0 [--dry-run] [--no-register] [--no-service] [--tailscale-serve] [--browsers] [--dir PATH] [--port N]"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-register) NO_REGISTER=1; shift ;;
    --no-service) NO_SERVICE=1; shift ;;
    --tailscale-serve) TS_SERVE=1; shift ;;
    --browsers) BROWSERS=1; shift ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

run() { if [[ "$DRY_RUN" == 1 ]]; then echo "[dry-run] $*" >&2; else "$@"; fi; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }; }
# sudo only if not already root
SUDO=""; [[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

ensure_poetry() {
  command -v poetry >/dev/null 2>&1 && return 0
  [[ "$DRY_RUN" == 1 ]] && { echo "[dry-run] would bootstrap Poetry to ~/.local/bin" >&2; return 0; }
  echo "Poetry not found; installing to ~/.local/bin ..." >&2
  run bash -c "curl -fsSL --proto '=https' --tlsv1.2 https://install.python-poetry.org | python3 -"
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v poetry >/dev/null 2>&1 || { echo "Poetry not on PATH. Add ~/.local/bin and retry." >&2; exit 1; }
}

need_cmd git; need_cmd python3
py_minor="$(python3 -c 'import sys; print(sys.version_info.minor)')"; py_major="$(python3 -c 'import sys; print(sys.version_info.major)')"
if [[ "${py_major:-0}" -lt 3 || ( "${py_major}" -eq 3 && "${py_minor:-0}" -lt 11 ) ]]; then
  echo "Python 3.11+ required; found $(python3 -V 2>&1)" >&2; exit 1
fi
ensure_poetry

INSTALL_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$INSTALL_DIR")"
[[ "$DRY_RUN" == 0 ]] && mkdir -p "$(dirname "$INSTALL_DIR")"

# --- 1. clone / update ---
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  echo "Cloning Kelly into $INSTALL_DIR ..." >&2
  run git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
else
  echo "Updating existing repo $INSTALL_DIR ..." >&2
  run git -C "$INSTALL_DIR" fetch origin "$BRANCH" --depth 1
  run git -C "$INSTALL_DIR" checkout "$BRANCH"
  run git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
fi

# --- 2. deps (mcp extra brings uvicorn) ---
echo "Installing Python dependencies (mcp extra) ..." >&2
run bash -c "cd \"$INSTALL_DIR\" && poetry install --extras mcp --no-interaction"
[[ "$BROWSERS" == 1 ]] && run bash -c "cd \"$INSTALL_DIR\" && poetry run patchright install chromium"

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[dry-run] Would resolve venv, ensure KELLY_MCP_TOKEN in .env, install systemd unit, register OpenClaw (http)." >&2
  exit 0
fi

VENV="$(cd "$INSTALL_DIR" && poetry env info -p)"
HTTP_BIN="$VENV/bin/kelly-mcp-http"
[[ -x "$HTTP_BIN" ]] || { echo "Expected kelly-mcp-http not executable at: $HTTP_BIN" >&2; exit 1; }

# --- 3. config + token ---
[[ -f "$INSTALL_DIR/config/kelly.md" ]] || cp "$INSTALL_DIR/config/kelly.example.md" "$INSTALL_DIR/config/kelly.md" 2>/dev/null || true
ENV_FILE="$INSTALL_DIR/.env"
[[ -f "$ENV_FILE" ]] || { cp "$INSTALL_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"; }

ensure_env() {  # ensure_env KEY VALUE  (only adds if KEY not present)
  grep -qE "^${1}=" "$ENV_FILE" || echo "${1}=${2}" >> "$ENV_FILE"
}
if ! grep -qE '^KELLY_MCP_TOKEN=.+' "$ENV_FILE"; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  # strip any empty placeholder line then append
  grep -vE '^KELLY_MCP_TOKEN=$' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  echo "KELLY_MCP_TOKEN=${TOKEN}" >> "$ENV_FILE"
  echo "Generated KELLY_MCP_TOKEN in $ENV_FILE" >&2
fi
ensure_env KELLY_MCP_HOST "$HOST"
ensure_env KELLY_MCP_PORT "$PORT"
ensure_env KELLY_DATA_DIR "$INSTALL_DIR/data"
chmod 600 "$ENV_FILE" || true
TOKEN="$(grep -E '^KELLY_MCP_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
PORT="$(grep -E '^KELLY_MCP_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

# --- 4. systemd service ---
if [[ "$NO_SERVICE" == 0 ]]; then
  need_cmd systemctl
  UNIT=/etc/systemd/system/kelly-mcp.service
  echo "Installing systemd unit -> $UNIT (User=$SERVICE_USER) ..." >&2
  TMP_UNIT="$(mktemp)"
  cat > "$TMP_UNIT" <<UNIT
[Unit]
Description=Kelly trip planner — streamable-HTTP MCP server
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${HTTP_BIN}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
UNIT
  run $SUDO cp "$TMP_UNIT" "$UNIT"
  rm -f "$TMP_UNIT"
  run $SUDO systemctl daemon-reload
  run $SUDO systemctl enable --now kelly-mcp
  sleep 1
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo "Service healthy: http://127.0.0.1:${PORT}/healthz" >&2
  else
    echo "WARN: health check failed; inspect: $SUDO journalctl -u kelly-mcp -n 50 --no-pager" >&2
  fi
fi

# --- 5. tailscale serve (remote access for local Claude Code) ---
if [[ "$TS_SERVE" == 1 ]]; then
  need_cmd tailscale
  echo "Fronting 127.0.0.1:${PORT} with tailscale serve (https) ..." >&2
  run $SUDO tailscale serve --bg --https=443 "http://127.0.0.1:${PORT}" || \
    echo "WARN: tailscale serve failed; run it manually (see docs/HTTP-MCP-DEPLOY.md)." >&2
  run tailscale serve status || true
fi

# --- 6. register with OpenClaw (HTTP transport) ---
MCP_URL="http://127.0.0.1:${PORT}/mcp"
register_cli() {
  command -v openclaw >/dev/null 2>&1 || return 1
  export MCP_URL TOKEN
  local json; json="$(python3 -c 'import json,os; print(json.dumps({"type":"http","url":os.environ["MCP_URL"],"headers":{"Authorization":"Bearer "+os.environ["TOKEN"]}}))')"
  echo "Registering MCP 'kelly' (http) via OpenClaw CLI ..." >&2
  run openclaw mcp set kelly "$json"
  run openclaw gateway restart || true
}
merge_json() {
  local cfg="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"
  export MCP_URL TOKEN cfg
  echo "Merging mcpServers.kelly (http) into $cfg ..." >&2
  run python3 <<'PY'
import json, os
from pathlib import Path
cfg = Path(os.environ["cfg"])
entry = {"type":"http","url":os.environ["MCP_URL"],"headers":{"Authorization":"Bearer "+os.environ["TOKEN"]}}
data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
data.setdefault("mcpServers", {})["kelly"] = entry
if cfg.exists(): cfg.replace(cfg.with_name(cfg.name + ".bak"))
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print("Wrote", cfg)
PY
}
if [[ "$NO_REGISTER" == 1 ]]; then
  echo "Skipping OpenClaw registration (--no-register)." >&2
else
  register_cli || { echo "openclaw CLI not found; writing config JSON instead." >&2; merge_json; echo "Restart the OpenClaw gateway." >&2; }
fi

echo "" >&2
echo "kelly-mcp-http: $HTTP_BIN  (http://127.0.0.1:${PORT}/mcp)" >&2
echo "secrets/token : $ENV_FILE   trips: $INSTALL_DIR/config/kelly.md" >&2
echo "Next: seed data ->  KELLY=\"$VENV/bin/kelly\" bash \"$INSTALL_DIR/scripts/seed-kelly-data.sh\"" >&2
echo "For local Claude Code over Tailscale, see docs/HTTP-MCP-DEPLOY.md §5." >&2
