# dbt diff layer, Great Expectations and OpenLineage (ADR-019).
.PHONY: dbt-gen dbt-parse dbt-build gx-check quality

DBT_FLAGS = --project-dir dbt --profiles-dir dbt

dbt-gen:
	uv run python scripts/gen_dbt_staging.py

dbt-parse:
	uv run dbt parse $(DBT_FLAGS)

dbt-build:
	mkdir -p runs
	uv run dbt build $(DBT_FLAGS)

gx-check:
	uv run python -m xstore.quality.run_checkpoint

quality:
	uv run python -m xstore.quality.run_all
