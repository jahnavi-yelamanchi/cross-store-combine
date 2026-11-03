"""Avro codec with Confluent wire format and an in-process schema registry."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from typing import Any

import fastavro

from xstore.stores.base import ChangeEvent

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "cdc" / "schema-registry" / "change_event.avsc"
MAGIC = 0


class LocalSchemaRegistry:
    """Subject -> versions; ids are global. Mirrors the Schema Registry REST model."""

    def __init__(self) -> None:
        self._by_id: dict[int, Any] = {}
        self._subjects: dict[str, list[int]] = {}

    def register(self, subject: str, schema: dict[str, Any]) -> int:
        parsed = fastavro.parse_schema(schema)
        for sid in self._subjects.get(subject, []):
            if self._by_id[sid] == parsed:
                return sid
        sid = len(self._by_id) + 1
        self._by_id[sid] = parsed
        self._subjects.setdefault(subject, []).append(sid)
        return sid

    def get(self, schema_id: int) -> Any:
        return self._by_id[schema_id]


class ChangeEventCodec:
    def __init__(self, registry: LocalSchemaRegistry, subject: str = "xstore.changes-value"):
        self.registry = registry
        self.schema_id = registry.register(subject, json.loads(SCHEMA_PATH.read_text()))

    def encode(self, event: ChangeEvent) -> bytes:
        record = event.model_dump()
        for side in ("before", "after"):
            value = record[side]
            record[side] = None if value is None else json.dumps(value, default=str)
        buf = io.BytesIO()
        buf.write(struct.pack(">bI", MAGIC, self.schema_id))
        fastavro.schemaless_writer(buf, self.registry.get(self.schema_id), record)
        return buf.getvalue()

    def decode(self, payload: bytes) -> ChangeEvent:
        magic, schema_id = struct.unpack(">bI", payload[:5])
        if magic != MAGIC:
            raise ValueError("not Confluent wire format")
        schema = self.registry.get(schema_id)
        record: Any = fastavro.schemaless_reader(io.BytesIO(payload[5:]), schema, schema)
        for side in ("before", "after"):
            if record[side] is not None:
                record[side] = json.loads(record[side])
        if hasattr(record["ts_ms"], "timestamp"):
            record["ts_ms"] = int(record["ts_ms"].timestamp() * 1000)
        return ChangeEvent.model_validate(record)
