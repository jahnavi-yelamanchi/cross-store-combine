"""Bootstrap contracts/<store>.yaml from stores/mappings + canonical Pydantic models.

Contracts are the source of truth once committed: existing files are kept unless --force.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from xstore.contracts.generate import generate_all
from xstore.contracts.models import CONTRACTS_DIR, dump_contract

HEADER = (
    "# Data contract (data product owned by {owner}). Bootstrapped by scripts/gen_contracts.py.\n"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=CONTRACTS_DIR)
    parser.add_argument("--force", action="store_true", help="overwrite existing contracts")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for store, contract in generate_all().items():
        path = args.out / f"{store}.yaml"
        if path.exists() and not args.force:
            print(f"keep  {path}")
            continue
        path.write_text(HEADER.format(owner=contract.info.owner) + dump_contract(contract))
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
