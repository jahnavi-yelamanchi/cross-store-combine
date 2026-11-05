# Status

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
