"""Pydantic models for the LLM tiebreak agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["fixture", "simulated", "live"]


class EntityRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    store: str
    entity_type: str
    key: str


class TiebreakRequest(BaseModel):
    """A candidate pair the matcher could not settle on its own."""

    model_config = ConfigDict(frozen=True)

    pair_id: str
    left: EntityRef
    right: EntityRef
    match_probability: float = Field(ge=0, le=1)


class TiebreakDecision(BaseModel):
    """Instructor response_model: the structured answer the model must return."""

    same_entity: bool = Field(description="True if both records describe the same real entity.")
    confidence: float = Field(ge=0, le=1, description="Probability the verdict is correct.")
    rationale: str = Field(max_length=300, description="One or two sentences citing the fields.")
    evidence_fields: list[str] = Field(
        default_factory=list, description="Canonical field names the verdict relied on."
    )


class Usage(BaseModel):
    """Token counts as reported by the provider. input_tokens excludes cached tokens."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


class TiebreakResult(BaseModel):
    request: TiebreakRequest
    decision: TiebreakDecision
    usage: Usage
    model: str
    provider: str
    source: Source
    latency_ms: float
    needs_human: bool = False
