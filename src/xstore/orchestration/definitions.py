"""Dagster software-defined assets for the reconcile loop, with schedule and CDC lag sensor.

ingest -> cdc_events -> diff -> detect -> propose, and diff -> matches -> propose.
Validate: `dagster definitions validate -m xstore.orchestration.definitions`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from dagster import (
    AssetCheckResult,
    AssetExecutionContext,
    AssetSelection,
    Config,
    ConfigurableResource,
    Definitions,
    RunRequest,
    ScheduleDefinition,
    SensorEvaluationContext,
    SensorResult,
    SkipReason,
    asset,
    asset_check,
    define_asset_job,
    in_process_executor,
    sensor,
)
from fastapi.testclient import TestClient

from xstore.cdc import TOPIC
from xstore.evals.snapshot import DEFAULT_DATA
from xstore.orchestration import steps
from xstore.proposals import SqlRepository
from xstore.proposals.app import create_app

DBT_DIR = Path(__file__).resolve().parents[3] / "dbt"


class StoresResource(ConfigurableResource):
    """Stores plus their CDC bus and detector view, shared per namespace in this process."""

    namespace: str = "default"
    stores: list[str] = list(steps.LOCAL_STORES)  # noqa: RUF012

    def runtime(self) -> steps.Runtime:
        return steps.runtime(self.namespace, self.stores)


class CdcResource(ConfigurableResource):
    """Consumer group and lag threshold. Real mode reads lag from Kafka/Redpanda when
    `bootstrap_servers` (or env KAFKA_BOOTSTRAP_SERVERS) is set."""

    group: str = steps.GROUP
    topic: str = TOPIC
    lag_threshold: int = 0
    bootstrap_servers: str = ""

    def servers(self) -> str:
        return self.bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")


class ProposalsApiResource(ConfigurableResource):
    """HTTP client for the proposals API: remote when `base_url` is set, else the FastAPI
    app in process over `database_url`."""

    base_url: str = ""
    database_url: str = "sqlite://"

    def client(self) -> httpx.Client | TestClient:
        if self.base_url:
            return httpx.Client(base_url=self.base_url, timeout=30)
        return TestClient(create_app(SqlRepository.from_env(self.database_url)))


class IngestConfig(Config):
    snapshot_dir: str = str(DEFAULT_DATA)
    limit: int = 50
    seed: bool = True


class DiffConfig(Config):
    run_dbt: bool = False


@asset(group_name="reconcile")
def ingest(
    context: AssetExecutionContext, config: IngestConfig, stores: StoresResource
) -> dict[str, int]:
    """Seed stores from the frozen snapshot subset (or only read them); records per store."""
    counts = steps.ingest(stores.runtime(), config.snapshot_dir, config.limit, config.seed)
    context.add_output_metadata({"records": sum(counts.values()), **counts})
    return counts


@asset(group_name="reconcile")
def cdc_events(
    context: AssetExecutionContext, ingest: dict[str, int], stores: StoresResource, cdc: CdcResource
) -> dict[str, int]:
    """Store outboxes pumped onto the xstore.changes bus."""
    out = steps.cdc_events(stores.runtime(), cdc.group)
    context.add_output_metadata(out)
    return out


@asset(group_name="reconcile")
def diff(
    context: AssetExecutionContext,
    config: DiffConfig,
    cdc_events: dict[str, int],
    stores: StoresResource,
    cdc: CdcResource,
) -> dict[str, Any]:
    """Bus consumed into the detector view; canonical cross-store comparison of touched keys.
    With `run_dbt` and a dbt project present, also runs `dbt build`."""
    out = steps.diff(stores.runtime(), cdc.group)
    if config.run_dbt:
        _dbt_build(context)
    context.add_output_metadata({"touched": len(out["touched"]), "conflicting": out["conflicting"]})
    return out


def _dbt_build(context: AssetExecutionContext) -> None:
    dbt = shutil.which("dbt")
    if not (DBT_DIR / "dbt_project.yml").exists() or dbt is None:
        context.log.info("dbt project or CLI not found; skipping dbt build")
        return
    cmd = [dbt, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    subprocess.run(cmd, check=True)


@asset(group_name="reconcile")
def detect(
    context: AssetExecutionContext, diff: dict[str, Any], stores: StoresResource
) -> list[dict[str, Any]]:
    """Baseline detections for the keys touched by this run's CDC events."""
    out = steps.detect(stores.runtime(), diff["touched"])
    by_type: dict[str, int] = {}
    for d in out:
        by_type[d["type"]] = by_type.get(d["type"], 0) + 1
    context.add_output_metadata({"detections": len(out), **by_type})
    return out


