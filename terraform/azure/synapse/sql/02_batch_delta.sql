-- Delta Lake table at lake/batch_delta/ (compacted batch state; one row per
-- batch_id; a Delta writer such as Spark or delta-rs MERGEs the Parquet deltas
-- into it; that writer is outside this repo). Serverless SQL reads
-- the _delta_log, so readers see the latest committed snapshot and never a
-- half-written file. Run after 01_lake_external_tables.sql.
USE xstore;
GO

-- Delta view: OPENROWSET FORMAT = 'DELTA' points at the table root folder.
-- Partition columns (none here) would be inferred from the log.
CREATE VIEW lake.batch_delta AS
SELECT batch_id, sku, quantity, weight_kg, warehouse, produced_at, expires_at, updated_at
FROM OPENROWSET(
  BULK 'lake/batch_delta/',
  DATA_SOURCE = 'lake',
  FORMAT = 'DELTA'
)
WITH (
  batch_id varchar(64),
  sku varchar(64),
  quantity bigint,
  weight_kg float,
  warehouse varchar(64),
  produced_at datetime2,
  expires_at datetime2,
  updated_at datetime2
) AS d;
GO

-- Delta external table: same snapshot through a DELTA file format.
CREATE EXTERNAL FILE FORMAT delta_lake
WITH (FORMAT_TYPE = DELTA);
GO

CREATE EXTERNAL TABLE lake.batch_delta_ext (
  batch_id varchar(64),
  sku varchar(64),
  quantity bigint,
  weight_kg float,
  warehouse varchar(64),
  produced_at datetime2,
  expires_at datetime2,
  updated_at datetime2
)
WITH (
  LOCATION = 'lake/batch_delta/',
  DATA_SOURCE = lake,
  FILE_FORMAT = delta_lake
);
GO

-- Drift check: rows where the Delta snapshot and the Parquet-derived current
-- state disagree (lagging compaction or a missed MERGE).
CREATE VIEW lake.batch_delta_drift AS
SELECT COALESCE(c.batch_id, d.batch_id) AS batch_id,
       CASE WHEN d.batch_id IS NULL THEN 'missing_in_delta'
            WHEN c.batch_id IS NULL THEN 'deleted_upstream'
            ELSE 'value_mismatch' END AS kind
FROM lake.batch_current AS c
FULL OUTER JOIN lake.batch_delta AS d ON d.batch_id = c.batch_id
WHERE d.batch_id IS NULL
   OR c.batch_id IS NULL
   OR c.quantity <> d.quantity
   OR c.weight_kg <> d.weight_kg
   OR c.warehouse <> d.warehouse
   OR c.updated_at <> d.updated_at;
GO
