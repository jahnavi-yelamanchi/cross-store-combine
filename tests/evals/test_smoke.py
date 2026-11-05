import json
import subprocess
import sys
from pathlib import Path

import pytest

from xstore.evals.matrix import Config, run_matrix

ROOT = Path(__file__).resolve().parents[2]
GATE_KEYS = {
    "run_id",
    "config",
    "model",
    "mode",
    "split",
    "precision",
    "recall",
    "f1",
    "cost_per_1k",
    "bad_write_rate",
    "approvals_needed",
    "latency_p50_ms",
    "latency_p95_ms",
    "per_type",
}


@pytest.mark.smoke
def test_matrix_rows_and_report(tmp_path: Path) -> None:
    configs = (
        Config("splink_only", None, "streaming"),
        Config("hybrid", "haiku", "batch"),
    )
    path = run_matrix("holdout", tmp_path / "runs", limit=150, configs=configs, run_id="smoke")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    hybrid = next(r for r in rows if r["config"] == "hybrid" and r["split"] == "holdout")
    assert set(hybrid) >= GATE_KEYS
    assert 0 <= hybrid["f1"] <= 1 and hybrid["cost_per_1k"] >= 0
    assert hybrid["bad_write_rate"] == 0.0
    assert rows[0]["latency_p95_ms"] >= rows[0]["latency_p50_ms"] > 0

    out = tmp_path / "results"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "report.py"),
            "--runs",
            str(path.parent),
            "--out",
            str(out),
        ],
        check=True,
        cwd=ROOT,
    )
    assert "hybrid" in (out / "results.md").read_text()
    assert (out / "f1_vs_cost.png").read_bytes()[:4] == b"\x89PNG"
