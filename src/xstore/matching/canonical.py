"""Canonical customer table across stores, input to the Splink matcher."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa

from xstore.entities import all_stores, load_mapping
from xstore.stores import Store

ENTITY = "customer"
CANON_FIELDS = ("name", "email", "phone", "country", "updated_at")
SCHEMA = pa.schema(
    [(c, pa.string()) for c in ("unique_id", "source_store", "key", *CANON_FIELDS)]
    + [(c, pa.string()) for c in ("name_norm", "email_norm", "phone_norm")]
)


def customer_stores() -> list[str]:
    """Stores whose mapping holds the customer entity."""
    return [s for s in all_stores() if ENTITY in load_mapping(s).entities]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm(value: str | None, *, digits: bool = False) -> str | None:
    if value is None:
        return None
    out = re.sub(r"\D", "", value) if digits else re.sub(r"\s+", " ", value.lower()).strip()
    return out or None


def to_row(store: str, record: Mapping[str, Any]) -> dict[str, str | None]:
    """Rename native fields to canonical ones; no validation, so drift survives."""
    m = load_mapping(store).for_entity(ENTITY)
    key = str(record.get(m.key, "")).removeprefix(m.key_prefix)
    row: dict[str, str | None] = {
        "unique_id": f"{store}:{key}",
        "source_store": store,
        "key": key,
    }
    for canon in CANON_FIELDS:
        native = m.fields.get(canon)
        row[canon] = _text(record.get(native)) if native else None
    row["name_norm"] = _norm(row["name"])
    row["email_norm"] = _norm(row["email"])
    row["phone_norm"] = _norm(row["phone"], digits=True)
    return row


def _table(rows: Iterable[dict[str, str | None]]) -> pa.Table:
    return pa.Table.from_pylist(list(rows), schema=SCHEMA)


def load_customers(data_dir: Path, limit_per_store: int | None = None) -> pa.Table:
    """Read <data_dir>/<store>/customer.jsonl for every customer-holding store."""
    rows: list[dict[str, str | None]] = []
    for store in customer_stores():
        path = Path(data_dir) / store / f"{ENTITY}.jsonl"
        if not path.exists():
            continue
        with path.open() as f:
            for i, line in enumerate(f):
                if limit_per_store is not None and i >= limit_per_store:
                    break
                if line.strip():
                    rows.append(to_row(store, json.loads(line)))
    return _table(rows)


def customers_from_stores(stores: Mapping[str, Store]) -> pa.Table:
    """Same table built from live stores via `scan("customer")`."""
    return _table(
        to_row(name, rec)
        for name, store in stores.items()
        if ENTITY in load_mapping(name).entities
        for rec in store.scan(ENTITY)
    )
