"""Deterministic inconsistency injection into native store records, with ground-truth labels."""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from xstore.entities import ENTITY_TYPES, EntityMapping, load_mapping
from xstore.entities.mapping import LB_PER_KG
from xstore.synth.generator import Native, Record

InjectionType = Literal[
    "duplicate",
    "stale",
    "typo",
    "missing",
    "type_drift",
    "orphaned",
    "schema_drift",
    "unit_mismatch",
]
TYPES: tuple[InjectionType, ...] = (
    "duplicate",
    "stale",
    "typo",
    "missing",
    "type_drift",
    "orphaned",
    "schema_drift",
    "unit_mismatch",
)
STALE_FIELD = {"customer": "email", "order": "status", "batch": "quantity"}
TYPO_FIELDS = ("name", "email")
DRIFT_FIELDS = {"order": ("amount",), "batch": ("quantity", "weight_kg")}
RENAMES = {
    "customer": {"email": "email_address", "phone": "phone_number"},
    "order": {"currency": "currency_code"},
    "batch": {"warehouse": "warehouse_code", "sku": "sku_code"},
}
LETTERS = "abcdefghijklmnopqrstuvwxyz"


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    type: InjectionType
    store: str
    entity_type: str
    key: str
    field: str | None = None
    original: Any = None
    injected: Any = None


Target = tuple[str, str, str | None]


def _candidates(data: Native, kind: InjectionType) -> list[Target]:
    out: list[Target] = []
    for store in sorted(data):
        mapping = load_mapping(store)
        for etype in sorted(data[store]):
            m = mapping.for_entity(etype)
            fields: list[str | None]
            if kind in ("duplicate", "missing"):
                fields = [None]
            elif kind == "stale":
                fields = [STALE_FIELD[etype]]
            elif kind == "typo":
                fields = list(TYPO_FIELDS) if etype == "customer" else []
            elif kind == "type_drift":
                fields = list(DRIFT_FIELDS.get(etype, ()))
            elif kind == "orphaned":
                fields = ["customer_id"] if etype == "order" else []
            elif kind == "schema_drift":
                fields = list(RENAMES[etype])
            else:
                fields = list(m.units)
            out.extend((store, etype, f) for f in fields)
    return out


def _typo(rng: random.Random, text: str) -> str:
    local, at, domain = text.partition("@")
    while True:
        chars = list(local)
        i = rng.randrange(len(chars))
        op = rng.choice(("swap", "drop", "replace", "insert"))
        if op == "swap" and i < len(chars) - 1:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        elif op == "drop" and len(chars) > 2:
            del chars[i]
        elif op == "replace":
            chars[i] = rng.choice(LETTERS)
        else:
            chars.insert(i, rng.choice(LETTERS))
        out = "".join(chars) + at + domain
        if out != text:
            return out


class _Injector:
    def __init__(self, data: Native, seed: int) -> None:
        self.data = data
        self.rng = random.Random(seed)
        self.used: set[tuple[str, str, str]] = set()

    def key_of(self, store: str, etype: str, rec: Record) -> str:
        m = load_mapping(store).for_entity(etype)
        native = rec[m.fields[ENTITY_TYPES[etype].key_field]]
        return str(native).removeprefix(m.key_prefix)

    def pick(self, store: str, etype: str) -> tuple[int, Record, str]:
        records = self.data[store][etype]
        while True:
            idx = self.rng.randrange(len(records))
            key = self.key_of(store, etype, records[idx])
            if (store, etype, key) not in self.used:
                self.used.add((store, etype, key))
                return idx, records[idx], key

    def apply(self, lid: str, kind: InjectionType) -> Label:
        store, etype, canon = self.rng.choice(_candidates(self.data, kind))
        m = load_mapping(store).for_entity(etype)
        idx, rec, key = self.pick(store, etype)
        field = m.fields[canon] if canon else None
        original: Any = rec[field] if field else None
        injected: Any
        if kind == "duplicate":
            injected = self.duplicate(store, etype, m, idx, rec, key)
            original = key
        elif kind == "stale":
            injected = self.stale(etype, m, rec, canon or "")
        elif kind == "typo":
            injected = _typo(self.rng, str(original))
        elif kind == "missing":
            original = self.data[store][etype].pop(idx)
            injected = None
        elif kind == "type_drift":
            injected = self.drift(canon or "", original)
        elif kind == "orphaned":
            injected = f"CX{self.rng.randrange(10**6):06d}"
        elif kind == "schema_drift":
            injected = RENAMES[etype][canon or ""]
            original = field
            assert field is not None
            rec[injected] = rec.pop(field)
        else:
            injected = self.unit(m, canon or "", original)
        if kind in ("stale", "typo", "type_drift", "orphaned", "unit_mismatch"):
            assert field is not None
            rec[field] = injected
        return Label(
            id=lid,
            type=kind,
            store=store,
            entity_type=etype,
            key=key,
            field=field,
            original=original,
            injected=injected,
        )

    def duplicate(
        self, store: str, etype: str, m: EntityMapping, idx: int, rec: Record, key: str
    ) -> str:
        new_key = f"{key[0]}D{self.rng.randrange(10**6):06d}"
        while (store, etype, new_key) in self.used:
            new_key = f"{key[0]}D{self.rng.randrange(10**6):06d}"
        self.used.add((store, etype, new_key))
        copy = dict(rec)
        copy[m.fields[ENTITY_TYPES[etype].key_field]] = m.native_key(new_key)
        updated = m.fields["updated_at"]
        copy[updated] = rec[updated] + timedelta(seconds=self.rng.randint(1, 600))
        if etype == "customer":
            name = m.fields["name"]
            copy[name] = str(rec[name]).lower()
        self.data[store][etype].insert(idx + 1, copy)
        return new_key

    def stale(self, etype: str, m: EntityMapping, rec: Record, canon: str) -> Any:
        entity = m.to_canonical(etype, rec)
        value = getattr(entity, canon)
        if canon == "email":
            old = f"{value.partition('@')[0]}@legacy.example.com"
        elif canon == "status":
            old = self.rng.choice([s for s in ("pending", "paid", "shipped") if s != value])
        else:
            old = value + self.rng.randint(1, 50)
        older = entity.updated_at - timedelta(days=self.rng.randint(30, 180))
        native = m.from_canonical(entity.model_copy(update={canon: old, "updated_at": older}))
        updated = m.fields["updated_at"]
        rec[updated] = native[updated]
        return native[m.fields[canon]]

    def drift(self, canon: str, value: Any) -> Any:
        if isinstance(value, int):
            return f"{value}.0" if canon == "quantity" else str(value)
        if isinstance(value, Decimal):
            return float(value)
        return str(value)

    def unit(self, m: EntityMapping, canon: str, value: Any) -> Any:
        if m.units[canon] == "cents":
            return int(value) // 100
        return round(float(value) / LB_PER_KG, 4)


def inject(data: Native, n: int = 500, seed: int = 0) -> list[Label]:
    """Mutate `data` in place with `n` inconsistencies balanced across TYPES."""
    inj = _Injector(data, seed)
    kinds = [TYPES[i % len(TYPES)] for i in range(n)]
    inj.rng.shuffle(kinds)
    return [inj.apply(f"inj-{i:04d}", kind) for i, kind in enumerate(kinds)]
