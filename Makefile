# Kelly travel-planner — common dev / container tasks.
# Run `make help` for the catalog.

SHELL := /bin/bash

PROJECT       := kelly-travel-planner
IMAGE         := $(PROJECT):local
IMAGE_BROWSER := $(PROJECT):browsers
IMAGE_FLIGHTS := $(PROJECT):flights
COMPOSE       := docker compose

# Honored by docker-compose.yml's build args.
INSTALL_BROWSERS ?= false
POETRY_EXTRAS   ?= mcp trips

.PHONY: help venv install test lint format \
        docker-build docker-build-flights docker-build-browsers \
        docker-shell docker-run docker-mcp \
        compose-build compose-mcp compose-cli clean clean-venv clean-docker

help:
	@echo "Setup:"
	@echo "  make venv                  Create in-project .venv (no global Poetry needed)"
	@echo "  make install               Same as venv (alias)"
	@echo ""
	@echo "Dev:"
	@echo "  make test                  Run pytest inside .venv"
	@echo "  make lint                  ruff check"
	@echo "  make format                ruff format"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build          Build image with mcp+trips ($(IMAGE))"
	@echo "  make docker-build-flights  Build flights-only image ($(IMAGE_FLIGHTS))"
	@echo "  make docker-build-browsers Build image with Chromium ($(IMAGE_BROWSER))"
	@echo "  make docker-shell          Open a shell in the image"
	@echo "  make docker-run CMD='kelly scan -c config/kelly.md'"
	@echo "  make docker-mcp            Run kelly-mcp via docker (stdio)"
	@echo ""
	@echo "Compose:"
	@echo "  make compose-build         docker compose build"
	@echo "  make compose-mcp           Run kelly-mcp via compose (stdio)"
	@echo "  make compose-cli CMD='kelly --help'"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean                 Remove caches"
	@echo "  make clean-venv            Remove .venv and .tools"
	@echo "  make clean-docker          Remove built images"

venv install:
	./scripts/setup-venv.sh

test:
	./.venv/bin/pytest

lint:
	./.venv/bin/ruff check .

format:
	./.venv/bin/ruff format .

docker-build:
	docker build --build-arg POETRY_EXTRAS="$(POETRY_EXTRAS)" -t $(IMAGE) .

docker-build-flights:
	docker build --build-arg POETRY_EXTRAS="mcp" -t $(IMAGE_FLIGHTS) .

docker-build-browsers:
	docker build --build-arg POETRY_EXTRAS="$(POETRY_EXTRAS)" \
	             --build-arg INSTALL_BROWSERS=true -t $(IMAGE_BROWSER) .

# Generic CLI runner with .env loaded from host and config/data bind-mounted.
DOCKER_RUN_FLAGS := --rm \
                    --env-file .env \
                    -v $(CURDIR)/config:/app/config:ro \
                    -v $(CURDIR)/data:/app/data

docker-shell:
	docker run -it $(DOCKER_RUN_FLAGS) --entrypoint /bin/bash $(IMAGE)

# Usage: make docker-run CMD='kelly scan -c config/kelly.md'
CMD ?= kelly --help
docker-run:
	docker run $(DOCKER_RUN_FLAGS) $(IMAGE) $(CMD)

# stdio MCP — keep STDIN open, no TTY, no log noise into the JSON-RPC stream.
docker-mcp:
	docker run -i $(DOCKER_RUN_FLAGS) $(IMAGE)

compose-build:
	INSTALL_BROWSERS=$(INSTALL_BROWSERS) $(COMPOSE) build

compose-mcp:
	$(COMPOSE) run --rm -T kelly

compose-cli:
	$(COMPOSE) run --rm kelly $(CMD)

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__

clean-venv:
	rm -rf .venv .tools

clean-docker:
	-docker rmi $(IMAGE) $(IMAGE_FLIGHTS) $(IMAGE_BROWSER) 2>/dev/null
