# ADR-005: Lake stores as append-only Parquet deltas with an Iceberg spec

- Context: S3 and ADLS Gen2 hold entities as Parquet with no update-in-place; reconciliation still needs read/write/delete, idempotency and CDC; Athena (Glue Data Catalog) and Synapse serverless SQL query them; Snowflake has no local emulator.
- Decision: one Parquet file per commit at `<container>/data/<seq>-<uuid>.parquet` holding one delta row with an `_op` column; current state is the last delta per key; idempotency keys and the change log live in `_xstore/manifest.json`; Arrow schema and Iceberg v2 spec (day(updated_at) partition, key sort) are generated from the canonical model; Snowflake is a real-mode-only MERGE stub.
- Alternative rejected: copy-on-write snapshot rewrite per commit; pyarrow S3FileSystem against moto; pyiceberg catalog writes.
- Why: deltas never rewrite data so a commit is one put plus a manifest update; boto3 bytes route through moto in-process where S3FileSystem does not; a generated spec cannot drift from the model.
- Consequence: reads replay all deltas (O(files), compaction needed at scale); single writer per container since the manifest is not conditionally updated; Iceberg tables are registered from the spec, not written by the adapter.
