# Kelly as a shared HTTP MCP server (Hetzner)

Kelly runs as **one long-lived, bearer-guarded streamable-HTTP MCP server** on
the Hetzner box. The same server is used by **OpenClaw** (over `127.0.0.1`) and
by your **local Claude Code** (over Tailscale) — so everything shares **one
SQLite store: one profile, one set of dossiers, one truth.**

Why HTTP and not stdio: stdio spawned one `kelly-mcp` child per session and left
orphans resident (~75 MB idle, +500 MB mid-scrape) — the **ghost-session** leak.
One long-lived process makes that impossible (clients are HTTP connections), and
a single process owning the DB removes cross-process lock contention. Kelly has
no background loop, so the only resident cost is the idle server.

> The `kelly` **CLI** remains as an offline/scripting fallback (every tool has a
> `kelly <kebab>` mirror). It hits the *local* SQLite, separate from the server.

## 1. Install / update on Hetzner

```bash
cd /opt/kelly-travel-planner            # your install dir
git pull
poetry install --extras mcp             # brings mcp + uvicorn; builds `kelly-mcp-http`
poetry run patchright install chromium  # if not already
```

## 2. Configure `.env` (repo root — Kelly auto-loads it)

```ini
# server
KELLY_MCP_TOKEN=<openssl rand -hex 32>     # REQUIRED — server refuses to start without it
KELLY_MCP_HOST=127.0.0.1                    # bind localhost; Tailscale fronts remote access
KELLY_MCP_PORT=8765
KELLY_DATA_DIR=/opt/kelly-travel-planner/data
# KELLY_MCP_ALLOWED_HOSTS=host.tailnet.ts.net   # optional — see note below
# providers (only on the server — clients never need these)
SEATSAERO_API_KEY=...
LITEAPI_API_KEY=...
```

Generate the token once: `openssl rand -hex 32`. Keep `.env` readable only by the
service user.

**Host policy:** FastMCP's DNS-rebinding guard rejects requests whose `Host`
isn't localhost (you'd see HTTP **421** when reaching the server through
`tailscale serve`). Because the server is already bearer-gated, loopback-bound,
and tailnet-only, that browser-origin guard is disabled by default. To re-enable
it with an explicit allowlist instead, set `KELLY_MCP_ALLOWED_HOSTS` to a CSV of
hostnames (e.g. your `*.ts.net` name) and restart.

## 3. Run it as a service

```bash
sudo cp deploy/kelly-mcp.service /etc/systemd/system/kelly-mcp.service
# edit User= and the /opt paths in the unit to match your install
sudo systemctl daemon-reload
sudo systemctl enable --now kelly-mcp
systemctl status kelly-mcp --no-pager
curl -s http://127.0.0.1:8765/healthz        # -> {"status":"ok"}
```

## 4. Register with OpenClaw (on Hetzner — localhost)

OpenClaw talks to the server over loopback, like HEM. Add a `kelly` entry to the
OpenClaw MCP config (HTTP transport + bearer):

```json
{
  "mcpServers": {
    "kelly": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": { "Authorization": "Bearer <KELLY_MCP_TOKEN>" }
    }
  }
}
```

Reload OpenClaw so it re-reads config and discovers
`skills/kelly-travel-planner/SKILL.md`. Then **remove any old stdio registration**
and clear leftover ghosts:

```bash
grep -rn "kelly-mcp\b" ~/.openclaw* ~/.config/openclaw* /etc/openclaw* 2>/dev/null   # find stdio entry
pkill -f "kelly-mcp$" || true        # kill stdio ghosts (NOT the -http server)
ps -eo pid,rss,etime,cmd | grep -iE "kelly-mcp|chromium" | grep -v grep
```

## 5. Reach it from local Claude Code (over Tailscale)

Expose the loopback server on your tailnet with TLS — no public port:

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status        # shows https://<host>.<tailnet>.ts.net/ -> 127.0.0.1:8765
```

Then point local Claude Code at it. In the repo's `.mcp.json` (or your user MCP
config):

```json
{
  "mcpServers": {
    "kelly": {
      "type": "http",
      "url": "https://<host>.<tailnet>.ts.net/mcp",
      "headers": { "Authorization": "Bearer <KELLY_MCP_TOKEN>" }
    }
  }
}
```

(If you mapped a path prefix in `tailscale serve`, append it before `/mcp`.)
Now local Claude Code and OpenClaw drive the **same** Kelly and see the **same**
data.

## 6. Seed the traveller data (once, on the server)

The `albinati` profile + the two dossiers were created on the dev box. Recreate
them in the **server's** SQLite (run on Hetzner so it writes the shared
`KELLY_DATA_DIR`):

```bash
KELLY="/opt/kelly-travel-planner/.venv/bin/kelly" bash scripts/seed-kelly-data.sh
```

Run once (session-create is not idempotent; the profile upsert is). Alternative:
`scp` the dev `kelly_history.sqlite` into the server's `KELLY_DATA_DIR`.

## 7. Verify end to end

```bash
# on the server
kelly session-list            # the two dossiers (Feb-2027 sun, Oct-2026 city/aurora)
kelly profile-get albinati
```

From **local Claude Code**, ask it to list trip sessions — the `kelly_session_list`
tool should return the same dossiers (proving the shared server + Tailscale path).
Confirm no `kelly-mcp` (stdio) process lingers in `ps` on the server.

## Security

- **Bearer required**: the server refuses to start without `KELLY_MCP_TOKEN` and
  401s every unauthenticated request. `/healthz` is the only unauthenticated route.
- **Bind localhost**: `KELLY_MCP_HOST=127.0.0.1`. Remote access is only via
  `tailscale serve` (TLS + tailnet ACLs) — never a public port.
- **Secrets on the server only**: provider keys live in the server `.env`;
  clients carry just the bearer token.

## Rollback

Stop the service (`sudo systemctl disable --now kelly-mcp`) and re-register the
stdio `kelly-mcp` if needed — the stdio entrypoint is unchanged. But the
ghost-session leak returns with it; prefer fixing the HTTP path.
