"""Airflow mirror of the Dagster reconcile assets (xstore.orchestration.definitions).

ingest >> cdc_events >> diff >> detect >> matches >> propose, every 15 minutes.
Tasks call the same `xstore.orchestration.steps` functions and pass small dicts via XCom.
Local in-memory stores need one process: `airflow dags test xstore_reconcile`.
Real mode: STORE_BACKEND, DATABASE_URL and XSTORE_PROPOSALS_URL come from the environment.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from airflow.decorators import dag, task

NAMESPACE = os.environ.get("XSTORE_NAMESPACE", "airflow")
LIMIT = int(os.environ.get("XSTORE_INGEST_LIMIT", "50"))


def _rt() -> Any:
    from xstore.orchestration import steps

    return steps.runtime(NAMESPACE)


@dag(
    dag_id="xstore_reconcile",
    schedule="*/15 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["xstore", "reconcile"],
)
def xstore_reconcile() -> None:
    @task
    def ingest() -> dict[str, int]:
        from xstore.evals.snapshot import DEFAULT_DATA
        from xstore.orchestration import steps

        snapshot_dir = os.environ.get("XSTORE_SNAPSHOT_DIR", str(DEFAULT_DATA))
        return steps.ingest(_rt(), snapshot_dir, LIMIT)

    @task
    def cdc_events(counts: dict[str, int]) -> dict[str, int]:
        from xstore.orchestration import steps

        return steps.cdc_events(_rt())

    @task
    def diff(events: dict[str, int]) -> dict[str, Any]:
        from xstore.orchestration import steps

        return steps.diff(_rt())

    @task
    def detect(compared: dict[str, Any]) -> list[dict[str, Any]]:
        from xstore.orchestration import steps

        return steps.detect(_rt(), compared["touched"])

    @task
    def matches(detections: list[dict[str, Any]]) -> dict[str, Any]:
        from xstore.orchestration import steps

        return steps.matches(_rt())

    @task
    def propose(detections: list[dict[str, Any]], pairs: dict[str, Any]) -> dict[str, int]:
        import httpx
        from airflow.operators.python import get_current_context
        from fastapi.testclient import TestClient

        from xstore.orchestration import steps
        from xstore.proposals import SqlRepository
        from xstore.proposals.app import create_app

        run_id = str(get_current_context()["run_id"])
        url = os.environ.get("XSTORE_PROPOSALS_URL")
        client = (
            httpx.Client(base_url=url, timeout=30)
            if url
            else TestClient(create_app(SqlRepository.from_env()))
        )
        with client:
            out = steps.propose(_rt(), detections, pairs["duplicates"], client, run_id)
        if out["errors"]:
            raise RuntimeError(f"{out['errors']} proposals failed to post")
        return out

    found = detect(diff(cdc_events(ingest())))
    propose(found, matches(found))


xstore_reconcile()
