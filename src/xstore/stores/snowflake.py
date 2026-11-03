"""Snowflake adapter stub. Real mode only: snowflake-connector-python (optional),
parameterized SQL, MERGE for upserts. No local backend and no outbox; changes
come from Snowflake streams in real mode."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent, WriteResult

APPLIED = "_XSTORE_APPLIED"


class SnowflakeStore:
    name = "snowflake"

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.mapping = load_mapping(self.name)

    def _cols(self, entity_type: str) -> list[str]:
        return list(self.mapping.for_entity(entity_type).fields.values())

    def _claim(self, cur: Any, idempotency_key: str) -> bool:
        cur.execute(
            f"MERGE INTO {APPLIED} t USING (SELECT %s AS K) s ON t.K = s.K "
            "WHEN NOT MATCHED THEN INSERT (K) VALUES (s.K)",
            (idempotency_key,),
        )
        return bool(cur.rowcount)

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        m = self.mapping.for_entity(entity_type)
        cols = self._cols(entity_type)
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM {m.container} WHERE {m.key} = %s", (key,))
            row = cur.fetchone()
        return {c: v for c, v in zip(cols, row, strict=True) if v is not None}

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        m = self.mapping.for_entity(entity_type)
        cols = self._cols(entity_type)
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM {m.container}")
            for row in cur:
                yield {c: v for c, v in zip(cols, row, strict=True) if v is not None}

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        m = self.mapping.for_entity(entity_type)
        cols = self._cols(entity_type)
        src = ", ".join(f"%s AS {c}" for c in cols)
        sets = ", ".join(f"t.{c} = s.{c}" for c in cols if c != m.key)
        sql = (
            f"MERGE INTO {m.container} t USING (SELECT {src}) s ON t.{m.key} = s.{m.key} "
            f"WHEN MATCHED THEN UPDATE SET {sets} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) "
            f"VALUES ({', '.join(f's.{c}' for c in cols)})"
        )
        with self.conn.cursor() as cur:
            if not self._claim(cur, idempotency_key):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            cur.execute(sql, tuple(record.get(c) for c in cols))
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        m = self.mapping.for_entity(entity_type)
        with self.conn.cursor() as cur:
            if not self._claim(cur, idempotency_key):
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            cur.execute(f"DELETE FROM {m.container} WHERE {m.key} = %s", (key,))
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        raise NotImplementedError("Snowflake changes come from Snowflake streams, not this stub")


def create_store(backend: str) -> SnowflakeStore:
    if backend == "local":
        raise NotImplementedError("Snowflake is real-mode only; set STORE_BACKEND=real")
    try:
        import snowflake.connector
    except ImportError as e:
        raise RuntimeError("snowflake real mode needs snowflake-connector-python") from e
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "XSTORE"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
        autocommit=True,
    )
    return SnowflakeStore(conn)
