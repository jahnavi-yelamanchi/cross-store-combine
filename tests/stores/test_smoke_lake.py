import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xstore.entities import Batch, Entity, Order, load_mapping
from xstore.stores import Store, get_store
from xstore.stores.iceberg_spec import ICEBERG_DIR, iceberg_spec
from xstore.stores.snowflake import create_store as snowflake_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ORDER = Order(
    order_id="o1",
    customer_id="c1",
    amount=Decimal("9.99"),
    status="paid",
    ordered_at=NOW,
    updated_at=NOW,
)
BATCH = Batch(
    batch_id="b1",
    sku="s1",
    quantity=3,
    weight_kg=1.5,
    warehouse="w1",
    produced_at=NOW,
    updated_at=NOW,
)


@pytest.mark.smoke
@pytest.mark.parametrize(("name", "entity"), [("s3_parquet", ORDER), ("adls_parquet", BATCH)])
def test_lake_round_trip_idempotent_and_streams(name: str, entity: Entity) -> None:
    store = get_store(name)
    assert isinstance(store, Store)
    t = entity.entity_type
    m = load_mapping(name).for_entity(t)
    rec = m.from_canonical(entity)
    assert store.write(t, rec, "k1").applied
    assert not store.write(t, rec, "k1").applied
    assert m.to_canonical(t, store.read(t, entity.key) or {}) == entity
    assert store.delete(t, entity.key, "k2").applied
    assert store.read(t, entity.key) is None
    assert [e.op for e in store.stream_changes()] == ["c", "d"]


@pytest.mark.smoke
def test_iceberg_spec_generated_and_snowflake_real_only() -> None:
    for t in ("customer", "order"):
        assert json.loads((ICEBERG_DIR / f"{t}.json").read_text()) == iceberg_spec(t)
    with pytest.raises(NotImplementedError, match="real-mode only"):
        snowflake_store("local")
