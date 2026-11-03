"""Store protocol shared by every adapter. Records are store-native dicts."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

Op = Literal["c", "u", "d"]  # Debezium op codes: create, update, delete


class ChangeEvent(BaseModel):
    """One row-level change; same shape for every store and for the CDC bus."""

    model_config = ConfigDict(frozen=True)

    store: str
    entity_type: str
    key: str
    op: Op
    seq: int
    ts_ms: int
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class WriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    applied: bool
    idempotency_key: str


@runtime_checkable
class Store(Protocol):
    name: str

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None: ...

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]: ...

    def write(
        self, entity_type: str, record: dict[str, Any], idempotency_key: str
    ) -> WriteResult: ...

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult: ...

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]: ...


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def backend() -> str:
    return os.environ.get("STORE_BACKEND", "local")


def get_store(name: str, **kwargs: Any) -> Store:
    """Load `xstore.stores.<name>` and call its `create_store(backend, **kwargs)`."""
    module = importlib.import_module(f"xstore.stores.{name}")
    store: Store = module.create_store(backend(), **kwargs)
    return store
