"""ADLS Gen2 Parquet adapter. Local backend: in-memory dict object store.
Real backend: azure-storage-file-datalake (optional) with DefaultAzureCredential.
Query path: Synapse serverless SQL OPENROWSET over the abfss:// path
(stores/sql/synapse_openrowset.sql)."""

from __future__ import annotations

import os
from typing import Any

from xstore.entities import load_mapping
from xstore.stores.parquet_base import ByteStore, DictByteStore, ParquetStore


class AdlsByteStore:
    def __init__(self, fs_client: Any) -> None:
        self.fs_client = fs_client

    def get(self, path: str) -> bytes | None:
        file = self.fs_client.get_file_client(path)
        if not file.exists():
            return None
        data: bytes = file.download_file().readall()
        return data

    def put(self, path: str, data: bytes) -> None:
        self.fs_client.get_file_client(path).upload_data(data, overwrite=True)

    def ls(self, prefix: str) -> list[str]:
        directory = prefix.rstrip("/")
        if not self.fs_client.get_directory_client(directory).exists():
            return []
        return sorted(
            p.name
            for p in self.fs_client.get_paths(path=directory, recursive=True)
            if not p.is_directory and p.name.startswith(prefix)
        )


class AdlsParquetStore(ParquetStore):
    name = "adls_parquet"

    def __init__(self, fs: ByteStore) -> None:
        super().__init__(fs, load_mapping(self.name))


def create_store(backend: str, filesystem: str = "xstore") -> AdlsParquetStore:
    if backend == "local":
        return AdlsParquetStore(DictByteStore())
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient
    except ImportError as e:
        raise RuntimeError(
            "adls_parquet real mode needs azure-identity and azure-storage-file-datalake"
        ) from e
    account = os.environ["AZURE_STORAGE_ACCOUNT"]
    service = DataLakeServiceClient(
        f"https://{account}.dfs.core.windows.net", credential=DefaultAzureCredential()
    )
    return AdlsParquetStore(AdlsByteStore(service.get_file_system_client(filesystem)))
