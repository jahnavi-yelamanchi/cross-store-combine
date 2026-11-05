"""Deterministic baseline detector for the 8 injected inconsistency types.

Per (entity_type, key) it compares each store's native record, renamed to canonical fields
(no Pydantic validation, so drift survives), against the other stores holding that key:

- missing: key present in >= 2 stores but absent from another store holding the entity type.
- schema_drift: a mapped native field is absent from the record (renamed column).
- type_drift: a value's JSON type differs from the store's modal type for that field.
- unit_mismatch: numeric value, after the store's declared unit conversion, is ~1/100 or
  ~1/2.20462 of the peer consensus.
- orphaned: order.customer_id not found among any store's customers.
- stale: value differs from the peer consensus and the record's updated_at is older.
- typo: string differs from a consensus of >= 2 peers by edit distance <= 2.
- duplicate: orders/batches whose content (minus key and updated_at) equals another key in
  the same store; customers from matcher pairs judged the same entity with different keys.

It is a baseline, not a perfect detector; misses and false positives are measured.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.entities.mapping import LB_PER_KG
from xstore.evals.snapshot import Snapshot, expected_types, jtype
from xstore.matching import CandidatePair

TYPES = (
    "duplicate",
    "stale",
    "typo",
    "missing",
    "type_drift",
    "orphaned",
    "schema_drift",
    "unit_mismatch",
)
NUMERIC = {"amount", "quantity", "weight_kg"}
UNIT_RATIOS = (100.0, LB_PER_KG)
MAX_TYPO_DISTANCE = 2


@dataclass(frozen=True, order=True)
class Detection:
    type: str
    store: str
    entity_type: str
    key: str
    field: str | None = None


def edit_distance(a: str, b: str, cap: int = MAX_TYPO_DISTANCE + 1) -> int:
    """Levenshtein distance, stopping early once every cell exceeds `cap`."""
    if abs(len(a) - len(b)) >= cap:
        return cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) >= cap:
            return cap
        prev = cur
    return min(prev[-1], cap)


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def canonical_values(store: str, entity_type: str, record: dict[str, Any]) -> dict[str, Any]:
    """Native record -> canonical field values: units converted, enum values mapped,
    numerics parsed to float, strings stripped. Absent fields are omitted."""
    m = load_mapping(store).for_entity(entity_type)
    out: dict[str, Any] = {}
    for canon, native in m.fields.items():
        value = record.get(native)
        if value is None:
            continue
        if canon in NUMERIC:
            num = _num(value)
            if num is None:
                continue
            unit = m.units.get(canon)
            value = num / 100 if unit == "cents" else num / LB_PER_KG if unit == "lb" else num
        else:
            value = str(value).strip()
            value = m.values.get(canon, {}).get(value, value)
        out[canon] = value
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 0.005 + 1e-6 * abs(b)
    return bool(a == b)


def _consensus(values: list[Any]) -> tuple[Any, int]:
    """Most common value (numbers rounded to cents) and its support."""
    norm = [round(v, 2) if isinstance(v, float) else v for v in values]
    value, support = Counter(norm).most_common(1)[0]
    return values[norm.index(value)], support


def _is_unit_ratio(mine: float, consensus: float) -> bool:
    if mine <= 0 or consensus <= 0:
        return False
    ratio = consensus / mine
    return any(abs(ratio - r) / r < 0.05 for r in UNIT_RATIOS)


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class Detector:
    """Detection over a Snapshot. `types` is the inferred store schema (modal JSON types);
    pass a precomputed one when the snapshot is filled incrementally (streaming)."""

    def __init__(
        self, snapshot: Snapshot, types: dict[tuple[str, str, str], str] | None = None
    ) -> None:
        self.snap = snapshot
        self.types = types if types is not None else expected_types(snapshot)

    def customer_known(self, key: str) -> bool:
        return any(
            key in self.snap.records[(s, "customer")] for s in self.snap.stores_for("customer")
        )

    def detect_all(self) -> set[Detection]:
        out: set[Detection] = set()
        for et in ENTITY_TYPES:
            for key in sorted(self.snap.keys(et)):
                out.update(self.detect_key(et, key))
        return out

    def detect_key(self, entity_type: str, key: str) -> list[Detection]:
        snap = self.snap
        recs = {s: snap.records[(s, entity_type)][key] for s in snap.holders(entity_type, key)}
        out = self._content_duplicates(entity_type, key, recs)
        if len(recs) < 2:
            return out
        for s in snap.stores_for(entity_type):
            if s not in recs:
                out.append(Detection("missing", s, entity_type, key))
        canon: dict[str, dict[str, Any]] = {}
        stamps: dict[str, datetime] = {}
        for s, rec in recs.items():
            m = load_mapping(s).for_entity(entity_type)
            for native in m.fields.values():
                if native not in rec:
                    out.append(Detection("schema_drift", s, entity_type, key, native))
            for native, value in rec.items():
                want = self.types.get((s, entity_type, native))
                if value is not None and want is not None and jtype(value) != want:
                    out.append(Detection("type_drift", s, entity_type, key, native))
            canon[s] = canonical_values(s, entity_type, rec)
            if (stamp := _ts(canon[s].get("updated_at"))) is not None:
                stamps[s] = stamp
        newest = max(stamps.values(), default=None)
        key_field = ENTITY_TYPES[entity_type].key_field
        for s in recs:
            native_of = load_mapping(s).for_entity(entity_type).fields
            older = newest is not None and s in stamps and stamps[s] < newest
            for f, mine in canon[s].items():
                if f in (key_field, "updated_at"):
                    continue
                if (
                    entity_type == "order"
                    and f == "customer_id"
                    and not self.customer_known(str(mine))
                ):
                    out.append(Detection("orphaned", s, entity_type, key, native_of[f]))
                    continue
                peers = [c[f] for o, c in canon.items() if o != s and f in c]
                if not peers:
                    continue
                consensus, support = _consensus(peers)
                if _same(mine, consensus):
                    continue
                kind: str | None = None
                if isinstance(mine, float) and isinstance(consensus, float):
                    if _is_unit_ratio(mine, consensus):
                        kind = "unit_mismatch"
                    elif older:
                        kind = "stale"
                elif older:
                    kind = "stale"
                elif (
                    isinstance(mine, str)
                    and support >= 2
                    and edit_distance(mine, str(consensus)) <= MAX_TYPO_DISTANCE
                ):
                    kind = "typo"
                if kind:
                    out.append(Detection(kind, s, entity_type, key, native_of[f]))
        return out

    def _content_duplicates(
        self, entity_type: str, key: str, recs: dict[str, dict[str, Any]]
    ) -> list[Detection]:
        if entity_type == "customer":
            return []
        out = []
        for s, rec in recs.items():
            for other in self.snap.same_content(s, entity_type, rec) - {key}:
                out.append(
                    Detection("duplicate", s, entity_type, self._original(entity_type, key, other))
                )
        return out

    def _original(self, entity_type: str, a: str, b: str) -> str:
        """Of two keys for one entity, the one held by more stores (the injected copy is not)."""
        na, nb = len(self.snap.holders(entity_type, a)), len(self.snap.holders(entity_type, b))
        if na != nb:
            return a if na > nb else b
        return min(a, b)


def pair_duplicates(
    snap: Snapshot, pairs: Iterable[CandidatePair], same: Callable[[CandidatePair], bool]
) -> set[Detection]:
    """Customer duplicates from matcher pairs with differing keys judged the same entity.
    Reported on the store holding the less widely held key, keyed by the other key."""
    out: set[Detection] = set()
    for p in pairs:
        if p.left_key == p.right_key or not same(p):
            continue
        nl = len(snap.holders("customer", p.left_key))
        nr = len(snap.holders("customer", p.right_key))
        if nl < nr or (nl == nr and p.left_key > p.right_key):
            out.add(Detection("duplicate", p.left_store, "customer", p.right_key))
        else:
            out.add(Detection("duplicate", p.right_store, "customer", p.left_key))
    return out
