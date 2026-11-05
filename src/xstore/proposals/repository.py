"""Proposal storage: in-memory, or SQLAlchemy Core (SQLite locally, Postgres via DATABASE_URL).

`transition` is a compare-and-set on status so concurrent approvals apply a fix once.
Idempotency-Key replies live next to proposals so replay survives restarts.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from xstore.proposals.models import Proposal, Status

Reply = tuple[int, Any]


@runtime_checkable
class ProposalRepository(Protocol):
    def add(self, proposal: Proposal) -> tuple[Proposal, bool]:
        """Insert unless the id exists; returns (stored proposal, created)."""
        ...

    def get(self, proposal_id: str) -> Proposal | None: ...

    def find(
        self,
        status: Status | None = None,
        safe: bool | None = None,
        detection_type: str | None = None,
    ) -> list[Proposal]: ...

    def transition(self, expected: Status, proposal: Proposal) -> bool:
        """Replace the stored proposal only if its status is still `expected`."""
        ...

    def get_reply(self, key: str) -> Reply | None: ...

    def put_reply(self, key: str, reply: Reply) -> None: ...


def _matches(p: Proposal, status: Status | None, safe: bool | None, dt: str | None) -> bool:
    return (
        (status is None or p.status == status)
        and (safe is None or p.safe == safe)
        and (dt is None or p.lineage.detection_type == dt)
    )


class InMemoryRepository:
    def __init__(self) -> None:
        self._rows: dict[str, Proposal] = {}
        self._replies: dict[str, Reply] = {}
        self._lock = threading.Lock()

    def add(self, proposal: Proposal) -> tuple[Proposal, bool]:
        with self._lock:
            existing = self._rows.get(proposal.id)
            if existing is not None:
                return existing, False
            self._rows[proposal.id] = proposal
            return proposal, True

    def get(self, proposal_id: str) -> Proposal | None:
        return self._rows.get(proposal_id)

    def find(
        self,
        status: Status | None = None,
        safe: bool | None = None,
        detection_type: str | None = None,
    ) -> list[Proposal]:
        rows = sorted(self._rows.values(), key=lambda p: (p.created_at, p.id))
        return [p for p in rows if _matches(p, status, safe, detection_type)]

    def transition(self, expected: Status, proposal: Proposal) -> bool:
        with self._lock:
            current = self._rows.get(proposal.id)
            if current is None or current.status != expected:
                return False
            self._rows[proposal.id] = proposal
            return True

    def get_reply(self, key: str) -> Reply | None:
        return self._replies.get(key)

    def put_reply(self, key: str, reply: Reply) -> None:
        with self._lock:
            self._replies.setdefault(key, reply)


metadata = MetaData()
proposals_table = Table(
    "proposals",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, index=True),
    Column("safe", Boolean, nullable=False),
    Column("detection_type", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
)
replies_table = Table(
    "proposal_idempotency",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("status_code", Integer, nullable=False),
    Column("body", JSON),
)


class SqlRepository:
    """Proposal rows with filter columns plus the full model as a JSON payload."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        metadata.create_all(engine)

    @classmethod
    def from_env(cls, url: str | None = None) -> SqlRepository:
        url = url or os.environ.get("DATABASE_URL") or "sqlite://"
        if url in ("sqlite://", "sqlite:///:memory:"):
            engine = create_engine(
                url, poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
        else:
            engine = create_engine(url)
        return cls(engine)

    @staticmethod
    def _values(p: Proposal) -> dict[str, Any]:
        return {
            "status": p.status,
            "safe": p.safe,
            "detection_type": p.lineage.detection_type,
            "payload": p.model_dump(mode="json"),
        }

    def add(self, proposal: Proposal) -> tuple[Proposal, bool]:
        row = {"id": proposal.id, "created_at": proposal.created_at, **self._values(proposal)}
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(proposals_table).values(**row))
        except IntegrityError:
            existing = self.get(proposal.id)
            if existing is None:
                raise
            return existing, False
        return proposal, True

    def get(self, proposal_id: str) -> Proposal | None:
        with self.engine.connect() as conn:
            payload = conn.execute(
                select(proposals_table.c.payload).where(proposals_table.c.id == proposal_id)
            ).scalar_one_or_none()
        return None if payload is None else Proposal.model_validate(payload)

    def find(
        self,
        status: Status | None = None,
        safe: bool | None = None,
        detection_type: str | None = None,
    ) -> list[Proposal]:
        t = proposals_table
        q = select(t.c.payload).order_by(t.c.created_at, t.c.id)
        if status is not None:
            q = q.where(t.c.status == status)
        if safe is not None:
            q = q.where(t.c.safe == safe)
        if detection_type is not None:
            q = q.where(t.c.detection_type == detection_type)
        with self.engine.connect() as conn:
            return [Proposal.model_validate(p) for p in conn.execute(q).scalars()]

    def transition(self, expected: Status, proposal: Proposal) -> bool:
        t = proposals_table
        with self.engine.begin() as conn:
            result = conn.execute(
                update(t)
                .where(t.c.id == proposal.id, t.c.status == expected)
                .values(**self._values(proposal))
            )
        return result.rowcount == 1

    def get_reply(self, key: str) -> Reply | None:
        t = replies_table
        with self.engine.connect() as conn:
            row = conn.execute(select(t.c.status_code, t.c.body).where(t.c.key == key)).first()
        return None if row is None else (row.status_code, row.body)

    def put_reply(self, key: str, reply: Reply) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(replies_table).values(key=key, status_code=reply[0], body=reply[1])
                )
        except IntegrityError:
            pass  # first reply for a key wins
