"""Inspect AI task: tiebreak agent verdicts vs frozen v1 ground truth.

uv run inspect eval evals/inspect/tiebreak.py --model mockllm/model --log-dir logs

Samples are customer pairs from the Splink tiebreak band plus labelled duplicates. Target is
"same" when both sides carry the same customer key or the pair is a labelled duplicate,
else "different". The solver runs xstore's TiebreakAgent (MockProvider unless
MODEL_PROVIDER says otherwise) over snapshot-backed stores and ignores the Inspect model.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.solver import Generate, Solver, TaskState, solver

from xstore.agent import EntityRef, TiebreakAgent, TiebreakRequest
from xstore.evals.snapshot import DEFAULT_DATA, load_snapshot, snapshot_stores
from xstore.matching import train_and_predict

N_SAMPLES = 50
SEED = 17


def duplicate_pairs() -> list[dict[str, Any]]:
    """Customer duplicate labels (both splits) as (store:copy, store:original) pairs."""
    out = []
    for name in ("labels.jsonl", "holdout.jsonl"):
        for line in (DEFAULT_DATA / name).open():
            lab = json.loads(line)
            if lab["type"] == "duplicate" and lab["entity_type"] == "customer":
                out.append(
                    {
                        "pair_id": f"{lab['store']}:{lab['injected']}|{lab['store']}:{lab['key']}",
                        "left": {"store": lab["store"], "key": lab["injected"]},
                        "right": {"store": lab["store"], "key": lab["key"]},
                        "match_probability": None,
                        "target": "same",
                    }
                )
    return sorted(out, key=lambda p: p["pair_id"])


def tiebreak_pairs() -> list[dict[str, Any]]:
    snap = load_snapshot()
    dup_keys = {p["left"]["key"] for p in duplicate_pairs()}
    out = []
    for p in train_and_predict(snap.customer_table()):
        if p.band != "tiebreak":
            continue
        same = p.left_key == p.right_key or bool({p.left_key, p.right_key} & dup_keys)
        out.append(
            {
                "pair_id": p.pair_id,
                "left": {"store": p.left_store, "key": p.left_key},
                "right": {"store": p.right_store, "key": p.right_key},
                "match_probability": p.match_probability,
                "target": "same" if same else "different",
            }
        )
    return out


def build_samples(n: int = N_SAMPLES) -> list[Sample]:
    dups = duplicate_pairs()
    ties = random.Random(SEED).sample(tiebreak_pairs(), max(0, n - len(dups)))
    return [
        Sample(id=p["pair_id"], input=json.dumps(p, sort_keys=True), target=p["target"])
        for p in dups + ties
    ]


@solver
def xstore_tiebreak(model: str | None = None) -> Solver:
    """Run the LangGraph tiebreak agent; write "same"/"different" to the completion."""
    os.environ.setdefault("MODEL_PROVIDER", "mock")
    agent = TiebreakAgent(snapshot_stores(load_snapshot()), model=model)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        p = json.loads(state.input_text)
        req = TiebreakRequest(
            pair_id=p["pair_id"],
            left=EntityRef(store=p["left"]["store"], entity_type="customer", key=p["left"]["key"]),
            right=EntityRef(
                store=p["right"]["store"], entity_type="customer", key=p["right"]["key"]
            ),
            match_probability=p["match_probability"] or 0.5,
        )
        result = await agent.arun(req)
        state.output.completion = "same" if result.decision.same_entity else "different"
        state.metadata.update(
            source=result.source,
            confidence=result.decision.confidence,
            needs_human=result.needs_human,
            model=result.model,
        )
        return state

    return solve


@task
def tiebreak(model: str | None = None) -> Task:
    return Task(
        dataset=MemoryDataset(build_samples(), name="xstore-tiebreak-frozen-v1"),
        solver=xstore_tiebreak(model),
        scorer=match(location="exact"),
    )
