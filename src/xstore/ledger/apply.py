"""Apply approved fixes exactly once per proposal id."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from xstore.ledger.ledger import Ledger
from xstore.ledger.models import DELETED, FixApplied, FixApproved, record_hash
from xstore.observability.instrument import observe_apply
from xstore.observability.tracing import span
from xstore.stores.base import Store


class ApplyReport(BaseModel):
    applied: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = []
    bad_writes: int = 0  # FixApplied whose read-back hash != intended hash; must stay 0


def readback_hash(store: Store, fix: FixApproved) -> str:
    """Hash of the record as read back, restricted to the fields the fix wrote.

    Store-managed metadata (e.g. Cosmos `_etag`, `_ts`) is excluded so the hash is
    deterministic across replays; a dropped or altered field still changes it.
    """
    back = store.read(fix.entity_type, fix.key)
    if back is None:
        return DELETED if fix.action == "delete" else record_hash({})
    if fix.action == "delete" or fix.record is None:
        return record_hash(back)
    return record_hash({k: back.get(k) for k in fix.record})


def intended_hash(fix: FixApproved) -> str:
    return DELETED if fix.action == "delete" else record_hash(fix.record)


def execute_fix(store: Store, fix: FixApproved) -> str:
    """Write or delete with key `fix:<proposal_id>`; return the read-back hash."""
    idem = f"fix:{fix.proposal_id}"
    if fix.action == "delete":
        store.delete(fix.entity_type, fix.key, idem)
    else:
        if fix.record is None:
            raise ValueError("upsert fix without a record")
        store.write(fix.entity_type, fix.record, idem)
    return readback_hash(store, fix)


def _proposal_ids(ledger: Ledger, type: Any) -> set[str]:
    return {str(e.payload["proposal_id"]) for e in ledger.events(types=[type])}


def apply_pending(ledger: Ledger, stores: Mapping[str, Store]) -> ApplyReport:
    """Apply each FixApproved lacking a FixApplied (or FixRejected) for its proposal id."""
    with span("ledger.apply_pending") as s:
        report = _apply_pending(ledger, stores)
        observe_apply(s, report)
    return report


def _apply_pending(ledger: Ledger, stores: Mapping[str, Store]) -> ApplyReport:
    done = _proposal_ids(ledger, "FixApplied") | _proposal_ids(ledger, "FixRejected")
    report = ApplyReport()
    for event in ledger.events(types=["FixApproved"]):
        fix = FixApproved.model_validate(event.payload)
        if fix.proposal_id in done:
            report.skipped += 1
            continue
        try:
            got = execute_fix(stores[fix.target_store], fix)
        except Exception as e:  # a bad target must not stop the batch
            report.failed.append((fix.proposal_id, f"{type(e).__name__}: {e}"))
            continue
        applied = FixApplied(
            proposal_id=fix.proposal_id,
            target_store=fix.target_store,
            entity_type=fix.entity_type,
            key=fix.key,
            action=fix.action,
            record_hash=got,
        )
        ledger.append("FixApplied", applied.model_dump(), f"applied:{fix.proposal_id}")
        done.add(fix.proposal_id)
        report.applied += 1
        if got != intended_hash(fix):
            report.bad_writes += 1
    return report
