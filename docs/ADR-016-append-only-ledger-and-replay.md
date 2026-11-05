# ADR-016: Event-sourced append-only ledger with hash chain and replay

- Context: approved fixes mutate 8 stores; we must prove what was applied, apply each fix once, and reproduce the result from the frozen snapshot.
- Decision: event sourcing on one `ledger_events` table (SQLite locally, Postgres in real mode) with unique idempotency keys, a sha256 hash chain, and DB triggers that abort UPDATE/DELETE; `apply_pending` writes each FixApproved with store idempotency key `fix:<proposal_id>` and records the read-back hash in FixApplied; `replay` seeds fresh isolated stores from the snapshot and re-applies in seq order.
- Alternative rejected: mutable fix-status rows updated in place; append-only enforced only in application code.
- Why: database triggers hold even for ad-hoc SQL; the hash chain detects edits that bypass triggers; idempotency keys at both ledger and store make crash-and-rerun safe; replay turns "the fixes are deterministic" into a check with an exit code.
- Consequence: corrections are new events, never edits; record hashes cover only the fields a fix wrote, so store metadata (Cosmos `_etag`, `_ts`) does not break replay; replay seeds only stores that fixes touched.
