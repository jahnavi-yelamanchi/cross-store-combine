"""Per-store field mappings: native record <-> canonical entity."""

from __future__ import annotations

from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from xstore.entities.models import ENTITY_TYPES, Entity

MAPPINGS_DIR = Path(__file__).resolve().parents[3] / "stores" / "mappings"

Unit = Literal["cents", "lb"]
LB_PER_KG = 2.20462


def _to_canonical_unit(value: Any, unit: Unit) -> Any:
    if value is None:
        return None
    if unit == "cents":
        return Decimal(int(value)) / 100
    return float(value) / LB_PER_KG


def _from_canonical_unit(value: Any, unit: Unit) -> Any:
    if value is None:
        return None
    if unit == "cents":
        return int(Decimal(value) * 100)
    return round(float(value) * LB_PER_KG, 4)


class EntityMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: str
    key: str
    key_prefix: str = ""
    fields: dict[str, str]
    units: dict[str, Unit] = {}
    values: dict[str, dict[str, str]] = {}

    def native_key(self, canonical_key: str) -> str:
        return f"{self.key_prefix}{canonical_key}"

    def to_canonical(self, entity_type: str, record: dict[str, Any]) -> Entity:
        data: dict[str, Any] = {}
        for canon, native in self.fields.items():
            if native not in record:
                continue
            value = record[native]
            if canon in self.units:
                value = _to_canonical_unit(value, self.units[canon])
            if canon in self.values and value is not None:
                value = self.values[canon].get(str(value), value)
            data[canon] = value
        key_field = ENTITY_TYPES[entity_type].key_field
        if self.key_prefix and isinstance(data.get(key_field), str):
            data[key_field] = data[key_field].removeprefix(self.key_prefix)
        return ENTITY_TYPES[entity_type].model_validate(data)

    def from_canonical(self, entity: Entity) -> dict[str, Any]:
        record: dict[str, Any] = {}
        reverse_values = {f: {v: k for k, v in m.items()} for f, m in self.values.items()}
        for canon, native in self.fields.items():
            value = getattr(entity, canon)
            if canon in self.units:
                value = _from_canonical_unit(value, self.units[canon])
            if canon in reverse_values and value is not None:
                value = reverse_values[canon].get(str(value), value)
            if canon == entity.key_field:
                value = self.native_key(str(value))
            record[native] = value
        return record


class StoreMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: str
    entities: dict[str, EntityMapping]

    def for_entity(self, entity_type: str) -> EntityMapping:
        if entity_type not in self.entities:
            raise KeyError(f"{self.store} does not hold entity type {entity_type!r}")
        return self.entities[entity_type]


@cache
def load_mapping(store: str, mappings_dir: Path = MAPPINGS_DIR) -> StoreMapping:
    raw = yaml.safe_load((mappings_dir / f"{store}.yaml").read_text())
    return StoreMapping.model_validate(raw)


def all_stores(mappings_dir: Path = MAPPINGS_DIR) -> list[str]:
    return sorted(p.stem for p in mappings_dir.glob("*.yaml"))
