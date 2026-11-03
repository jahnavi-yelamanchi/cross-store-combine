"""Cosmos DB adapter. Local backend: in-memory containers with `_etag`/`_ts`.

Real backend needs `azure-cosmos` and `azure-identity` (not project dependencies;
install them in the deployment image). It connects to COSMOS_ENDPOINT with
DefaultAzureCredential, i.e. Entra workload identity on AKS; no keys.
Idempotency keys go to the `_xstore_applied` container. Locally the change feed is
an ordered log with a monotonically increasing `_lsn`, exposed as ChangeEvent.seq.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent, Op, WriteResult, now_ms

DB_NAME = "xstore"
APPLIED = "_xstore_applied"


class CosmosHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


def _status(e: Exception) -> int | None:
    code = getattr(e, "status_code", None)
    return code if isinstance(code, int) else None


def _to_json(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _to_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_json(x) for x in v]
    return v


class MemoryContainer:
    """Subset of azure.cosmos ContainerProxy used by the store; partition key is /id."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        item = {**_to_json(body), "_etag": f'"{uuid.uuid4()}"', "_ts": int(time.time())}
        self.items[item["id"]] = item
        return dict(item)

    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        if item not in self.items:
            raise CosmosHttpError(404)
        return dict(self.items[item])

    def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        if body["id"] in self.items:
            raise CosmosHttpError(409)
        return self._stamp(body)

    def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._stamp(body)

    def delete_item(self, item: str, partition_key: str) -> None:
        if self.items.pop(item, None) is None:
            raise CosmosHttpError(404)

    def read_all_items(self) -> Iterator[dict[str, Any]]:
        return iter([dict(i) for i in self.items.values()])


class CosmosStore:
    name = "cosmosdb"

    def __init__(self, containers: dict[str, Any], local: bool = True) -> None:
        self.containers = containers
        self.local = local
        self.mapping = load_mapping(self.name)
        self.feed: list[ChangeEvent] = []
        self.lsn = 0

    def _container(self, entity_type: str) -> Any:
        return self.containers[self.mapping.for_entity(entity_type).container]

    def _get(self, entity_type: str, key: str) -> dict[str, Any] | None:
        try:
            item: dict[str, Any] = self._container(entity_type).read_item(key, partition_key=key)
            return item
        except Exception as e:
            if _status(e) == 404:
                return None
            raise

    def _claim(self, idempotency_key: str) -> bool:
        try:
            self.containers[APPLIED].create_item({"id": idempotency_key})
            return True
        except Exception as e:
            if _status(e) == 409:
                return False
            raise

    def _emit(
        self,
        entity_type: str,
        key: str,
        op: Op,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.lsn += 1
        self.feed.append(
            ChangeEvent(
                store=self.name,
                entity_type=entity_type,
                key=key,
                op=op,
                seq=self.lsn,
                ts_ms=now_ms(),
                before=before,
                after=after,
            )
        )

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        return self._get(entity_type, key)

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        yield from self._container(entity_type).read_all_items()

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        if not self._claim(idempotency_key):
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        key = str(record[self.mapping.for_entity(entity_type).key])
        before = self._get(entity_type, key)
        after = self._container(entity_type).upsert_item({**record, "id": key})
        self._emit(entity_type, key, "c" if before is None else "u", before, after)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        if not self._claim(idempotency_key):
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        before = self._get(entity_type, key)
        if before is not None:
            self._container(entity_type).delete_item(key, partition_key=key)
            # Real latest-version change feed omits deletes; the local feed emits them.
            self._emit(entity_type, key, "d", before, None)
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        if self.local:
            yield from (e for e in self.feed if e.seq > since)
            return
        for entity_type, m in self.mapping.entities.items():
            for item in self.containers[m.container].query_items_change_feed(
                start_time="Beginning"
            ):
                if item["_lsn"] > since:
                    yield ChangeEvent(
                        store=self.name,
                        entity_type=entity_type,
                        key=item["id"],
                        op="u",  # latest-version mode does not distinguish create
                        seq=item["_lsn"],
                        ts_ms=item["_ts"] * 1000,
                        after=item,
                    )


def _real_containers(names: set[str]) -> dict[str, Any]:
    try:
        from azure.cosmos import CosmosClient, PartitionKey
        from azure.identity import DefaultAzureCredential
    except ImportError as e:
        raise RuntimeError(
            "cosmosdb real backend needs `azure-cosmos` and `azure-identity` installed"
        ) from e
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=DefaultAzureCredential())
    db = client.create_database_if_not_exists(DB_NAME)
    return {
        n: db.create_container_if_not_exists(id=n, partition_key=PartitionKey(path="/id"))
        for n in names
    }


def create_store(backend: str, containers: dict[str, Any] | None = None) -> CosmosStore:
    names = {m.container for m in load_mapping("cosmosdb").entities.values()} | {APPLIED}
    if containers is not None:
        return CosmosStore(containers, local=backend == "local")
    if backend == "local":
        return CosmosStore({n: MemoryContainer() for n in names})
    return CosmosStore(_real_containers(names), local=False)
