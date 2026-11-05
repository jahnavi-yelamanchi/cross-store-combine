"""Comparison matrix on the frozen snapshot: one JSON line per config to <out>/<run_id>.jsonl.

uv run python scripts/run_matrix.py --split holdout --out runs [--limit N]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from xstore.evals.matrix import run_matrix
from xstore.evals.snapshot import DEFAULT_DATA


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_matrix")
    ap.add_argument("--split", choices=["train", "holdout"], required=True)
    ap.add_argument("--out", type=Path, default=Path("runs"))
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--limit", type=int, default=None, help="keys per entity type")
    args = ap.parse_args(argv)
    os.environ.setdefault("STORE_BACKEND", "local")
    os.environ.setdefault("MODEL_PROVIDER", "mock")
    logging.getLogger("splink").setLevel(logging.ERROR)
    t0 = time.perf_counter()
    path = run_matrix(args.split, args.out, args.data, args.limit)
    for line in path.read_text().splitlines():
        r = json.loads(line)
        print(
            f"{r['config']:<20} {r['model']:<7} {r['mode']:<9} f1={r['f1']:.3f} "
            f"p={r['precision']:.3f} r={r['recall']:.3f} cost/1k=${r['cost_per_1k']:.5f} "
            f"bad={r['bad_write_rate']:.3f} fix={r['correct_fix_rate']:.3f} [{r['note']}]"
        )
    print(f"-> {path} ({time.perf_counter() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
