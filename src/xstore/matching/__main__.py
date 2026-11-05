"""CLI: python -m xstore.matching --data evals/frozen/v1 --out runs/matches.parquet"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from xstore.matching.canonical import load_customers
from xstore.matching.splink_matcher import pairs_to_table, train_and_predict


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m xstore.matching")
    ap.add_argument("--data", type=Path, default=Path("evals/frozen/v1"))
    ap.add_argument("--out", type=Path, default=Path("runs/matches.parquet"))
    ap.add_argument("--model", type=Path, default=None, help="write trained model JSON")
    ap.add_argument("--limit", type=int, default=None, help="records per store")
    args = ap.parse_args(argv)

    table = load_customers(args.data, limit_per_store=args.limit)
    pairs = train_and_predict(table, model_path=args.model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pairs_to_table(pairs), args.out)
    counts = Counter(p.band for p in pairs)
    print(f"records={table.num_rows} pairs={len(pairs)} -> {args.out}")
    for band in ("match", "tiebreak", "non_match"):
        print(f"{band}: {counts[band]}")


if __name__ == "__main__":
    main()
