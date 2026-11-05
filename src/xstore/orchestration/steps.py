"""Reconcile steps shared by the Dagster assets and the Airflow DAG.

Each step takes a `Runtime` (stores, CDC bus, detector view) and returns small JSON-able
values, so orchestrators can pass them between tasks (Dagster IO managers, Airflow XCom).
Locally the runtime is in memory and registered per namespace, so one process must run
the whole graph (Dagster in-process executor, `airflow dags test`).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from xstore.cdc import InProcessBus, pump
from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.evals.detection import Detector, canonical_values
from xstore.evals.snapshot import DEFAULT_DATA, Snapshot, expected_types, load_snapshot
from xstore.matching import CandidatePair, train_and_predict
from xstore.pipeline.demo import POLICY_PATH, REVIEW_CONFIDENCE
from xstore.pipeline.demo import Detector as PolicyDetector
from xstore.policy import PolicyEngine, Resolution, load_policy
from xstore.proposals import Evidence, Fix, Lineage, ProposalCreate
from xstore.stores import Store, get_store

# Stores whose local backends keep JSON records as written (moto and Parquet re-type values).
LOCAL_STORES = ("postgres", "mongodb", "cosmosdb", "hubspot")
GROUP = "xstore-reconcile"
VALUE_KINDS = frozenset({"stale", "typo", "missing", "type_drift", "unit_mismatch"})
MATCH_THRESHOLD = 0.9
DETECTOR = "xstore-orchestration"
Key = tuple[str, str]


@dataclass
class Runtime:
    stores: dict[str, Store]
    bus: InProcessBus = field(default_factory=InProcessBus)
    cursors: dict[str, int] = field(default_factory=dict)
    view: Snapshot = field(default_factory=Snapshot)


_RUNTIMES: dict[str, Runtime] = {}


def runtime(namespace: str = "default", stores: Sequence[str] = LOCAL_STORES) -> Runtime:
    """Process-wide runtime per namespace; stores come from `get_store` (STORE_BACKEND)."""
    if namespace not in _RUNTIMES:
        _RUNTIMES[namespace] = Runtime({s: get_store(s) for s in stores})
    return _RUNTIMES[namespace]


def jsonable(value: Any) -> Any:
    def enc(v: Any) -> Any:
        if isinstance(v, datetime | date):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        raise TypeError(type(v).__name__)

    return json.loads(json.dumps(value, default=enc))


def ingest(
    rt: Runtime, snapshot_dir: str | Path = DEFAULT_DATA, limit: int | None = 50, seed: bool = True
) -> dict[str, int]:
    """Seed stores from the frozen snapshot subset (idempotent writes), or only count records.
    Returns records per store."""
    if seed:
        snap = load_snapshot(Path(snapshot_dir), limit)
        for (name, et), recs in sorted(snap.records.items()):
            if name not in rt.stores:
                continue
            for key, rec in recs.items():
                rt.stores[name].write(et, rec, f"seed:{name}:{et}:{key}")
    return {
        name: sum(sum(1 for _ in store.scan(et)) for et in load_mapping(name).entities)
        for name, store in rt.stores.items()
    }


def cdc_events(rt: Runtime, group: str = GROUP) -> dict[str, int]:
    """Pump store outboxes onto the bus. Returns events published and group lag."""
    published = pump(rt.stores.values(), rt.bus, rt.cursors)
    return {"published": published, "lag": rt.bus.lag(group)}


def _strip_system(store: str, entity_type: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Drop store-added system fields (e.g. Cosmos `_etag`, `_ts`) that are not mapped."""
    mapped = set(load_mapping(store).for_entity(entity_type).fields.values())
    return {k: v for k, v in rec.items() if not k.startswith("_") or k in mapped}


def diff(rt: Runtime, group: str = GROUP) -> dict[str, Any]:
    """Consume the bus into the detector view and compare touched keys across stores in
    canonical form. Returns touched keys and keys whose stores disagree."""
    touched: set[Key] = set()
    for ev in rt.bus.poll(group):
        recs = rt.view.records[(ev.store, ev.entity_type)]
        if ev.after is None:
            recs.pop(ev.key, None)
        else:
            rt.view.add(ev.store, ev.entity_type, _strip_system(ev.store, ev.entity_type, ev.after))
        touched.add((ev.entity_type, ev.key))
    for name in rt.stores:  # every mapped (store, entity) is a holder, even if empty
        for et in load_mapping(name).entities:
            rt.view.records.setdefault((name, et), {})
    conflicting = [k for k in sorted(touched) if _disagrees(rt.view, *k)]
    return {"touched": [list(k) for k in sorted(touched)], "conflicting": len(conflicting)}


def _disagrees(view: Snapshot, entity_type: str, key: str) -> bool:
    holders = view.holders(entity_type, key)
    if len(holders) != len(view.stores_for(entity_type)):
        return True
    canon = [canonical_values(s, entity_type, view.records[(s, entity_type)][key]) for s in holders]
    fields = {f for c in canon for f in c} - {"updated_at"}
    return any(len({json.dumps(c.get(f), default=str) for c in canon}) > 1 for f in fields)


