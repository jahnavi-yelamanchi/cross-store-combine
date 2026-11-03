"""Iceberg table spec (format v2) for the S3 lake, generated from the canonical
model + s3_parquet mapping. Types match `parquet_base.arrow_schema`.

Regenerate: `uv run python -m xstore.stores.iceberg_spec`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pyarrow as pa

from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.stores.parquet_base import arrow_type

ICEBERG_DIR = Path(__file__).resolve().parents[3] / "stores" / "iceberg"
STORE = "s3_parquet"
PARTITION_FIELD_ID = 1000


def iceberg_type(t: pa.DataType) -> str:
    if pa.types.is_decimal(t):
        return f"decimal({t.precision}, {t.scale})"
    if pa.types.is_timestamp(t):
        return "timestamptz"
    if pa.types.is_int64(t):
        return "long"
    if pa.types.is_float64(t):
        return "double"
    return "string"


def iceberg_spec(entity_type: str) -> dict[str, Any]:
    m = load_mapping(STORE).for_entity(entity_type)
    model = ENTITY_TYPES[entity_type]
    fields: list[dict[str, Any]] = []
    ids: dict[str, int] = {}
    for i, (canon, native) in enumerate(m.fields.items(), start=1):
        annotation = model.model_fields[canon].annotation
        ids[native] = i
        fields.append(
            {
                "id": i,
                "name": native,
                "required": type(None) not in get_args(annotation),
                "type": iceberg_type(arrow_type(annotation, m.units.get(canon))),
            }
        )
    return {
        "format-version": 2,
        "table-name": f"xstore.{entity_type}",
        "current-schema-id": 0,
        "schemas": [
            {
                "type": "struct",
                "schema-id": 0,
                "identifier-field-ids": [ids[m.key]],
                "fields": fields,
            }
        ],
        "default-spec-id": 0,
        "partition-specs": [
            {
                "spec-id": 0,
                "fields": [
                    {
                        "name": "updated_at_day",
                        "transform": "day",
                        "source-id": ids[m.fields["updated_at"]],
                        "field-id": PARTITION_FIELD_ID,
                    }
                ],
            }
        ],
        "default-sort-order-id": 1,
        "sort-orders": [
            {
                "order-id": 1,
                "fields": [
                    {
                        "transform": "identity",
                        "source-id": ids[m.key],
                        "direction": "asc",
                        "null-order": "nulls-first",
                    }
                ],
            }
        ],
        "properties": {
            "write.format.default": "parquet",
            "write.parquet.compression-codec": "zstd",
        },
    }


def write_specs() -> None:
    ICEBERG_DIR.mkdir(parents=True, exist_ok=True)
    for entity_type in load_mapping(STORE).entities:
        path = ICEBERG_DIR / f"{entity_type}.json"
        path.write_text(json.dumps(iceberg_spec(entity_type), indent=2) + "\n")


if __name__ == "__main__":
    write_specs()
