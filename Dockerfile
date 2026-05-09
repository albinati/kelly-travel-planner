# Kelly travel-planner runtime image.
#
# Defaults are slim and safe:
#   - Non-root user (uid/gid 1000)
#   - .env is NEVER copied into the image (kept out by .dockerignore); pass at
#     runtime via `--env-file .env` or compose `env_file:`.
#   - Read-only config bind mount and writable data bind mount expected at runtime.
#
# Build args:
#   PYTHON_VERSION   Python base (default 3.11)
#   POETRY_EXTRAS    Space-separated poetry extras to install. Default
#                    "mcp trips". Use "mcp" alone to skip pyairbnb/patchright.
#   INSTALL_BROWSERS If "true", install Chromium for patchright (Eurostar scraping).
#                    Adds ~400MB. Off by default. Only meaningful when the
#                    `trips` extra is included.
#
# Build:
#   docker build -t kelly-travel-planner:local .
# Flights only (no pyairbnb/patchright):
#   docker build --build-arg POETRY_EXTRAS="mcp" -t kelly-travel-planner:flights .
# With browsers (Eurostar scraping):
#   docker build --build-arg INSTALL_BROWSERS=true -t kelly-travel-planner:browsers .

ARG PYTHON_VERSION=3.11
ARG POETRY_EXTRAS="mcp trips"

############################
# Builder stage
############################
FROM python:${PYTHON_VERSION}-slim AS builder

ARG POETRY_EXTRAS

# Poetry 2.x is required: poetry.lock uses lock-version 2.1.
ENV POETRY_VERSION=2.3.3 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=true \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build deps for any C extensions in the dep tree
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | POETRY_VERSION=${POETRY_VERSION} python3 -
ENV PATH="${POETRY_HOME}/bin:${PATH}"

WORKDIR /app

# Copy dependency manifests first so layer cache survives source edits.
COPY pyproject.toml poetry.lock ./

# Install dependencies (without the project itself) into /app/.venv.
RUN poetry install --extras "${POETRY_EXTRAS}" --no-root --no-interaction

# Now copy the project source and install the kelly package itself.
COPY src/ ./src/
COPY README.md ./
RUN poetry install --extras "${POETRY_EXTRAS}" --only-root --no-interaction

############################
# Runtime stage
############################
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG INSTALL_BROWSERS=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:${PATH}" \
    KELLY_PROJECT_ROOT=/app \
    KELLY_DATA_DIR=/app/data \
    KELLY_CONFIG_PATH=/app/config/kelly.md

# tini gives clean signal handling for stdio MCP child processes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring in the prebuilt venv and the application source.
COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
COPY pyproject.toml README.md ./
COPY config/kelly.example.md ./config/kelly.example.md

# Optionally install Chromium + system deps for patchright (Eurostar scraping).
# Done as root before user switch; --with-deps lets patchright apt-get install
# the libs Chromium needs without us tracking the list by hand.
RUN if [ "$INSTALL_BROWSERS" = "true" ]; then \
        /app/.venv/bin/patchright install --with-deps chromium ; \
    fi

# Create unprivileged user and ensure mount points are writable.
# uid/gid 1000 matches the conventional first regular user on Linux hosts so
# bind-mounted ./data writes don't end up root-owned.
RUN groupadd --gid 1000 kelly \
 && useradd  --uid 1000 --gid 1000 --home /app --shell /bin/bash --no-create-home kelly \
 && mkdir -p /app/data /app/config \
 && chown -R kelly:kelly /app

USER kelly

# Default command serves stdio MCP. Override with `kelly ...` for CLI usage:
#   docker run --rm --env-file .env -v $(pwd)/config:/app/config:ro \
#     -v $(pwd)/data:/app/data kelly-travel-planner:local kelly scan
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["kelly-mcp"]
