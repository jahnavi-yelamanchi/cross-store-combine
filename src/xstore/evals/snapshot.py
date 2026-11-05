"""Frozen snapshot in memory: native records per (store, entity_type), read-only Store view."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa

from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.matching.canonical import SCHEMA as CUSTOMER_SCHEMA
from xstore.matching.canonical import to_row
from xstore.stores.base import ChangeEvent, WriteResult, now_ms

DEFAULT_DATA = Path(__file__).resolve().parents[3] / "evals" / "frozen" / "v1"
Record = dict[str, Any]


def native_key(store: str, entity_type: str, record: Record) -> str:
    m = load_mapping(store).for_entity(entity_type)
    return str(record.get(m.key, "")).removeprefix(m.key_prefix)


def jtype(value: Any) -> str:
    """JSON type name of a native value."""
    return type(value).__name__


class Snapshot:
    """(store, entity_type) -> key -> native record, plus a content index for duplicates."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, Record]] = defaultdict(dict)
        self._fp: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def add(self, store: str, entity_type: str, record: Record) -> str:
        key = native_key(store, entity_type, record)
        self.records[(store, entity_type)][key] = record
        if entity_type != "customer":
            self._fp[(store, entity_type)][fingerprint(store, entity_type, record)].add(key)
        return key

    def stores_for(self, entity_type: str) -> list[str]:
        """Stores whose mapping holds entity_type (present in the snapshot or not)."""
        return sorted({s for s, et in self.records if et == entity_type})

    def holders(self, entity_type: str, key: str) -> list[str]:
        return [s for s in self.stores_for(entity_type) if key in self.records[(s, entity_type)]]

    def same_content(self, store: str, entity_type: str, record: Record) -> set[str]:
        return set(self._fp[(store, entity_type)].get(fingerprint(store, entity_type, record), ()))

    def keys(self, entity_type: str) -> set[str]:
        return {k for (s, et), recs in self.records.items() if et == entity_type for k in recs}

    def n_entities(self) -> int:
        return sum(len(self.keys(et)) for et in ENTITY_TYPES)

    def empty_like(self) -> Snapshot:
        out = Snapshot()
        for pair in self.records:
            out.records[pair] = {}
        return out

    def customer_table(self) -> pa.Table:
        rows = [
            to_row(s, rec)
            for s in self.stores_for("customer")
            for rec in self.records[(s, "customer")].values()
        ]
        return pa.Table.from_pylist(rows, schema=CUSTOMER_SCHEMA)


def fingerprint(store: str, entity_type: str, record: Record) -> str:
    """Record content minus business key and updated_at; equal for injected copies."""
    m = load_mapping(store).for_entity(entity_type)
    skip = {m.key, m.fields["updated_at"]}
    return json.dumps({k: v for k, v in record.items() if k not in skip}, sort_keys=True)


def load_snapshot(data_dir: Path = DEFAULT_DATA, limit: int | None = None) -> Snapshot:
    """Read <data_dir>/<store>/<entity_type>.jsonl. `limit` keeps the first N sorted keys per
    entity type across stores (injected duplicate keys sort last, so they drop out)."""
    raw: dict[tuple[str, str], list[Record]] = {}
    for path in sorted(Path(data_dir).glob("*/*.jsonl")):
        store, entity_type = path.parent.name, path.stem
        raw[(store, entity_type)] = [json.loads(line) for line in path.open() if line.strip()]
    keep: dict[str, set[str]] | None = None
    if limit is not None:
        by_type: dict[str, set[str]] = defaultdict(set)
        for (store, et), recs in raw.items():
            by_type[et].update(native_key(store, et, r) for r in recs)
        keep = {et: set(sorted(keys)[:limit]) for et, keys in by_type.items()}
    snap = Snapshot()
    for (store, et), recs in raw.items():
        snap.records[(store, et)] = {}
        for rec in recs:
            if keep is None or native_key(store, et, rec) in keep[et]:
                snap.add(store, et, rec)
    return snap


def expected_types(snap: Snapshot) -> dict[tuple[str, str, str], str]:
    """Modal JSON type per (store, entity_type, native field): the inferred store schema."""
    counts: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for (store, et), recs in snap.records.items():
        for rec in recs.values():
            for field, value in rec.items():
                if value is not None:
                    counts[(store, et, field)][jtype(value)] += 1
    return {k: c.most_common(1)[0][0] for k, c in counts.items()}


class SnapshotStore:
    """Read-only Store over one store's records in a Snapshot (evidence for the agent)."""

    def __init__(self, name: str, snapshot: Snapshot) -> None:
        self.name = name
        self.snapshot = snapshot

    def read(self, entity_type: str, key: str) -> Record | None:
        return self.snapshot.records.get((self.name, entity_type), {}).get(key)

    def scan(self, entity_type: str) -> Iterator[Record]:
        yield from self.snapshot.records.get((self.name, entity_type), {}).values()

    def write(self, entity_type: str, record: Record, idempotency_key: str) -> WriteResult:
        raise NotImplementedError("snapshot stores are read-only")

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        raise NotImplementedError("snapshot stores are read-only")

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        """Every record as a create event, in file order; seq starts at 1."""
        seq = 0
        for (store, et), recs in sorted(self.snapshot.records.items()):
            if store != self.name:
                continue
            for key, rec in recs.items():
                seq += 1
                if seq > since:
                    yield ChangeEvent(
                        store=store,
                        entity_type=et,
                        key=key,
                        op="c",
                        seq=seq,
                        ts_ms=now_ms(),
                        after=rec,
                    )


def snapshot_stores(snap: Snapshot) -> dict[str, SnapshotStore]:
    return {s: SnapshotStore(s, snap) for s in sorted({s for s, _ in snap.records})}
