"""Run the Great Expectations checkpoint over tables in the DuckDB warehouse.

Usage: python -m xstore.quality.run_checkpoint [--checkpoint PATH] [--warehouse PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT = ROOT / "great_expectations" / "checkpoints" / "canonical.yml"


@dataclass(frozen=True)
class ExpectationOutcome:
    table: str
    expectation: str
    column: str | None
    success: bool
    unexpected_count: int | None


@dataclass(frozen=True)
class CheckpointOutcome:
    name: str
    success: bool
    outcomes: list[ExpectationOutcome]

    def summary(self) -> str:
        lines = [f"checkpoint {self.name}: {'PASS' if self.success else 'FAIL'}"]
        for o in self.outcomes:
            mark = "ok  " if o.success else "FAIL"
            bad = "" if o.unexpected_count is None else f" unexpected={o.unexpected_count}"
            lines.append(f"  {mark} {o.table}.{o.column or '*'} {o.expectation}{bad}")
        return "\n".join(lines)


def warehouse_path(warehouse: Path | str | None = None, default: str = "") -> Path:
    """Explicit path, else XSTORE_WAREHOUSE, else the default (same rule as dbt/profiles.yml)."""
    return Path(
        warehouse or os.environ.get("XSTORE_WAREHOUSE") or default or "runs/warehouse.duckdb"
    )


def read_table(warehouse: Path, table: str) -> pd.DataFrame:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        ident = '"' + table.replace('"', '""') + '"'
        return con.execute(f"select * from {ident}").df()


def run_checkpoint(
    checkpoint: Path = CHECKPOINT, warehouse: Path | str | None = None
) -> CheckpointOutcome:
    """Validate each table named in the checkpoint YAML against its expectation suite."""
    os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")
    from great_expectations.data_context.types.base import ProgressBarsConfig

    import great_expectations as gx

    cfg = yaml.safe_load(checkpoint.read_text())
    db = warehouse_path(warehouse, cfg.get("warehouse", ""))
    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False)
    source = context.data_sources.add_pandas("xstore_warehouse")
    base = checkpoint.parent.parent
    definitions = []
    frames: dict[str, pd.DataFrame] = {}
    for v in cfg["validations"]:
        table = v["table"]
        frames[table] = read_table(db, table)
        suite = context.suites.add(
            gx.ExpectationSuite(**json.loads((base / v["suite"]).read_text()))
        )
        batch = source.add_dataframe_asset(table).add_batch_definition_whole_dataframe(table)
        definitions.append(
            context.validation_definitions.add(
                gx.ValidationDefinition(name=table, data=batch, suite=suite)
            )
        )
    outcomes: list[ExpectationOutcome] = []
    ok = True
    for definition in definitions:
        result: Any = definition.run(batch_parameters={"dataframe": frames[definition.name]})
        ok = ok and bool(result.success)
        for r in result.results:
            cfg_ = r.expectation_config
            outcomes.append(
                ExpectationOutcome(
                    table=definition.name,
                    expectation=cfg_.type,
                    column=cfg_.kwargs.get("column"),
                    success=bool(r.success),
                    unexpected_count=r.result.get("unexpected_count"),
                )
            )
    return CheckpointOutcome(name=cfg["name"], success=ok, outcomes=outcomes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    ap.add_argument("--warehouse", type=Path, default=None)
    args = ap.parse_args(argv)
    outcome = run_checkpoint(args.checkpoint, args.warehouse)
    print(outcome.summary())
    return 0 if outcome.success else 1


if __name__ == "__main__":
    sys.exit(main())
