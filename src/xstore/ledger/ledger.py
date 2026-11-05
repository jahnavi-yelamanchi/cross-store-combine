"""Append-only, hash-chained event ledger on SQLAlchemy Core (SQLite locally, Postgres real).

Immutability is enforced by the database: triggers abort UPDATE/DELETE on `ledger_events`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from xstore.ledger.models import (
    GENESIS_HASH,
    EventType,
    LedgerEvent,
    canonical_json,
    chain_hash,
)
from xstore.stores.base import backend

TABLE = "ledger_events"
PG_APPEND_ONLY_SQL = Path(__file__).parent / "sql" / "postgres_append_only.sql"
SQLITE_APPEND_ONLY = [
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_{op.lower()} BEFORE {op} ON {TABLE} "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
    for op in ("UPDATE", "DELETE")
]


class Ledger:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.meta = MetaData()
        self.table = Table(
            TABLE,
            self.meta,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("event_id", Text, nullable=False, unique=True),
            Column("idempotency_key", Text, nullable=False, unique=True),
            Column("type", Text, nullable=False),
            Column("payload", Text, nullable=False),
            Column("ts", Text, nullable=False),
            Column("prev_hash", Text, nullable=False),
            Column("hash", Text, nullable=False),
        )
        self.meta.create_all(engine)
        self._install_append_only()

    def _install_append_only(self) -> None:
        dialect = self.engine.dialect.name
        with self.engine.begin() as conn:
            if dialect == "sqlite":
                for ddl in SQLITE_APPEND_ONLY:
                    conn.exec_driver_sql(ddl)
            elif dialect == "postgresql":
                conn.exec_driver_sql(PG_APPEND_ONLY_SQL.read_text())

    def _to_event(self, row: Any) -> LedgerEvent:
        return LedgerEvent(
            seq=row.seq,
            event_id=row.event_id,
            type=row.type,
            idempotency_key=row.idempotency_key,
            payload=json.loads(row.payload),
            ts=datetime.fromisoformat(row.ts),
            prev_hash=row.prev_hash,
            hash=row.hash,
        )

    def _by_key(self, conn: Connection, idempotency_key: str) -> LedgerEvent | None:
        t = self.table
        row = conn.execute(select(t).where(t.c.idempotency_key == idempotency_key)).first()
        return self._to_event(row) if row else None

    def append(self, type: EventType, payload: dict[str, Any], idempotency_key: str) -> LedgerEvent:
        """Append one event; a repeated idempotency key returns the existing event."""
        body = canonical_json(payload)
        normalized = json.loads(body)
        t = self.table
        try:
            with self.engine.begin() as conn:
                if self.engine.dialect.name == "postgresql":
                    # Serialize appenders so the hash chain cannot fork.
                    conn.exec_driver_sql(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE")
                existing = self._by_key(conn, idempotency_key)
                if existing is not None:
                    return existing
                last = conn.execute(select(t.c.hash).order_by(t.c.seq.desc()).limit(1)).first()
                prev = last.hash if last else GENESIS_HASH
                values = {
                    "event_id": str(uuid.uuid4()),
                    "idempotency_key": idempotency_key,
                    "type": type,
                    "payload": body,
                    "ts": datetime.now(UTC).isoformat(),
                    "prev_hash": prev,
                    "hash": chain_hash(prev, type, idempotency_key, normalized),
                }
                pk = conn.execute(insert(t).values(**values)).inserted_primary_key
                if pk is None:
                    raise RuntimeError("ledger insert returned no seq")
                return LedgerEvent(seq=pk[0], **{**values, "payload": normalized})
        except IntegrityError:
            with self.engine.connect() as conn:  # lost a race on the same key
                existing = self._by_key(conn, idempotency_key)
            if existing is None:
                raise
            return existing

    def events(
        self, since_seq: int = 0, types: Iterable[EventType] | None = None
    ) -> Iterator[LedgerEvent]:
        t = self.table
        q = select(t).where(t.c.seq > since_seq).order_by(t.c.seq)
        if types is not None:
            q = q.where(t.c.type.in_(list(types)))
        with self.engine.connect() as conn:
            rows = conn.execute(q).fetchall()
        for row in rows:
            yield self._to_event(row)

    def count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(self.table)).scalar_one())

    def verify_chain(self) -> bool:
        """Recompute every hash from its predecessor; False on any break or edit."""
        prev = GENESIS_HASH
        for e in self.events():
            if e.prev_hash != prev:
                return False
            if chain_hash(prev, e.type, e.idempotency_key, e.payload) != e.hash:
                return False
            prev = e.hash
        return True


def create_ledger(url: str | None = None) -> Ledger:
    """In-memory SQLite locally; `sqlite:///runs/ledger.db` for a file; DATABASE_URL real."""
    if url is None:
        url = "sqlite://" if backend() == "local" else os.environ["DATABASE_URL"]
    if url.startswith("sqlite"):
        path = url.removeprefix("sqlite:///")
        if path != url and path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url)
    return Ledger(engine)
