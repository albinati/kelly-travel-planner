# Kelly

Travel-hacking helper: planned trips and opportunistic searches using **SerpApi** (Google Flights cash) and **Seats.aero** (awards), with config in **Markdown**, **SQLite** history for typical/high/low context, and an **MCP** server for agents (OpenClaw, Claude Code, OpenCode, Cursor).

See [docs/MCP.md](docs/MCP.md) for host setup. For **OpenClaw on a VPS** (including **`curl | bash` install**), see [docs/OPENCLAW-VPS.md](docs/OPENCLAW-VPS.md).

**One-liner install + OpenClaw MCP registration** (see script for env/flags):

```bash
curl -fsSL --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
```

```bash
poetry install --extras mcp --with dev
cp .env.example .env
cp config/kelly.example.md config/kelly.md
poetry run kelly scan --config config/kelly.md
poetry run kelly opportunities --config config/kelly.md
poetry run kelly-mcp
```

If `poetry install` reports a stale `poetry.lock` after a pull, run `poetry lock` once, then install again.
