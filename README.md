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

## Run in a container or venv

Two safe local-run paths are wired up. Both keep secrets in `.env` (never baked
into images, `chmod 600`) and persist SQLite history in `./data`.

### Native venv (no global Poetry needed)

```bash
./scripts/setup-venv.sh           # creates .venv via Poetry 2.x (auto-bootstrapped if missing)
source .venv/bin/activate
kelly config-show -c config/kelly.md
kelly-mcp                         # stdio MCP server
```

The script creates `./.venv` in-project (so `.venv/bin/kelly-mcp` is a stable
absolute path you can hand to MCP hosts) and seeds `.env` / `config/kelly.md`
from the example files on first run. Override the interpreter with
`PYTHON=python3.11 ./scripts/setup-venv.sh`.

### Docker

```bash
make docker-build                              # slim image, no browsers (~250MB)
make docker-build-browsers                     # with Chromium for Eurostar (~700MB)
make docker-run CMD='kelly scan -c config/kelly.md'
make docker-mcp                                # stdio MCP — pipe JSON-RPC to stdin
```

Or with `docker compose`:

```bash
docker compose build
docker compose run --rm kelly kelly --help
docker compose run --rm -T kelly              # stdio MCP
```

The image runs as a non-root user (uid/gid 1000), excludes `.env` and `data/`
via `.dockerignore`, and expects `.env` at run time via `--env-file` /
`env_file:`. `./config` is mounted read-only and `./data` writable so SQLite
history survives image rebuilds. `INSTALL_BROWSERS=true` is an opt-in build
arg for patchright/Chromium (Eurostar scraping); leave it off otherwise.
