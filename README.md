# Kelly

Travel-hacking helper: planned trips and opportunistic searches using **Duffel** (cash) and **Seats.aero** (awards), with config in **Markdown**, **SQLite** history for typical/high/low context, and an **MCP** server for agents (OpenClaw, Claude Code, OpenCode, Cursor).

See [docs/MCP.md](docs/MCP.md) for host setup.

```bash
poetry install --extras mcp --with dev
cp .env.example .env
cp config/kelly.example.md config/kelly.md
poetry run kelly scan --config config/kelly.md
poetry run kelly opportunities --config config/kelly.md
poetry run kelly-mcp
```
