"""Load a frozen snapshot (`<store>/<entity_type>.jsonl`) and seed stores from it."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from xstore.entities import load_mapping
from xstore.stores.base import Store, backend, get_store

Snapshot = dict[str, dict[str, list[dict[str, Any]]]]
NO_LOCAL_BACKEND = frozenset({"snowflake"})


def load_snapshot(snapshot_dir: Path, store_names: Iterable[str] | None = None) -> Snapshot:
    """Read native records per store and entity type; snowflake is skipped."""
    wanted = set(store_names) if store_names is not None else None
    snapshot: Snapshot = {}
    for store_dir in sorted(p for p in Path(snapshot_dir).iterdir() if p.is_dir()):
        name = store_dir.name
        if name in NO_LOCAL_BACKEND or (wanted is not None and name not in wanted):
            continue
        snapshot[name] = {
            f.stem: [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
            for f in sorted(store_dir.glob("*.jsonl"))
        }
    return snapshot


def record_key(store: str, entity_type: str, record: dict[str, Any]) -> str:
    """Key as the store's read/delete take it (native key field, prefix removed)."""
    m = load_mapping(store).for_entity(entity_type)
    return str(record[m.key]).removeprefix(m.key_prefix)


def _decode(value: Any, arrow_type: Any) -> Any:
    """JSON value -> Python value for a typed (Parquet) column; unparseable values pass through."""
    import pyarrow as pa

    if value is None:
        return None
    try:
        if pa.types.is_timestamp(arrow_type) and isinstance(value, str):
            return datetime.fromisoformat(value)
        if pa.types.is_decimal(arrow_type):
            return Decimal(str(value))
        if pa.types.is_integer(arrow_type) and isinstance(value, str | float):
            d = Decimal(str(value))
            return int(d) if d == d.to_integral_value() else value
        if pa.types.is_floating(arrow_type) and isinstance(value, str):
            return float(value)
    except (ValueError, ArithmeticError):
        return value
    return value


def to_native(store: Store, entity_type: str, record: dict[str, Any]) -> dict[str, Any]:
    """Decode JSON-serialized values for stores with a typed schema (Parquet lakes)."""
    schemas = getattr(store, "schemas", None)
    if not schemas or entity_type not in schemas:
        return record
    schema = schemas[entity_type]
    return {
        k: _decode(v, schema.field(k).type) if k in schema.names else v for k, v in record.items()
    }


def seed_stores(
    snapshot: Snapshot, factory: Callable[[str], Store] = get_store
) -> dict[str, Store]:
    """Write every snapshot record with key `seed:<store>:<entity_type>:<key>`.

    Records are written as-is; only typed Parquet columns get JSON strings decoded.
    """
    stores: dict[str, Store] = {}
    for name, entities in snapshot.items():
        if name in NO_LOCAL_BACKEND:
            continue
        store = stores[name] = factory(name)
        for entity_type, records in entities.items():
            for record in records:
                key = record_key(name, entity_type, record)
                native = to_native(store, entity_type, record)
                store.write(entity_type, native, f"seed:{name}:{entity_type}:{key}")
    return stores


def isolated_factory(region: str) -> Callable[[str], Store]:
    """Factory of fresh local stores; moto-backed ones live in `region` (own backend)."""
    if backend() != "local":
        raise RuntimeError("isolated_factory needs STORE_BACKEND=local")

    def factory(name: str) -> Store:
        if name == "dynamodb":
            return _dynamodb(region)
        if name == "s3_parquet":
            return _s3_parquet(region)
        return get_store(name)

    return factory


def _start_moto() -> Any:
    from moto import mock_aws

    mock = mock_aws()
    mock.start(reset=False)  # keep other regions' state intact
    return mock


def _dynamodb(region: str) -> Store:
    import boto3
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.dynamodb.models import dynamodb_backends

    from xstore.stores.dynamodb import DynamoDBStore

    mock = _start_moto()
    dynamodb_backends[DEFAULT_ACCOUNT_ID][region].reset()  # fresh table per replay
    client = boto3.client(
        "dynamodb",
        region_name=region,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    return DynamoDBStore(client, mock=mock)


def _s3_parquet(region: str) -> Store:
    import boto3

    from xstore.stores.s3_parquet import S3ByteStore, S3ParquetStore

    mock = _start_moto()
    client = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    # S3 buckets are global in moto, so a unique bucket gives a fresh lake.
    bucket = f"xstore-replay-{region}-{uuid.uuid4().hex[:12]}"
    if region == "us-east-1":
        client.create_bucket(Bucket=bucket)
    else:
        client.create_bucket(
            Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region}
        )
    return S3ParquetStore(S3ByteStore(client, bucket), mock)
