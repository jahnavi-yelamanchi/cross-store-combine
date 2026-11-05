"""HubSpot CRM adapter over httpx. Local backend: the in-process FastAPI stub
(`xstore.crm.app`) via TestClient, so no port is opened. Real backend:
api.hubapi.com with HUBSPOT_TOKEN.

Native record = object properties (all strings) plus `id`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import httpx

from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent, Op, WriteResult

# httpx.Client in real mode; Starlette TestClient (httpx2) locally. No common base class.
HttpClient = Any


REAL_BASE_URL = "https://api.hubapi.com"
OBJECTS = "/crm/v3/objects"
OPS: dict[str, Op] = {"creation": "c", "propertyChange": "u", "deletion": "d"}


def _to_str(value: Any) -> str:
    if value is None:
        return ""  # HubSpot clears a property with an empty string
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


class HubSpotStore:
    name = "hubspot"

    def __init__(self, client: HttpClient, has_change_log: bool = True) -> None:
        self.client = client
        self.has_change_log = has_change_log
        self.mapping = load_mapping(self.name)
        self.entity_by_singular = {
            m.container.removesuffix("s"): t for t, m in self.mapping.entities.items()
        }

    def _path(self, entity_type: str, key: str | None = None) -> str:
        base = f"{OBJECTS}/{self.mapping.for_entity(entity_type).container}"
        return base if key is None else f"{base}/{key}"

    @staticmethod
    def _native(obj: dict[str, Any]) -> dict[str, Any]:
        props = {k: v for k, v in obj["properties"].items() if v not in (None, "")}
        return {**props, "id": obj["id"]}

    def _get(self, entity_type: str, key: str, archived: bool = False) -> dict[str, Any] | None:
        resp = self.client.get(
            self._path(entity_type, key), params={"archived": str(archived).lower()}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        obj: dict[str, Any] = resp.json()
        return obj

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        obj = self._get(entity_type, key)
        return None if obj is None else self._native(obj)

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 100}
        while True:
            resp = self.client.get(self._path(entity_type), params=params)
            resp.raise_for_status()
            page = resp.json()
            for obj in page["results"]:
                yield self._native(obj)
            after = page.get("paging", {}).get("next", {}).get("after")
            if after is None:
                return
            params["after"] = after

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        key_field = self.mapping.for_entity(entity_type).key
        key = str(record[key_field])
        props = {k: _to_str(v) for k, v in record.items() if k != key_field}
        headers = {"Idempotency-Key": idempotency_key}
        if self._get(entity_type, key) is None:
            # The stub accepts a client-supplied id for determinism; real HubSpot assigns ids.
            body = {"id": key, "properties": props}
            resp = self.client.post(self._path(entity_type), json=body, headers=headers)
        else:
            body = {"properties": props}
            resp = self.client.patch(self._path(entity_type, key), json=body, headers=headers)
        resp.raise_for_status()
        return self._result(resp, idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        resp = self.client.delete(
            self._path(entity_type, key), headers={"Idempotency-Key": idempotency_key}
        )
        resp.raise_for_status()
        return self._result(resp, idempotency_key)

    @staticmethod
    def _result(resp: httpx.Response, idempotency_key: str) -> WriteResult:
        replayed = resp.headers.get("Idempotent-Replayed") == "true"
        return WriteResult(applied=not replayed, idempotency_key=idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        if not self.has_change_log:
            raise NotImplementedError("real HubSpot pushes webhooks; no pull change log")
        resp = self.client.get("/xstore/changes", params={"since": since})
        resp.raise_for_status()
        for event in resp.json():
            singular, kind = event["subscriptionType"].split(".", 1)
            entity_type = self.entity_by_singular[singular]
            key = str(event["objectId"])
            op = OPS[kind]
            after = None
            if op != "d":
                obj = self._get(entity_type, key) or self._get(entity_type, key, archived=True)
                after = None if obj is None else self._native(obj)
            yield ChangeEvent(
                store=self.name,
                entity_type=entity_type,
                key=key,
                op=op,
                seq=event["eventId"],
                ts_ms=event["occurredAt"],
                after=after,
            )


def create_store(backend: str, client: HttpClient | None = None) -> HubSpotStore:
    if client is not None:
        return HubSpotStore(client)
    if backend == "local":
        from fastapi.testclient import TestClient

        from xstore.crm.app import create_app

        local = TestClient(create_app(), headers={"Authorization": "Bearer local"})
        return HubSpotStore(local)
    token = os.environ["HUBSPOT_TOKEN"]
    real = httpx.Client(base_url=REAL_BASE_URL, headers={"Authorization": f"Bearer {token}"})
    return HubSpotStore(real, has_change_log=False)
