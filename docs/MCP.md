# MCP host setup

Kelly exposes a **stdio** MCP server: `kelly-mcp` (after `poetry install --extras mcp`).

**Running OpenClaw on a VPS?** See [OPENCLAW-VPS.md](OPENCLAW-VPS.md) for clone, Poetry, absolute paths, `openclaw.json` example, and a **`curl | bash` installer** ([`scripts/install-openclaw-kelly.sh`](../scripts/install-openclaw-kelly.sh)) that mirrors OpenClaw-style one-liner installs.

## OpenClaw (default)

Use your OpenClaw MCP flow to add a command that runs Kelly from this repo, for example:

```bash
cd /path/to/kelly-travel-planner
poetry run kelly-mcp
```

Point the host at the project directory so `KELLY_CONFIG_PATH` and `KELLY_DATA_DIR` resolve as expected (or set them in the environment for the spawned process).

## Claude Code

Add to `.mcp.json` in your project or home config (shape may vary by Claude Code version):

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

## OpenCode

Mirror the same `command` / `args` / `cwd` pattern in `opencode.json` per OpenCode’s MCP documentation.

## Cursor

Add a custom MCP server in Cursor settings with command `poetry`, args `run`, `kelly-mcp`, and working directory set to this repository.

## Resource

- `kelly://config` — read-only view of the Markdown file at `KELLY_CONFIG_PATH` (default `config/kelly.md`).

## Optional: travel-hacking-toolkit data

```bash
git submodule add https://github.com/borski/travel-hacking-toolkit.git vendor/travel-hacking-toolkit
```

Then either rely on auto-discovery of `vendor/travel-hacking-toolkit/data` or set `TRAVEL_HACKING_TOOLKIT_DATA`.
