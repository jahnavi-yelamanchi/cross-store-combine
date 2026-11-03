"""FastAPI stub shaped like HubSpot CRM v3 objects (contacts, deals). In-memory.

Writes honor `Idempotency-Key`: a replayed key returns the original response with
`Idempotent-Replayed: true`. `GET /xstore/changes` exposes webhook-shaped events.
Run: `uvicorn xstore.crm.app:app`.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ObjectType = Literal["contacts", "deals"]
SINGULAR = {"contacts": "contact", "deals": "deal"}
Reply = tuple[int, Any]


class ObjectInput(BaseModel):
    properties: dict[str, str]
    id: str | None = None  # stub-only: real HubSpot assigns ids


class BatchUpsertInput(BaseModel):
    inputs: list[ObjectInput]


@dataclass
class CrmState:
    objects: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {"contacts": {}, "deals": {}}
    )
    replies: dict[str, Reply] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Any bearer token is accepted locally; the header must be present."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")


def create_app() -> FastAPI:
    app = FastAPI(title="xstore fake HubSpot CRM", dependencies=[Depends(require_auth)])
    state = CrmState()
    app.state.crm = state

    def emit(object_type: str, object_id: str, kind: str) -> None:
        seq = len(state.changes) + 1
        state.changes.append(
            {
                "eventId": seq,
                "subscriptionType": f"{SINGULAR[object_type]}.{kind}",
                "objectId": object_id,
                "occurredAt": _ms(),
            }
        )

    def idempotent(key: str | None, run: Callable[[], Reply]) -> Response:
        if key is not None and key in state.replies:
            status, body = state.replies[key]
            headers = {"Idempotent-Replayed": "true"}
            if body is None:
                return Response(status_code=status, headers=headers)
            return JSONResponse(body, status, headers=headers)
        status, body = run()
        if key is not None and status < 400:
            state.replies[key] = (status, body)
        if body is None:
            return Response(status_code=status)
        return JSONResponse(body, status)

    def upsert(object_type: str, body: ObjectInput) -> Reply:
        object_id = body.id or str(next(state.ids))
        existing = state.objects[object_type].get(object_id)
        ts = _now()
        if existing is not None and not existing["archived"]:
            existing["properties"].update(body.properties)
            existing["updatedAt"] = ts
            emit(object_type, object_id, "propertyChange")
            return 200, dict(existing)
        obj = {
            "id": object_id,
            "properties": dict(body.properties),
            "createdAt": ts,
            "updatedAt": ts,
            "archived": False,
        }
        state.objects[object_type][object_id] = obj
        emit(object_type, object_id, "creation")
        return 201, dict(obj)

    @app.post("/crm/v3/objects/{object_type}", status_code=201)
    def create(
        object_type: ObjectType,
        body: ObjectInput,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        def run() -> Reply:
            existing = state.objects[object_type].get(body.id or "")
            if existing is not None and not existing["archived"]:
                return 409, {"status": "error", "message": "object already exists"}
            return upsert(object_type, body)

        return idempotent(idempotency_key, run)

    @app.get("/crm/v3/objects/{object_type}/{object_id}")
    def read(object_type: ObjectType, object_id: str, archived: bool = False) -> dict[str, Any]:
        obj = state.objects[object_type].get(object_id)
        if obj is None or obj["archived"] != archived:
            raise HTTPException(404, "object not found")
        return obj

    @app.patch("/crm/v3/objects/{object_type}/{object_id}")
    def patch(
        object_type: ObjectType,
        object_id: str,
        body: ObjectInput,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        def run() -> Reply:
            obj = state.objects[object_type].get(object_id)
            if obj is None or obj["archived"]:
                return 404, {"status": "error", "message": "object not found"}
            obj["properties"].update(body.properties)
            obj["updatedAt"] = _now()
            emit(object_type, object_id, "propertyChange")
            return 200, dict(obj)

        return idempotent(idempotency_key, run)

    @app.delete("/crm/v3/objects/{object_type}/{object_id}", status_code=204)
    def archive(
        object_type: ObjectType,
        object_id: str,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        def run() -> Reply:
            obj = state.objects[object_type].get(object_id)
            if obj is not None and not obj["archived"]:
                obj["archived"] = True
                obj["archivedAt"] = obj["updatedAt"] = _now()
                emit(object_type, object_id, "deletion")
            return 204, None

        return idempotent(idempotency_key, run)

    @app.get("/crm/v3/objects/{object_type}")
    def list_objects(object_type: ObjectType, limit: int = 10, after: str | None = None) -> Any:
        live = [o for o in state.objects[object_type].values() if not o["archived"]]
        start = int(after) if after else 0
        out: dict[str, Any] = {"results": live[start : start + limit]}
        if start + limit < len(live):
            out["paging"] = {"next": {"after": str(start + limit)}}
        return out

    @app.post("/crm/v3/objects/{object_type}/batch/upsert")
    def batch_upsert(
        object_type: ObjectType,
        body: BatchUpsertInput,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        def run() -> Reply:
            results = [upsert(object_type, i)[1] for i in body.inputs]
            return 200, {"status": "COMPLETE", "results": results}

        return idempotent(idempotency_key, run)

    @app.get("/xstore/changes")
    def changes(since: int = 0) -> list[dict[str, Any]]:
        return [e for e in state.changes if e["eventId"] > since]

    return app


app = create_app()
