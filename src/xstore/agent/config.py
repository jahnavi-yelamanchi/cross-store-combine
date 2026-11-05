"""Agent config: model aliases, prompt caching flag, token limits."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

CONFIG_PATH = Path(__file__).with_name("config.yaml")


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    min_cacheable_tokens: int = 1024
    simulated_error_rate: float = 0.05


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default: str
    prompt_caching: bool = True
    max_tokens: int = 512
    models: dict[str, ModelSpec]

    def resolve(self, model: str | None = None) -> tuple[str, ModelSpec]:
        """Map an alias or model id to (alias, spec). Unknown ids get a default spec."""
        name = model or os.environ.get("XSTORE_TIEBREAK_MODEL") or self.default
        if name in self.models:
            return name, self.models[name]
        for alias, spec in self.models.items():
            if spec.id == name:
                return alias, spec
        return name, ModelSpec(id=name)

    def spec_for_id(self, model_id: str) -> ModelSpec:
        return self.resolve(model_id)[1]

    def caching_enabled(self) -> bool:
        flag = os.environ.get("XSTORE_PROMPT_CACHING")
        if flag is None:
            return self.prompt_caching
        return flag.strip().lower() not in {"0", "false", "no", "off", ""}


@cache
def load_config(path: Path = CONFIG_PATH) -> AgentConfig:
    return AgentConfig.model_validate(yaml.safe_load(path.read_text()))
