# MCP host setup

> **⚠️ Deprecated.** Kelly is now **CLI-first** — the `kelly-mcp` entrypoint is
> disabled in `pyproject.toml` (kept in-tree, reversible). Use the `kelly` CLI
> (every former tool has a `kelly <command>` mirror). This page is retained for
> reference / if you re-enable the server. The OpenClaw integration is the
> skill at `skills/kelly-travel-planner/SKILL.md`, which drives the CLI.

Kelly ships a **stdio** MCP server: `kelly-mcp` (after `poetry install --extras mcp`). 4 tools + 1 resource — see [main README](../README.md) for the catalog.

**Running OpenClaw on a VPS?** See [OPENCLAW-VPS.md](OPENCLAW-VPS.md) for clone, Poetry, absolute paths, `openclaw.json` example, and a **`curl | bash` installer** ([`scripts/install-openclaw-kelly.sh`](../scripts/install-openclaw-kelly.sh)).

## OpenClaw

```bash
cd /path/to/kelly-travel-planner
poetry run kelly-mcp
```

Point the host at the project directory so `KELLY_CONFIG_PATH` and `KELLY_DATA_DIR` resolve as expected (or set them in the environment for the spawned process).

## Claude Code

Add to `.mcp.json` in your project or home config:

```json
{
  "mcpServers": {
    "kelly": {
      "command": "poetry",
      "args": ["run", "kelly-mcp"],
      "cwd": "/path/to/kelly-travel-planner"
    }
  }
}
```

For a stable absolute path, point `command` directly at `/path/to/kelly-travel-planner/.venv/bin/kelly-mcp` after running `./scripts/setup-venv.sh`.

## OpenCode

Mirror the same `command` / `args` / `cwd` pattern in `opencode.json` per OpenCode's MCP documentation.

## Cursor

Add a custom MCP server in Cursor settings with command `poetry`, args `run`, `kelly-mcp`, and working directory set to this repository.

## Tools

| Tool | What it does |
| --- | --- |
| `kelly_load_config` | Parse `kelly.md` → JSON of frontmatter + trains + stays |
| `kelly_eurostar_search` | One-shot Eurostar fare scrape (bypass declared trips) |
| `kelly_airbnb_search` | One-shot Airbnb whole-listing search |
| `kelly_plan_trip` | Look up `<trip_id>-out` / `<trip_id>-back` / `<trip_id>` in `kelly.md` and curate a shortlist |

All search tools accept `persist: bool = True`; pass `False` to skip the SQLite write.

## Resource

- `kelly://config` — the current `kelly.md` as Markdown.

## Browsers

Eurostar scraping needs Chromium installed via patchright:

```bash
patchright install chromium
```

Or build the Docker image with `INSTALL_BROWSERS=true` (default in the supplied `Dockerfile`).
