.PHONY: dagster-validate dagster-dev airflow-check

DAGSTER_DIR := orchestration/dagster

dagster-validate:
	uv run dagster definitions validate -m xstore.orchestration.definitions

dagster-dev:
	@echo "DAGSTER_HOME=$(abspath $(DAGSTER_DIR)) uv run dagster dev -w $(DAGSTER_DIR)/workspace.yaml"

airflow-check:
	uv run python -m py_compile orchestration/airflow/dags/xstore_reconcile.py
