from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xstore.entities import Batch, Order, all_stores, load_mapping

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.smoke
def test_mappings_round_trip_with_units() -> None:
    assert len(all_stores()) == 8
    order = Order(
        order_id="o1",
        customer_id="c1",
        amount=Decimal("12.34"),
        status="paid",
        ordered_at=NOW,
        updated_at=NOW,
    )
    pg = load_mapping("postgres").for_entity("order")
    native = pg.from_canonical(order)
    assert native["amount_cents"] == 1234
    assert pg.to_canonical("order", native) == order

    hs = load_mapping("hubspot").for_entity("order")
    assert hs.from_canonical(order)["dealstage"] == "closedwon"
    assert hs.to_canonical("order", hs.from_canonical(order)) == order

    batch = Batch(
        batch_id="b1",
        sku="S",
        quantity=3,
        weight_kg=10.0,
        warehouse="W",
        produced_at=NOW,
        updated_at=NOW,
    )
    cos = load_mapping("cosmosdb").for_entity("batch")
    back = cos.to_canonical("batch", cos.from_canonical(batch))
    assert isinstance(back, Batch)
    assert back.weight_kg == pytest.approx(10.0, rel=1e-4)
