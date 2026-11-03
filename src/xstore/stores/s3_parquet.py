"""S3 Parquet adapter. Local backend: moto (`mock_aws`) in-process, objects via
boto3 put/get of Parquet bytes. Tables are registered as Iceberg in the Glue Data
Catalog and queried by Athena (stores/iceberg, stores/sql/athena_iceberg_ddl.sql)."""

from __future__ import annotations

import os
from typing import Any

import boto3
from moto import mock_aws

from xstore.entities import load_mapping
from xstore.stores.parquet_base import ParquetStore

LOCAL_BUCKET = "xstore-local"
LOCAL_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}


class S3ByteStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def get(self, path: str) -> bytes | None:
        try:
            body: bytes = self.client.get_object(Bucket=self.bucket, Key=path)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        return body

    def put(self, path: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=path, Body=data)

    def ls(self, prefix: str) -> list[str]:
        keys: list[str] = []
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        )
        for page in pages:
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)


class S3ParquetStore(ParquetStore):
    name = "s3_parquet"

    def __init__(self, fs: S3ByteStore, mock: Any = None) -> None:
        super().__init__(fs, load_mapping(self.name))
        self.mock = mock  # keeps moto patched for the store's lifetime


def create_store(backend: str, bucket: str | None = None) -> S3ParquetStore:
    if backend == "local":
        os.environ.update(LOCAL_ENV)
        mock = mock_aws()
        mock.start()
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=bucket or LOCAL_BUCKET)
        return S3ParquetStore(S3ByteStore(client, bucket or LOCAL_BUCKET), mock)
    name = bucket or os.environ.get("XSTORE_S3_BUCKET", "xstore-lake")
    return S3ParquetStore(S3ByteStore(boto3.client("s3"), name))
