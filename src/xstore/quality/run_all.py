"""dbt build + Great Expectations checkpoint, wrapped in OpenLineage START/COMPLETE/FAIL.

Usage: python -m xstore.quality.run_all [--snapshot DIR] [--warehouse PATH] [--events DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from xstore.quality.lineage import (
    EVENTS_DIR,
    LineageRun,
    checked_input,
    store_datasets,
    warehouse_outputs,
)
from xstore.quality.lineage import Assertion as OLAssertion
from xstore.quality.run_checkpoint import CHECKPOINT, CheckpointOutcome, run_checkpoint

ROOT = Path(__file__).resolve().parents[3]
DBT_DIR = ROOT / "dbt"
SNAPSHOT = ROOT / "evals" / "frozen" / "v1"
WAREHOUSE = Path("runs/warehouse.duckdb")
JOB_NAME = "quality.run_all"


class DbtError(RuntimeError):
    pass


@dataclass
class RunAllResult:
    success: bool
    run_id: str
    models: list[str] = field(default_factory=list)
    checkpoint: CheckpointOutcome | None = None
    error: str | None = None


@contextmanager
def _env(key: str, value: str) -> Iterator[None]:
    old = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _release_duckdb() -> None:
    """dbt-duckdb caches its DuckDB handle per process; close it so later readers can open."""
    from dbt.adapters.duckdb.connections import DuckDBConnectionManager

    env = DuckDBConnectionManager._ENV
    if env is not None:
        env.close()
    DuckDBConnectionManager.close_all_connections()


def run_dbt(
    command: str,
    snapshot_dir: Path = SNAPSHOT,
    warehouse: Path = WAREHOUSE,
    target_path: Path | None = None,
) -> list[str]:
    """Invoke dbt in-process; return the models it built. Raises DbtError on errors."""
    from dbt.cli.main import dbtRunner

    args = [command, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--quiet"]
    args += ["--vars", json.dumps({"snapshot_dir": str(snapshot_dir)})]
    if target_path is not None:
        args += ["--target-path", str(target_path), "--log-path", str(target_path / "logs")]
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    with _env("XSTORE_WAREHOUSE", str(warehouse)):
        try:
            res = dbtRunner().invoke(args)
        finally:
            _release_duckdb()
    if not res.success:
        failed = []
        for r in getattr(res.result, "results", None) or []:
            if str(r.status) in ("error", "fail"):
                failed.append(f"{r.node.name}: {r.message}")
        raise DbtError(f"dbt {command} failed: {res.exception or '; '.join(failed)}")
    return [
        r.node.name
        for r in getattr(res.result, "results", None) or []
        if str(r.node.resource_type) == "model"
    ]


def run_all(
    snapshot_dir: Path = SNAPSHOT,
    warehouse: Path = WAREHOUSE,
    events_dir: Path = EVENTS_DIR,
    checkpoint: Path = CHECKPOINT,
    target_path: Path | None = None,
) -> RunAllResult:
    lineage = LineageRun(JOB_NAME, store_datasets(snapshot_dir), events_dir=events_dir)
    lineage.start()
    models: list[str] = []
    try:
        models = run_dbt("build", snapshot_dir, warehouse, target_path)
        outcome = run_checkpoint(checkpoint, warehouse)
    except Exception as e:
        lineage.fail(str(e), warehouse_outputs(warehouse, models))
        return RunAllResult(False, lineage.run_id, models, error=str(e))
    assertions: dict[str, list[OLAssertion]] = {}
    for o in outcome.outcomes:
        assertions.setdefault(o.table, []).append(
            OLAssertion(assertion=o.expectation, success=o.success, column=o.column)
        )
    checked = [checked_input(warehouse, t, a) for t, a in assertions.items()]
    outputs = warehouse_outputs(warehouse, models)
    if not outcome.success:
        lineage.fail(outcome.summary(), outputs)
        return RunAllResult(False, lineage.run_id, models, outcome, outcome.summary())
    lineage.complete(outputs, checked)
    return RunAllResult(True, lineage.run_id, models, outcome)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    ap.add_argument("--warehouse", type=Path, default=None)
    ap.add_argument("--events", type=Path, default=EVENTS_DIR)
    args = ap.parse_args(argv)
    warehouse = args.warehouse or Path(os.environ.get("XSTORE_WAREHOUSE") or WAREHOUSE)
    result = run_all(args.snapshot, warehouse, args.events)
    if result.checkpoint is not None:
        print(result.checkpoint.summary())
    print(
        f"run {result.run_id}: {'COMPLETE' if result.success else 'FAIL'}"
        f" ({len(result.models)} models)"
    )
    if result.error and result.checkpoint is None:
        print(result.error, file=sys.stderr)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
