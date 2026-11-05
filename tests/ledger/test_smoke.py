import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from xstore.ledger import apply_pending, create_ledger
from xstore.ledger.replay import replay
from xstore.stores import get_store
from xstore.stores.seed import load_snapshot, seed_stores

SNAPSHOT = {
    "postgres": {
        "customer": [
            {"id": "C1", "full_name": "Ann Lee", "email": "ann@x.io", "country_code": "FR"},
            {"id": "C2", "full_name": "Bo Kim", "email": "bo@x.io", "country_code": "GB"},
        ]
    },
    "mongodb": {"customer": [{"_id": "C1", "name": "Ann Lee", "email": "ann@x.io"}]},
    "cosmosdb": {"customer": [{"id": "C1", "displayName": "Ann Lee", "email": "ann@x.io"}]},
}


def _write_snapshot(root: Path) -> Path:
    for store, entities in SNAPSHOT.items():
        (root / store).mkdir()
        for entity_type, records in entities.items():
            lines = "\n".join(json.dumps(r) for r in records)
            (root / store / f"{entity_type}.jsonl").write_text(lines + "\n")
    return root


def _fix(pid: str, store: str, key: str, action: str, record: dict | None) -> dict:
    return {
        "proposal_id": pid,
        "target_store": store,
        "entity_type": "customer",
        "key": key,
        "action": action,
        "record": record,
        "approved_by": "alice",
    }


@pytest.mark.smoke
def test_ledger_apply_is_idempotent_append_only_and_replays(tmp_path: Path) -> None:
    snap = _write_snapshot(tmp_path)
    stores = seed_stores(load_snapshot(snap), get_store)
    assert set(stores) == {"postgres", "mongodb", "cosmosdb"}
    assert stores["postgres"].read("customer", "C2") is not None

    ledger = create_ledger()
    fixed = {"_id": "C1", "name": "Ann Lee", "email": "ann.lee@x.io"}
    first = ledger.append("FixApproved", _fix("p1", "mongodb", "C1", "upsert", fixed), "ap:p1")
    ledger.append("FixApproved", _fix("p2", "postgres", "C2", "delete", None), "ap:p2")
    dup = ledger.append("FixApproved", _fix("p1", "mongodb", "C1", "upsert", fixed), "ap:p1")
    assert dup.seq == first.seq and ledger.count() == 2

    report = apply_pending(ledger, stores)
    assert (report.applied, report.failed, report.bad_writes) == (2, [], 0)
    assert apply_pending(ledger, stores).applied == 0
    assert stores["mongodb"].read("customer", "C1") == fixed
    assert stores["postgres"].read("customer", "C2") is None

    with pytest.raises(DatabaseError, match="append-only"), ledger.engine.begin() as conn:
        conn.execute(text("UPDATE ledger_events SET type = 'x' WHERE seq = 1"))
    with pytest.raises(DatabaseError, match="append-only"), ledger.engine.begin() as conn:
        conn.execute(text("DELETE FROM ledger_events"))

    result = replay(ledger, snap)
    assert result.ok and result.chain_ok and result.mismatches == []
    assert (result.events, result.fixes_replayed) == (4, 2)
