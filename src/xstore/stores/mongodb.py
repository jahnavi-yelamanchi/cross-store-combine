"""MongoDB adapter. Local backend: mongomock. Outbox collection stands in for
change streams locally; Debezium's MongoDB connector covers real mode.

Database name comes from MONGO_DB (placeholder default until the real database exists).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database

from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent, Op, WriteResult, now_ms

DEFAULT_DB_NAME = "xstore"


def _encode(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    return {k: Decimal128(v) if isinstance(v, Decimal) else v for k, v in doc.items()}


def _decode_value(v: Any) -> Any:
    if isinstance(v, Decimal128):
        return v.to_decimal()
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=UTC)  # BSON dates are UTC
    return v


def _decode(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    return {k: _decode_value(v) for k, v in doc.items()}


class MongoStore:
    name = "mongodb"

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self.db = db
        self.mapping = load_mapping(self.name)
        self.applied = db["_xstore_applied"]
        self.outbox = db["_xstore_outbox"]
        self.counters = db["_xstore_counters"]
        self.outbox.create_index([("seq", ASCENDING)], unique=True)

    def _coll(self, entity_type: str) -> Any:
        return self.db[self.mapping.for_entity(entity_type).container]

    def _claim(self, idempotency_key: str) -> bool:
        res = self.applied.update_one(
            {"_id": idempotency_key}, {"$setOnInsert": {"_id": idempotency_key}}, upsert=True
        )
        return res.upserted_id is not None

    def _emit(
        self,
        entity_type: str,
        key: str,
        op: Op,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        counter = self.counters.find_one_and_update(
            {"_id": "outbox"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert counter is not None
        self.outbox.insert_one(
            {
                "seq": counter["seq"],
                "entity_type": entity_type,
                "key": key,
                "op": op,
                "ts_ms": now_ms(),
                "before": _encode(before),
                "after": _encode(after),
            }
        )

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        return _decode(self._coll(entity_type).find_one({"_id": key}))

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        for doc in self._coll(entity_type).find({}):
            yield _decode(doc) or {}

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        if not self._claim(idempotency_key):
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        key = str(record[self.mapping.for_entity(entity_type).key])
        before = self._coll(entity_type).find_one_and_replace(
            {"_id": key}, _encode({**record, "_id": key}), upsert=True
        )
        before = _decode(before)
        self._emit(entity_type, key, "c" if before is None else "u", before, record)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        before = _decode(self._coll(entity_type).find_one_and_delete({"_id": key}))
        if before is not None:
            self._emit(entity_type, key, "d", before, None)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        for doc in self.outbox.find({"seq": {"$gt": since}}).sort("seq", ASCENDING):
            doc.pop("_id", None)
            doc["before"], doc["after"] = _decode(doc["before"]), _decode(doc["after"])
            yield ChangeEvent(store=self.name, **doc)


def create_store(backend: str, client: Any = None, db_name: str | None = None) -> MongoStore:
    if client is None:
        if backend == "local":
            import mongomock

            client = mongomock.MongoClient()
        else:
            from pymongo import MongoClient

            client = MongoClient(os.environ["MONGO_URI"], tz_aware=True)
    return MongoStore(client[db_name or os.environ.get("MONGO_DB", DEFAULT_DB_NAME)])
