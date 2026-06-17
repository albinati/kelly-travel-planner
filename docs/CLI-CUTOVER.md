# Kelly CLI-first cutover (Hetzner)

Kelly moved from a resident **stdio MCP server** to a **CLI-first** model (PR #23).
The old `kelly-mcp` was spawned once per OpenClaw session and left orphaned
processes resident (~75 MB idle each, **+500 MB** mid Chromium scrape) — the
"ghost session" leak that thrashed the VPS. The `kelly` CLI runs and **exits**,
so nothing stays resident. This runbook switches the Hetzner box over.

> Kelly is stateless (all state in SQLite), so it needs no daemon — unlike HEM,
> which legitimately stays resident. There is **no server, no port, no systemd
> unit** to deploy here.

## 0. Prereqs

- SSH to the Hetzner host that runs OpenClaw + Kelly.
- Know Kelly's install dir (`$KELLY_DIR`) and data dir (`KELLY_DATA_DIR`).

## 1. Update the code

```bash
cd "$KELLY_DIR"
git fetch origin && git checkout main && git pull     # after PR #23 merges
poetry install                                        # recreates `kelly`; drops kelly-mcp
patchright install chromium                            # if not already present
```

`poetry install` regenerates console scripts from `pyproject.toml`: `kelly` is
present, `kelly-mcp` is **not** (its entry is disabled). If a stale
`kelly-mcp` binary lingers in the venv, remove it:

```bash
rm -f "$(poetry env info -p)/bin/kelly-mcp"
```

## 2. Kill the MCP registration + any ghosts

Find wherever OpenClaw registered the stdio server and remove that entry
(top-level `mcpServers.kelly`, an `mcp.servers` map, or whatever your build
uses), then reload OpenClaw. Locate it:

```bash
grep -rn "kelly-mcp\|\"kelly\"" ~/.openclaw* ~/.config/openclaw* /etc/openclaw* 2>/dev/null
```

Clear processes that are already orphaned:

```bash
pkill -f kelly-mcp || true
ps -eo pid,rss,etime,cmd | grep -iE "kelly-mcp|chromium" | grep -v grep   # confirm none linger
```

Then restart/reload the OpenClaw gateway so it re-reads config and picks up the
skill (`nativeSkills: auto` discovers `skills/kelly-travel-planner/SKILL.md`).

## 3. Seed the traveller data

The `albinati` profile and the two active trip dossiers were created on the dev
box; their SQLite lives there, not on Hetzner. Recreate them via the CLI — no DB
copy needed:

```bash
KELLY="$(poetry env info -p)/bin/kelly" bash scripts/seed-kelly-data.sh
```

**Run once** — `session-create` makes a new dossier each run, so re-running
duplicates the sessions (the profile is an idempotent upsert). Override the
binary with `KELLY=/abs/path/kelly` if it's not on `PATH`. (Alternative: just
`scp` the `kelly_history.sqlite` from the dev box into `KELLY_DATA_DIR`.)

## 4. Verify

```bash
kelly --help                 # lists the 28 commands
kelly profile-get albinati   # the family profile
kelly session-list           # the two dossiers (Feb-2027 sun, Oct-2026 city/aurora)
kelly session-get <id>       # the Copenhagen dossier carries its 2 avios snapshots
```

From OpenClaw, ask it to plan/recall a trip and confirm it drives `kelly <cmd>`
in the shell (not an MCP tool) and that **no `kelly-mcp` process appears** in
`ps` afterwards.

## Rollback (if needed)

CLI-first is reversible — `mcp_server.py` is untouched in-tree:

1. Re-enable the entry in `pyproject.toml`:
   `kelly-mcp = "kelly.mcp_server:main"`
2. `poetry install --extras mcp`
3. Restore the OpenClaw `mcpServers.kelly` registration and reload.

But the ghost-session memory leak comes back with it — only roll back if the CLI
path is genuinely broken, not for convenience.
