from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xstore.entities import Order, load_mapping
from xstore.stores import Store, get_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.smoke
def test_hubspot_round_trip_idempotent_and_streams() -> None:
    store = get_store("hubspot")
    assert isinstance(store, Store)
    m = load_mapping("hubspot").for_entity("order")
    order = Order(
        order_id="o1",
        customer_id="c1",
        amount=Decimal("9.99"),
        status="paid",
        ordered_at=NOW,
        updated_at=NOW,
    )
    assert store.write("order", m.from_canonical(order), "k1").applied
    assert not store.write("order", m.from_canonical(order), "k1").applied
    native = store.read("order", "o1") or {}
    assert native["amount"] == "9.99"
    assert m.to_canonical("order", native) == order
    assert store.delete("order", "o1", "k2").applied
    assert store.read("order", "o1") is None
    assert [e.op for e in store.stream_changes()] == ["c", "d"]
