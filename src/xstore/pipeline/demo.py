"""Local end-to-end demo: seed, inject, detect over CDC, propose, approve, apply, replay.

Usage: python -m xstore.pipeline.demo --customers 200 --injections 20 --seed 7
Exits 1 if any applied fix read back differently (bad write) or replay fails.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import random
import shutil
import sys
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

from fastapi.testclient import TestClient
from pydantic import BaseModel

from xstore.cdc import InProcessBus, pump
from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.ledger import FixApproved, Ledger, apply_pending, create_ledger
from xstore.ledger.replay import replay
from xstore.observability.instrument import observe_detect, observe_lag, observe_proposal
from xstore.observability.tracing import set_attrs, span
from xstore.policy import PolicyEngine, Resolution, load_policy
from xstore.proposals import Evidence, Fix, Lineage, Proposal, ProposalCreate, SqlRepository
from xstore.proposals.app import create_app
from xstore.stores import Store
from xstore.stores.seed import load_snapshot, record_key, seed_stores, to_native
from xstore.synth.freeze import _line
from xstore.synth.generator import Native, generate
from xstore.synth.injector import InjectionType, Label, _candidates, _Injector

POLICY_PATH = Path(__file__).resolve().parents[3] / "policy" / "policy.yaml"
DEFAULT_STORES = ("postgres", "mongodb", "cosmosdb")
ENTITIES = ("customer", "batch")  # order skipped: Decimal amounts lose type in JSON snapshots
VALUE_KINDS: tuple[InjectionType, ...] = ("stale", "typo", "missing", "type_drift", "unit_mismatch")
RULE_CONFIDENCE = {
    "agreement": 0.95,
    "field_override": 0.95,
    "only_source": 0.8,
    "recency": 0.8,
    "source_priority": 0.8,
}
REVIEW_CONFIDENCE = 0.5  # sensitive fields (policy require_approval) never auto-approve
DETECTOR = "cdc-policy"
GROUP = "detector"


class Detection(BaseModel):
    type: InjectionType
    entity_type: str
    key: str
    stores: list[str]
    field: str | None
    values: dict[str, Any]
    rule: str
    resolved: Any = None
    confidence: float


class DemoReport(BaseModel):
    run_id: str
    injections: int
    effective_injections: int
    observable_injections: int
    detections: list[Detection]
    recall: float
    undetected: list[str]
    proposals: int
    safe_proposals: int
    approved: int
    applied: int
    reapplied: int
    failed: int
    bad_writes: int
    replay_ok: bool
    replay_fixes: int
    remaining_conflicts: int
    remaining_total: int
    cdc_events: int


def _jsonable(value: Any) -> Any:
    def enc(v: Any) -> Any:
        if isinstance(v, datetime | date):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        raise TypeError(type(v).__name__)

    return json.loads(json.dumps(value, default=enc))


def _write_snapshot(out: Path, data: Native) -> None:
    if out.exists():
        shutil.rmtree(out)
    for store, by_type in data.items():
        (out / store).mkdir(parents=True, exist_ok=True)
        for etype, recs in by_type.items():
            (out / store / f"{etype}.jsonl").write_text("".join(_line(r) for r in recs))


def _numeric(entity_type: str, field: str) -> bool:
    ann = ENTITY_TYPES[entity_type].model_fields[field].annotation
    return bool({int, float, Decimal} & (set(get_args(ann)) or {ann}))


def _holders(stores: dict[str, Store], entity_type: str) -> list[str]:
    return [s for s in stores if entity_type in load_mapping(s).entities]


class Detector:
    """Reads one key from every store, resolves it under the policy, reports conflicts."""

    def __init__(self, stores: dict[str, Store], engine: PolicyEngine) -> None:
        self.stores = stores
        self.engine = engine

    def observe(self, entity_type: str, key: str) -> dict[str, dict[str, Any]]:
        out = {}
        for s in _holders(self.stores, entity_type):
            rec = self.stores[s].read(entity_type, key)
            if rec is not None:
                out[s] = rec
        return out

    def canonical(self, store: str, entity_type: str, rec: dict[str, Any]) -> dict[str, Any]:
        """Native names -> canonical names and units; no model validation."""
        m = load_mapping(store).for_entity(entity_type)
        obs: dict[str, Any] = {}
        for canon, native in m.fields.items():
            if native not in rec:
                continue
            value = rec[native]
            # e.g. "123.0" cents stays raw; normalization handles or flags it
            with contextlib.suppress(ValueError, ArithmeticError):
                value = self.engine.to_canonical_unit(store, entity_type, canon, value)
            if canon == ENTITY_TYPES[entity_type].key_field and isinstance(value, str):
                value = value.removeprefix(m.key_prefix)
            obs[canon] = value
        return obs

    def detect(
        self, entity_type: str, key: str
    ) -> tuple[list[Detection], Resolution | None, dict[str, dict[str, Any]]]:
        native = self.observe(entity_type, key)
        if not native:
            return [], None, native
        obs = {s: self.canonical(s, entity_type, r) for s, r in native.items()}
        res = self.engine.resolve(entity_type, key, obs)
        rows: list[Detection] = []
        for s in _holders(self.stores, entity_type):
            if s not in native:
                conf = min(
                    (RULE_CONFIDENCE[d.rule] for d in res.decisions.values() if d.candidates),
                    default=0.0,
                )
                rows.append(self._row("missing", res, s, None, {s: None}, "only_source", conf))
        stamps = {s: self.engine.normalize(entity_type, "updated_at", o.get("updated_at"))
                  for s, o in obs.items()}  # fmt: skip
        for f in res.conflicts:
            dec = res.decisions[f]
            kind = self._guess(entity_type, f, dec.candidates, stamps)
            rows.append(self._row(kind, res, None, f, dec.candidates, dec.rule, None))
        for s, rec in native.items():
            m = load_mapping(s).for_entity(entity_type)
            for canon, nf in m.fields.items():
                v = rec.get(nf)
                if isinstance(v, str) and _numeric(entity_type, canon) and canon in res.decisions:
                    dec = res.decisions[canon]
                    rows.append(self._row("type_drift", res, s, canon, {s: v}, dec.rule, None))
        return rows, res, native

    def _row(
        self,
        kind: InjectionType,
        res: Resolution,
        store: str | None,
        field: str | None,
        values: dict[str, Any],
        rule: str,
        conf: float | None,
    ) -> Detection:
        if conf is None:
            conf = RULE_CONFIDENCE.get(rule, 0.0)
            dec = res.decisions.get(field or "")
            if dec is not None and dec.requires_approval:
                conf = min(conf, REVIEW_CONFIDENCE)
        resolved = res.decisions[field].value if field else None
        return Detection(
            type=kind,
            entity_type=res.entity_type,
            key=res.key,
            stores=[store] if store else sorted(values),
            field=field,
            values=_jsonable(values),
            rule=rule,
            resolved=_jsonable(resolved),
            confidence=conf,
        )

    def _guess(
        self, entity_type: str, field: str, cands: dict[str, Any], stamps: dict[str, Any]
    ) -> InjectionType:
        if any(field in load_mapping(s).for_entity(entity_type).units for s in cands):
            return "unit_mismatch"
        if field == "updated_at" or len({stamps.get(s) for s in cands}) > 1:
            return "stale"
        return "typo" if all(isinstance(v, str) for v in cands.values()) else "stale"


def _fix_record(
    store: str, entity_type: str, res: Resolution, current: dict[str, Any] | None
) -> dict[str, Any]:
    """Target-store native record carrying the resolved values (JSON-native types)."""
    m = load_mapping(store).for_entity(entity_type)
    entity = ENTITY_TYPES[entity_type].model_validate(res.record)
    resolved = _jsonable(m.from_canonical(entity))
    if current is None:
        return resolved
    base = {k: v for k, v in current.items() if not k.startswith("_") or k in m.fields.values()}
    return {**base, **resolved}


def _target_stores(rows: list[Detection], res: Resolution, engine: PolicyEngine) -> set[str]:
    targets: set[str] = set()
    tol = engine.policy.type_coercion.numeric_tolerance
    for r in rows:
        if r.type in ("missing", "type_drift") or r.field is None:
            targets.update(r.stores)
            continue
        dec = res.decisions[r.field]
        for s, v in dec.candidates.items():
            same = v == dec.value
            if isinstance(v, int | float | Decimal) and isinstance(
                dec.value, int | float | Decimal
            ):
                same = abs(Decimal(str(v)) - Decimal(str(dec.value))) <= tol
            if not same:
                targets.add(s)
    return targets


def _apply_injections(stores: dict[str, Store], data: Native, labels: list[Label]) -> list[Label]:
    """Write each injected record through Store.write/delete; return labels that took effect."""
    effective = []
    for lab in labels:
        store = stores[lab.store]
        before = store.read(lab.entity_type, lab.key)
        idem = f"inject:{lab.id}"
        if lab.type == "missing":
            store.delete(lab.entity_type, lab.key, idem)
        else:
            rec = next(
                r
                for r in data[lab.store][lab.entity_type]
                if record_key(lab.store, lab.entity_type, r) == lab.key
            )
            native = to_native(store, lab.entity_type, json.loads(_line(rec)))
            store.write(lab.entity_type, native, idem)
        if store.read(lab.entity_type, lab.key) != before:
            effective.append(lab)
    return effective


def _observable(lab: Label, stores: dict[str, Store]) -> bool:
    """Cross-store comparison needs a second witness for the key after injection."""
    present = [
        s for s in _holders(stores, lab.entity_type) if stores[s].read(lab.entity_type, lab.key)
    ]
    return len(present) >= (1 if lab.type == "missing" else 2)


def _detected(lab: Label, rows: list[Detection]) -> bool:
    canon = None
    if lab.field is not None:
        fields = load_mapping(lab.store).for_entity(lab.entity_type).fields
        canon = next((c for c, n in fields.items() if n == lab.field), None)
    return any(
        r.entity_type == lab.entity_type
        and r.key == lab.key
        and lab.store in r.stores
        and (canon is None or r.field == canon)
        and (lab.type != "missing" or r.type == "missing")
        for r in rows
    )


def run_demo(
    n_customers: int,
    n_injections: int,
    seed: int,
    workdir: Path,
    stores: Sequence[str] = DEFAULT_STORES,
) -> DemoReport:
    run_id = f"demo-s{seed}-c{n_customers}-i{n_injections}"
    with span("pipeline.run", run_id=run_id, seed=seed, stores=",".join(stores)) as sp:
        out = _run_demo(n_customers, n_injections, seed, Path(workdir), stores, run_id)
        set_attrs(
            sp, detections=len(out.detections), applied=out.applied, bad_writes=out.bad_writes
        )
    return out


def _run_demo(
    n_customers: int,
    n_injections: int,
    seed: int,
    workdir: Path,
    stores: Sequence[str],
    run_id: str,
) -> DemoReport:
    policy = load_policy(POLICY_PATH)
    engine = PolicyEngine(policy)

    # a. seed: synthetic company -> baseline snapshot -> local stores
    with span("pipeline.seed"):
        full = generate(seed, customers=n_customers, orders=0, batches=max(n_customers // 2, 1))
        data: Native = {
            s: {e: recs for e, recs in full[s].items() if e in ENTITIES}
            for s in stores
            if s in full
        }
        _write_snapshot(workdir / "snapshot", data)
        live = seed_stores(load_snapshot(workdir / "snapshot", stores))
        bus, cursors = InProcessBus(), dict[str, int]()
        cdc_events = pump(live.values(), bus, cursors)
        bus.poll(GROUP)  # detector starts after the seed load

    # b. inject value-level drift through Store.write so it flows through the outboxes
    with span("pipeline.inject", injections=n_injections):
        injected = copy.deepcopy(data)
        kinds = [k for k in VALUE_KINDS if _candidates(injected, k)]
        plan = [kinds[i % len(kinds)] for i in range(n_injections)]
        random.Random(seed).shuffle(plan)
        inj = _Injector(injected, seed)
        labels = [inj.apply(f"inj-{i:04d}", k) for i, k in enumerate(plan)]
        _write_snapshot(workdir / "snapshot_injected", injected)  # replay base, see ADR-018
        effective = _apply_injections(live, injected, labels)
        observable = [lab for lab in effective if _observable(lab, live)]

    # c. detect: CDC -> bus -> touched keys -> policy resolve
    with span("pipeline.pump") as sp:
        cdc_events += pump(live.values(), bus, cursors)
        set_attrs(sp, cdc_lag=observe_lag(bus, GROUP))
    with span("pipeline.detect") as sp:
        events = bus.poll(GROUP)
        observe_lag(bus, GROUP)
        touched = sorted({(e.entity_type, e.key) for e in events})
        first_ts: dict[tuple[str, str], int] = {}
        for e in events:
            first_ts[(e.entity_type, e.key)] = min(
                e.ts_ms, first_ts.get((e.entity_type, e.key), e.ts_ms)
            )
        detector = Detector(live, engine)
        detections: list[Detection] = []
        by_key: dict[tuple[str, str], tuple[list[Detection], Resolution, dict[str, Any]]] = {}
        for et, key in touched:
            rows, res, native = detector.detect(et, key)
            if rows and res is not None:
                detections.extend(rows)
                by_key[(et, key)] = (rows, res, native)
        observe_detect(sp, detections, len(touched))

    # e/f. propose over the FastAPI approval API; approval appends FixApproved to the ledger
    ledger = create_ledger("sqlite://")
    client = TestClient(create_app(SqlRepository.from_env("sqlite://"), _on_approve(ledger)))
    keys_by_pid: dict[str, tuple[str, str]] = {}
    with span("pipeline.propose") as sp:
        for (et, key), (rows, res, native) in by_key.items():
            for target in sorted(_target_stores(rows, res, engine)):
                mine = [r for r in rows if target in r.stores]
                record = _fix_record(target, et, res, native.get(target))
                if native.get(target) is not None and record == _jsonable(native[target]):
                    continue
                body = ProposalCreate(
                    fix=Fix(
                        target_store=target, entity_type=et, key=key, action="upsert", record=record
                    ),
                    evidence=[
                        Evidence(
                            store=s, entity_type=et, key=key, value=_jsonable(v), source="store"
                        )
                        for s, v in native.items()
                    ],
                    confidence=min(r.confidence for r in mine),
                    lineage=Lineage(
                        run_id=run_id,
                        detector=DETECTOR,
                        detection_type=mine[0].type,
                        policy_version=res.policy_version,
                        policy_digest=res.policy_digest,
                        cdc_seq=dict(cursors),
                    ),
                )
                resp = client.post("/proposals", json=body.model_dump(mode="json"))
                resp.raise_for_status()
                keys_by_pid[resp.json()["id"]] = (et, key)
                observe_proposal(resp.json()["status"], first_ts.get((et, key)))
        set_attrs(sp, proposals=len(keys_by_pid))
    with span("pipeline.approve") as sp:
        safe = len(client.get("/proposals", params={"status": "pending", "safe": True}).json())
        resp = client.post(
            "/proposals/approve-safe", json={"decided_by": "auto-safe", "reason": "confidence>=0.9"}
        )
        resp.raise_for_status()
        approved: list[str] = resp.json()["approved"]
        for _ in approved:
            observe_proposal("approved")
        set_attrs(sp, safe=safe, approved=len(approved))

    # g. apply exactly once; a second pass must be a no-op
    with span("pipeline.apply"):
        report = apply_pending(ledger, live)
        again = apply_pending(ledger, live)
        cdc_events += pump(live.values(), bus, cursors)
        bus.poll(GROUP)
        observe_lag(bus, GROUP)
    approved_keys = {keys_by_pid[p] for p in approved} - {
        keys_by_pid[p] for p in keys_by_pid if p not in approved
    }
    after = [r for et, key in by_key for r in detector.detect(et, key)[0]]

    # h. replay applied fixes on fresh stores seeded from the post-injection snapshot
    with span("pipeline.replay") as sp:
        rep = replay(ledger, workdir / "snapshot_injected")
        set_attrs(sp, ok=rep.ok, fixes=rep.fixes_replayed)
    hits = [lab for lab in observable if _detected(lab, detections)]
    return DemoReport(
        run_id=run_id,
        injections=len(labels),
        effective_injections=len(effective),
        observable_injections=len(observable),
        detections=detections,
        recall=len(hits) / len(observable) if observable else 1.0,
        undetected=[lab.id for lab in observable if lab not in hits],
        proposals=len(keys_by_pid),
        safe_proposals=safe,
        approved=len(approved),
        applied=report.applied,
        reapplied=again.applied,
        failed=len(report.failed),
        bad_writes=report.bad_writes + again.bad_writes,
        replay_ok=rep.ok,
        replay_fixes=rep.fixes_replayed,
        remaining_conflicts=sum((r.entity_type, r.key) in approved_keys for r in after),
        remaining_total=len(after),
        cdc_events=cdc_events,
    )


def _on_approve(ledger: Ledger) -> Any:
    def hook(p: Proposal) -> None:
        fix = FixApproved(
            proposal_id=p.id,
            target_store=p.fix.target_store,
            entity_type=p.fix.entity_type,
            key=p.fix.key,
            action=p.fix.action,
            record=p.fix.record,
            approved_by=p.decided_by or "unknown",
        )
        ledger.append("FixApproved", fix.model_dump(), f"approved:{p.id}")

    return hook


def _cell(v: Any, width: int) -> str:
    text = v if isinstance(v, str) else json.dumps(v, default=str)
    return text if len(text) <= width else text[: width - 1] + "~"


def format_table(rows: list[Detection]) -> str:
    cols = [("TYPE", 13), ("ENTITY", 8), ("KEY", 8), ("STORES", 26), ("FIELD", 10),
            ("VALUES", 48), ("RULE", 15), ("RESOLVED", 28)]  # fmt: skip
    lines = ["  ".join(n.ljust(w) for n, w in cols)]
    for r in rows:
        vals = " ".join(f"{s[:2]}={_cell(v, 22)}" for s, v in r.values.items())
        cells = [r.type, r.entity_type, r.key, ",".join(r.stores), r.field or "-", vals,
                 r.rule, "-" if r.resolved is None else r.resolved]  # fmt: skip
        lines.append("  ".join(_cell(c, w).ljust(w) for c, (_, w) in zip(cells, cols, strict=True)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m xstore.pipeline.demo")
    p.add_argument("--customers", type=int, default=200)
    p.add_argument("--injections", type=int, default=20)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workdir", type=Path, default=Path("runs/demo"))
    p.add_argument("--stores", default=",".join(DEFAULT_STORES))
    args = p.parse_args(argv)
    report = run_demo(
        args.customers, args.injections, args.seed, args.workdir, args.stores.split(",")
    )
    print(format_table(report.detections))
    print()
    summary = report.model_dump(exclude={"detections"})
    summary["detections"] = len(report.detections)
    for k, v in summary.items():
        print(f"{k:22} {v}")
    return 0 if report.bad_writes == 0 and report.replay_ok and not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
