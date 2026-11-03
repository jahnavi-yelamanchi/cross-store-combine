"""Shared Parquet lake core for the S3 and ADLS Gen2 adapters.

Layout per entity container: append-only delta files
`<container>/data/<seq:012d>-<uuid>.parquet`, one row per commit with an `_op`
column (c/u/d). Current state = last delta per key. Idempotency keys, the next
seq and the change log live in `_xstore/manifest.json`. Single writer assumed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, get_args

import pyarrow as pa
import pyarrow.parquet as pq

from xstore.entities import ENTITY_TYPES, EntityMapping, StoreMapping
from xstore.stores.base import ChangeEvent, Op, WriteResult, now_ms

OP = "_op"
EXTRA = "_extra"  # native fields not in the mapping (schema drift) as JSON
MANIFEST = "_xstore/manifest.json"


class ByteStore(Protocol):
    """Minimal object-store surface: whole-object get/put and prefix listing."""

    def get(self, path: str) -> bytes | None: ...

    def put(self, path: str, data: bytes) -> None: ...

    def ls(self, prefix: str) -> list[str]: ...


class DictByteStore:
    """In-memory object store (local ADLS backend)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, path: str) -> bytes | None:
        return self.objects.get(path)

    def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data

    def ls(self, prefix: str) -> list[str]:
        return sorted(p for p in self.objects if p.startswith(prefix))


def arrow_type(annotation: Any, unit: str | None = None) -> pa.DataType:
    """Arrow type for a canonical field annotation (Optional unwrapped)."""
    if unit == "cents":
        return pa.int64()
    types = set(get_args(annotation)) or {annotation}
    if Decimal in types:
        return pa.decimal128(18, 2)
    if datetime in types:
        return pa.timestamp("us", tz="UTC")
    if int in types:
        return pa.int64()
    if float in types:
        return pa.float64()
    return pa.string()


def arrow_schema(entity_type: str, m: EntityMapping) -> pa.Schema:
    model = ENTITY_TYPES[entity_type]
    fields = [
        pa.field(native, arrow_type(model.model_fields[canon].annotation, m.units.get(canon)))
        for canon, native in m.fields.items()
    ]
    return pa.schema([*fields, pa.field(OP, pa.string()), pa.field(EXTRA, pa.string())])


def _jsonable(record: dict[str, Any] | None) -> dict[str, Any] | None:
    return json.loads(json.dumps(record, default=str)) if record is not None else None


class ParquetStore:
    name: str

    def __init__(self, fs: ByteStore, mapping: StoreMapping) -> None:
        self.fs = fs
        self.mapping = mapping
        self.schemas = {t: arrow_schema(t, m) for t, m in mapping.entities.items()}

    def _manifest(self) -> dict[str, Any]:
        raw = self.fs.get(MANIFEST)
        if raw is None:
            return {"next_seq": 1, "applied": [], "changes": []}
        manifest: dict[str, Any] = json.loads(raw)
        return manifest

    def _save(self, manifest: dict[str, Any]) -> None:
        self.fs.put(MANIFEST, json.dumps(manifest).encode())

    def _files(self, entity_type: str) -> list[str]:
        prefix = f"{self.mapping.for_entity(entity_type).container}/data/"
        return [p for p in self.fs.ls(prefix) if p.endswith(".parquet")]

    def _rows(self, entity_type: str) -> Iterator[dict[str, Any]]:
        for path in self._files(entity_type):
            data = self.fs.get(path)
            if data is not None:
                yield from pq.read_table(pa.BufferReader(data)).to_pylist()

    def _current(self, entity_type: str) -> dict[str, dict[str, Any]]:
        pk = self.mapping.for_entity(entity_type).key
        state: dict[str, dict[str, Any]] = {}
        for row in self._rows(entity_type):
            if row[OP] == "d":
                state.pop(str(row[pk]), None)
            else:
                state[str(row[pk])] = row
        return state

    @staticmethod
    def _to_record(row: dict[str, Any]) -> dict[str, Any]:
        extra = row.get(EXTRA)
        record = {k: v for k, v in row.items() if k not in (OP, EXTRA) and v is not None}
        if extra:
            record.update(json.loads(extra))
        return record

    def _commit(self, entity_type: str, row: dict[str, Any], seq: int) -> None:
        schema = self.schemas[entity_type]
        table = pa.Table.from_pylist([row], schema=schema)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        container = self.mapping.for_entity(entity_type).container
        self.fs.put(
            f"{container}/data/{seq:012d}-{uuid.uuid4().hex}.parquet", sink.getvalue().to_pybytes()
        )

    def _apply(
        self,
        entity_type: str,
        key: str,
        idempotency_key: str,
        record: dict[str, Any] | None,
    ) -> WriteResult:
        manifest = self._manifest()
        if idempotency_key in manifest["applied"]:
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        manifest["applied"].append(idempotency_key)
        before = self.read(entity_type, key)
        op: Op = "d" if record is None else ("c" if before is None else "u")
        if not (op == "d" and before is None):
            seq = manifest["next_seq"]
            manifest["next_seq"] = seq + 1
            self._commit(entity_type, self._to_row(entity_type, key, record, op), seq)
            manifest["changes"].append(
                {
                    "seq": seq,
                    "entity_type": entity_type,
                    "key": key,
                    "op": op,
                    "ts_ms": now_ms(),
                    "before": _jsonable(before),
                    "after": _jsonable(record),
                }
            )
        self._save(manifest)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def _to_row(
        self, entity_type: str, key: str, record: dict[str, Any] | None, op: Op
    ) -> dict[str, Any]:
        pk = self.mapping.for_entity(entity_type).key
        cols = set(self.schemas[entity_type].names) - {OP, EXTRA}
        if record is None:
            return {pk: key, OP: op}
        row = {k: v for k, v in record.items() if k in cols}
        extra = {k: v for k, v in record.items() if k not in cols}
        row[OP] = op
        row[EXTRA] = json.dumps(extra, default=str) if extra else None
        return row

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        row = self._current(entity_type).get(key)
        return self._to_record(row) if row is not None else None

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        for row in self._current(entity_type).values():
            yield self._to_record(row)

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        key = str(record[self.mapping.for_entity(entity_type).key])
        return self._apply(entity_type, key, idempotency_key, record)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        return self._apply(entity_type, key, idempotency_key, None)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        for c in self._manifest()["changes"]:
            if c["seq"] > since:
                yield ChangeEvent(store=self.name, **c)
