"""Postgres adapter via SQLAlchemy Core. Local backend: SQLite in memory.

Changes go to a transactional outbox table in the same transaction as the write;
in real mode Debezium tails the WAL instead (see cdc/debezium).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any, get_args

from sqlalchemy import (
    BigInteger,
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy import delete as sa_delete
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeEngine

from xstore.entities import ENTITY_TYPES, EntityMapping, load_mapping
from xstore.stores.base import ChangeEvent, Op, WriteResult, now_ms

EXTRA = "_extra"  # native fields not in the mapping (schema drift) land here as JSON


def _column_type(annotation: Any, unit: str | None) -> TypeEngine[Any]:
    if unit == "cents":
        return BigInteger()
    types = set(get_args(annotation)) or {annotation}
    if int in types:
        return BigInteger()
    if float in types:
        return Float()
    if Decimal in types:
        return Numeric(18, 2)
    return Text()


def _to_db(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime | date) else value


def _from_db(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


class PostgresStore:
    name = "postgres"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.mapping = load_mapping(self.name)
        self.meta = MetaData()
        self.tables: dict[str, Table] = {
            t: self._table(t, m) for t, m in self.mapping.entities.items()
        }
        self.applied = Table(
            "_xstore_applied", self.meta, Column("idempotency_key", Text, primary_key=True)
        )
        self.outbox = Table(
            "_xstore_outbox",
            self.meta,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("entity_type", Text, nullable=False),
            Column("key", Text, nullable=False),
            Column("op", Text, nullable=False),
            Column("ts_ms", BigInteger, nullable=False),
            Column("before", Text),
            Column("after", Text),
        )
        self.meta.create_all(engine)

    def _table(self, entity_type: str, m: EntityMapping) -> Table:
        model = ENTITY_TYPES[entity_type]
        cols = [
            Column(
                native,
                _column_type(model.model_fields[canon].annotation, m.units.get(canon)),
                primary_key=native == m.key,
            )
            for canon, native in m.fields.items()
        ]
        return Table(m.container, self.meta, *cols, Column(EXTRA, Text))

    def _row_to_record(self, row: Any) -> dict[str, Any]:
        data = dict(row._mapping)
        extra = data.pop(EXTRA, None)
        record = {k: _from_db(v) for k, v in data.items() if v is not None}
        if extra:
            record.update(json.loads(extra))
        return record

    def _record_to_row(self, entity_type: str, record: dict[str, Any]) -> dict[str, Any]:
        cols = set(self.tables[entity_type].c.keys())
        row = {k: _to_db(v) for k, v in record.items() if k in cols}
        extra = {k: v for k, v in record.items() if k not in cols}
        row[EXTRA] = json.dumps(extra, default=str) if extra else None
        return row

    def _claim(self, conn: Connection, idempotency_key: str) -> bool:
        seen = conn.execute(
            select(self.applied.c.idempotency_key).where(
                self.applied.c.idempotency_key == idempotency_key
            )
        ).first()
        if seen:
            return False
        conn.execute(insert(self.applied).values(idempotency_key=idempotency_key))
        return True

    def _emit(
        self,
        conn: Connection,
        entity_type: str,
        key: str,
        op: Op,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        conn.execute(
            insert(self.outbox).values(
                entity_type=entity_type,
                key=key,
                op=op,
                ts_ms=now_ms(),
                before=json.dumps(before, default=str) if before is not None else None,
                after=json.dumps(after, default=str) if after is not None else None,
            )
        )

    def _get(self, conn: Connection, entity_type: str, key: str) -> dict[str, Any] | None:
        table = self.tables[entity_type]
        pk = self.mapping.for_entity(entity_type).key
        row = conn.execute(select(table).where(table.c[pk] == key)).first()
        return self._row_to_record(row) if row else None

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return self._get(conn, entity_type, key)

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        with self.engine.connect() as conn:
            for row in conn.execute(select(self.tables[entity_type])):
                yield self._row_to_record(row)

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        table = self.tables[entity_type]
        pk = self.mapping.for_entity(entity_type).key
        key = str(record[pk])
        with self.engine.begin() as conn:
            if not self._claim(conn, idempotency_key):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            before = self._get(conn, entity_type, key)
            row = self._record_to_row(entity_type, record)
            if before is None:
                conn.execute(insert(table).values(**row))
            else:
                conn.execute(update(table).where(table.c[pk] == key).values(**row))
            self._emit(conn, entity_type, key, "c" if before is None else "u", before, record)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        table = self.tables[entity_type]
        pk = self.mapping.for_entity(entity_type).key
        with self.engine.begin() as conn:
            if not self._claim(conn, idempotency_key):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            before = self._get(conn, entity_type, key)
            if before is not None:
                conn.execute(sa_delete(table).where(table.c[pk] == key))
                self._emit(conn, entity_type, key, "d", before, None)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(self.outbox).where(self.outbox.c.seq > since).order_by(self.outbox.c.seq)
            )
            for r in rows:
                yield ChangeEvent(
                    store=self.name,
                    entity_type=r.entity_type,
                    key=r.key,
                    op=r.op,
                    seq=r.seq,
                    ts_ms=r.ts_ms,
                    before=json.loads(r.before) if r.before else None,
                    after=json.loads(r.after) if r.after else None,
                )


def create_store(backend: str, url: str | None = None) -> PostgresStore:
    if backend == "local":
        engine = create_engine(
            url or "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_engine(url or os.environ["DATABASE_URL"])
    return PostgresStore(engine)
