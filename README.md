# Kelly

Travel-hacking helper: planned trips and opportunistic searches using **RapidAPI** (Google Flights–style cash, macro price graph) and optional **SerpApi**, plus **Seats.aero** (awards), with config in **Markdown**, **SQLite** history, and an **MCP** server (macro / mid / micro tools — see [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md)).

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
