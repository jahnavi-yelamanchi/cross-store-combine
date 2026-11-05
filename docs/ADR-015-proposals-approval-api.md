# ADR-015: Proposals as evidence and lineage records behind an approval API

- Context: detected inconsistencies turn into store writes; a bad write costs more than a missed fix, so each fix needs a reason, a source trail and a decision.
- Decision: a Pydantic `Proposal` carries the fix (full native record or delete), evidence, confidence and lineage (run, detector, matcher, model, policy digest, CDC offsets); a FastAPI app stores it via SQLAlchemy (SQLite locally, Postgres via DATABASE_URL) and exposes approve, reject and approve-safe; ids are sha256 of fix + run_id + detection_type.
- Alternative rejected: random ids with dedupe queries; agents writing to stores directly and logging afterwards.
- Why: content-hash ids make detection reruns idempotent (200 on resubmit); compare-and-set on status plus `Idempotency-Key` replay applies a fix at most once; only high-confidence upserts are auto-safe, deletes always need a person.
- Consequence: a changed fix in the same run is a new proposal; `on_approve` is the one hook that applies writes, and a raise there returns the proposal to pending.
