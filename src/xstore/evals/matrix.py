"""Comparison matrix: splink_only / llm_only / hybrid x model x batch|streaming x policy.

Each config emits one JSON line in the scripts/eval_gate.py schema plus extra fields.
Detection is the deterministic baseline (xstore.evals.detection); configs differ in how
customer candidate pairs from the Splink matcher are judged (threshold vs LLM tiebreak),
how detections arrive (one pass vs CDC replay) and which conflict policy picks fixes.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from xstore.agent import (
    EntityRef,
    MockProvider,
    ModelProvider,
    TiebreakAgent,
    TiebreakRequest,
    TiebreakResult,
    Usage,
)
from xstore.agent.config import load_config
from xstore.cdc import InProcessBus
from xstore.entities import ENTITY_TYPES, load_mapping
from xstore.entities.mapping import LB_PER_KG
from xstore.evals.detection import Detection, Detector, canonical_values, pair_duplicates
from xstore.evals.pricing import ledger_write_cost, load_prices, splink_cost_per_1k, token_cost
from xstore.evals.snapshot import (
    DEFAULT_DATA,
    Snapshot,
    SnapshotStore,
    expected_types,
    jtype,
    load_snapshot,
)
from xstore.matching import CandidatePair, train_and_predict
from xstore.policy import FieldDecision, Policy, PolicyEngine, load_policy

Split = Literal["train", "holdout"]
Mode = Literal["batch", "streaming"]
SPLIT_FILES: dict[str, str] = {"train": "labels.jsonl", "holdout": "holdout.jsonl"}
POLICY_PATH = Path(__file__).resolve().parents[3] / "policy" / "policy.yaml"
FIX_TYPES = {"stale", "typo", "type_drift", "unit_mismatch", "orphaned"}
# Auto-apply only rules that cannot pick the injected bad value on their own (ADR-017).
AUTO_RULES = frozenset({"agreement", "only_source", "field_override"})
SPLINK_THRESHOLD = 0.5
LLM_ONLY_SAMPLE = 500
STREAM_BATCH = 500


@dataclass(frozen=True)
class Config:
    name: str
    model: str | None  # agent alias (haiku/sonnet); None = no LLM
    mode: Mode
    policy: Literal["policy", "newest_wins"] = "policy"

    @property
    def judge(self) -> str:
        return {"splink_only": "threshold", "llm_only": "agent_all"}.get(
            self.name, "agent_tiebreak"
        )


# Gate reads the last hybrid/holdout row, so hybrid/haiku/batch goes last.
CONFIGS: tuple[Config, ...] = (
    Config("splink_only", None, "batch"),
    Config("splink_only", None, "streaming"),
    Config("llm_only", "haiku", "batch"),
    Config("llm_only", "sonnet", "batch"),
    Config("hybrid_newest_wins", "haiku", "batch", "newest_wins"),
    Config("hybrid", "sonnet", "batch"),
    Config("hybrid", "sonnet", "streaming"),
    Config("hybrid", "haiku", "streaming"),
    Config("hybrid", "haiku", "batch"),
)


@dataclass
class AgentLedger:
    """Agent calls made by one config: usage, cost and decision sources."""

    model_id: str
    calls: int = 0
    cost_usd: float = 0.0
    usage: Usage = field(default_factory=Usage)
    sources: Counter[str] = field(default_factory=Counter)

    def add(self, r: TiebreakResult) -> None:
        self.calls += 1
        self.cost_usd += token_cost(r.usage, self.model_id)
        self.usage = Usage(
            input_tokens=self.usage.input_tokens + r.usage.input_tokens,
            output_tokens=self.usage.output_tokens + r.usage.output_tokens,
            cache_read_tokens=self.usage.cache_read_tokens + r.usage.cache_read_tokens,
            cache_write_tokens=self.usage.cache_write_tokens + r.usage.cache_write_tokens,
        )
        self.sources[r.source] += 1


def request_for(p: CandidatePair) -> TiebreakRequest:
    return TiebreakRequest(
        pair_id=p.pair_id,
        left=EntityRef(store=p.left_store, entity_type="customer", key=p.left_key),
        right=EntityRef(store=p.right_store, entity_type="customer", key=p.right_key),
        match_probability=p.match_probability,
    )


def newest_wins(policy: Policy) -> Policy:
    """Ablation: every entity resolves by recency only, no field overrides."""
    data = policy.model_dump()
    for d in data["defaults"].values():
        d["strategy"] = "recency"
    data["field_overrides"] = {}
    return Policy.model_validate(data)


# --- labels and scoring ---------------------------------------------------------------------


def load_labels(data_dir: Path, split: str) -> list[dict[str, Any]]:
    path = Path(data_dir) / SPLIT_FILES[split]
    return [json.loads(line) for line in path.open() if line.strip()]


def label_key(x: dict[str, Any] | Detection) -> tuple[str, str, str, str]:
    if isinstance(x, Detection):
        return (x.type, x.store, x.entity_type, x.key)
    return (x["type"], x["store"], x["entity_type"], x["key"])


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


@dataclass
class Score:
    tp: list[Detection]
    fp: list[Detection]
    fn: list[dict[str, Any]]
    precision: float
    recall: float
    f1: float
    per_type: dict[str, dict[str, float | int]]


def score(
    detections: Iterable[Detection],
    labels: list[dict[str, Any]],
    ignore: Iterable[dict[str, Any]] = (),
) -> Score:
    """Match on (type, store, entity_type, key). Detections hitting `ignore` (labels of the
    other split) are neither true nor false positives."""
    want = {label_key(lab): lab for lab in labels}
    skip = {label_key(lab) for lab in ignore}
    tp, fp = [], []
    for d in sorted(set(detections)):
        k = label_key(d)
        if k in want:
            tp.append(d)
        elif k not in skip:
            fp.append(d)
    hit = {label_key(d) for d in tp}
    fn = [lab for k, lab in want.items() if k not in hit]
    per_type: dict[str, dict[str, float | int]] = {}
    for t in sorted({lab["type"] for lab in labels} | {d.type for d in tp + fp}):
        a = sum(d.type == t for d in tp)
        b = sum(d.type == t for d in fp)
        c = sum(lab["type"] == t for lab in fn)
        p, r, _ = _prf(a, b, c)
        per_type[t] = {"precision": p, "recall": r, "tp": a, "fp": b, "fn": c}
    p, r, f = _prf(len(tp), len(fp), len(fn))
    return Score(tp, fp, fn, p, r, f, per_type)


# --- fixes under policy ---------------------------------------------------------------------


def _to_native(store: str, entity_type: str, canon: str, value: Any, want: str | None) -> Any:
    m = load_mapping(store).for_entity(entity_type)
    if value is None:
        return None
    unit = m.units.get(canon)
    if unit == "cents":
        value = round(float(value) * 100)
    elif unit == "lb":
        value = round(float(value) * LB_PER_KG, 4)
    reverse = {v: k for k, v in m.values.get(canon, {}).items()}
    value = reverse.get(str(value), value)
    if want == "int":
        return round(float(value))
    if want == "float":
        return float(value)
    if want == "str":
        return str(value)
    return value


def _equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if jtype(a) != jtype(b):
        return False
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return False


@dataclass
class FixOutcome:
    approvals: int = 0
    auto: int = 0
    bad: int = 0
    auto_ungated: int = 0
    bad_ungated: int = 0
    labelled: int = 0
    correct: int = 0


def evaluate_fixes(
    snap: Snapshot,
    detections: Iterable[Detection],
    labels: dict[tuple[str, str, str, str], dict[str, Any]],
    engine: PolicyEngine,
    types: dict[tuple[str, str, str], str],
) -> FixOutcome:
    """Resolve a fix per detection with the policy engine; decide auto-apply vs approval;
    compare labelled field fixes with ground-truth `original`."""
    out = FixOutcome()
    for d in detections:
        lab = labels.get(label_key(d))
        if d.type in ("duplicate", "schema_drift"):
            out.approvals += 1  # delete/merge and mapping changes always need a human
            continue
        obs = {
            s: canonical_values(s, d.entity_type, snap.records[(s, d.entity_type)][d.key])
            for s in snap.holders(d.entity_type, d.key)
        }
        if d.type == "missing":
            res = engine.resolve(d.entity_type, d.key, obs)
            if res.conflicts:
                out.approvals += 1
            else:
                out.auto += 1
                out.auto_ungated += 1
            continue
        if d.type not in FIX_TYPES or d.field is None:
            out.approvals += 1
            continue
        fields = load_mapping(d.store).for_entity(d.entity_type).fields
        canon = next((c for c, n in fields.items() if n == d.field), None)
        if canon is None:
            out.approvals += 1
            continue
        dec: FieldDecision | None = engine.resolve(d.entity_type, d.key, obs).decisions.get(canon)
        if dec is None:
            out.approvals += 1
            continue
        fix = _to_native(
            d.store, d.entity_type, canon, dec.value, types.get((d.store, d.entity_type, d.field))
        )
        if lab is not None:
            target = lab["original"]
        else:
            target = snap.records[(d.store, d.entity_type)][d.key].get(d.field)
        good = _equal(fix, target)
        if lab is not None:
            out.labelled += 1
            out.correct += good
        if not dec.requires_approval:
            out.auto_ungated += 1
            out.bad_ungated += not good
        if dec.rule in AUTO_RULES and not dec.requires_approval:
            out.auto += 1
            out.bad += not good
        else:
            out.approvals += 1
    return out


# --- runs -----------------------------------------------------------------------------------


@dataclass
class Shared:
    """Inputs shared by every config of one split: snapshot, matcher pairs, labels."""

    data_dir: Path
    split: str
    snap: Snapshot
    types: dict[tuple[str, str, str], str]
    pairs: list[CandidatePair]
    splink_ms: float
    labels: list[dict[str, Any]]
    other: list[dict[str, Any]]
    limit: int | None
    provider_factory: Callable[[], ModelProvider] = MockProvider

    @classmethod
    def build(cls, data_dir: Path, split: str, limit: int | None = None) -> Shared:
        snap = load_snapshot(data_dir, limit)
        t0 = time.perf_counter()
        pairs = train_and_predict(snap.customer_table())
        splink_ms = (time.perf_counter() - t0) * 1000
        other_split = "holdout" if split == "train" else "train"
        loaded = {et: snap.keys(et) for et in ENTITY_TYPES}

        def keep(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if r["key"] in loaded[r["entity_type"]]]

        return cls(
            data_dir=data_dir,
            split=split,
            snap=snap,
            types=expected_types(snap),
            pairs=pairs,
            splink_ms=splink_ms,
            labels=keep(load_labels(data_dir, split)),
            other=keep(load_labels(data_dir, other_split)),
            limit=limit,
        )


def _agent(stores_from: Snapshot, model: str, provider: ModelProvider) -> TiebreakAgent:
    stores = {s: SnapshotStore(s, stores_from) for s in sorted({s for s, _ in stores_from.records})}
    return TiebreakAgent(stores, provider=provider, model=model)


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 3)


def judge_batch(
    cfg: Config, sh: Shared, ledger: AgentLedger | None
) -> tuple[set[str], dict[str, Any]]:
    """Pair ids judged same entity, plus sampling notes."""
    pairs = sh.pairs
    if cfg.judge == "threshold" or ledger is None or cfg.model is None:
        return {p.pair_id for p in pairs if p.match_probability >= SPLINK_THRESHOLD}, {}
    agent = _agent(sh.snap, cfg.model, sh.provider_factory())
    if cfg.judge == "agent_tiebreak":
        same: set[str] = {p.pair_id for p in pairs if p.band == "match"}
        tie = [p for p in pairs if p.band == "tiebreak"]
        for r in agent.run_many(request_for(p) for p in tie):
            ledger.add(r)
            if r.decision.same_entity:
                same.add(r.request.pair_id)
        return same, {"agent_pairs": len(tie), "agent_pairs_sampled": len(tie)}
    # llm_only: every candidate pair is the agent's call. Pairs with equal keys cannot yield a
    # duplicate detection, so they are sampled for cost only; differing-key pairs all run.
    diff = [p for p in pairs if p.left_key != p.right_key]
    same_key = [p for p in pairs if p.left_key == p.right_key]
    sample = random.Random(0).sample(same_key, min(LLM_ONLY_SAMPLE, len(same_key)))
    same = set()
    diff_ledger = AgentLedger(ledger.model_id)
    for r in agent.run_many(request_for(p) for p in diff):
        diff_ledger.add(r)
        if r.decision.same_entity:
            same.add(r.request.pair_id)
    sample_ledger = AgentLedger(ledger.model_id)
    for r in agent.run_many(request_for(p) for p in sample):
        sample_ledger.add(r)
    scale = len(same_key) / len(sample) if sample else 0.0
    ledger.calls = diff_ledger.calls + round(sample_ledger.calls * scale)
    ledger.cost_usd = diff_ledger.cost_usd + sample_ledger.cost_usd * scale
    u1, u2 = diff_ledger.usage, sample_ledger.usage
    ledger.usage = Usage(
        input_tokens=u1.input_tokens + round(u2.input_tokens * scale),
        output_tokens=u1.output_tokens + round(u2.output_tokens * scale),
        cache_read_tokens=u1.cache_read_tokens + round(u2.cache_read_tokens * scale),
        cache_write_tokens=u1.cache_write_tokens + round(u2.cache_write_tokens * scale),
    )
    ledger.sources = diff_ledger.sources + sample_ledger.sources
    return same, {
        "agent_pairs": len(pairs),
        "agent_pairs_sampled": len(diff) + len(sample),
        "sample_note": (
            f"llm_only: all {len(diff)} differing-key pairs judged; {len(sample)} of "
            f"{len(same_key)} equal-key pairs sampled, their cost extrapolated x{scale:.1f}"
        ),
    }


def run_batch(
    cfg: Config, sh: Shared, ledger: AgentLedger | None
) -> tuple[set[Detection], list[float], dict[str, Any]]:
    t0 = time.perf_counter()
    dets = Detector(sh.snap, sh.types).detect_all()
    same, notes = judge_batch(cfg, sh, ledger)
    dets |= pair_duplicates(sh.snap, sh.pairs, lambda p: p.pair_id in same)
    run_ms = (time.perf_counter() - t0) * 1000 + sh.splink_ms
    return dets, [run_ms], notes


def run_streaming(
    cfg: Config, sh: Shared, ledger: AgentLedger | None
) -> tuple[set[Detection], list[float], dict[str, Any]]:
    """Replay every store's records as CDC create events through the in-process Kafka-style
    bus (Avro wire format), round-robin across stores, in micro-batches. After each poll the
    touched keys are re-detected and newly complete customer pairs judged. Latency per event
    is detection completion time minus the event's ts_ms. Matcher scores come from the
    batch-trained Splink model (trained offline, scored on arrival)."""
    state = sh.snap.empty_like()
    det = Detector(state, sh.types)
    bus = InProcessBus()
    agent = (
        _agent(state, cfg.model, sh.provider_factory())
        if cfg.model is not None and ledger is not None
        else None
    )
    by_uid: dict[str, list[CandidatePair]] = defaultdict(list)
    for p in sh.pairs:
        by_uid[p.left_id].append(p)
        by_uid[p.right_id].append(p)
    feeds = [
        SnapshotStore(s, sh.snap).stream_changes() for s in sorted({s for s, _ in sh.snap.records})
    ]
    buckets: dict[tuple[str, str], list[Detection]] = {}
    decided: set[str] = set()
    same: list[CandidatePair] = []
    latencies: list[float] = []
    agent_calls = 0
    while feeds:
        sent = 0
        while feeds and sent < STREAM_BATCH:
            for feed in list(feeds):
                event = next(feed, None)
                if event is None:
                    feeds.remove(feed)
                    continue
                bus.publish(event)
                sent += 1
        events = bus.poll("evals-detector")
        touched: set[tuple[str, str]] = set()
        for ev in events:
            if ev.after is not None:
                state.add(ev.store, ev.entity_type, ev.after)
                touched.add((ev.entity_type, ev.key))
        touched |= {k for k, ds in buckets.items() if any(d.type == "orphaned" for d in ds)}
        for et, key in touched:
            buckets[(et, key)] = det.detect_key(et, key)
        for ev in events:
            if ev.entity_type != "customer":
                continue
            for p in by_uid.get(f"{ev.store}:{ev.key}", ()):
                if p.pair_id in decided or not (
                    p.left_key in state.records[(p.left_store, "customer")]
                    and p.right_key in state.records[(p.right_store, "customer")]
                ):
                    continue
                decided.add(p.pair_id)
                if agent is None or ledger is None:
                    ok = p.match_probability >= SPLINK_THRESHOLD
                elif p.band == "tiebreak":
                    r = agent.run(request_for(p))
                    ledger.add(r)
                    agent_calls += 1
                    ok = r.decision.same_entity
                else:
                    ok = p.band == "match"
                if ok:
                    same.append(p)
        done = time.time() * 1000
        latencies.extend(done - ev.ts_ms for ev in events)
    dets = {d for ds in buckets.values() for d in ds}
    dets |= pair_duplicates(state, same, lambda _: True)
    notes = {"events": len(latencies), "stream_batch": STREAM_BATCH, "agent_pairs": agent_calls}
    return dets, latencies, notes


def run_config(cfg: Config, sh: Shared, run_id: str, policy: Policy) -> dict[str, Any]:
    ledger = None
    model_id = "none"
    if cfg.model is not None:
        model_id = load_config().resolve(cfg.model)[1].id
        ledger = AgentLedger(model_id)
    runner = run_streaming if cfg.mode == "streaming" else run_batch
    dets, latencies, notes = runner(cfg, sh, ledger)
    sc = score(dets, sh.labels, sh.other)
    pol = policy if cfg.policy == "policy" else newest_wins(policy)
    labelled = {label_key(lab): lab for lab in sh.labels}
    scored = sc.tp + sc.fp  # detections matching the other split are left out
    fx = evaluate_fixes(sh.snap, scored, labelled, PolicyEngine(pol), sh.types)
    n_ent = sh.snap.n_entities()
    llm_cost = ledger.cost_usd if ledger else 0.0
    cost_per_1k = llm_cost / n_ent * 1000 + splink_cost_per_1k()
    simulated = bool(ledger and ledger.sources.get("simulated"))
    usage = ledger.usage if ledger else Usage()
    note = "simulated" if simulated else ("no LLM" if ledger is None else "fixture")
    row: dict[str, Any] = {
        "run_id": run_id,
        "config": cfg.name,
        "model": cfg.model or "none",
        "model_id": model_id,
        "mode": cfg.mode,
        "split": sh.split,
        "policy": cfg.policy,
        "precision": sc.precision,
        "recall": sc.recall,
        "f1": sc.f1,
        "cost_per_1k": round(cost_per_1k, 6),
        "bad_write_rate": round(fx.bad / fx.auto, 4) if fx.auto else 0.0,
        "approvals_needed": fx.approvals,
        "latency_p50_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
        "latency_p95_ms": _pct(latencies, 0.95),
        "per_type": sc.per_type,
        "correct_fix_rate": round(fx.correct / fx.labelled, 4) if fx.labelled else 0.0,
        "bad_write_rate_ungated": (
            round(fx.bad_ungated / fx.auto_ungated, 4) if fx.auto_ungated else 0.0
        ),
        "auto_applied": fx.auto,
        "labelled_fixes": fx.labelled,
        "ledger_write_cost_usd": {
            k: round(v, 8) for k, v in ledger_write_cost(len(scored)).items()
        },
        "llm_cost_usd": round(llm_cost, 6),
        "agent_calls": ledger.calls if ledger else 0,
        "usage": usage.model_dump(),
        "usage_per_1k": {k: round(v / n_ent * 1000, 1) for k, v in usage.model_dump().items()},
        "decision_sources": dict(ledger.sources) if ledger else {},
        "n_entities": n_ent,
        "n_labels": len(sh.labels),
        "n_detections": len(scored),
        "tp": len(sc.tp),
        "fp": len(sc.fp),
        "fn": len(sc.fn),
        "candidate_pairs": len(sh.pairs),
        "tiebreak_pairs": sum(p.band == "tiebreak" for p in sh.pairs),
        "splink_ms": round(sh.splink_ms, 1),
        "limit": sh.limit,
        "prices_as_of": load_prices()["as_of"],
        "note": note,
        **notes,
    }
    return row


def run_matrix(
    split: str,
    out: Path,
    data_dir: Path = DEFAULT_DATA,
    limit: int | None = None,
    configs: Iterable[Config] = CONFIGS,
    run_id: str | None = None,
) -> Path:
    """Run every config on one split; write <out>/<run_id>.jsonl. Returns the file path."""
    if split not in SPLIT_FILES:
        raise ValueError(f"split must be one of {sorted(SPLIT_FILES)}")
    run_id = run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{split}"
    sh = Shared.build(data_dir, split, limit)
    policy = load_policy(POLICY_PATH)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_id}.jsonl"
    with path.open("w") as fh:
        for cfg in configs:
            row = run_config(cfg, sh, run_id, policy)
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
    return path
