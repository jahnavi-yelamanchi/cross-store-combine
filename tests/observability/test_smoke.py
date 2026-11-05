import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from xstore.observability import init_tracing, render
from xstore.observability.tracing import RUN_ID_ATTR
from xstore.pipeline.demo import run_demo

DASHBOARD = Path(__file__).resolve().parents[2] / "observability/grafana/dashboards/xstore.json"
PANELS = {
    "Detections by type",
    "Bad writes",
    "$ per 1k entities",
    "CDC lag",
    "Tiebreak latency p50/p95",
    "Event-to-proposal p95",
    "Proposals by status",
}


@pytest.mark.smoke
def test_demo_emits_spans_metrics_and_dashboard_parses(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    init_tracing(exporter=exporter)
    try:
        report = run_demo(n_customers=30, n_injections=6, seed=7, workdir=tmp_path)
    finally:
        init_tracing()  # drop the test exporter
    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["pipeline.run"]
    assert root.parent is None
    for name in ("pipeline.detect", "pipeline.apply", "ledger.apply_pending"):
        assert spans[name].attributes is not None
        assert spans[name].attributes[RUN_ID_ATTR] == report.run_id
        assert spans[name].context.trace_id == root.context.trace_id
    detect, apply_pending = spans["pipeline.detect"].parent, spans["ledger.apply_pending"].parent
    assert detect is not None and detect.span_id == root.context.span_id
    assert apply_pending is not None
    assert apply_pending.span_id == spans["pipeline.apply"].context.span_id

    text = render()
    assert "xstore_bad_writes_total 0.0" in text
    detections = [
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith("xstore_detections_total{")
    ]
    assert sum(detections) > 0

    dash = json.loads(DASHBOARD.read_text())
    assert dash["schemaVersion"] >= 39
    assert PANELS.issubset(p["title"] for p in dash["panels"])
