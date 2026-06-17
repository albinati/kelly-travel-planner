# Kelly as a shared HTTP MCP server (Hetzner)

Kelly runs as **one long-lived, bearer-guarded streamable-HTTP MCP server** on
the Hetzner box, **loopback-only**. **OpenClaw** uses it over `127.0.0.1`; your
**local Claude Code** reaches it over an **SSH tunnel** (§5). Everything shares
**one SQLite store: one profile, one set of dossiers, one truth.**

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

OpenClaw talks to the server over loopback, like HEM. Register with the OpenClaw
CLI — it stores the server under `mcp.servers.kelly` and probes before saving:

```bash
openclaw mcp add kelly \
  --url http://127.0.0.1:8765/mcp \
  --transport streamable-http \
  --header "Authorization=Bearer <KELLY_MCP_TOKEN>" \
  --timeout 120
openclaw mcp reload
openclaw mcp probe kelly        # expect: kelly: 25 tools
```

Use `/mcp` with **no trailing slash** (`/mcp/` 307-redirects). The stored entry is
the OpenClaw shape (NOT the Claude-Code `{type:"http", mcpServers}` shape):

```json
{ "mcp": { "servers": { "kelly": {
  "url": "http://127.0.0.1:8765/mcp",
  "transport": "streamable-http",
  "headers": { "Authorization": "Bearer <KELLY_MCP_TOKEN>" }
} } } }
```

If you previously had a stdio `kelly-mcp` registered, remove it and clear ghosts:

```bash
pkill -f "kelly-mcp$" || true        # stdio ghosts (NOT the -http server)
ps -eo pid,rss,etime,cmd | grep -iE "kelly-mcp|chromium" | grep -v grep
```

## 5. Reach it from local Claude Code (SSH tunnel — no Tailscale port)

> **⚠️ Do NOT `tailscale serve --https=443` to Kelly.** On a typical box 443 is the
> OpenClaw chat gateway — pointing it at Kelly hijacks it (your `/chat` then 401s).
> Kelly only needs loopback (OpenClaw uses it locally); reach it from your laptop
> with an SSH local-forward, which consumes **no Tailscale port**.

On your laptop, tunnel the server's loopback port:

```bash
ssh -fN -L 8765:127.0.0.1:8765 <server>        # autossh -M0 -fN ... to keep it up
```

Then point local Claude Code at the tunnelled port. In `.mcp.json` (this **is** the
Claude-Code `{type:"http"}` shape — correct for Claude Code, different from the
OpenClaw `mcp.servers` shape in §4):

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

Now local Claude Code and OpenClaw drive the **same** Kelly and see the **same**
data. If you really want tailnet HTTPS instead of a tunnel, serve Kelly on a
dedicated **non-443** path/port you've confirmed is free — never the default 443.

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

From **local Claude Code** (with the §5 tunnel up), ask it to list trip sessions —
the `kelly_session_list` tool should return the same dossiers (proving the shared
server). Confirm no `kelly-mcp` (stdio) process lingers in `ps` on the server.

## Security

- **Bearer required**: the server refuses to start without `KELLY_MCP_TOKEN` and
  401s every unauthenticated request. `/healthz` is the only unauthenticated route.
- **Bind localhost**: `KELLY_MCP_HOST=127.0.0.1`. Remote access is only via the
  SSH tunnel (§5) — never a public port, and never via `tailscale serve --https=443`
  (that's the OpenClaw gateway).
- **Secrets on the server only**: provider keys live in the server `.env`;
  clients carry just the bearer token.

## Rollback

Stop the service (`sudo systemctl disable --now kelly-mcp`) and re-register the
stdio `kelly-mcp` if needed — the stdio entrypoint is unchanged. But the
ghost-session leak returns with it; prefer fixing the HTTP path.
