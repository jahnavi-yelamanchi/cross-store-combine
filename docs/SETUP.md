# Setup

## Local (default, no keys)

```bash
uv sync
make check        # ruff, mypy, smoke tests
make e2e          # inject -> detect -> propose -> approve -> apply -> replay
make demo-local   # same loop, prints the detection table
```

Every run uses `STORE_BACKEND=local` and `MODEL_PROVIDER=mock`:

| Store | Local backend |
|---|---|
| Postgres | SQLite (SQLAlchemy) |
| MongoDB | mongomock |
| DynamoDB, S3 | moto |
| Cosmos DB, ADLS Gen2 | in-memory |
| HubSpot | FastAPI stub, in-process |
| Snowflake | none (real mode only) |
| LLM | MockProvider: recorded fixtures, else labelled simulated verdicts |

`make up` starts the compose stack (Postgres, MongoDB, Redpanda, Debezium, Schema Registry, DynamoDB Local, MinIO, Dagster, Langfuse, Prometheus, Grafana). Needs Docker; not run in CI.

## Real mode

Set the variables for the layers you use, then `make bootstrap` (writes `.env` with `STORE_BACKEND=real`). `make local` switches back.

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | tiebreak agent, fixture recording, promptfoo |
| `OPENAI_API_KEY` | optional OpenAI provider (`openai` not a declared dependency) |
| `XSTORE_TIEBREAK_MODEL`, `XSTORE_PROMPT_CACHING` | model alias/id, caching on/off |
| `DATABASE_URL` | Postgres store, proposals API, ledger |
| `MONGO_URI`, `MONGO_DB` | MongoDB store |
| `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (or role) | DynamoDB, S3, Athena, Glue |
| `XSTORE_S3_BUCKET` | S3 Parquet lake |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_FEDERATED_TOKEN_FILE` | Entra workload identity (DefaultAzureCredential) |
| `COSMOS_ENDPOINT`, `AZURE_STORAGE_ACCOUNT` | Cosmos DB, ADLS Gen2 |
| `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_PASSWORD` or key | Snowflake store, dbt `snowflake` target |
| `HUBSPOT_TOKEN` | HubSpot CRM |
| `KAFKA_BOOTSTRAP_SERVERS` | Dagster CDC-lag sensor |
| `OPENLINEAGE_URL` | OpenLineage HTTP transport (e.g. Marquez) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP export |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Langfuse agent traces |
| `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `XSTORE_OPEN_ISSUES=1` | contract-drift issues |

Optional SDKs for real mode, not declared dependencies: `azure-cosmos`, `azure-identity`, `azure-storage-file-datalake`, `snowflake-connector-python`, `confluent-kafka`, `opentelemetry-exporter-otlp`, `openai`.

## Cloud

```bash
make plan-aws     # terraform/aws: init -backend=false, plan (needs AWS credentials)
make plan-azure   # terraform/azure (needs ARM_* / az login)
```

Fixtures for the mock provider are re-recorded in real mode with `python -m xstore.agent.record_fixtures --pairs <jsonl>`.
