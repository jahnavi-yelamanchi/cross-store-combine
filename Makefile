.PHONY: install lint fmt typecheck smoke test check crm

export STORE_BACKEND ?= local
export MODEL_PROVIDER ?= mock

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

smoke:
	uv run pytest -m smoke

test:
	uv run pytest

check: lint typecheck smoke

crm:
	uv run uvicorn xstore.crm.app:app --port 8030
