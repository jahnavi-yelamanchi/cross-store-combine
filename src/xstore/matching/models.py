"""Matcher config and output models."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SETTINGS_PATH = Path(__file__).with_name("settings.yaml")

Band = Literal["match", "non_match", "tiebreak"]


class MatchBands(BaseModel):
    """match_probability >= match_at -> match; < non_match_below -> non_match."""

    model_config = ConfigDict(extra="forbid")

    match_at: float = Field(default=0.95, ge=0, le=1)
    non_match_below: float = Field(default=0.50, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> MatchBands:
        if self.non_match_below > self.match_at:
            raise ValueError("non_match_below must be <= match_at")
        return self

    def band(self, p: float) -> Band:
        if p >= self.match_at:
            return "match"
        if p < self.non_match_below:
            return "non_match"
        return "tiebreak"


class MatcherSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = 42
    bands: MatchBands = MatchBands()
    predict_threshold: float = 0.01
    u_max_pairs: float = 1e6
    deterministic_rules: list[str]
    deterministic_recall: float = Field(gt=0, le=1)
    blocking_rules: list[list[str]]
    em_training_rules: list[list[str]]


@cache
def load_settings(path: Path = SETTINGS_PATH) -> MatcherSettings:
    return MatcherSettings.model_validate(yaml.safe_load(path.read_text()))


class CandidatePair(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair_id: str
    entity_type: Literal["customer"] = "customer"
    left_store: str
    left_key: str
    right_store: str
    right_key: str
    match_probability: float
    match_weight: float
    band: Band

    @property
    def left_id(self) -> str:
        return f"{self.left_store}:{self.left_key}"

    @property
    def right_id(self) -> str:
        return f"{self.right_store}:{self.right_key}"
