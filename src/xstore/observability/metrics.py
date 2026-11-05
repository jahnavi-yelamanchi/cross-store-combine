"""Prometheus metrics in a dedicated CollectorRegistry (no default process collectors).

$ per 1k entities in PromQL:
  1000 * increase(xstore_llm_cost_usd_total[1h]) / increase(xstore_entities_processed_total[1h])
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

REGISTRY = CollectorRegistry(auto_describe=True)

DETECTIONS = Counter(
    "xstore_detections", "Cross-store conflicts detected.", ["type", "store"], registry=REGISTRY
)
BAD_WRITES = Counter(
    "xstore_bad_writes", "Applied fixes whose read-back hash differs; must stay 0.",
    registry=REGISTRY,
)  # fmt: skip
FIXES_APPLIED = Counter("xstore_fixes_applied", "Fixes applied to stores.", registry=REGISTRY)
PROPOSALS = Counter("xstore_proposals", "Proposal transitions.", ["status"], registry=REGISTRY)
LLM_COST = Counter("xstore_llm_cost_usd", "LLM spend in USD.", ["model"], registry=REGISTRY)
ENTITIES = Counter("xstore_entities_processed", "Entity keys examined.", registry=REGISTRY)
CDC_LAG = Gauge("xstore_cdc_lag_events", "Unconsumed CDC events.", ["group"], registry=REGISTRY)
TIEBREAK_LATENCY = Histogram(
    "xstore_tiebreak_latency_seconds", "Tiebreak agent wall time.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30), registry=REGISTRY,
)  # fmt: skip
EVENT_TO_PROPOSAL = Histogram(
    "xstore_cdc_event_to_proposal_seconds", "CDC event timestamp to proposal created.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300), registry=REGISTRY,
)  # fmt: skip


def record_detections(rows: Iterable[Any]) -> None:
    """Rows with `.type` and `.stores` (pipeline.demo.Detection)."""
    for r in rows:
        for store in r.stores:
            DETECTIONS.labels(type=r.type, store=store).inc()


def record_apply(applied: int, bad_writes: int) -> None:
    FIXES_APPLIED.inc(applied)
    BAD_WRITES.inc(bad_writes)


def record_tiebreak(model: str, usage: Any, latency_s: float) -> float:
    """Observe latency and add token cost; returns the USD cost (0.0 if model is unpriced)."""
    TIEBREAK_LATENCY.observe(latency_s)
    try:
        from xstore.evals.pricing import token_cost

        cost = token_cost(usage, model)
    except Exception:  # unpriced model or missing prices file
        cost = 0.0
    LLM_COST.labels(model=model).inc(cost)
    return cost


def render() -> str:
    """Prometheus text exposition of REGISTRY."""
    return generate_latest(REGISTRY).decode()


def start_metrics_server(port: int = 9464, addr: str = "0.0.0.0") -> None:
    """Serve /metrics for Prometheus; not started in tests."""
    start_http_server(port, addr=addr, registry=REGISTRY)
