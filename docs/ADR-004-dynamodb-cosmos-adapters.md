# ADR-004: DynamoDB and Cosmos DB adapters with in-store idempotency and local change logs

- Context: DynamoDB and Cosmos DB must satisfy the ADR-003 `Store` contract locally without cloud accounts; DynamoDB Streams and the Cosmos DB change feed are unavailable or partial offline.
- Decision: DynamoDB on moto, one table holding data, `IDEMPOTENCY#<key>` and `STREAM#<seq>` items written in one TransactWriteItems; Cosmos DB on in-memory containers with an `_xstore_applied` container and an `_lsn`-ordered change feed; real mode uses the boto3 default chain and Entra workload identity via DefaultAzureCredential.
- Alternative rejected: moto's DynamoDB Streams emulation and the Cosmos DB emulator container.
- Why: moto stream support is partial and the emulator is heavy in CI; a ChangeEvent log has the same shape `stream_record_to_change_event` produces from real stream records.
- Consequence: real Cosmos latest-version change feed has no deletes or create/update split, so delete drift there comes from scans; issue #6 swaps the DynamoDB stream log for real DynamoDB Streams.
