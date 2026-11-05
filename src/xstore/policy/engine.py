"""Deterministic conflict policy: load policy.yaml, resolve per-field values across stores."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from xstore.entities.mapping import _to_canonical_unit, all_stores, load_mapping
from xstore.entities.models import ENTITY_TYPES

Strategy = Literal["source_priority", "recency"]
Rule = Literal["only_source", "agreement", "field_override", "recency", "source_priority"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Recency(_Frozen):
    field: str = "updated_at"
    prefer: Literal["newest", "oldest"] = "newest"
    tie_breaker: Literal["source_priority"] = "source_priority"
    max_staleness_days: int | None = Field(default=None, ge=0)


class EntityDefaults(_Frozen):
    strategy: Strategy = "source_priority"
    source_priority: list[str] = Field(min_length=1)
    recency: Recency = Recency()


class FieldRule(_Frozen):
    strategy: Strategy = "source_priority"
    source_priority: list[str] = Field(min_length=1)


class UnitNormalization(_Frozen):
    canonical_units: dict[str, str] = {}
    source_units_from: str = "stores/mappings"


class TypeCoercion(_Frozen):
    numeric_strings: bool = True
    datetime_strings: bool = True
    strip_strings: bool = True
    lowercase_fields: list[str] = []
    numeric_tolerance: Decimal = Decimal("0")


class RequireApproval(_Frozen):
    deletes: Literal[True] = True
    sensitive_fields: list[str] = []


class DiffProposals(_Frozen):
    min_overrides: int = Field(default=3, ge=1)
    proposed_by: str = "heuristic"


class AppliedChange(_Frozen):
    diff_id: str
    path: str
    approved_by: str
    version: int


class Policy(_Frozen):
    version: int = Field(ge=1)
    defaults: dict[str, EntityDefaults]
    field_overrides: dict[str, dict[str, FieldRule]] = {}
    unit_normalization: UnitNormalization = UnitNormalization()
    type_coercion: TypeCoercion = TypeCoercion()
    require_approval: RequireApproval = RequireApproval()
    diff_proposals: DiffProposals = DiffProposals()
    changes: list[AppliedChange] = []

    @model_validator(mode="after")
    def _known_entities_and_stores(self) -> Policy:
        stores = set(all_stores())
        for et in (*self.defaults, *self.field_overrides):
            if et not in ENTITY_TYPES:
                raise ValueError(f"unknown entity type {et!r}")
        priorities = [d.source_priority for d in self.defaults.values()] + [
            r.source_priority for rules in self.field_overrides.values() for r in rules.values()
        ]
        for prio in priorities:
            if unknown := set(prio) - stores:
                raise ValueError(f"unknown stores in source_priority: {sorted(unknown)}")
            if len(set(prio)) != len(prio):
                raise ValueError(f"duplicate stores in source_priority: {prio}")
        for et, rules in self.field_overrides.items():
            for f in rules:
                if f not in ENTITY_TYPES[et].model_fields:
                    raise ValueError(f"unknown field {et}.{f}")
        return self

    def digest(self) -> str:
        """sha256 of canonical JSON (sorted keys, no whitespace)."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


def load_policy(path: str | Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(Path(path).read_text()))


def check_policy(policy: Policy) -> list[str]:
    """Cross-check the policy against store mappings; returns problems (empty = ok)."""
    problems: list[str] = []
    for store in all_stores():
        for et, em in load_mapping(store).entities.items():
            for f, unit in em.units.items():
                if f"{et}.{f}" not in policy.unit_normalization.canonical_units:
                    problems.append(f"{store}.{et}.{f} has unit {unit!r} but no canonical unit")
    for et, d in policy.defaults.items():
        holders = {s for s in all_stores() if et in load_mapping(s).entities}
        if extra := set(d.source_priority) - holders:
            problems.append(f"defaults.{et} lists stores without {et}: {sorted(extra)}")
        if missing := holders - set(d.source_priority):
            problems.append(f"defaults.{et} missing stores: {sorted(missing)}")
    return problems


class FieldDecision(_Frozen):
    field: str
    value: Any
    source_store: str | None
    rule: Rule
    candidates: dict[str, Any]
    requires_approval: bool = False


class Resolution(_Frozen):
    entity_type: str
    key: str
    decisions: dict[str, FieldDecision]
    record: dict[str, Any]
    conflicts: list[str]
    policy_version: int
    policy_digest: str


