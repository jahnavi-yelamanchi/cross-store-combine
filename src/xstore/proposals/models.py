"""Proposal records: a fix plus the evidence and lineage that justify it."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from xstore.synth.injector import InjectionType

DetectionType = InjectionType
Status = Literal["pending", "approved", "rejected"]
SAFE_CONFIDENCE = 0.9


class Evidence(BaseModel):
    """One observed value backing a proposal."""

    model_config = ConfigDict(frozen=True)

    store: str
    entity_type: str
    key: str
    field: str | None = None
    value: Any = None
    observed_at: datetime | None = None
    source: Literal["store", "cdc", "matcher", "agent", "policy"]


class Lineage(BaseModel):
    """Which run, detector, matcher, model and policy produced the proposal."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    detector: str
    detection_type: DetectionType
    matcher: str | None = None
    match_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    agent_model: str | None = None
    policy_version: int | None = None
    policy_digest: str | None = None
    cdc_seq: dict[str, int] | None = None


class Fix(BaseModel):
    """The write to apply; `record` is the full native record for an upsert."""

    model_config = ConfigDict(frozen=True)

    target_store: str
    entity_type: str
    key: str
    action: Literal["upsert", "delete"]
    record: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _record_matches_action(self) -> Fix:
        if self.action == "upsert" and self.record is None:
            raise ValueError("upsert requires record")
        if self.action == "delete" and self.record is not None:
            raise ValueError("delete takes no record")
        return self


class ProposalCreate(BaseModel):
    """Input to POST /proposals."""

    fix: Fix
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    lineage: Lineage


class Decision(BaseModel):
    decided_by: str = Field(min_length=1)
    reason: str | None = None


class Proposal(ProposalCreate):
    id: str
    created_at: datetime
    status: Status = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safe(self) -> bool:
        """Auto-approvable: high-confidence upsert. Deletes are never safe."""
        return self.confidence >= SAFE_CONFIDENCE and self.fix.action == "upsert"

    @classmethod
    def new(cls, body: ProposalCreate, now: datetime | None = None) -> Proposal:
        return cls(id=proposal_id(body), created_at=now or datetime.now(UTC), **dict(body))

    def decide(self, status: Status, decision: Decision) -> Proposal:
        return self.model_copy(
            update={
                "status": status,
                "decided_by": decision.decided_by,
                "decided_at": datetime.now(UTC),
                "reason": decision.reason,
            }
        )


def proposal_id(body: ProposalCreate) -> str:
    """sha256 over fix, run_id and detection_type; reruns of a detection share an id."""
    content = {
        "fix": body.fix.model_dump(mode="json"),
        "run_id": body.lineage.run_id,
        "detection_type": body.lineage.detection_type,
    }
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
