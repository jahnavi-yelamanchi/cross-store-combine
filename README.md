# Cross-Store Reconciliation Agent

The same business entity lives in Postgres, MongoDB, DynamoDB/Cosmos DB, S3/ADLS Parquet and a CRM. Detect drift via CDC, match entities probabilistically with an LLM tiebreak, propose fixes with evidence, apply idempotently after human approval, keep a replayable ledger.

Status: see [docs/STATUS.md](docs/STATUS.md).
