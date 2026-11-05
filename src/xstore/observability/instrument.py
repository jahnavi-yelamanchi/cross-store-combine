"""Call-site helpers: one call records span attributes, metrics and (for LLM calls) Langfuse."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span

from xstore.observability import langfuse_hook, metrics
from xstore.observability.tracing import current_run_id, set_attrs

if TYPE_CHECKING:
    from xstore.agent.models import TiebreakResult
    from xstore.cdc import InProcessBus
    from xstore.ledger.apply import ApplyReport


def observe_tiebreak(s: Span, result: TiebreakResult) -> None:
    u = result.usage
    cost = metrics.record_tiebreak(result.model, u, result.latency_ms / 1000)
    set_attrs(
        s,
        **{"gen_ai.request.model": result.model, "gen_ai.system": result.provider},
        model=result.model,
        provider=result.provider,
        source=result.source,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_write_tokens=u.cache_write_tokens,
        cost_usd=cost,
        same_entity=result.decision.same_entity,
        needs_human=result.needs_human,
    )
    langfuse_hook.record_generation(result, cost, current_run_id())


def observe_apply(s: Span, report: ApplyReport) -> None:
    metrics.record_apply(report.applied, report.bad_writes)
    set_attrs(
        s,
        applied=report.applied,
        skipped=report.skipped,
        failed=len(report.failed),
        bad_writes=report.bad_writes,
    )


def observe_lag(bus: InProcessBus, group: str) -> int:
    lag = bus.lag(group)
    metrics.CDC_LAG.labels(group=group).set(lag)
    return lag


def observe_detect(s: Span, rows: Iterable[Any], entities: int) -> None:
    rows = list(rows)
    metrics.record_detections(rows)
    metrics.ENTITIES.inc(entities)
    set_attrs(s, entities=entities, detections=len(rows))


def observe_proposal(status: str, event_ts_ms: int | None = None) -> None:
    metrics.PROPOSALS.labels(status=status).inc()
    if event_ts_ms is not None:
        metrics.EVENT_TO_PROPOSAL.observe(max(time.time() * 1000 - event_ts_ms, 0) / 1000)
