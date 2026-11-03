# Status

## Day 1 (2026-11-03)

Run mode: `STORE_BACKEND=local`, `MODEL_PROVIDER=mock`. No keys needed for anything below.

### Open PRs (merge in order: #9, #10, then #14, #15, #13, #12, #16, #11)

| Issue | PR | Branch | Base it is stacked on | Opened via | Known gaps |
|---|---|---|---|---|---|
| #1 entities + mappings | #9 | feat/1-entity-models | main | gh (REST) | - |
| #2 Store protocol, Postgres, MongoDB | #10 | feat/2-store-protocol | #9 | GitHub app | 2 |
| #3 DynamoDB, Cosmos DB | #14 | feat/3-dynamo-cosmos | #10 | gh (REST) | - |
| #4 S3/ADLS Parquet, Iceberg, Snowflake | #15 | feat/4-parquet-adapters | #10 | GitHub app | 2 |
| #5 HubSpot CRM stub | #13 | feat/5-hubspot-crm | #10 | gh (REST) | - |
| #6 CDC | #16 | feat/6-cdc | #14 | GitHub app | 2 |
| #7 synthetic data + frozen v1 | #12 | feat/7-synthetic-generator | #9 | gh (REST) | - |
| #8 CI | #11 | feat/8-ci-workflow | main | GitHub app | 2 |

All PRs target `main`; stacked PRs show parent commits until the parent merges.

### Workflow deviations
- Bot authorship unavailable: PRs opened through the Claude GitHub app connection show author `jahnavi-yelamanchi`, same as GH_TOKEN. Even-numbered PRs (#10, #11, #15, #16) are the "bot" set and carry the Known gaps.
- `gh issue create` / `gh pr create` use GraphQL, blocked by the proxy; issues and PRs were created via REST (`gh api`).
- Branches are stacked, not all cut from main: adapters need the Store protocol, CDC needs the DynamoDB adapter.

### What runs (on each branch)
- `make check`: ruff, mypy, `pytest -m smoke`.
- Local adapters: postgres (SQLite), mongodb (mongomock), dynamodb (moto), cosmosdb (in-memory), s3_parquet (moto), adls_parquet (in-memory), hubspot (FastAPI in-process).
- `make freeze` / `make verify-frozen`: evals/frozen/v1 (10k entities, 500 labels, 100 held out).
- CDC: stores -> outbox -> in-process bus (Avro) -> consumer groups.

### Needs keys (real mode, untested)
- DATABASE_URL, MONGO_URI, AWS_* (+ XSTORE_S3_BUCKET), COSMOS_ENDPOINT + Entra identity, AZURE_STORAGE_ACCOUNT, SNOWFLAKE_*, HUBSPOT_TOKEN.
- azure-cosmos, azure-identity, azure-storage-file-datalake, snowflake-connector-python are optional imports, not dependencies.

### Other gaps
- moto state is process-global; tests/cdc resets it. Once all PRs merge, other moto-backed smoke tests may need the same isolation.
- Snowflake and HubSpot real-mode `stream_changes` raise NotImplementedError (Snowflake streams / webhook receiver not built).
- Smoke tests not run on the combined tree; each branch passes on its own.
