from datetime import UTC, datetime

import pytest
from moto import mock_aws

from xstore.cdc import InProcessBus, pump
from xstore.cdc.cosmos_change_feed import handle_documents
from xstore.cdc.dynamodb_streams import handle_batch
from xstore.entities import Customer, load_mapping
from xstore.stores import get_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.smoke
def test_stores_to_bus_with_avro_and_stream_adapters() -> None:
    mock_aws().reset()  # moto state is process-global; isolate from other smoke tests
    bus = InProcessBus()
    cust = Customer(customer_id="cdc1", name="Ada", email="ada@x.io", updated_at=NOW)
    stores = [get_store("postgres"), get_store("mongodb"), get_store("dynamodb")]
    for s in stores:
        s.write(
            "customer", load_mapping(s.name).for_entity("customer").from_canonical(cust), "cdc-k1"
        )
    cursors: dict[str, int] = {}
    assert pump(stores, bus, cursors) == 3
    assert pump(stores, bus, cursors) == 0
    events = bus.poll("detector")
    assert {e.store for e in events} == {"postgres", "mongodb", "dynamodb"}
    assert all(e.key == "cdc1" and e.op == "c" for e in events)
    assert bus.lag("detector") == 0

    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {"pk": {"S": "CUSTOMER#cdc1"}},
            "NewImage": {"pk": {"S": "CUSTOMER#cdc1"}, "name": {"S": "Ada L"}},
            "SequenceNumber": "100",
            "ApproximateCreationDateTime": 1767225600,
        },
    }
    bookkeeping = {**record, "dynamodb": {**record["dynamodb"], "Keys": {"pk": {"S": "STREAM#1"}}}}
    assert handle_batch({"Records": [record, bookkeeping]}, bus) == {"batchItemFailures": []}
    doc = {"id": "cdc1", "displayName": "Ada", "_lsn": 7, "_ts": 1767225600, "_etag": "x"}
    assert handle_documents("customers", [doc], bus) == 1
    later = bus.poll("detector")
    mock_aws().reset()
    assert {(e.store, e.op) for e in later} == {("dynamodb", "u"), ("cosmosdb", "u")}
