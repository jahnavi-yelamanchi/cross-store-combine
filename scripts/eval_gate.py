"""Fail CI when eval results fall below evals/thresholds.yaml; write a job summary.

runs/*.jsonl lines: {"run_id", "config", "model", "mode", "split", "precision", "recall",
"f1", "cost_per_1k", "bad_write_rate", "approvals_needed", "latency_p50_ms",
"latency_p95_ms", "per_type": {type: {"precision", "recall"}}}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml


def latest_run(runs_dir: Path, config: str, split: str) -> dict[str, Any] | None:
    rows = [
        json.loads(line)
        for f in sorted(runs_dir.glob("*.jsonl"))
        for line in f.read_text().splitlines()
        if line.strip()
    ]
    rows = [r for r in rows if r.get("config") == config and r.get("split") == split]
    return rows[-1] if rows else None


def inspect_accuracy(log_dir: Path) -> float | None:
    try:
        from inspect_ai.log import list_eval_logs, read_eval_log
    except ImportError:
        return None
    logs = list_eval_logs(str(log_dir))
    if not logs:
        return None
    log = read_eval_log(logs[0])
    if log.results is None or not log.results.scores:
        return None
    return float(log.results.scores[0].metrics["accuracy"].value)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--inspect-logs", default="logs")
    ap.add_argument("--thresholds", default="evals/thresholds.yaml")
    args = ap.parse_args()
    t = yaml.safe_load(Path(args.thresholds).read_text())
    g = t["gate"]
    checks: list[tuple[str, Any, str, bool]] = []
    run = latest_run(Path(args.runs), g["config"], g["split"])
    if run is None:
        checks.append((f"{g['config']}/{g['split']} run", "missing", "present", False))
    else:
        checks += [
            ("f1", run["f1"], f">= {g['min_f1']}", run["f1"] >= g["min_f1"]),
            ("recall", run["recall"], f">= {g['min_recall']}", run["recall"] >= g["min_recall"]),
            (
                "bad_write_rate",
                run["bad_write_rate"],
                f"<= {g['max_bad_write_rate']}",
                run["bad_write_rate"] <= g["max_bad_write_rate"],
            ),
            (
                "cost_per_1k",
                run["cost_per_1k"],
                f"<= {g['max_cost_per_1k_usd']}",
                run["cost_per_1k"] <= g["max_cost_per_1k_usd"],
            ),
        ]
    acc = inspect_accuracy(Path(args.inspect_logs))
    if acc is not None:
        need = t["inspect"]["min_accuracy"]
        checks.append(("inspect accuracy", acc, f">= {need}", acc >= need))
    lines = ["## Eval gate", "", "| check | value | threshold | pass |", "|---|---|---|---|"]
    lines += [f"| {n} | {v} | {th} | {'yes' if ok else 'NO'} |" for n, v, th, ok in checks]
    text = "\n".join(lines) + "\n"
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(text)
    return 0 if all(ok for *_, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
