# Kelly + OpenClaw on a VPS

Kelly’s MCP server uses **stdio**: OpenClaw (or its gateway) **spawns** `kelly-mcp` as a child process. Kelly must live **on the same machine** as that process (same VPS/container namespace). You do **not** open a public TCP port for Kelly when using stdio.

## Scripted install (`curl | bash`, like OpenClaw’s installer)

From any machine with Git, Python 3.11+, and network access to GitHub:

```bash
curl -fsSL --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
```

**Private repo:** use SSH and export the remote before piping:

```bash
export KELLY_REPO_URL=git@github.com:albinati/kelly-travel-planner.git
curl -fsSL --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
```

Useful options and env vars are documented in the script header. Preview without changes:

```bash
curl -fsSL .../install-openclaw-kelly.sh | bash -s -- --dry-run
```

The script will:

1. Install **Poetry** if missing (official installer → `~/.local/bin`).
2. **Clone** or **pull** Kelly into `~/kelly-travel-planner` (override with `KELLY_INSTALL_DIR` or `--dir`).
3. Run **`poetry install --extras mcp`**.
4. Register Kelly with **`openclaw mcp set kelly '<json>'`** when the `openclaw` CLI is on `PATH`, otherwise **merge** `mcpServers.kelly` into `~/.openclaw/openclaw.json` (override with `OPENCLAW_CONFIG_PATH`).
5. Run **`openclaw gateway restart`** when the CLI is available.

If your OpenClaw build uses a different config shape than top-level `mcpServers`, adjust the generated config or register Kelly manually once.

---

## 1. Server prerequisites

- **Python 3.11+**
- **Git** (SSH key or token for your private repo)
- **Poetry** — [install](https://python-poetry.org/docs/#installation), or use `pipx install poetry`

```bash
sudo apt update && sudo apt install -y python3 python3-venv git curl build-essential
curl -sSL https://install.python-poetry.org | python3 -
# add Poetry to PATH, e.g. echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

## 2. Clone and install Kelly

Pick a fixed path (examples use `/opt/kelly-travel-planner` or `~/kelly-travel-planner`).

```bash
git clone git@github.com:albinati/kelly-travel-planner.git /opt/kelly-travel-planner
cd /opt/kelly-travel-planner
export PATH="$HOME/.local/bin:$PATH"
poetry install --extras mcp --no-interaction
poetry run patchright install chromium      # one-time, for Eurostar scraping
```

Resolve the **real** path to the MCP binary (avoids `poetry run` lookup issues under OpenClaw):

```bash
poetry run which kelly-mcp
# e.g. /opt/kelly-travel-planner/.venv/bin/kelly-mcp
```

## 3. Config on the VPS

```bash
cp config/kelly.example.md config/kelly.md
patchright install chromium                      # one-time, for Eurostar scraping
# edit config/kelly.md: declare your trips (## Trains and ## Stays sections)
```

No API keys are required: `pyairbnb` and `patchright` work key-free. `.env` is optional and only used for path overrides.

Optional explicit paths (only if you do not rely on repo-root resolution):

- `KELLY_CONFIG_PATH=/opt/kelly-travel-planner/config/kelly.md`
- `KELLY_DATA_DIR=/opt/kelly-travel-planner/data` (SQLite history; ensure the user running OpenClaw can write here)
- `KELLY_PROJECT_ROOT=/opt/kelly-travel-planner` (only if `cwd` is not the repo root)

## 4. OpenClaw MCP entry

Configure Kelly in OpenClaw’s MCP config (often `openclaw.json` / `openclaw.json5` or the path your install documents — e.g. `~/.config/openclaw/`). Shape is usually a `mcp.servers` map; **use the absolute `kelly-mcp` path** from step 2.

Example (adjust paths and match your OpenClaw schema if it differs):

```json
{
  "mcp": {
    "servers": {
      "kelly": {
        "command": "/opt/kelly-travel-planner/.venv/bin/kelly-mcp",
        "args": [],
        "cwd": "/opt/kelly-travel-planner",
        "description": "Kelly group trip planner (Eurostar + Airbnb)",
        "env": {
          "KELLY_CONFIG_PATH": "/opt/kelly-travel-planner/config/kelly.md",
          "KELLY_DATA_DIR": "/opt/kelly-travel-planner/data"
        }
      }
    }
  }
}
```

Then **restart the OpenClaw gateway** (or equivalent) and verify:

```bash
openclaw mcp list
```

(Exact CLI may vary by OpenClaw version.)

## 5. User / permissions

The OS user that runs **OpenClaw** must be able to:

- Execute `/opt/kelly-travel-planner/.venv/bin/kelly-mcp`
- Read `config/kelly.md` and `.env` (if used)
- Create/write `data/kelly_history.sqlite` under `KELLY_DATA_DIR`

Example:

```bash
sudo chown -R openclaw:openclaw /opt/kelly-travel-planner
# or run OpenClaw as the same user that owns the clone
```

## 6. Updates

```bash
cd /opt/kelly-travel-planner
git pull
poetry install --extras mcp --no-interaction
# restart OpenClaw gateway
```

## 7. Docker / separate containers

If OpenClaw runs **inside Docker**, Kelly must either:

- Be **installed in the same image** as OpenClaw and invoked with a path inside that image, or  
- Use an MCP **remote** transport (HTTP/SSE) — Kelly currently ships **stdio only**; adding streamable HTTP would be a separate change.

## 8. Smoke test without OpenClaw

On the VPS:

```bash
cd /opt/kelly-travel-planner
poetry run kelly config-show -c config/kelly.md
poetry run kelly plan <trip_id> --no-persist
```

---

For generic MCP host snippets (Claude Code, Cursor), see [MCP.md](MCP.md).
