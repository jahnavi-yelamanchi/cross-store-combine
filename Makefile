.PHONY: install lint fmt typecheck smoke test check crm freeze verify-frozen proposals-api policy-check match

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

freeze:
	uv run python -m xstore.synth.freeze --out evals/frozen/v1 --seed 42

verify-frozen:
	uv run python -m xstore.synth.freeze --out evals/frozen/v1 --verify

crm:
	uv run uvicorn xstore.crm.app:app --port 8030

proposals-api:
	uv run uvicorn xstore.proposals.app:app --port 8040

policy-check:
	uv run python -m xstore.policy check policy/policy.yaml

match:
	uv run python -m xstore.matching --data evals/frozen/v1 --out runs/matches.parquet
