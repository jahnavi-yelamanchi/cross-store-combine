import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from xstore.quality.run_all import run_all

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "evals" / "frozen" / "v1"


def _subset(dst: Path, n: int = 5) -> None:
    """First n records of every store/entity file, with one planted name conflict."""
    for src in FROZEN.glob("*/*.jsonl"):
        lines = src.read_text().splitlines()[:n]
        if src.parent.name == "hubspot" and src.stem == "customer":
            rec = json.loads(lines[0])
            rec["firstname"] = "Planted Name"
            lines[0] = json.dumps(rec)
        (dst / src.parent.name).mkdir(parents=True, exist_ok=True)
        (dst / src.parent.name / src.name).write_text("\n".join(lines) + "\n")


@pytest.mark.smoke
def test_dbt_gx_openlineage_run(tmp_path: Path) -> None:
    snap, wh, events = tmp_path / "snap", tmp_path / "wh.duckdb", tmp_path / "ol"
    _subset(snap)
    result = run_all(snap, wh, events, target_path=tmp_path / "target")
    assert result.success, result.error
    assert {"reconciled_customers", "canonical_customers", "canonical_orders"} <= set(result.models)
    assert result.checkpoint is not None and result.checkpoint.success

    with duckdb.connect(str(wh), read_only=True) as con:
        assert con.execute("select count(*) from reconciled_customers").fetchone() == (5,)
        name, conflicts, n = con.execute(
            "select name, conflict_fields, n_sources from reconciled_customers "
            "where customer_id = 'C000001'"
        ).fetchone() or (None, None, None)
    assert name == "Planted Name"  # hubspot wins names per policy.yaml
    assert conflicts == "name" and n == 6

    states = [json.loads(p.read_text())["eventType"] for p in sorted(events.glob("*.json"))]
    assert states == ["START", "COMPLETE"]


@pytest.mark.smoke
def test_generated_dbt_files_in_sync() -> None:
    gen = [sys.executable, str(ROOT / "scripts" / "gen_dbt_staging.py"), "--check"]
    out = subprocess.run(gen, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr
