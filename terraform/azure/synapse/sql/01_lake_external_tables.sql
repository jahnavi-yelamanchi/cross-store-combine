-- Synapse serverless SQL: external objects over the ADLS Gen2 `lake` filesystem.
-- Run as a member of the workspace Entra admin group. No credential objects:
-- serverless SQL passes the caller's Entra token to storage (no keys, no SAS).
-- Replace <account> with the lake storage account (output lake_abfss_root).
CREATE DATABASE xstore COLLATE Latin1_General_100_BIN2_UTF8;
GO
USE xstore;
GO

CREATE EXTERNAL DATA SOURCE lake
WITH (LOCATION = 'abfss://lake@<account>.dfs.core.windows.net');
GO

CREATE EXTERNAL FILE FORMAT parquet_snappy
WITH (FORMAT_TYPE = PARQUET, DATA_COMPRESSION = 'org.apache.hadoop.io.compress.SnappyCodec');
GO

CREATE SCHEMA lake;
GO

-- Append-only Parquet deltas written by xstore.stores.adls_parquet
-- (`<container>/data/<seq>-<uuid>.parquet`, one row per commit, `_op` c/u/d).
CREATE EXTERNAL TABLE lake.batch_deltas (
  batch_id varchar(64),
  sku varchar(64),
  quantity bigint,
  weight_kg float,
  warehouse varchar(64),
  produced_at datetime2,
  expires_at datetime2,
  updated_at datetime2,
  _op char(1)
)
WITH (
  LOCATION = 'lake/batch/data/*.parquet',
  DATA_SOURCE = lake,
  FILE_FORMAT = parquet_snappy
);
GO

-- Current state: latest delta per key, deletes dropped. File names sort by seq.
CREATE VIEW lake.batch_current AS
WITH ranked AS (
  SELECT r.*,
         ROW_NUMBER() OVER (PARTITION BY r.batch_id ORDER BY r.filepath() DESC) AS rn
  FROM OPENROWSET(
    BULK 'lake/batch/data/*.parquet',
    DATA_SOURCE = 'lake',
    FORMAT = 'PARQUET'
  ) AS r
)
SELECT batch_id, sku, quantity, weight_kg, warehouse, produced_at, expires_at, updated_at
FROM ranked
WHERE rn = 1 AND _op <> 'd';
GO
