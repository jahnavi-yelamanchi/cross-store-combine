"""OpenLineage RunEvents for quality pipeline runs.

Locally events go to JSON files under runs/openlineage/; with OPENLINEAGE_URL set they are
POSTed to that endpoint (e.g. Marquez), with OPENLINEAGE_API_KEY as bearer token if set.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import InputDataset, Job, OutputDataset, Run, RunEvent, RunState
from openlineage.client.facet_v2 import (
    RunFacet,
    data_quality_assertions_dataset,
    error_message_run,
    job_type_job,
)
from openlineage.client.transport.file import FileConfig, FileTransport
from openlineage.client.transport.http import HttpConfig, HttpTransport
from openlineage.client.transport.transport import Transport
from openlineage.client.uuid import generate_new_uuid

PRODUCER = "https://github.com/jahnavi-yelamanchi/cross-store-combine"
JOB_NAMESPACE = "xstore"
EVENTS_DIR = Path("runs/openlineage")
Assertion = data_quality_assertions_dataset.Assertion


def make_transport(events_dir: Path = EVENTS_DIR) -> Transport:
    url = os.environ.get("OPENLINEAGE_URL")
    if url:
        params: dict[str, object] = {"url": url}
        if key := os.environ.get("OPENLINEAGE_API_KEY"):
            params["auth"] = {"type": "api_key", "apiKey": key}
        return HttpTransport(HttpConfig.from_dict(params))
    events_dir.mkdir(parents=True, exist_ok=True)
    return FileTransport(FileConfig(log_file_path=str(events_dir / "event"), append=False))


def store_datasets(snapshot_dir: Path) -> list[InputDataset]:
    """One input dataset per <store>/<entity>.jsonl in a snapshot; namespace xstore://<store>."""
    return [
        InputDataset(namespace=f"xstore://{p.parent.name}", name=p.stem)
        for p in sorted(snapshot_dir.glob("*/*.jsonl"))
    ]


def warehouse_namespace(warehouse: Path | str) -> str:
    return f"duckdb://{warehouse}"


def warehouse_outputs(warehouse: Path | str, tables: Iterable[str]) -> list[OutputDataset]:
    ns = warehouse_namespace(warehouse)
    return [OutputDataset(namespace=ns, name=f"main.{t}") for t in tables]


def checked_input(
    warehouse: Path | str, table: str, assertions: Sequence[Assertion]
) -> InputDataset:
    """A warehouse table read by a validation step, carrying its data quality assertions."""
    facet = data_quality_assertions_dataset.DataQualityAssertionsDatasetFacet(
        assertions=list(assertions)
    )
    return InputDataset(
        namespace=warehouse_namespace(warehouse),
        name=f"main.{table}",
        inputFacets={"dataQualityAssertions": facet},
    )


class LineageRun:
    """One job run: emit start(), then complete() or fail()."""

    def __init__(
        self,
        job_name: str,
        inputs: Sequence[InputDataset] = (),
        client: OpenLineageClient | None = None,
        events_dir: Path = EVENTS_DIR,
    ) -> None:
        self.client = client or OpenLineageClient(transport=make_transport(events_dir))
        self.run_id = str(generate_new_uuid())
        self.job = Job(
            namespace=JOB_NAMESPACE,
            name=job_name,
            facets={
                "jobType": job_type_job.JobTypeJobFacet(
                    processingType="BATCH", integration="DBT", jobType="JOB"
                )
            },
        )
        self.inputs = list(inputs)
        self.events: list[RunEvent] = []

    def _emit(
        self,
        state: RunState,
        inputs: Sequence[InputDataset],
        outputs: Sequence[OutputDataset],
        error: str | None = None,
    ) -> RunEvent:
        facets: dict[str, RunFacet] = {}
        if error is not None:
            facets["errorMessage"] = error_message_run.ErrorMessageRunFacet(
                message=error, programmingLanguage="python"
            )
        event = RunEvent(
            eventType=state,
            eventTime=datetime.now(UTC).isoformat(),
            run=Run(runId=self.run_id, facets=facets),
            job=self.job,
            producer=PRODUCER,
            inputs=list(inputs),
            outputs=list(outputs),
        )
        self.client.emit(event)
        self.events.append(event)
        return event

    def start(self) -> RunEvent:
        return self._emit(RunState.START, self.inputs, [])

    def complete(
        self, outputs: Sequence[OutputDataset] = (), extra_inputs: Sequence[InputDataset] = ()
    ) -> RunEvent:
        return self._emit(RunState.COMPLETE, [*self.inputs, *extra_inputs], outputs)

    def fail(self, error: str, outputs: Sequence[OutputDataset] = ()) -> RunEvent:
        return self._emit(RunState.FAIL, self.inputs, outputs, error=error)
