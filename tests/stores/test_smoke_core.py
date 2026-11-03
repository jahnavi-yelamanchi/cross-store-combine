from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xstore.entities import Order, load_mapping
from xstore.stores import Store, get_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.smoke
@pytest.mark.parametrize("name", ["postgres", "mongodb"])
def test_store_round_trip_idempotent_and_streams(name: str) -> None:
    store = get_store(name)
    assert isinstance(store, Store)
    m = load_mapping(name).for_entity("order")
    order = Order(
        order_id="o1",
        customer_id="c1",
        amount=Decimal("9.99"),
        status="paid",
        ordered_at=NOW,
        updated_at=NOW,
    )
    rec = m.from_canonical(order)
    assert store.write("order", rec, "k1").applied
    assert not store.write("order", rec, "k1").applied
    assert m.to_canonical("order", store.read("order", "o1") or {}) == order
    assert store.delete("order", "o1", "k2").applied
    store.write("order", rec, "k3")
    assert not store.delete("order", "o1", "k2").applied  # replayed delete is a no-op
    assert store.read("order", "o1") is not None
    assert [e.op for e in store.stream_changes()] == ["c", "d", "c"]
