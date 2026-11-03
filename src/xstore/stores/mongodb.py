"""MongoDB adapter (mongomock locally). Writes claim an idempotency key; real mode
runs claim + write + outbox in one transaction (stores/setup/mongodb.yaml). DB: MONGO_DB."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
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

    def __init__(self, db: Database[dict[str, Any]], transactions: bool = False) -> None:
        self.db = db
        self.transactions = transactions
        self.mapping = load_mapping(self.name)
        self.applied = db["_xstore_applied"]
        self.outbox = db["_xstore_outbox"]
        self.counters = db["_xstore_counters"]
        self.outbox.create_index([("seq", ASCENDING)], unique=True)

    def _coll(self, entity_type: str) -> Any:
        return self.db[self.mapping.for_entity(entity_type).container]

    def _atomic(self, fn: Callable[[Any], WriteResult]) -> WriteResult:
        if not self.transactions:
            return fn(None)
        with self.db.client.start_session() as session:
            result: WriteResult = session.with_transaction(fn)
            return result

    def _claim(self, idempotency_key: str, session: Any) -> bool:
        res = self.applied.update_one(
            {"_id": idempotency_key},
            {"$setOnInsert": {"_id": idempotency_key, "claimed_at": datetime.now(UTC)}},
            upsert=True,
            session=session,
        )
        return res.upserted_id is not None

    def _emit(
        self,
        entity_type: str,
        key: str,
        op: Op,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        session: Any,
    ) -> None:
        counter = self.counters.find_one_and_update(
            {"_id": "outbox"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
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
            },
            session=session,
        )

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        return _decode(self._coll(entity_type).find_one({"_id": key}))

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        for doc in self._coll(entity_type).find({}):
            yield _decode(doc) or {}

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        key = str(record[self.mapping.for_entity(entity_type).key])

        def apply(session: Any) -> WriteResult:
            if not self._claim(idempotency_key, session):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            before = _decode(
                self._coll(entity_type).find_one_and_replace(
                    {"_id": key}, _encode({**record, "_id": key}), upsert=True, session=session
                )
            )
            self._emit(entity_type, key, "c" if before is None else "u", before, record, session)
            return WriteResult(applied=True, idempotency_key=idempotency_key)

        return self._atomic(apply)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        def apply(session: Any) -> WriteResult:
            if not self._claim(idempotency_key, session):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            before = _decode(
                self._coll(entity_type).find_one_and_delete({"_id": key}, session=session)
            )
            if before is not None:
                self._emit(entity_type, key, "d", before, None, session)
            return WriteResult(applied=True, idempotency_key=idempotency_key)

        return self._atomic(apply)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        for doc in self.outbox.find({"seq": {"$gt": since}}).sort("seq", ASCENDING):
            doc.pop("_id", None)
            doc["before"], doc["after"] = _decode(doc["before"]), _decode(doc["after"])
            yield ChangeEvent(store=self.name, **doc)


def create_store(backend: str, client: Any = None, db_name: str | None = None) -> MongoStore:
    transactions = backend != "local"
    if client is None:
        if backend == "local":
            import mongomock

            client = mongomock.MongoClient()
        else:
            from pymongo import MongoClient

            client = MongoClient(os.environ["MONGO_URI"], tz_aware=True)
    db = client[db_name or os.environ.get("MONGO_DB", DEFAULT_DB_NAME)]
    return MongoStore(db, transactions=transactions)
