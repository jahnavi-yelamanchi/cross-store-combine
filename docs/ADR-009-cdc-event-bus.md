# ADR-009: One ChangeEvent envelope over Avro, in-process bus mirroring Kafka

- Context: CDC comes from Debezium (Postgres, MongoDB), DynamoDB Streams and the Cosmos DB change feed; no broker may run here.
- Decision: every source maps to one `ChangeEvent` envelope, Avro-encoded in Confluent wire format against a schema registry; an in-process bus keeps Kafka semantics (keyed partitions, consumer-group offsets, lag).
- Alternative rejected: per-source event shapes; plain Python queue without serialization.
- Why: detectors consume one schema; encoding on the local bus catches schema breaks the same way Redpanda + Schema Registry would.
- Consequence: Debezium configs unwrap to the envelope via SMTs; real deployment needs a small mapper per connector topic.
