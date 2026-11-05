# Cross-Store Reconciliation Agent

The same business entity lives in Postgres, MongoDB, DynamoDB/Cosmos DB, S3/ADLS Parquet and a CRM. Detect drift via CDC, match entities probabilistically with an LLM tiebreak, propose fixes with evidence, apply idempotently after human approval, keep a replayable ledger.

![F1 vs cost per configuration, holdout split](results/f1_vs_cost.png)

LLM points are simulated by the offline mock provider; see Disclosed.

## Layers

| Layer | What | Tech |
|---|---|---|
| Entities | Canonical Customer, Order, Batch; per-store field/unit/enum mappings | Pydantic, `stores/mappings/*.yaml` |
| Stores | One `Store` protocol: read, scan, idempotent write/delete, stream_changes | Postgres, MongoDB, DynamoDB, Cosmos DB, S3 + ADLS Gen2 Parquet (Arrow, Iceberg spec), Snowflake stub, HubSpot stub (FastAPI) |
| CDC | One change envelope from every source | Debezium, DynamoDB Streams, Cosmos DB change feed, Kafka/Redpanda, Avro + Schema Registry |
| Diff | Staging per store -> reconciled model; quality and schema checks | dbt on DuckDB, dbt tests, Great Expectations, data contracts, OpenLineage |
| Match | Probabilistic entity resolution across stores | Splink (Fellegi-Sunter, blocking rules, EM) |
| Tiebreak | Low-confidence pairs decided with evidence | LangGraph, Instructor, MCP evidence tool, Claude Haiku 5.5 default, prompt caching |
| Policy | Deterministic conflict resolution; agent proposes policy diffs | `policy/policy.yaml` |
| Approval | Proposals with evidence, confidence, lineage; approve/reject | FastAPI |
| Ledger | Event-sourced, append-only; idempotent apply; replay + verify | SQLite locally, Postgres; DB triggers + hash chain |
| Orchestration | ingest -> diff -> detect -> propose | Dagster software-defined assets, schedule, CDC-lag sensor; Airflow mirror |
| Observability | Traces, metrics, dashboards, LLM traces | OpenTelemetry, Prometheus, Grafana, Langfuse |
| Deploy | Local stack, cluster, cloud | Docker Compose, Helm, kind, Terraform (AWS: MSK, RDS, Lambda, SQS DLQ, EKS; Azure: Event Hubs, Synapse, Functions, Event Grid, AKS) |
| Evals | Comparison matrix, tiebreak task set, prompt regression, CI gate | Inspect AI, promptfoo, GitHub Actions |

## How I chose

| Pick | Over | Why |
|---|---|---|
| Store protocol on native records | Protocol on canonical entities | Type and schema drift stay visible to detection (ADR-003) |
| Idempotency key claimed inside each store + outbox | App-level dedupe | Survives restarts; CDC semantics without a broker (ADR-003) |
| Splink baseline, LLM only in the uncertain band | LLM-only matching | ~60k pairs; LLM-only costs ~280x more for the same F1 (ADR-012, ADR-017) |
| Instructor structured output + MCP evidence tool | Regex-parsed text; agent reading stores directly | Typed decisions; one audited evidence path (ADR-013) |
| Deterministic policy engine, agent proposes diffs | LLM picks winning values | Reproducible fixes; humans approve policy changes (ADR-014) |
| Content-hash proposal ids | Random ids + dedupe queries | Re-running detection is idempotent (ADR-015) |
| Append-only ledger enforced by DB triggers | Mutable status rows | Replayable audit trail; tamper-evident hash chain (ADR-016) |
| Avro envelope on an in-process bus with Kafka semantics | Plain queue | Same schema checks as Redpanda + Schema Registry, no broker (ADR-009) |
| Frozen seeded snapshot with hash + holdout | Regenerated data | Comparable runs, no leakage (ADR-007) |
| Dagster software-defined assets | Airflow-only | Asset lineage and checks; Airflow DAG mirrored (ADR-020) |
| Data contracts per domain-owned store (data mesh) | Silent coercion | Schema drift fails the pipeline and opens an issue (ADR-021) |
| Redpanda locally | Kafka + ZooKeeper | One container, Kafka API + Schema Registry built in (ADR-025) |
| Entra workload identity / IRSA | Keys in config | No long-lived secrets (ADR-023, ADR-024) |

