.PHONY: metrics otel-console

# Sample Prometheus exposition after an in-process demo run (no server, no port).
metrics:
	STORE_BACKEND=local MODEL_PROVIDER=mock uv run python -m xstore.observability --customers 60 --injections 8 --seed 7

# Demo with OpenTelemetry spans printed by ConsoleSpanExporter.
otel-console:
	XSTORE_OTEL_CONSOLE=1 STORE_BACKEND=local MODEL_PROVIDER=mock uv run python -m xstore.pipeline.demo --customers 60 --injections 8 --seed 7 --workdir runs/otel-demo