class PolicyEngine:
    """Pure resolver over canonical-field observations from several stores."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self._digest = policy.digest()
        self._adapters: dict[tuple[str, str], TypeAdapter[Any]] = {}

    def to_canonical_unit(self, store: str, entity_type: str, field: str, value: Any) -> Any:
        """Convert a native value to canonical units using the store mapping's declared unit."""
        unit = load_mapping(store).for_entity(entity_type).units.get(field)
        return value if unit is None else _to_canonical_unit(value, unit)

    def requires_approval(
        self, entity_type: str, field: str | None = None, op: str = "set"
    ) -> bool:
        ra = self.policy.require_approval
        if op == "delete":
            return ra.deletes
        return f"{entity_type}.{field}" in ra.sensitive_fields

    def normalize(self, entity_type: str, field: str, value: Any) -> Any:
        """Type coercion only; wrong-unit values are not guessed."""
        tc = self.policy.type_coercion
        if value is None:
            return None
        if isinstance(value, str):
            if tc.strip_strings:
                value = value.strip()
            if f"{entity_type}.{field}" in tc.lowercase_fields:
                value = value.lower()
            if value == "":
                return None
        model_fields = ENTITY_TYPES[entity_type].model_fields
        if field not in model_fields or model_fields[field].annotation is None:
            return value
        ann: Any = model_fields[field].annotation
        if isinstance(value, str):
            kind = _kind(ann)
            if (kind == "number" and not tc.numeric_strings) or (
                kind == "datetime" and not tc.datetime_strings
            ):
                return value
        adapter = self._adapters.get((entity_type, field))
        if adapter is None:
            adapter = self._adapters[(entity_type, field)] = TypeAdapter(ann)
        try:
            return adapter.validate_python(value)
        except ValidationError:
            return value

    def _rank(self, prio: list[str], store: str) -> tuple[int, str]:
        return (prio.index(store) if store in prio else len(prio), store)

    def _same(self, a: Any, b: Any) -> bool:
        na, nb = _as_decimal(a), _as_decimal(b)
        if na is not None and nb is not None:
            return abs(na - nb) <= self.policy.type_coercion.numeric_tolerance
        return bool(a == b)

    def resolve(
        self, entity_type: str, key: str, observations: Mapping[str, dict[str, Any]]
    ) -> Resolution:
        if entity_type not in self.policy.defaults:
            raise KeyError(f"no policy defaults for entity type {entity_type!r}")
        d = self.policy.defaults[entity_type]
        rec_field = d.recency.field
        obs = {
            s: {f: self.normalize(entity_type, f, v) for f, v in r.items()}
            for s, r in sorted(observations.items())
        }
        stamps = {s: r[rec_field] for s, r in obs.items() if isinstance(r.get(rec_field), datetime)}
        fresh = set(obs)
        if d.recency.max_staleness_days is not None and stamps:
            cutoff = max(stamps.values()) - timedelta(days=d.recency.max_staleness_days)
            fresh = {s for s in obs if s not in stamps or stamps[s] >= cutoff}

        order = list(ENTITY_TYPES[entity_type].model_fields)
        fields = sorted({f for r in obs.values() for f in r}, key=lambda f: _field_pos(order, f))
        decisions: dict[str, FieldDecision] = {}
        conflicts: list[str] = []
        for f in fields:
            cands = {s: r[f] for s, r in obs.items() if r.get(f) is not None}
            if not cands:
                decisions[f] = FieldDecision(
                    field=f, value=None, source_store=None, rule="only_source", candidates={}
                )
                continue
            override = self.policy.field_overrides.get(entity_type, {}).get(f)
            prio = (override or d).source_priority
            strategy = (override or d).strategy
            if f == rec_field:
                strategy = "recency"
            by_prio = sorted(cands, key=lambda s: self._rank(prio, s))
            agree = all(self._same(cands[by_prio[0]], cands[s]) for s in by_prio[1:])
            rule: Rule
            if len(cands) == 1:
                store, rule = by_prio[0], "only_source"
            elif agree:
                store, rule = by_prio[0], "agreement"
            else:
                conflicts.append(f)
                eligible = [s for s in by_prio if s in fresh] or by_prio
                if strategy == "recency":
                    store, tied = self._newest(eligible, stamps, d.recency.prefer)
                    rule = "source_priority" if tied else "recency"
                else:
                    store = eligible[0]
                    rule = "field_override" if override else "source_priority"
            decisions[f] = FieldDecision(
                field=f,
                value=cands[store],
                source_store=store,
                rule=rule,
                candidates=cands,
                requires_approval=rule not in ("only_source", "agreement")
                and self.requires_approval(entity_type, f),
            )
        return Resolution(
            entity_type=entity_type,
            key=key,
            decisions=decisions,
            record={f: dec.value for f, dec in decisions.items()},
            conflicts=conflicts,
            policy_version=self.policy.version,
            policy_digest=self._digest,
        )

    @staticmethod
    def _newest(
        stores_by_prio: list[str], stamps: dict[str, datetime], prefer: str
    ) -> tuple[str, bool]:
        """Pick by timestamp; ties (or missing stamps) fall back to priority order."""
        dated = [s for s in stores_by_prio if s in stamps]
        if not dated:
            return stores_by_prio[0], True
        pick = max if prefer == "newest" else min
        best = pick(stamps[s] for s in dated)
        winners = [s for s in dated if stamps[s] == best]
        return winners[0], len(winners) > 1


def _field_pos(order: list[str], f: str) -> tuple[int, str]:
    return (order.index(f) if f in order else len(order), f)


def _kind(ann: Any) -> str:
    text = str(ann)
    if any(t in text for t in ("Decimal", "float", "int")):
        return "number"
    if "datetime" in text:
        return "datetime"
    return "other"


def _as_decimal(v: Any) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, int | float | Decimal):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None
