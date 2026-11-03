"""Cosmos DB change feed -> bus. `handle_documents` matches the Azure Functions
cosmosDBTrigger input (a list of changed documents, latest-version mode)."""

from __future__ import annotations

from typing import Any

from xstore.cdc.bus import InProcessBus
from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent

SYSTEM_FIELDS = ("_rid", "_self", "_etag", "_attachments", "_lsn")


def document_to_change_event(container: str, doc: dict[str, Any]) -> ChangeEvent:
    mapping = load_mapping("cosmosdb")
    entity_type = next(t for t, m in mapping.entities.items() if m.container == container)
    key = str(doc[mapping.for_entity(entity_type).key])
    return ChangeEvent(
        store="cosmosdb",
        entity_type=entity_type,
        key=key,
        op="u",  # latest-version feed does not distinguish create from update
        seq=int(doc["_lsn"]),
        ts_ms=int(doc["_ts"]) * 1000,
        after={k: v for k, v in doc.items() if k not in SYSTEM_FIELDS},
    )


def handle_documents(container: str, docs: list[dict[str, Any]], bus: InProcessBus) -> int:
    for doc in docs:
        bus.publish(document_to_change_event(container, doc))
    return len(docs)