## Experiment

- Synthetic company, seeded: 10,063 entities (4k customers, 4k orders, 2k batches) across 7 local stores; 500 injected inconsistencies of 8 types (duplicate, stale, typo, missing, type drift, orphaned, schema drift, unit mismatch); frozen at `evals/frozen/v1` with a sha256 manifest; 400 train / 100 held out.
- Configs: Splink-only, LLM-only, hybrid (Splink + LLM tiebreak); Haiku 5.5 vs Sonnet 5.5; batch vs streaming (CDC replay through the Avro bus); policy ablation (source priority vs newest-wins); DynamoDB vs Cosmos DB ledger write cost.
- Metrics: precision/recall/F1 overall and per type, correct-fix rate, bad-write rate, approvals needed, $ per 1k entities, CDC-event-to-detection latency p50/p95.
- Run: `make eval` (matrix both splits + report), `make demo-local`, `make e2e`. Gate thresholds: `evals/thresholds.yaml`.

## Results

Holdout split, 100 labels. Full tables: [results/results.md](results/results.md).

| Config | Model | F1 | $ / 1k entities | Correct fix | Bad write |
|---|---|---|---|---|---|
| Splink-only | - | 0.873 | 0 | 0.769 | 0 |
| Hybrid | Haiku 5.5 | 0.952 | 0.0016 | 0.769 | 0 |
| Hybrid | Sonnet 5.5 | 0.980 | 0.032 | 0.769 | 0 |
| LLM-only | Haiku 5.5 | 0.952 | 0.443 | 0.769 | 0 |
| LLM-only | Sonnet 5.5 | 0.980 | 8.85 | 0.769 | 0 |
| Hybrid, newest-wins policy | Haiku 5.5 | 0.952 | 0.0016 | 0.677 | 0 |

- Hybrid matches LLM-only F1 at ~1/280 of the cost: only 214 of 60,165 candidate pairs reach the LLM.
- Policy ablation: newest-wins drops correct-fix rate 0.769 -> 0.677.
- Streaming latency (CDC event -> detection): p50 ~33 ms, p95 100-120 ms.
- Bad writes stay 0 because auto-apply is limited to agreement / only-source / field-override resolutions; ungated, 11.8% of auto-applied fixes would be wrong.
- `make demo-local`: 20 injections, recall 1.0, 10 safe fixes applied, 0 bad writes, replay verified.

## Disclosed

- No model was called. Tiebreak verdicts and token counts come from `MockProvider`: recorded fixtures where present, otherwise rule-based verdicts with a seeded per-model error rate and estimated tokens. They measure the harness, not Claude.
- Eval gate fails on one check: Inspect tiebreak accuracy 0.78 vs 0.90 (mock routes drifted same-key pairs to "needs human"). Fix: record real fixtures.
- The detector targets the injector's fault patterns; recall 1.0 on every type is optimistic for real data.
- LLM-only cost is partly extrapolated from a 500-pair sample of equal-key pairs. Ledger write prices are placeholders.
- No cloud resource, container or cluster was started: Terraform is validated with `-backend=false`, Helm is linted, compose and kind are not run. Real-mode adapters (Snowflake, Cosmos DB SDK, ADLS SDK, HubSpot API) are untested.
- GitHub Actions did not run during the build (account billing lock); every check ran locally.

Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) · [SETUP](docs/SETUP.md) · [ADRs](docs/) · [KEYWORD_MAP](docs/KEYWORD_MAP.md) · [STATUS](docs/STATUS.md)
