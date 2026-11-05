# Status

## Day 3 (2026-11-05)

Run mode: `STORE_BACKEND=local`, `MODEL_PROVIDER=mock`. PRs open, not merged.

| Issue | PR | What |
|---|---|---|
| #42 dbt, GX, OpenLineage + Day 3 base commit | #56 | Merge first: carries deps and the `mk/*.mk` include the others build on |
| #43 Dagster + Airflow | #53 | Assets, checks, schedule, CDC-lag sensor; Airflow mirror |
| #44 data contracts | #50 | contracts/*.yaml, check CLI, drift issues, contracts.yml |
| #45 observability | #51 | OTel spans, Prometheus metrics, Grafana, Langfuse hook |
| #46 terraform/aws | #54 | MSK, RDS, DynamoDB, S3+Glue+Athena, Lambda, SQS DLQ, IAM, EKS |
| #47 terraform/azure | #55 | Event Hubs, Cosmos, ADLS, Synapse, Functions, Event Grid, Entra, AKS |
| #48 compose, Helm, kind | #52 | `make up` (not run), Helm EKS/AKS overlays, kind config |
| #49 docs | (this PR) | README, ARCHITECTURE, SETUP, `make bootstrap`, KEYWORD_MAP, STATUS, ADR-026 |

Merge order: #56, then any order. Combined tree of all eight branches merged locally without conflicts.

### What runs (combined tree)
- ruff, mypy (79 files), `pytest` 25 passed (24 smoke + 1 e2e).
- `make dbt-build`: PASS=59 WARN=1 (planted orphans) ERROR=0; `make gx-check` passes; `make quality` writes OpenLineage START/COMPLETE.
- `make dagster-validate` passes; `make airflow-check` (py_compile).
- `make contracts-check` finds all 62 schema_drift + 62 type_drift labels in frozen v1 (exits 1 by design); clean synthetic data passes.
- `make helm-lint` (strict, default/EKS/AKS) and `make compose-validate` (`docker compose config`) pass; `terraform fmt -check` passes.
- All 72 keywords verified in code/config (docs/KEYWORD_MAP.md).

### Not validated
- `terraform init/validate/plan` (AWS and Azure): proxy blocks registry.terraform.io (403). No `.terraform.lock.hcl`. Run `make tf-aws-validate` / `make tf-azure-validate` with registry access.
- kubeconform: binary download blocked; manifests only `helm lint` + `helm template`.
- Nothing started: no containers, cluster, broker, scheduler, cloud resource.
- GitHub Actions: account billing lock; no workflow has run. No bot-authored PRs possible; all PRs from the owner token; no deliberate gaps.

### Needs keys / setup (real mode)
- See docs/SETUP.md; `make bootstrap` checks per-layer env vars and writes `.env`.
- Debezium Avro converter jars under deploy/compose/connect-plugins/ before connectors register.
- Postgres driver (psycopg) not declared; required for DATABASE_URL in real mode and multi-replica API.
- Lambda layer with fastavro/pydantic/pyyaml (`lambda_layer_arns`).

### Gaps
- Eval gate still fails Inspect accuracy 0.78 < 0.90 (mock provider); needs recorded real fixtures.
- AWS Lambda decodes CDC into an in-process bus; no forwarding to MSK yet. Azure Function forwards JSON, not Avro.
- Nothing writes the Delta table Synapse reads; no matcher span in traces (demo doesn't call the matcher).
- dbt reconciles customers and orders only (batches staged, not reconciled); recency rule not modelled in dbt.
- Contracts nightly job fails by design on the frozen snapshot (opens issues); add continue-on-error if a green job is preferred.
- Day 1 known gaps from #21, #22, #25 still on main.

## Day 2 (2026-11-05)

Run mode: `STORE_BACKEND=local`, `MODEL_PROVIDER=mock`. All Day 2 PRs merged by the owner account.

| Issue | PR | What |
|---|---|---|
| #33 eval workflows + Day 2 deps | #34 | eval-gate.yml, nightly-eval.yml, thresholds, `scripts/eval_gate.py` |
| #29 proposals API | #35 | FastAPI approve/reject/approve-safe, evidence + lineage |
| #28 policy engine | #36 | policy/policy.yaml, deterministic resolve, approved policy diffs |
| #27 tiebreak agent | #37 | LangGraph + Instructor + MCP evidence tool; Haiku 5.5 default |
| #26 Splink matcher | #38 | Fellegi-Sunter on DuckDB, EM, match/tiebreak bands |
| #30 ledger | #39 | Append-only event-sourced ledger, idempotent apply, `make replay` |
| #32 e2e + demo | #40 | `make demo-local`, `make e2e` |
| #31 evals | #41 | Inspect AI task, promptfoo config, matrix runner, report + plot |

### What runs
- `make check` (ruff, mypy, 17 smoke tests), `make e2e` (1 test, ~1.4 s), `make demo-local` (recall 1.0, 10 fixes applied, 0 bad writes, replay ok).
- `make match` (60,165 pairs, 214 in tiebreak, ~4 s), `make policy-check`, `make replay` (~85 s on full snapshot), `make eval` + `make report` (~32 s per split).
- Holdout (simulated LLM): hybrid Haiku F1 0.952 at $0.0016/1k entities; Sonnet F1 0.980 at $0.032/1k; splink_only 0.873; bad-write rate 0.

### Eval gate status: FAILING
- Inspect tiebreak accuracy 0.78 vs threshold 0.90. MockProvider gives drifted same-key pairs confidence 0.5; validate step routes < 0.6 to "different, needs human". Not tuned away; needs recorded real fixtures (`python -m xstore.agent.record_fixtures`, real mode).
- All other gate checks pass.

### Deviations
- GitHub Actions did not run: "account is locked due to a billing issue". No bot-authored PRs possible (bot-pr.yml needs Actions), so all PRs use the owner token (rule 2c) and no deliberate gaps were added (rule 2d applies to bot PRs only). `type:gap` labels on #26/#28/#30/#32 are moot.
- Checks run locally before every merge: ruff, mypy, smoke, e2e.
- No commits backdated; real timestamps only.
- mcp 2.3 removed `mcp.server.fastmcp`; agent uses `mcp.server.mcpserver.MCPServer`.
- Starlette TestClient is now httpx2-based; HubSpot adapter client typed as `Any`.

### Needs keys (real mode, untested)
- ANTHROPIC_API_KEY (tiebreak, fixture recording, promptfoo), OPENAI_API_KEY (optional provider; `openai` not a declared dep), DATABASE_URL (proposals, ledger + append-only trigger SQL), MONGO_URI/MONGO_DB.

### Gaps
- Day 1 known gaps from #21, #22, #25 still on main.
- Ledger write prices in evals/prices.yaml are placeholders (marked).
- promptfoo not run (needs node + key).
- Splink non_match band empty in practice (prediction threshold 0.01 drops low pairs).
- Evals detector targets injector patterns; recall 1.0 on every type is optimistic.

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