@asset(group_name="reconcile")
def matches(
    context: AssetExecutionContext, diff: dict[str, Any], stores: StoresResource
) -> dict[str, Any]:
    """Splink customer pairs over the view; duplicate candidates above the threshold."""
    out = steps.matches(stores.runtime())
    context.add_output_metadata({"pairs": out["pairs"], "duplicates": len(out["duplicates"])})
    return out


@asset(group_name="reconcile")
def propose(
    context: AssetExecutionContext,
    detect: list[dict[str, Any]],
    matches: dict[str, Any],
    stores: StoresResource,
    proposals_api: ProposalsApiResource,
) -> dict[str, int]:
    """Fix proposals posted to the approval API; counts by outcome."""
    with proposals_api.client() as client:
        out = steps.propose(
            stores.runtime(), detect, matches["duplicates"], client, context.run.run_id
        )
    context.add_output_metadata(out)
    return out


@asset_check(asset=ingest)
def ingest_nonempty(ingest: dict[str, int]) -> AssetCheckResult:
    empty = sorted(s for s, n in ingest.items() if n == 0)
    return AssetCheckResult(passed=not empty, metadata={"empty_stores": ", ".join(empty)})


@asset_check(asset=diff)
def cdc_lag_drained(
    diff: dict[str, Any], stores: StoresResource, cdc: CdcResource
) -> AssetCheckResult:
    lag = stores.runtime().bus.lag(cdc.group, cdc.topic)
    return AssetCheckResult(passed=lag == 0, metadata={"lag": lag})


@asset_check(asset=propose)
def propose_error_rate_zero(propose: dict[str, int]) -> AssetCheckResult:
    rate = propose["errors"] / propose["posted"] if propose["posted"] else 0.0
    return AssetCheckResult(passed=rate == 0, metadata={"error_rate": rate})


ASSETS = [ingest, cdc_events, diff, detect, matches, propose]
CHECKS = [ingest_nonempty, cdc_lag_drained, propose_error_rate_zero]

# In-process: local stores and bus live in memory, so every step must share one process.
reconcile_job = define_asset_job(
    "reconcile_job", selection=AssetSelection.all(), executor_def=in_process_executor
)
reconcile_schedule = ScheduleDefinition(
    name="reconcile_every_15m", job=reconcile_job, cron_schedule="*/15 * * * *"
)


def kafka_lag(servers: str, group: str, topic: str) -> tuple[int, dict[int, int]]:
    """Consumer-group lag and end offsets from Kafka/Redpanda. Raises ImportError when
    confluent_kafka is not installed."""
    from confluent_kafka import Consumer, TopicPartition

    consumer: Any = Consumer(
        {"bootstrap.servers": servers, "group.id": group, "enable.auto.commit": False}
    )
    try:
        meta = consumer.list_topics(topic, timeout=10)
        parts = [TopicPartition(topic, p) for p in sorted(meta.topics[topic].partitions)]
        ends: dict[int, int] = {}
        lag = 0
        for tp in consumer.committed(parts, timeout=10):
            low, high = consumer.get_watermark_offsets(tp, timeout=10)
            ends[tp.partition] = high
            lag += high - (tp.offset if tp.offset >= 0 else low)
        return lag, ends
    finally:
        consumer.close()


@sensor(job=reconcile_job, minimum_interval_seconds=60)
def cdc_lag_sensor(
    context: SensorEvaluationContext, stores: StoresResource, cdc: CdcResource
) -> SensorResult | SkipReason:
    """Request a reconcile run when the consumer group lags by more than the threshold.
    Cursor holds the end offsets seen; the run key dedups requests for the same offsets."""
    if servers := cdc.servers():
        try:
            lag, ends = kafka_lag(servers, cdc.group, cdc.topic)
        except ImportError:
            return SkipReason("KAFKA_BOOTSTRAP_SERVERS set but confluent_kafka not installed")
        except Exception as exc:  # broker unreachable, unknown topic
            return SkipReason(f"kafka lag unavailable: {exc}")
    else:
        bus = stores.runtime().bus
        lag, ends = bus.lag(cdc.group, cdc.topic), bus.end_offsets(cdc.topic)
    cursor = json.dumps(ends, sort_keys=True)
    if lag <= cdc.lag_threshold:
        return SkipReason(f"lag {lag} <= {cdc.lag_threshold}")
    if cursor == context.cursor:
        return SkipReason(f"lag {lag}; run already requested for these offsets")
    return SensorResult(
        run_requests=[RunRequest(run_key=f"cdc-lag-{cursor}", tags={"cdc_lag": str(lag)})],
        cursor=cursor,
    )


defs = Definitions(
    assets=ASSETS,
    asset_checks=CHECKS,
    jobs=[reconcile_job],
    schedules=[reconcile_schedule],
    sensors=[cdc_lag_sensor],
    resources={
        "stores": StoresResource(),
        "cdc": CdcResource(),
        "proposals_api": ProposalsApiResource(),
    },
)
