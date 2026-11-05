"""OpenTelemetry tracing: one TracerProvider, vendor-neutral exporter picked from env.

Exporter order: OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set and the exporter package
is installed; console when XSTORE_OTEL_CONSOLE=1; otherwise spans are created but not exported.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span

SCOPE = "xstore"
RUN_ID_ATTR = "xstore.run_id"
_provider: TracerProvider | None = None
_run_id: ContextVar[str | None] = ContextVar("xstore_run_id", default=None)


def _otlp_exporter() -> SpanExporter | None:
    try:  # opentelemetry-exporter-otlp is optional and not a project dependency
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        return None
    exporter: SpanExporter = OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_* env vars
    return exporter


def init_tracing(
    service_name: str = "xstore", exporter: SpanExporter | None = None
) -> TracerProvider:
    """(Re)build the module TracerProvider. `exporter` overrides env selection (tests)."""
    global _provider
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "cross-store-combine",
            "deployment.environment": os.environ.get("XSTORE_ENV", "local"),
            "xstore.store_backend": os.environ.get("STORE_BACKEND", "local"),
            "xstore.model_provider": os.environ.get("MODEL_PROVIDER", "mock"),
        }
    )
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and (otlp := _otlp_exporter()):
        provider.add_span_processor(BatchSpanProcessor(otlp))
    elif os.environ.get("XSTORE_OTEL_CONSOLE") == "1":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    if _provider is not None:
        _provider.shutdown()
    _provider = provider
    return provider


def get_tracer() -> trace.Tracer:
    return (_provider or init_tracing()).get_tracer(SCOPE)


def current_run_id() -> str | None:
    return _run_id.get()


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """Child of the current span; `run_id` is inherited from the enclosing span when omitted."""
    run_id = attrs.pop("run_id", None) or _run_id.get()
    token = _run_id.set(run_id)
    try:
        with get_tracer().start_as_current_span(name) as s:
            if run_id:
                s.set_attribute(RUN_ID_ATTR, run_id)
            set_attrs(s, **attrs)
            yield s  # start_as_current_span records exceptions and sets ERROR status
    finally:
        _run_id.reset(token)


def set_attrs(s: Span, **attrs: Any) -> None:
    """Set scalar attributes under the `xstore.` prefix; None is skipped."""
    for k, v in attrs.items():
        if v is None:
            continue
        key = k if "." in k else f"xstore.{k}"
        s.set_attribute(key, v if isinstance(v, bool | int | float | str) else str(v))
