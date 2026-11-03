-- Synapse serverless SQL over ADLS Gen2 Parquet deltas: latest non-deleted row per batch.
WITH deltas AS (
  SELECT r.*, r.filepath() AS file_path
  FROM OPENROWSET(
    BULK 'abfss://xstore@<account>.dfs.core.windows.net/lake/batch/data/*.parquet',
    FORMAT = 'PARQUET'
  ) AS r
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY batch_id ORDER BY file_path DESC) AS rn
  FROM deltas
)
SELECT batch_id, sku, quantity, weight_kg, warehouse, produced_at, expires_at, updated_at
FROM ranked
WHERE rn = 1 AND _op <> 'd';
