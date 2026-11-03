"""Write a frozen eval set: per-store JSONL, labels, holdout and a hashed manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from xstore.synth.generator import generate
from xstore.synth.injector import Label, inject

MANIFEST = "MANIFEST.json"


def _line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")) + "\n"


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != MANIFEST):
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


def split(labels: list[Label], holdout: int, seed: int) -> tuple[list[Label], list[Label]]:
    held = set(random.Random(seed).sample(range(len(labels)), holdout))
    train = [lab for i, lab in enumerate(labels) if i not in held]
    return train, [lab for i, lab in enumerate(labels) if i in held]


def freeze(
    out: Path,
    seed: int,
    customers: int = 4000,
    orders: int = 4000,
    batches: int = 2000,
    injections: int = 500,
    holdout: int = 100,
) -> dict[str, Any]:
    data = generate(seed, customers, orders, batches)
    labels = inject(data, injections, seed)
    train, held = split(labels, holdout, seed)
    if out.exists():
        shutil.rmtree(out)
    records: dict[str, int] = {}
    for store, by_type in data.items():
        (out / store).mkdir(parents=True)
        for etype, recs in by_type.items():
            (out / store / f"{etype}.jsonl").write_text("".join(_line(r) for r in recs))
            records[f"{store}/{etype}"] = len(recs)
    for name, labs in (("labels.jsonl", train), ("holdout.jsonl", held)):
        (out / name).write_text("".join(_line(lab.model_dump()) for lab in labs))
    manifest = {
        "seed": seed,
        "entities": {"customer": customers, "order": orders, "batch": batches},
        "injections": dict(sorted(Counter(lab.type for lab in labels).items())),
        "labels": len(train),
        "holdout": len(held),
        "records": records,
        "sha256": tree_hash(out),
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify(out: Path) -> bool:
    expected = json.loads((out / MANIFEST).read_text())["sha256"]
    return bool(expected == tree_hash(out))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m xstore.synth.freeze")
    p.add_argument("--out", type=Path, default=Path("evals/frozen/v1"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--verify", action="store_true", help="recompute hash and compare to manifest")
    args = p.parse_args(argv)
    if args.verify:
        ok = verify(args.out)
        print(f"{args.out}: {'ok' if ok else 'HASH MISMATCH'}")
        return 0 if ok else 1
    print(freeze(args.out, args.seed)["sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
