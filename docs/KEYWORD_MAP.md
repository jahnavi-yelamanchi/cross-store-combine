# Keyword map

Every keyword verified against the combined tree of PRs #56, #53, #50, #51, #54, #55, #52 and main (docs excluded unless noted).

| Keyword | Path(s) | Usage |
|---|---|---|
| Postgres | src/xstore/stores/postgres.py, src/xstore/ledger/sql/postgres_append_only.sql, terraform/aws/modules/rds/main.tf | SQLAlchemy store; ledger append-only trigger; RDS with logical replication |
| MongoDB | src/xstore/stores/mongodb.py, cdc/debezium/mongodb-connector.json | mongomock store, transactions in real mode; Debezium source |
| DynamoDB | src/xstore/stores/dynamodb.py, terraform/aws/modules/dynamodb/main.tf | Single-table store on moto; PAY_PER_REQUEST table + PITR |
| DynamoDB Streams | src/xstore/cdc/dynamodb_streams.py, terraform/aws/modules/cdc_lambda/main.tf | Stream record -> ChangeEvent; event source mapping |
| Cosmos DB | src/xstore/stores/cosmosdb.py, terraform/azure/modules/cosmos/main.tf | In-memory container store; serverless account, keys off |
| change feed | src/xstore/cdc/cosmos_change_feed.py, terraform/azure/functions/cosmos_change_feed/function_app.py | LSN-ordered feed; cosmos_db_trigger with leases |
| S3 | src/xstore/stores/s3_parquet.py, terraform/aws/modules/lake/main.tf | Parquet lake on moto; versioned SSE-KMS bucket |
| Athena | stores/sql/athena_iceberg_ddl.sql, terraform/aws/modules/lake/main.tf | Iceberg DDL; engine v3 workgroup |
| Glue Data Catalog | stores/sql/athena_iceberg_ddl.sql, terraform/aws/modules/lake/main.tf | `xstore` database; Iceberg catalog table |
| ADLS Gen2 | src/xstore/stores/adls_parquet.py, terraform/azure/modules/adls/main.tf | Parquet lake in-memory; HNS account + `lake` filesystem |
| Synapse | stores/sql/synapse_openrowset.sql, terraform/azure/modules/synapse/main.tf, terraform/azure/synapse/sql/ | OPENROWSET over Parquet/Delta; Entra-only workspace |
| Snowflake | src/xstore/stores/snowflake.py, dbt/profiles.yml | Real-mode adapter with MERGE; unused dbt target |
| BigQuery | dbt/profiles.yml | Unused dbt target, credentials via env_var() |
| Iceberg | src/xstore/stores/iceberg_spec.py, stores/iceberg/*.json, terraform/aws/modules/lake/main.tf | Generated v2 table specs; Glue Iceberg table |
| Delta Lake | terraform/azure/synapse/sql/02_batch_delta.sql | OPENROWSET FORMAT='DELTA' view and external table |
| Parquet | src/xstore/stores/parquet_base.py, terraform/azure/synapse/sql/01_lake_external_tables.sql | Append-only delta files; Synapse external tables |
| Arrow | src/xstore/stores/parquet_base.py, src/xstore/matching/canonical.py | Arrow schemas from canonical models; customer table |
| DuckDB | dbt/profiles.yml, src/xstore/matching/splink_matcher.py | dbt-duckdb warehouse; Splink backend |
| Kafka | src/xstore/cdc/bus.py, src/xstore/orchestration/definitions.py, deploy/compose/compose.yaml | Kafka-semantics bus; lag sensor; Kafka API via Redpanda |
| Redpanda | deploy/compose/compose.yaml, cdc/schema-registry/subjects.yaml | Local broker + built-in Schema Registry |
| MSK | terraform/aws/modules/msk/main.tf | IAM-auth cluster, optional MSK Connect Debezium |
| Event Hubs | terraform/azure/modules/eventhubs/main.tf | Kafka endpoint, hubs incl. DLQ, Avro schema group |
| Debezium | cdc/debezium/*.json, deploy/compose/compose.yaml | Postgres pgoutput + MongoDB connectors; Connect service |
| CDC | src/xstore/cdc/, src/xstore/stores/base.py | One ChangeEvent envelope; per-store outbox |
| Schema Registry | cdc/schema-registry/subjects.yaml, src/xstore/cdc/avro.py | BACKWARD subjects; local registry |
| Avro | cdc/schema-registry/change_event.avsc, src/xstore/cdc/avro.py | Confluent wire-format codec |
| dbt | dbt/, src/xstore/quality/run_all.py | Staging per store, reconciled + canonical marts; dbtRunner |
| dbt tests | dbt/models/marts/_marts.yml, dbt/tests/assert_no_negative_amounts.sql | unique, not_null, relationships, accepted_values, singular |
| Great Expectations | great_expectations/, src/xstore/quality/run_checkpoint.py | Suite on canonical_customers; GX 1.x validation |
| OpenLineage | src/xstore/quality/lineage.py | START/COMPLETE/FAIL run events, file or HTTP transport |
| data contracts | contracts/*.yaml, src/xstore/contracts/, .github/workflows/contracts.yml | Per-store contracts; drift fails pipeline, opens issue |
| data mesh | contracts/*.yaml, src/xstore/contracts/generate.py | Domain-owned contracts (owner/domain per store) |
| Dagster | src/xstore/orchestration/definitions.py, orchestration/dagster/ | Definitions, resources, schedule, asset checks |
| software-defined assets | src/xstore/orchestration/definitions.py | ingest -> cdc_events -> diff -> detect -> matches -> propose |
| sensors | src/xstore/orchestration/definitions.py | `cdc_lag_sensor`: bus or Kafka lag -> RunRequest |
| Airflow | orchestration/airflow/dags/xstore_reconcile.py | Mirrored DAG (not run) |
| Splink | src/xstore/matching/splink_matcher.py, src/xstore/matching/settings.yaml | Probabilistic matcher; u sampling, m by EM |
| Fellegi-Sunter | src/xstore/matching/splink_matcher.py | Match weights per comparison level |
| blocking rules | src/xstore/matching/settings.yaml | email, phone, name prefix + country |
| entity resolution | src/xstore/matching/ | dedupe_only over all stores; union-find clusters |
| record linkage | src/xstore/matching/canonical.py, src/xstore/matching/splink_matcher.py | Cross-store customer table and linkage |
| LangGraph | src/xstore/agent/graph.py | fetch_evidence -> decide -> validate |
| MCP | src/xstore/agent/evidence_mcp.py | `xstore-evidence` server, `get_evidence` tool |
| Pydantic | src/xstore/entities/models.py, src/xstore/proposals/models.py, src/xstore/contracts/models.py | Canonical entities, proposals, contracts |
| Instructor | src/xstore/agent/providers.py | `response_model=TiebreakDecision` |
| prompt caching | src/xstore/agent/providers.py, evals/prices.yaml | cache_control on system prompt; cache reads priced |
| event sourcing | src/xstore/ledger/ | FixApproved/FixApplied drive apply and replay |
| idempotency key | src/xstore/stores/*.py, src/xstore/proposals/app.py, src/xstore/ledger/ledger.py | In-store claims; Idempotency-Key header; unique ledger key |
| append-only ledger | src/xstore/ledger/ledger.py, src/xstore/ledger/sql/postgres_append_only.sql | Triggers abort UPDATE/DELETE; hash chain |
| replay | src/xstore/ledger/replay.py, Makefile `replay` | Reseed isolated stores, re-apply, verify hashes |
| dead-letter queue | terraform/aws/modules/cdc_lambda/main.tf, cdc/debezium/*.json, terraform/azure/modules/eventgrid/main.tf | SQS DLQ; Connect DLQ topic; Event Grid dead-letter |
| SQS | terraform/aws/modules/cdc_lambda/main.tf | KMS-encrypted DLQ for the stream mapping |
| Lambda | terraform/aws/modules/cdc_lambda/main.tf, terraform/aws/lambda/handler.py | CDC adapter with ReportBatchItemFailures |
| Azure Functions | terraform/azure/modules/functions/main.tf, terraform/azure/functions/cosmos_change_feed/ | Flex Consumption app; change feed -> Event Hubs |
| Event Grid | terraform/azure/modules/eventgrid/main.tf | BlobCreated on lake -> Event Hubs |
| Entra workload identity | terraform/azure/modules/identity/main.tf, deploy/helm/xstore/values-aks.yaml | Federated credential; pod label + client-id annotation |
| IAM | terraform/aws/modules/iam/main.tf | Least-privilege Lambda role; IRSA reconciler role |
| Terraform | terraform/aws/, terraform/azure/, mk/terraform-*.mk | Two stacks; init -backend=false + validate |
| Helm | deploy/helm/xstore/ | Chart with EKS/AKS overlays |
| Kubernetes | deploy/helm/xstore/templates/, deploy/kind/kind-config.yaml | Deployments, CronJob, HPA, PDB, NetworkPolicy; kind cluster |
| EKS | terraform/aws/modules/eks/main.tf, deploy/helm/xstore/values-eks.yaml | Optional cluster + OIDC; IRSA annotation |
| AKS | terraform/azure/modules/aks/main.tf, deploy/helm/xstore/values-aks.yaml | Optional cluster with OIDC + workload identity |
| GitHub Actions | .github/workflows/ | ci, eval-gate, nightly-eval, contracts, bot-pr, db-setup-checklist |
| OpenTelemetry | src/xstore/observability/tracing.py | Spans: pipeline run -> steps -> agent -> apply |
| Prometheus | src/xstore/observability/metrics.py, observability/prometheus/ | Metrics registry; scrape config + alerts |
| Grafana | observability/grafana/ | Dashboard JSON + provisioning |
| Langfuse | src/xstore/observability/langfuse_hook.py, deploy/compose/compose.yaml | Agent generations; local service |
| Inspect AI | evals/inspect/tiebreak.py | Tiebreak task, custom solver |
| promptfoo | evals/promptfoo/promptfooconfig.yaml | Tiebreak prompt regression |
| FastAPI | src/xstore/proposals/app.py, src/xstore/crm/app.py | Approval API; HubSpot stub |
| Mermaid | docs/ARCHITECTURE.md | Pipeline diagram |
| ADR | docs/ADR-001..026 | Decision records |
