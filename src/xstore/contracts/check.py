"""Check native records against a store's data contract; report drift, never coerce."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from xstore.contracts.models import (
    CONTRACTS_DIR,
    ContractField,
    DataContract,
    FieldType,
    Severity,
    contract_for,
    load_contract,
)
from xstore.stores.base import Store

ViolationKind = Literal["schema_drift", "type_drift", "missing_field", "enum"]
Record = dict[str, Any]
MAX_SAMPLES = 5
_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")

__all__ = [
    "EntityReport",
    "Violation",
    "check_native",
    "check_records",
    "check_snapshot",
    "check_store",
    "load_contract",
    "report",
]


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    store: str
    entity_type: str
    field: str
    """Native field; a suspected rename reads `old->new`."""
    count: int
    sample_keys: list[str]
    total: int = 0
    severity: Severity = "warn"


class EntityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    store: str
    entity_type: str
    owner: str
    records: int
    violations: list[Violation]

    @property
    def failed(self) -> bool:
        return any(v.severity == "fail" for v in self.violations)


def type_ok(ftype: FieldType, value: Any) -> bool:
    """Whether a native value is an acceptable encoding of the contract type."""
    if isinstance(value, bool):
        return ftype == "boolean"
    match ftype:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int)
        case "number":
            return isinstance(value, int | float | Decimal)
        case "decimal":
            return isinstance(value, int | Decimal) or (
                isinstance(value, str) and bool(_DECIMAL.match(value))
            )
        case "timestamp":
            if isinstance(value, datetime):
                return True
            if not isinstance(value, str):
                return False
            try:
                datetime.fromisoformat(value)
            except ValueError:
                return False
            return True
        case "boolean":
            return False


def _pair_renames(
    absent: list[str], unknown: list[str], record: Record, fields: Mapping[str, ContractField]
) -> list[tuple[str, str]]:
    """Greedy (old, new) pairs: a declared field gone and an unknown one of a compatible type."""
    scored = sorted(
        (
            (SequenceMatcher(None, old.lower(), new.lower()).ratio(), old, new)
            for old in absent
            for new in unknown
            if record[new] is None or type_ok(fields[old].type, record[new])
        ),
        reverse=True,
    )
    pairs: list[tuple[str, str]] = []
    used: set[str] = set()
    for _, old, new in scored:
        if old not in used and new not in used:
            pairs.append((old, new))
            used.update((old, new))
    return pairs


def check_records(
    contract: DataContract, entity_type: str, records: Iterable[Record]
) -> list[Violation]:
    """Aggregate violations per (kind, field); severity comes from the contract policy."""
    model = contract.models[entity_type]
    fields = model.fields
    hits: dict[tuple[ViolationKind, str], list[str]] = {}
    total = 0

    def hit(kind: ViolationKind, field: str, key: str) -> None:
        hits.setdefault((kind, field), []).append(key)

    for rec in records:
        total += 1
        key = str(rec.get(model.primary_key, "")).removeprefix(model.key_prefix)
        unknown = [f for f in rec if f not in fields]
        absent = [f for f in fields if f not in rec]
        for old, new in _pair_renames(absent, unknown, rec, fields):
            hit("schema_drift", f"{old}->{new}", key)
            absent.remove(old)
            unknown.remove(new)
        for f in unknown:
            hit("schema_drift", f, key)
        for f in absent:
            if fields[f].required:
                hit("missing_field", f, key)
        for name, spec in fields.items():
            if name not in rec:
                continue
            value = rec[name]
            if value is None:
                if spec.required:
                    hit("missing_field", name, key)
            elif not type_ok(spec.type, value):
                hit("type_drift", name, key)
            elif spec.enum is not None and str(value) not in spec.enum:
                hit("enum", name, key)

    policy = contract.policy
    out: list[Violation] = []
    for (kind, field), keys in sorted(hits.items()):
        severity: Severity = getattr(policy, kind)
        if kind == "type_drift" and len(keys) > policy.type_drift_threshold * total:
            severity = "fail"
        out.append(
            Violation(
                kind=kind,
                store=contract.store,
                entity_type=entity_type,
                field=field,
                count=len(keys),
                sample_keys=keys[:MAX_SAMPLES],
                total=total,
                severity=severity,
            )
        )
    return out


def report(contract: DataContract, entity_type: str, records: list[Record]) -> EntityReport:
    return EntityReport(
        store=contract.store,
        entity_type=entity_type,
        owner=contract.info.owner,
        records=len(records),
        violations=check_records(contract, entity_type, records),
    )


def check_store(store: Store, contract: DataContract) -> list[EntityReport]:
    """Scan every entity type the contract declares from a live Store."""
    return [report(contract, et, list(store.scan(et))) for et in contract.models]


def check_native(
    data: Mapping[str, Mapping[str, list[Record]]], contracts_dir: Path = CONTRACTS_DIR
) -> list[EntityReport]:
    """Check {store: {entity_type: records}}; stores without a contract are a schema drift."""
    out: list[EntityReport] = []
    for store in sorted(data):
        contract = contract_for(store, contracts_dir)
        for et in sorted(data[store]):
            if et not in contract.models:
                recs = data[store][et]
                v = Violation(
                    kind="schema_drift",
                    store=store,
                    entity_type=et,
                    field="<undeclared entity>",
                    count=len(recs),
                    sample_keys=[],
                    total=len(recs),
                    severity=contract.policy.schema_drift,
                )
                out.append(
                    EntityReport(
                        store=store,
                        entity_type=et,
                        owner=contract.info.owner,
                        records=len(recs),
                        violations=[v],
                    )
                )
                continue
            out.append(report(contract, et, list(data[store][et])))
    return out


def load_snapshot(snapshot_dir: Path) -> dict[str, dict[str, list[Record]]]:
    """Native records from a frozen snapshot: <store>/<entity_type>.jsonl."""
    data: dict[str, dict[str, list[Record]]] = {}
    for path in sorted(Path(snapshot_dir).glob("*/*.jsonl")):
        lines = path.read_text().splitlines()
        data.setdefault(path.parent.name, {})[path.stem] = [json.loads(x) for x in lines if x]
    return data


def check_snapshot(
    snapshot_dir: Path | str, contracts_dir: Path = CONTRACTS_DIR
) -> list[EntityReport]:
    return check_native(load_snapshot(Path(snapshot_dir)), contracts_dir)
