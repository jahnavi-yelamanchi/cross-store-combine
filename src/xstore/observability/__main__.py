"""Run a small in-process demo and print the Prometheus exposition. No server is started.

Usage: python -m xstore.observability [--customers 60 --injections 8 --seed 7]
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from xstore.observability.metrics import render


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m xstore.observability")
    p.add_argument("--customers", type=int, default=60)
    p.add_argument("--injections", type=int, default=8)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    from xstore.pipeline.demo import run_demo

    with tempfile.TemporaryDirectory() as d:
        run_demo(args.customers, args.injections, args.seed, Path(d))
    print(render(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
