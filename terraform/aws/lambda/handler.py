"""AWS Lambda entrypoint for the DynamoDB Streams event source mapping.

The zip built by modules/cdc_lambda mirrors the repo layout (src/xstore,
cdc/schema-registry, stores/mappings) so path lookups in xstore resolve.
Third-party deps (fastavro, pydantic, pyyaml) come from a Lambda layer.
Events go to an in-process bus: this validates and reports poison records;
forwarding to MSK needs a Kafka-backed bus.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

_streams = importlib.import_module("xstore.cdc.dynamodb_streams")
_bus = importlib.import_module("xstore.cdc.bus")

log = logging.getLogger()
log.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Decode stream records into ChangeEvents; report poison records as batch item failures."""
    result: dict[str, Any] = _streams.handle_batch(event, _bus.InProcessBus())
    log.info(
        "records=%d failures=%d",
        len(event.get("Records", [])),
        len(result["batchItemFailures"]),
    )
    return result
