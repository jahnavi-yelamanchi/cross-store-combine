"""Bootstrap contracts from stores/mappings/*.yaml plus the canonical Pydantic models."""

from __future__ import annotations

import types
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Union, get_args, get_origin

import annotated_types as at
from pydantic import EmailStr

from xstore.contracts.models import (
    Contact,
    ContractField,
    ContractModel,
    DataContract,
    FieldType,
    Freshness,
    Info,
    QualityRule,
)
from xstore.entities import ENTITY_TYPES, EntityMapping, StoreMapping, all_stores, load_mapping

# store -> (owner team, domain, product description)
OWNERS: dict[str, tuple[str, str, str]] = {
    "hubspot": ("crm-team", "crm", "Contacts and deals managed by sales in HubSpot."),
    "postgres": ("orders-platform", "orders", "System of record for orders and production."),
    "dynamodb": ("orders-platform", "orders", "Single-table checkout state."),
    "mongodb": ("storefront-team", "commerce", "Storefront customer profiles and carts."),
    "cosmosdb": ("supply-chain", "supply-chain", "Warehouse batches and B2B customers."),
    "adls_parquet": ("supply-chain", "supply-chain", "Batch lineage in the Azure lake."),
    "s3_parquet": ("data-platform", "analytics", "Iceberg tables in the AWS lake."),
    "snowflake": ("data-platform", "analytics", "Warehouse marts for reporting."),
}
LAKE_STORES = frozenset({"adls_parquet", "s3_parquet", "snowflake"})
FRESHNESS_HOURS = {"customer": 24, "order": 1, "batch": 6}
PII = frozenset({"email", "phone"})
CANONICAL_UNITS = {"amount": "currency_major", "weight_kg": "kg", "quantity": "each"}
PY_TYPES: dict[Any, FieldType] = {
    str: "string",
    int: "integer",
    float: "number",
    Decimal: "decimal",
    datetime: "timestamp",
    bool: "boolean",
}


def _type_of(annotation: Any) -> tuple[FieldType, bool, list[str] | None, str | None]:
    """(contract type, nullable, enum, format) for a Pydantic field annotation."""
    nullable = False
    if get_origin(annotation) in (Union, types.UnionType):
        args = get_args(annotation)
        nullable = type(None) in args
        annotation = next(a for a in args if a is not type(None))
    if get_origin(annotation) is Literal:
        return "string", nullable, [str(v) for v in get_args(annotation)], None
    if annotation is EmailStr:
        return "string", nullable, None, "email"
    return PY_TYPES[annotation], nullable, None, None


def _model(store: str, entity_type: str, m: EntityMapping) -> ContractModel:
    cls = ENTITY_TYPES[entity_type]
    pk = m.fields[cls.key_field]
    fields: dict[str, ContractField] = {}
    quality: list[QualityRule] = [QualityRule(type="unique", field=pk)]
    if m.key_prefix:
        quality.append(QualityRule(type="pattern", field=pk, pattern=f"^{m.key_prefix}"))
    for canon, native in m.fields.items():
        info = cls.model_fields[canon]
        ftype, nullable, enum, fmt = _type_of(info.annotation)
        unit = CANONICAL_UNITS.get(canon)
        if canon in m.units:
            unit = m.units[canon]
            ftype = "integer" if unit == "cents" else "number"
        if enum and canon in m.values:
            reverse = {v: k for k, v in m.values[canon].items()}
            enum = [reverse.get(v, v) for v in enum]
        for meta in info.metadata:
            if isinstance(meta, at.Ge):
                quality.append(QualityRule(type="range", field=native, min=meta.ge))
            elif isinstance(meta, at.MaxLen) and canon != cls.key_field:
                lo = next((x.min_length for x in info.metadata if isinstance(x, at.MinLen)), None)
                quality.append(
                    QualityRule(type="length", field=native, min=lo, max=meta.max_length)
                )
        fields[native] = ContractField(
            type=ftype,
            canonical=f"{cls.__name__}.{canon}",
            required=not nullable,
            primary_key=native == pk,
            enum=enum,
            unit=unit,
            format=fmt,
            pii=canon in PII,
        )
    hours = 24 if store in LAKE_STORES else FRESHNESS_HOURS[entity_type]
    return ContractModel(
        description=f"{cls.__name__} records as {store} stores them natively.",
        table=m.container,
        primary_key=pk,
        key_prefix=m.key_prefix,
        freshness=Freshness(field=m.fields["updated_at"], max_age_hours=hours),
        fields=fields,
        quality=quality,
    )


def contract_from_mapping(mapping: StoreMapping) -> DataContract:
    owner, domain, description = OWNERS[mapping.store]
    return DataContract(
        id=f"urn:datacontract:xstore:{mapping.store}",
        store=mapping.store,
        info=Info(
            title=f"{mapping.store} data product",
            version="1.0.0",
            owner=owner,
            domain=domain,
            description=description,
            contact=Contact(name=owner, channel=f"#{owner}"),
        ),
        models={et: _model(mapping.store, et, m) for et, m in mapping.entities.items()},
    )


def generate_all() -> dict[str, DataContract]:
    return {s: contract_from_mapping(load_mapping(s)) for s in all_stores()}
