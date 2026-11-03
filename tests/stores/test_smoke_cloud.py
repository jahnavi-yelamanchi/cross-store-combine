from datetime import UTC, datetime

import pytest

from xstore.entities import Customer, load_mapping
from xstore.stores import Store, get_store
from xstore.stores.dynamodb import stream_record_to_change_event

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.smoke
@pytest.mark.parametrize("name", ["dynamodb", "cosmosdb"])
def test_cloud_store_round_trip_idempotent_and_streams(name: str) -> None:
    store = get_store(name)
    assert isinstance(store, Store)
    m = load_mapping(name).for_entity("customer")
    customer = Customer(customer_id="c1", name="Ada", email="ada@example.com", updated_at=NOW)
    rec = m.from_canonical(customer)
    assert store.write("customer", rec, "k1").applied
    assert not store.write("customer", rec, "k1").applied
    assert m.to_canonical("customer", store.read("customer", "c1") or {}) == customer
    assert store.delete("customer", "c1", "k2").applied
    assert store.read("customer", "c1") is None
    assert [e.op for e in store.stream_changes()] == ["c", "d"]

    event = stream_record_to_change_event(
        {
            "eventName": "INSERT",
            "dynamodb": {
                "ApproximateCreationDateTime": 1767225600.0,
                "Keys": {"pk": {"S": "ORDER#o1"}},
                "NewImage": {"pk": {"S": "ORDER#o1"}, "amount": {"N": "9.99"}},
                "SequenceNumber": "100",
            },
        }
    )
    assert (event.entity_type, event.key, event.op, event.seq) == ("order", "o1", "c", 100)
    assert event.after is not None and str(event.after["amount"]) == "9.99"
