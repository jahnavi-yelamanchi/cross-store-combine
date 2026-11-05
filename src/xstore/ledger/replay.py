"""Replay the ledger against fresh stores seeded from a frozen snapshot.

Proves determinism: every applied fix must reproduce its recorded record hash.
Usage: python -m xstore.ledger.replay --ledger sqlite:///runs/ledger.db --snapshot evals/frozen/v1
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from xstore.ledger.apply import execute_fix
from xstore.ledger.ledger import Ledger, create_ledger
from xstore.ledger.models import FixApplied, FixApproved
from xstore.stores.base import Store
from xstore.stores.seed import isolated_factory, load_snapshot, seed_stores

REPLAY_REGION = "eu-west-3"


class ReplayReport(BaseModel):
    events: int
    fixes_replayed: int
    mismatches: list[str]
    chain_ok: bool
    ok: bool


def replay(
    ledger: Ledger,
    snapshot_dir: Path,
    factory: Callable[[str], Store] | None = None,
) -> ReplayReport:
    """Seed fresh stores, re-apply applied fixes in seq order, compare record hashes."""
    factory = factory or isolated_factory(REPLAY_REGION)
    events = list(ledger.events())
    recorded = {
        a.proposal_id: a
        for a in (FixApplied.model_validate(e.payload) for e in events if e.type == "FixApplied")
    }
    targets = {a.target_store for a in recorded.values()}  # seed only stores fixes touched
    stores = seed_stores(load_snapshot(Path(snapshot_dir), targets), factory)
    mismatches: list[str] = []
    replayed = 0
    for e in events:
        if e.type != "FixApproved":
            continue
        fix = FixApproved.model_validate(e.payload)
        want = recorded.get(fix.proposal_id)
        if want is None:
            continue  # never applied originally (pending, failed or rejected)
        store = stores.get(fix.target_store)
        if store is None:
            mismatches.append(f"{fix.proposal_id}: store {fix.target_store} not in snapshot")
            continue
        try:
            got = execute_fix(store, fix)
        except Exception as exc:
            mismatches.append(f"{fix.proposal_id}: {type(exc).__name__}: {exc}")
            continue
        replayed += 1
        if got != want.record_hash:
            mismatches.append(
                f"{fix.proposal_id}: hash {got[:12]} != recorded {want.record_hash[:12]}"
            )
    chain_ok = ledger.verify_chain()
    return ReplayReport(
        events=len(events),
        fixes_replayed=replayed,
        mismatches=mismatches,
        chain_ok=chain_ok,
        ok=chain_ok and not mismatches,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", required=True, help="SQLAlchemy URL of the ledger")
    p.add_argument("--snapshot", required=True, type=Path)
    p.add_argument("--region", default=REPLAY_REGION, help="moto region for replay stores")
    args = p.parse_args(argv)
    report = replay(create_ledger(args.ledger), args.snapshot, isolated_factory(args.region))
    print(report.model_dump_json(indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
