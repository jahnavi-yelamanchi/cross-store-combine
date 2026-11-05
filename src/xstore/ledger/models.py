"""Ledger event and payload models; canonical JSON and hashing helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EventType = Literal["FixApproved", "FixApplied", "FixRejected", "PolicyChanged"]
Action = Literal["upsert", "delete"]
GENESIS_HASH = "0" * 64
DELETED = "deleted"


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace; Decimal/datetime as str. Stable across a JSON round trip."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def record_hash(record: dict[str, Any] | None) -> str:
    return DELETED if record is None else sha256(canonical_json(record))


def chain_hash(prev_hash: str, type: str, idempotency_key: str, payload: dict[str, Any]) -> str:
    return sha256(prev_hash + type + idempotency_key + canonical_json(payload))


class LedgerEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    event_id: str
    type: EventType
    idempotency_key: str
    payload: dict[str, Any]
    ts: datetime
    prev_hash: str
    hash: str


class FixApproved(BaseModel):
    """Payload of a FixApproved event; `record` is store-native, None for deletes."""

    proposal_id: str
    target_store: str
    entity_type: str
    key: str
    action: Action
    record: dict[str, Any] | None = None
    approved_by: str


class FixApplied(BaseModel):
    proposal_id: str
    target_store: str
    entity_type: str
    key: str
    action: Action
    record_hash: str