def detect(rt: Runtime, touched: Iterable[Sequence[str]]) -> list[dict[str, Any]]:
    """Baseline detector (xstore.evals) over the view, for touched keys only."""
    det = Detector(rt.view, expected_types(rt.view))
    out = {d for et, key in touched for d in det.detect_key(et, key)}
    return [asdict(d) for d in sorted(out)]


def matches(rt: Runtime, threshold: float = MATCH_THRESHOLD) -> dict[str, Any]:
    """Splink over the view's customers. Returns pair counts and duplicate candidates
    (differing keys judged one entity; the less widely held key is the duplicate)."""
    table = rt.view.customer_table()
    if table.num_rows < 2:
        return {"pairs": 0, "bands": {}, "duplicates": []}
    pairs = train_and_predict(table)
    dups = [_duplicate(rt.view, p) for p in pairs if p.left_key != p.right_key]
    return {
        "pairs": len(pairs),
        "bands": dict(Counter(p.band for p in pairs)),
        "duplicates": [d for d in dups if d["match_probability"] >= threshold],
    }


def _duplicate(view: Snapshot, p: CandidatePair) -> dict[str, Any]:
    nl = len(view.holders("customer", p.left_key))
    nr = len(view.holders("customer", p.right_key))
    left_dup = nl < nr or (nl == nr and p.left_key > p.right_key)
    dup, orig = ((p.left_store, p.left_key), (p.right_store, p.right_key))
    if not left_dup:
        dup, orig = orig, dup
    return {
        "store": dup[0],
        "key": dup[1],
        "original_store": orig[0],
        "original_key": orig[1],
        "match_probability": p.match_probability,
    }


def fix_record(
    store: str, entity_type: str, res: Resolution, current: dict[str, Any] | None
) -> dict[str, Any]:
    """Target-store native record carrying the policy-resolved values."""
    m = load_mapping(store).for_entity(entity_type)
    resolved = jsonable(m.from_canonical(ENTITY_TYPES[entity_type].model_validate(res.record)))
    if current is None:
        return dict(resolved)
    return {**_strip_system(store, entity_type, current), **resolved}


def propose(
    rt: Runtime,
    detections: Iterable[dict[str, Any]],
    duplicates: Iterable[dict[str, Any]],
    client: httpx.Client | TestClient,
    run_id: str,
) -> dict[str, int]:
    """Post value-level fixes (policy-resolved) and duplicate deletes to the proposals API."""
    engine = PolicyEngine(load_policy(POLICY_PATH))
    resolver = PolicyDetector(rt.stores, engine)
    targets: dict[Key, dict[str, str]] = {}
    for d in detections:
        if d["type"] in VALUE_KINDS and d["store"] in rt.stores:
            targets.setdefault((d["entity_type"], d["key"]), {}).setdefault(d["store"], d["type"])
    bodies: list[ProposalCreate] = []
    skipped = 0
    for (et, key), by_store in sorted(targets.items()):
        rows, res, native = resolver.detect(et, key)
        if res is None:
            skipped += 1
            continue
        evidence = [
            Evidence(store=s, entity_type=et, key=key, value=jsonable(v), source="store")
            for s, v in native.items()
        ]
        for store, kind in sorted(by_store.items()):
            try:
                record = fix_record(store, et, res, native.get(store))
            except ValidationError:
                skipped += 1
                continue
            if native.get(store) is not None and record == jsonable(native[store]):
                continue
            conf = min((r.confidence for r in rows if store in r.stores), default=REVIEW_CONFIDENCE)
            bodies.append(
                ProposalCreate(
                    fix=Fix(
                        target_store=store, entity_type=et, key=key, action="upsert", record=record
                    ),
                    evidence=evidence,
                    confidence=conf,
                    lineage=Lineage(
                        run_id=run_id,
                        detector=DETECTOR,
                        detection_type=kind,
                        policy_version=res.policy_version,
                        policy_digest=res.policy_digest,
                        cdc_seq=dict(rt.cursors),
                    ),
                )
            )
    for dup in duplicates:
        if dup["store"] not in rt.stores:
            continue
        bodies.append(
            ProposalCreate(
                fix=Fix(
                    target_store=dup["store"],
                    entity_type="customer",
                    key=dup["key"],
                    action="delete",
                ),
                evidence=[
                    Evidence(store=s, entity_type="customer", key=k, source="matcher")
                    for s, k in (
                        (dup["store"], dup["key"]),
                        (dup["original_store"], dup["original_key"]),
                    )
                ],
                confidence=dup["match_probability"],
                lineage=Lineage(
                    run_id=run_id,
                    detector=DETECTOR,
                    detection_type="duplicate",
                    matcher="splink",
                    match_probability=dup["match_probability"],
                    cdc_seq=dict(rt.cursors),
                ),
            )
        )
    counts = Counter[str]()
    for body in bodies:
        resp = client.post("/proposals", json=body.model_dump(mode="json"))
        counts[
            "created" if resp.status_code == 201 else "existing" if resp.is_success else "errors"
        ] += 1
    safe = client.get("/proposals", params={"status": "pending", "safe": True})
    return {
        "posted": len(bodies),
        "created": counts["created"],
        "existing": counts["existing"],
        "errors": counts["errors"],
        "skipped": skipped,
        "pending_safe": len(safe.json()) if safe.is_success else 0,
    }
