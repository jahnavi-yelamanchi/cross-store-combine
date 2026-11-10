"""Azure Functions (Python v2 model): Cosmos DB change feed -> Event Hubs.

Each trigger hands the changed documents to
`xstore.cdc.cosmos_change_feed.handle_documents`, which maps them to the
canonical ChangeEvent envelope, then forwards the envelopes to the
`xstore.changes` hub. Connections are identity-based (`<name>__*` app settings).

Package layout (built by `make func-azure-package`): this file at the root plus
`src/xstore`, `stores/mappings` and `cdc/schema-registry` copied from the repo,
so xstore resolves its mappings and Avro schema relative to the package root.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import azure.functions as func

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from xstore.cdc.bus import InProcessBus
from xstore.cdc.cosmos_change_feed import handle_documents

DATABASE = "xstore"
HUB = "xstore.changes"
GROUP = "azure-function"

app = func.FunctionApp()


def _forward(container: str, docs: func.DocumentList, out: func.Out[list[str]]) -> None:
    bus = InProcessBus()
    count = handle_documents(container, [doc.to_dict() for doc in docs], bus)
    out.set([event.model_dump_json() for event in bus.poll(GROUP)])
    logging.info("forwarded %d %s changes to %s", count, container, HUB)


@app.function_name("customers_change_feed")
@app.cosmos_db_trigger(
    arg_name="docs",
    connection="CosmosDbConnection",
    database_name=DATABASE,
    container_name="customers",
    lease_container_name="leases",
    lease_container_prefix="customers-",
    create_lease_container_if_not_exists=False,
)
@app.event_hub_output(arg_name="out", event_hub_name=HUB, connection="EventHubConnection")
def customers_change_feed(docs: func.DocumentList, out: func.Out[list[str]]) -> None:
    _forward("customers", docs, out)


@app.function_name("batches_change_feed")
@app.cosmos_db_trigger(
    arg_name="docs",
    connection="CosmosDbConnection",
    database_name=DATABASE,
    container_name="batches",
    lease_container_name="leases",
    lease_container_prefix="batches-",
    create_lease_container_if_not_exists=False,
)
@app.event_hub_output(arg_name="out", event_hub_name=HUB, connection="EventHubConnection")
def batches_change_feed(docs: func.DocumentList, out: func.Out[list[str]]) -> None:
    _forward("batches", docs, out)
