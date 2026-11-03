"""DynamoDB Streams -> bus. `handler` has the AWS Lambda event-source-mapping
signature; failed records are reported via batchItemFailures so SQS DLQ redrive
(configured on the mapping) catches poison records."""

from __future__ import annotations

from typing import Any

from xstore.cdc.bus import InProcessBus
from xstore.stores.dynamodb import stream_record_to_change_event

DATA_EVENTS = {"INSERT", "MODIFY", "REMOVE"}


def is_data_record(record: dict[str, Any]) -> bool:
    """Skip the adapter's own bookkeeping items (IDEMPOTENCY#, STREAM#, counters)."""
    pk = record["dynamodb"]["Keys"]["pk"]["S"]
    return record["eventName"] in DATA_EVENTS and not pk.startswith(
        ("IDEMPOTENCY#", "STREAM#", "COUNTER#")
    )


def handle_batch(event: dict[str, Any], bus: InProcessBus) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        if not is_data_record(record):
            continue
        try:
            bus.publish(stream_record_to_change_event(record))
        except (KeyError, ValueError):
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
