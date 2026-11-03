# Keyword map

Paths are as of the branch that adds them (PR in brackets).

| Keyword | Path(s) | Usage |
|---|---|---|
| Pydantic | src/xstore/entities/models.py, src/xstore/stores/base.py (#9, #10) | Canonical entities, ChangeEvent, WriteResult |
| Postgres | src/xstore/stores/postgres.py, stores/mappings/postgres.yaml (#10) | SQLAlchemy adapter; SQLite locally, DATABASE_URL in real mode |
| MongoDB | src/xstore/stores/mongodb.py, stores/mappings/mongodb.yaml (#10) | pymongo adapter; mongomock locally |
| DynamoDB | src/xstore/stores/dynamodb.py (#14) | Single-table adapter on moto, TransactWriteItems |
| DynamoDB Streams | src/xstore/stores/dynamodb.py, src/xstore/cdc/dynamodb_streams.py (#14, #16) | NEW_AND_OLD_IMAGES spec; stream record -> ChangeEvent |
| Cosmos DB | src/xstore/stores/cosmosdb.py (#14) | In-memory container locally; azure-cosmos in real mode |
| change feed | src/xstore/stores/cosmosdb.py, src/xstore/cdc/cosmos_change_feed.py (#14, #16) | LSN-ordered feed; trigger documents -> ChangeEvent |
| S3 | src/xstore/stores/s3_parquet.py (#15) | Parquet lake on moto S3 |
| Athena | stores/sql/athena_iceberg_ddl.sql (#15) | Iceberg table DDL |
| Glue Data Catalog | stores/sql/athena_iceberg_ddl.sql (#15) | `xstore` database for Athena tables |
| ADLS Gen2 | src/xstore/stores/adls_parquet.py (#15) | Parquet lake; in-memory locally |
| Synapse | stores/sql/synapse_openrowset.sql (#15) | Serverless OPENROWSET over ADLS Parquet |
| Snowflake | src/xstore/stores/snowflake.py, stores/mappings/snowflake.yaml (#15) | Real-mode-only adapter, MERGE writes |
| Iceberg | src/xstore/stores/iceberg_spec.py, stores/iceberg/*.json (#15) | Generated v2 table specs |
| Parquet | src/xstore/stores/parquet_base.py (#15) | Append-only delta files |
| Arrow | src/xstore/stores/parquet_base.py (#15) | Arrow schema from canonical models |
| CDC | src/xstore/stores/base.py, src/xstore/cdc/ (#10, #16) | Outbox per store, ChangeEvent bus |
| Debezium | cdc/debezium/*.json (#16) | Postgres pgoutput and MongoDB change-stream connectors |
| Kafka | src/xstore/cdc/bus.py, cdc/debezium/*.json (#16) | Bus with Kafka partition/offset semantics; Connect configs |
| Schema Registry | cdc/schema-registry/subjects.yaml, src/xstore/cdc/avro.py (#16) | BACKWARD subjects; local registry |
| Avro | cdc/schema-registry/change_event.avsc, src/xstore/cdc/avro.py (#16) | Confluent wire format codec |
| dead-letter queue | cdc/debezium/*.json, src/xstore/cdc/dynamodb_streams.py (#16) | Connect DLQ topic; batchItemFailures |
| Lambda | src/xstore/cdc/dynamodb_streams.py (#16) | Event-source-mapping handler shape |
| Azure Functions | src/xstore/cdc/cosmos_change_feed.py (#16) | cosmosDBTrigger handler shape |
| Entra workload identity | src/xstore/stores/cosmosdb.py (#14) | DefaultAzureCredential, no keys |
| idempotency key | src/xstore/stores/*.py, src/xstore/crm/app.py (#10, #13, #14, #15) | Claimed in-store per write; Idempotency-Key header |
| FastAPI | src/xstore/crm/app.py (#13) | HubSpot-shaped CRM stub |
| GitHub Actions | .github/workflows/ci.yml (#11) | ruff, mypy, smoke |
| ADR | docs/ADR-001..009 | Decision records |
