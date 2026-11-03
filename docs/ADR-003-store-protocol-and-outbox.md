# ADR-003: Store protocol on native records with idempotency table and outbox

- Context: 8 heterogeneous stores must be read, diffed, fixed and replayed through one interface.
- Decision: `Store` protocol (read, scan, write, delete, stream_changes) over store-native dicts; writes take an idempotency key recorded in the store; local adapters emit changes to an outbox in the same write.
- Alternative rejected: protocol over canonical entities; CDC only via Debezium.
- Why: native records keep type and schema drift visible to detection; idempotency in the store survives process restarts; the outbox gives CDC semantics without a broker.
- Consequence: callers map native <-> canonical via `EntityMapping`; real mode swaps the outbox for Debezium.
