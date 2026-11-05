"""MCP server "xstore-evidence": exposes native store records to the tiebreak agent.

mcp 2.x renamed FastMCP to MCPServer; this module uses the 2.x name.
Run over stdio for external MCP clients: `python -m xstore.agent.evidence_mcp`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer

from xstore.entities import load_mapping
from xstore.stores import Store, get_store

SERVER_NAME = "xstore-evidence"


def _json_safe(record: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = json.loads(json.dumps(record, default=str, sort_keys=True))
    return safe


def evidence_for(
    stores: Mapping[str, Store], store: str, entity_type: str, key: str
) -> dict[str, Any]:
    """Native record plus native->canonical field names. No validation, so drift survives."""
    out: dict[str, Any] = {
        "store": store,
        "entity_type": entity_type,
        "key": key,
        "found": False,
        "record": None,
        "field_names": {},
        "canonical": {},
    }
    if store not in stores:
        out["error"] = f"unknown store {store!r}"
        return out
    try:
        fields = load_mapping(store).for_entity(entity_type).fields
    except (KeyError, FileNotFoundError) as exc:
        out["error"] = str(exc)
        return out
    record = stores[store].read(entity_type, key)
    if record is None:
        return out
    native = _json_safe(record)
    reverse = {n: c for c, n in fields.items()}
    out.update(
        found=True,
        record=native,
        field_names={n: reverse[n] for n in native if n in reverse},
        canonical={reverse[n]: v for n, v in native.items() if n in reverse},
    )
    return out


def build_server(stores: Mapping[str, Store]) -> MCPServer:
    server: MCPServer = MCPServer(
        SERVER_NAME, instructions="Read-only evidence for entity matching."
    )

    @server.tool()
    def get_evidence(store: str, entity_type: str, key: str) -> dict[str, Any]:
        """Return the native record for (store, entity_type, key) with canonical field names."""
        return evidence_for(stores, store, entity_type, key)

    return server


def main() -> None:
    names = os.environ.get("XSTORE_EVIDENCE_STORES", "postgres,mongodb").split(",")
    build_server({n: get_store(n) for n in names if n}).run("stdio")


if __name__ == "__main__":
    main()
