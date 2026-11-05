"""Agent-proposed policy diffs. Proposals are data; only approved diffs change a Policy."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from xstore.policy.engine import AppliedChange, Policy


class ReviewOutcome(BaseModel):
    """One human review of a resolved field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: str
    field: str
    chosen_store: str
    policy_store: str
    approved: bool
    review_id: str = ""


class PolicyDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    path: str = Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
    op: Literal["set", "remove"]
    old: Any = None
    new: Any = None
    rationale: str
    evidence: list[str] = []
    proposed_by: str
    status: Literal["proposed", "approved", "rejected"] = "proposed"


class PolicyDiffError(ValueError):
    pass


@runtime_checkable
class DiffProposer(Protocol):
    """Anything that turns review outcomes into policy diffs (heuristic now, LLM agent later)."""

    def propose(self, policy: Policy, outcomes: Sequence[ReviewOutcome]) -> list[PolicyDiff]: ...


def _get(data: Any, parts: list[str]) -> Any:
    for p in parts:
        if not isinstance(data, dict) or p not in data:
            return None
        data = data[p]
    return data


def _diff_id(path: str, op: str, new: Any) -> str:
    blob = json.dumps({"path": path, "op": op, "new": new}, sort_keys=True, default=str)
    return "pd-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


class HeuristicProposer:
    """Propose putting a store first for a field once humans picked it >= min_overrides times."""

    def propose(self, policy: Policy, outcomes: Sequence[ReviewOutcome]) -> list[PolicyDiff]:
        counts: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for i, o in enumerate(outcomes):
            if o.approved and o.chosen_store != o.policy_store:
                ref = o.review_id or f"review#{i}"
                counts[(o.entity_type, o.field, o.chosen_store)].append(
                    f"{ref}: chose {o.chosen_store} over {o.policy_store}"
                )
        dumped = policy.model_dump(mode="json")
        diffs: list[PolicyDiff] = []
        for (et, field, store), evidence in sorted(counts.items()):
            if len(evidence) < policy.diff_proposals.min_overrides or et not in policy.defaults:
                continue
            override = policy.field_overrides.get(et, {}).get(field)
            base = override or policy.defaults[et]
            if base.source_priority[0] == store:
                continue
            path = f"field_overrides.{et}.{field}"
            new = {
                "strategy": "source_priority",
                "source_priority": [store, *(s for s in base.source_priority if s != store)],
            }
            diffs.append(
                PolicyDiff(
                    id=_diff_id(path, "set", new),
                    path=path,
                    op="set",
                    old=_get(dumped, path.split(".")),
                    new=new,
                    rationale=(
                        f"Reviewers chose {store} over the policy pick for {et}.{field} "
                        f"{len(evidence)} times (threshold {policy.diff_proposals.min_overrides})."
                    ),
                    evidence=evidence,
                    proposed_by=policy.diff_proposals.proposed_by,
                )
            )
        return diffs


def propose_diffs(
    policy: Policy,
    decisions_log: Iterable[ReviewOutcome],
    proposer: DiffProposer | None = None,
) -> list[PolicyDiff]:
    return (proposer or HeuristicProposer()).propose(policy, list(decisions_log))


def apply_diff(policy: Policy, diff: PolicyDiff, approved_by: str) -> Policy:
    """Return a new Policy with the diff applied and version + 1. Input is not mutated."""
    if diff.status != "approved":
        raise PolicyDiffError(f"diff {diff.id} is {diff.status!r}, not approved")
    if not approved_by.strip():
        raise PolicyDiffError("approved_by is required")
    data = copy.deepcopy(policy.model_dump(mode="json"))
    *parents, leaf = diff.path.split(".")
    if parents and parents[0] in ("version", "changes"):
        raise PolicyDiffError(f"path {diff.path!r} is not editable")
    node = data
    for p in parents:
        if diff.op == "set":
            node = node.setdefault(p, {})
        elif p not in node:
            raise PolicyDiffError(f"path {diff.path!r} not found")
        else:
            node = node[p]
        if not isinstance(node, dict):
            raise PolicyDiffError(f"path {diff.path!r} crosses a non-mapping")
    if diff.op == "set":
        node[leaf] = copy.deepcopy(diff.new)
    elif leaf in node:
        del node[leaf]
    else:
        raise PolicyDiffError(f"path {diff.path!r} not found")
    data["version"] = policy.version + 1
    data["changes"].append(
        AppliedChange(
            diff_id=diff.id, path=diff.path, approved_by=approved_by, version=data["version"]
        ).model_dump()
    )
    return Policy.model_validate(data)


def _nest(path: str, value: Any) -> str:
    if value is None:
        return ""
    for p in reversed(path.split(".")):
        value = {p: value}
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False)


def render_diff(diff: PolicyDiff) -> str:
    """Unified-diff-style YAML for human review."""
    old = _nest(diff.path, diff.old).splitlines(keepends=True)
    new = [] if diff.op == "remove" else _nest(diff.path, diff.new).splitlines(keepends=True)
    head = [
        f"# policy diff {diff.id} ({diff.status}) by {diff.proposed_by}\n",
        f"# {diff.op} {diff.path}\n",
        f"# rationale: {diff.rationale}\n",
        *(f"# evidence: {e}\n" for e in diff.evidence),
    ]
    body = difflib.unified_diff(old, new, "a/policy/policy.yaml", "b/policy/policy.yaml", n=10)
    return "".join(head) + "".join(body)
