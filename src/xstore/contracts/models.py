"""Data contract schema: one domain-owned data product per store (data mesh)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

FieldType = Literal["string", "integer", "number", "decimal", "timestamp", "boolean"]
Severity = Literal["fail", "warn"]


class _Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )


class ContractField(_Base):
    type: FieldType
    canonical: str
    description: str = ""
    required: bool = False
    primary_key: bool = False
    enum: list[str] | None = None
    unit: str | None = None
    format: str | None = None
    pii: bool = False


class QualityRule(_Base):
    type: Literal["unique", "range", "length", "pattern"]
    field: str
    min: int | float | None = None
    max: int | float | None = None
    pattern: str | None = None


class Freshness(_Base):
    field: str
    max_age_hours: int = Field(gt=0)


class ContractModel(_Base):
    description: str = ""
    table: str
    primary_key: str
    key_prefix: str = ""
    freshness: Freshness
    fields: dict[str, ContractField]
    quality: list[QualityRule] = []


class Contact(_Base):
    name: str
    channel: str


class Info(_Base):
    title: str
    version: str
    owner: str
    domain: str
    description: str = ""
    contact: Contact


class Policy(_Base):
    """What each violation kind does to the pipeline."""

    schema_drift: Severity = "fail"
    missing_field: Severity = "fail"
    enum: Severity = "fail"
    type_drift: Severity = "warn"
    type_drift_threshold: float = Field(default=0.01, ge=0, le=1)
    """Type drift fails when the share of drifted records exceeds this."""


class DataContract(_Base):
    data_contract_specification: str = "1.1.0"
    id: str
    store: str
    info: Info
    policy: Policy = Policy()
    models: dict[str, ContractModel]


def load_contract(path: Path | str) -> DataContract:
    return DataContract.model_validate(yaml.safe_load(Path(path).read_text()))


def contract_for(store: str, contracts_dir: Path = CONTRACTS_DIR) -> DataContract:
    return load_contract(contracts_dir / f"{store}.yaml")


def dump_contract(contract: DataContract) -> str:
    """YAML with defaults omitted, except the header and policy, which stay explicit."""
    data = contract.model_dump(by_alias=True, exclude_defaults=True, mode="json")
    out = {
        "dataContractSpecification": contract.data_contract_specification,
        "id": contract.id,
        "store": contract.store,
        "info": data["info"],
        "policy": contract.policy.model_dump(by_alias=True, mode="json"),
        "models": data["models"],
    }
    return yaml.safe_dump(out, sort_keys=False, width=100)
