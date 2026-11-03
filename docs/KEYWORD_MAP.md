# Keyword map

Paths are as of the branch that adds them (PR in brackets).

| Keyword | Path(s) | Usage |
|---|---|---|
| Pydantic | src/xstore/entities/models.py, src/xstore/stores/base.py (#9, #19) | Canonical entities, ChangeEvent, WriteResult |
| Postgres | src/xstore/stores/postgres.py, stores/mappings/postgres.yaml (#19) | SQLAlchemy adapter; SQLite locally, DATABASE_URL in real mode |
| MongoDB | src/xstore/stores/mongodb.py, stores/mappings/mongodb.yaml (#19) | pymongo adapter; mongomock locally |
| DynamoDB | src/xstore/stores/dynamodb.py (#20) | Single-table adapter on moto, TransactWriteItems |
| DynamoDB Streams | src/xstore/stores/dynamodb.py, src/xstore/cdc/dynamodb_streams.py (#20, #22) | NEW_AND_OLD_IMAGES spec; stream record -> ChangeEvent |
| Cosmos DB | src/xstore/stores/cosmosdb.py (#20) | In-memory container locally; azure-cosmos in real mode |
| change feed | src/xstore/stores/cosmosdb.py, src/xstore/cdc/cosmos_change_feed.py (#20, #22) | LSN-ordered feed; trigger documents -> ChangeEvent |
| S3 | src/xstore/stores/s3_parquet.py (#21) | Parquet lake on moto S3 |
| Athena | stores/sql/athena_iceberg_ddl.sql (#21) | Iceberg table DDL |
| Glue Data Catalog | stores/sql/athena_iceberg_ddl.sql (#21) | `xstore` database for Athena tables |
| ADLS Gen2 | src/xstore/stores/adls_parquet.py (#21) | Parquet lake; in-memory locally |
| Synapse | stores/sql/synapse_openrowset.sql (#21) | Serverless OPENROWSET over ADLS Parquet |
| Snowflake | src/xstore/stores/snowflake.py, stores/mappings/snowflake.yaml (#21) | Real-mode-only adapter, MERGE writes |
| Iceberg | src/xstore/stores/iceberg_spec.py, stores/iceberg/*.json (#21) | Generated v2 table specs |
| Parquet | src/xstore/stores/parquet_base.py (#21) | Append-only delta files |
| Arrow | src/xstore/stores/parquet_base.py (#21) | Arrow schema from canonical models |
| CDC | src/xstore/stores/base.py, src/xstore/cdc/ (#19, #22) | Outbox per store, ChangeEvent bus |
| Debezium | cdc/debezium/*.json (#22) | Postgres pgoutput and MongoDB change-stream connectors |
| Kafka | src/xstore/cdc/bus.py, cdc/debezium/*.json (#22) | Bus with Kafka partition/offset semantics; Connect configs |
| Schema Registry | cdc/schema-registry/subjects.yaml, src/xstore/cdc/avro.py (#22) | BACKWARD subjects; local registry |
| Avro | cdc/schema-registry/change_event.avsc, src/xstore/cdc/avro.py (#22) | Confluent wire format codec |
| dead-letter queue | cdc/debezium/*.json, src/xstore/cdc/dynamodb_streams.py (#22) | Connect DLQ topic; batchItemFailures |
| Lambda | src/xstore/cdc/dynamodb_streams.py (#22) | Event-source-mapping handler shape |
| Azure Functions | src/xstore/cdc/cosmos_change_feed.py (#22) | cosmosDBTrigger handler shape |
| Entra workload identity | src/xstore/stores/cosmosdb.py (#20) | DefaultAzureCredential, no keys |
| idempotency key | src/xstore/stores/*.py, src/xstore/crm/app.py (#19, #23, #20, #21) | Claimed in-store per write; Idempotency-Key header |
| FastAPI | src/xstore/crm/app.py (#23) | HubSpot-shaped CRM stub |
| GitHub Actions | .github/workflows/ci.yml (#25) | ruff, mypy, smoke |
| ADR | docs/ADR-001..010 | Decision records |
