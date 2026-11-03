# Status

## Day 1 (2026-11-03)

Run mode: `STORE_BACKEND=local`, `MODEL_PROVIDER=mock`. No keys needed for anything below.

### PRs (merge in order: #19, #20, #21, #23, #24, #22, #25)

| Issue | PR | Branch | Stacked on | Author | Known gaps |
|---|---|---|---|---|---|
| #1 entities + mappings | #9 (merged) | feat/1-entity-models | main | owner | - |
| #17 bot PR workflow | #18 (merged) | feat/17-bot-pr-workflow | main | owner | - |
| #2 Store protocol, Postgres, MongoDB | #19 | bot/2-store-protocol | main | github-actions[bot] | 2 |
| #3 DynamoDB, Cosmos DB | #20 | bot/3-dynamo-cosmos | #19 | github-actions[bot] | - |
| #4 S3/ADLS Parquet, Iceberg, Snowflake | #21 | bot/4-parquet-adapters | #19 | github-actions[bot] | 2 |
| #5 HubSpot CRM stub | #23 | bot/5-hubspot-crm | #19 | github-actions[bot] | - |
| #6 CDC | #22 | bot/6-cdc | #20 | github-actions[bot] | 2 |
| #7 synthetic data + frozen v1 | #24 | bot/7-synthetic-generator | main | github-actions[bot] | - |
| #8 CI | #25 | bot/8-ci-workflow | main | github-actions[bot] | 2 |

All PRs target `main`; stacked PRs show parent commits until the parent merges. #10-#16 closed, replaced by #19-#25.

### Workflow deviations
- PRs from the owner token and the Claude GitHub app both showed the owner as author. Pushes to `bot/<issue>-<slug>` now open PRs as github-actions[bot] (ADR-010, `.github/workflows/bot-pr.yml`); bodies edited after creation.
- `gh issue create` / `gh pr create` use GraphQL, blocked by the proxy; issues and PRs created via REST (`gh api`).
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
- moto state is process-global; tests/cdc resets it before and after its run.
- Snowflake and HubSpot real-mode `stream_changes` raise NotImplementedError (Snowflake streams / webhook receiver not built).
- Merging #24 after #23 conflicts in Makefile (`crm` vs `freeze`/`verify-frozen` targets); keep both.
- Combined tree of all 8 branches checked locally: ruff, mypy, 11 smoke tests and `make verify-frozen` pass.
