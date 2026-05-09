#!/usr/bin/env bash
# Bootstrap a local Python virtualenv for Kelly with no global Poetry required.
#
# Strategy (tried in order):
#   1. If `poetry` is on PATH, use it directly to create an in-project .venv.
#   2. Else, install Poetry into a private tools venv (.tools/venv-poetry) and
#      drive it from there. Nothing is installed system-wide.
#
# Usage:
#   ./scripts/setup-venv.sh                    # install runtime + mcp + dev
#   EXTRAS="mcp analytics" ./scripts/setup-venv.sh
#   PYTHON=python3.11 ./scripts/setup-venv.sh
#
# After setup:
#   source .venv/bin/activate
#   kelly --help
#   kelly-mcp

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

PY="${PYTHON:-python3}"
EXTRAS="${EXTRAS:-mcp trips}"
WITH_DEV="${WITH_DEV:-1}"

if ! command -v "${PY}" >/dev/null 2>&1; then
    echo "[kelly] python interpreter '${PY}' not found. Set PYTHON=python3.11 or install it." >&2
    exit 1
fi

# Verify >=3.11 (project requires ^3.11).
"${PY}" - <<'PY' || { echo "[kelly] need Python >=3.11" >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

echo "[kelly] using $("${PY}" --version) at $(command -v "${PY}")"

# Resolve a poetry command without polluting the user's environment.
# poetry.lock is lock-version 2.1, so Poetry >= 2.0 is required.
need_bootstrap=1
if command -v poetry >/dev/null 2>&1; then
    if poetry --version 2>/dev/null | grep -Eq 'version 2\.'; then
        POETRY_CMD=(poetry)
        echo "[kelly] using existing poetry: $(command -v poetry) ($(poetry --version))"
        need_bootstrap=0
    else
        echo "[kelly] found poetry $(poetry --version 2>/dev/null) but lockfile needs >=2.0 — bootstrapping a private copy"
    fi
fi

if [ "${need_bootstrap}" = "1" ]; then
    TOOLS_VENV="${PROJECT_ROOT}/.tools/venv-poetry"
    if [ ! -x "${TOOLS_VENV}/bin/poetry" ]; then
        echo "[kelly] installing poetry into ${TOOLS_VENV}"
        "${PY}" -m venv "${TOOLS_VENV}"
        "${TOOLS_VENV}/bin/pip" install --upgrade pip >/dev/null
        "${TOOLS_VENV}/bin/pip" install "poetry>=2.0,<3"
    fi
    POETRY_CMD=("${TOOLS_VENV}/bin/poetry")
fi

# Force in-project .venv so MCP hosts can reference .venv/bin/kelly-mcp directly.
"${POETRY_CMD[@]}" config --local virtualenvs.in-project true >/dev/null
"${POETRY_CMD[@]}" config --local virtualenvs.create true >/dev/null

INSTALL_ARGS=(install --no-interaction)
for extra in ${EXTRAS}; do
    INSTALL_ARGS+=(--extras "${extra}")
done
if [ "${WITH_DEV}" = "1" ]; then
    INSTALL_ARGS+=(--with dev)
fi

echo "[kelly] poetry ${INSTALL_ARGS[*]}"
"${POETRY_CMD[@]}" "${INSTALL_ARGS[@]}"

# Seed .env / kelly.md from examples on first setup; never overwrite existing files.
if [ ! -f "${PROJECT_ROOT}/.env" ] && [ -f "${PROJECT_ROOT}/.env.example" ]; then
    cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
    chmod 600 "${PROJECT_ROOT}/.env"
    echo "[kelly] created .env from .env.example (chmod 600). Fill in API keys before scanning."
fi
if [ ! -f "${PROJECT_ROOT}/config/kelly.md" ] && [ -f "${PROJECT_ROOT}/config/kelly.example.md" ]; then
    cp "${PROJECT_ROOT}/config/kelly.example.md" "${PROJECT_ROOT}/config/kelly.md"
    echo "[kelly] created config/kelly.md from kelly.example.md. Edit passengers/watchlist before scanning."
fi

cat <<EOF

[kelly] venv ready at ${PROJECT_ROOT}/.venv

Activate:
    source .venv/bin/activate

Try it:
    kelly --help
    kelly config-show -c config/kelly.md
    kelly-mcp                                # stdio MCP server

EOF
