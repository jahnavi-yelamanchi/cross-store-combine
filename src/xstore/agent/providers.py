"""Model provider adapters. MODEL_PROVIDER selects mock (default), anthropic or openai."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from xstore.agent.config import AgentConfig, load_config
from xstore.agent.models import TiebreakDecision, Usage

FIXTURES_DIR = Path(__file__).with_name("fixtures")


class ModelProvider(Protocol):
    name: str

    def decide(
        self, model: str, system: str, prompt: str, evidence: dict[str, Any]
    ) -> tuple[TiebreakDecision, Usage, str]:
        """Return (decision, usage, source) for one prompt. `model` is a model id."""
        ...


def fixture_key(model: str, system: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}\n{system}\n{prompt}".encode()).hexdigest()


def fixture_path(model: str, system: str, prompt: str, root: Path = FIXTURES_DIR) -> Path:
    return root / f"{fixture_key(model, system, prompt)}.json"


def write_fixture(
    model: str,
    system: str,
    prompt: str,
    decision: TiebreakDecision,
    usage: Usage,
    root: Path = FIXTURES_DIR,
) -> Path:
    path = fixture_path(model, system, prompt, root)
    payload = {"model": model, "decision": decision.model_dump(), "usage": usage.model_dump()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


# --- simulation -------------------------------------------------------------------------------


def _norm_email(v: Any) -> str:
    return str(v).strip().lower() if v else ""


def _norm_phone(v: Any) -> str:
    return re.sub(r"\D", "", str(v))[-10:] if v else ""


def _norm_name(v: Any) -> str:
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", str(v or "").lower()).split()))


def _seeded_unit(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def simulate_decision(evidence: dict[str, Any], error_rate: float, model: str) -> TiebreakDecision:
    """Rule-based stand-in for a model verdict, with a seeded per-model error rate."""
    left = evidence.get("left", {}).get("canonical", {}) or {}
    right = evidence.get("right", {}).get("canonical", {}) or {}
    score, weight, used = 0.0, 0.0, []

    def compare(field: str, w: float, sim: float | None) -> None:
        nonlocal score, weight
        if sim is None:
            return
        score += w * sim
        weight += w
        used.append(field)

    el, er = _norm_email(left.get("email")), _norm_email(right.get("email"))
    compare("email", 0.4, float(el == er) if el and er else None)
    pl, pr = _norm_phone(left.get("phone")), _norm_phone(right.get("phone"))
    compare("phone", 0.25, float(pl == pr) if pl and pr else None)
    nl, nr = _norm_name(left.get("name")), _norm_name(right.get("name"))
    compare("name", 0.25, difflib.SequenceMatcher(None, nl, nr).ratio() if nl and nr else None)
    cl, cr = left.get("country"), right.get("country")
    compare("country", 0.1, float(str(cl).upper() == str(cr).upper()) if cl and cr else None)

    found = evidence.get("left", {}).get("found") and evidence.get("right", {}).get("found")
    if not found or weight == 0:
        return TiebreakDecision(
            same_entity=False,
            confidence=0.3,
            rationale="Simulated: missing record or no comparable fields.",
            evidence_fields=used,
        )
    ratio = score / weight
    same = ratio >= 0.6
    confidence = round(min(0.99, 0.5 + abs(ratio - 0.6) * 1.2), 3)
    pair_id = str(evidence.get("pair_id", ""))
    if _seeded_unit(pair_id, model) < error_rate:
        same = not same
        confidence = round(max(0.55, confidence - 0.2), 3)
    verdict = "same" if same else "different"
    return TiebreakDecision(
        same_entity=same,
        confidence=confidence,
        rationale=f"Simulated: weighted agreement {ratio:.2f} over {', '.join(used)} -> {verdict}.",
        evidence_fields=used,
    )


class MockProvider:
    """Offline provider. Replays recorded fixtures; otherwise simulates.

    Fixture hit (source="fixture"): decision and usage recorded from a live call, keyed by
    sha256(model + "\\n" + system + "\\n" + prompt).

    Fixture miss (source="simulated"): a rule-based verdict over normalized email, phone
    digits, difflib name similarity and country, flipped at a seeded per-model error rate,
    with token counts estimated as chars/4. This is NOT real model output; numbers derived
    from simulated results measure the harness, not the model.
    """

    name = "mock"

    def __init__(self, config: AgentConfig | None = None, fixtures_dir: Path = FIXTURES_DIR):
        self.config = config or load_config()
        self.fixtures_dir = fixtures_dir
        self._cached_systems: set[str] = set()

    def decide(
        self, model: str, system: str, prompt: str, evidence: dict[str, Any]
    ) -> tuple[TiebreakDecision, Usage, str]:
        path = fixture_path(model, system, prompt, self.fixtures_dir)
        if path.exists():
            raw = json.loads(path.read_text())
            return (
                TiebreakDecision.model_validate(raw["decision"]),
                Usage.model_validate(raw["usage"]),
                "fixture",
            )
        spec = self.config.spec_for_id(model)
        decision = simulate_decision(evidence, spec.simulated_error_rate, model)
        return decision, self._simulate_usage(model, system, prompt, decision), "simulated"

    def _simulate_usage(
        self, model: str, system: str, prompt: str, decision: TiebreakDecision
    ) -> Usage:
        system_tokens, prompt_tokens = max(1, len(system) // 4), max(1, len(prompt) // 4)
        output_tokens = max(1, len(decision.model_dump_json()) // 4)
        spec = self.config.spec_for_id(model)
        if not self.config.caching_enabled() or system_tokens < spec.min_cacheable_tokens:
            return Usage(input_tokens=system_tokens + prompt_tokens, output_tokens=output_tokens)
        key = fixture_key(model, system, "")
        if key in self._cached_systems:
            return Usage(
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=system_tokens,
            )
        self._cached_systems.add(key)
        return Usage(
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=system_tokens,
        )


class AnthropicProvider:
    """Live Claude calls through Instructor; the static system prompt is cache_control'd."""

    name = "anthropic"

    def __init__(self, config: AgentConfig | None = None) -> None:
        import anthropic
        import instructor

        self.config = config or load_config()
        self.client = instructor.from_anthropic(anthropic.Anthropic())

    def decide(
        self, model: str, system: str, prompt: str, evidence: dict[str, Any]
    ) -> tuple[TiebreakDecision, Usage, str]:
        system_param: Any = system
        if self.config.caching_enabled():
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        decision, raw = self.client.messages.create_with_completion(
            model=model,
            max_tokens=self.config.max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": prompt}],
            response_model=TiebreakDecision,
        )
        u = raw.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
        )
        return decision, usage, "live"


class OpenAIProvider:
    """Live OpenAI calls through Instructor. `openai` is optional and not a declared dependency."""

    name = "openai"

    def __init__(self, config: AgentConfig | None = None) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("MODEL_PROVIDER=openai needs the openai package") from exc
        import instructor

        self.config = config or load_config()
        self.client = instructor.from_openai(openai.OpenAI())

    def decide(
        self, model: str, system: str, prompt: str, evidence: dict[str, Any]
    ) -> tuple[TiebreakDecision, Usage, str]:
        decision, raw = self.client.chat.completions.create_with_completion(
            model=model,
            max_tokens=self.config.max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_model=TiebreakDecision,
        )
        u = raw.usage
        details = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", None) or 0) if details else 0
        usage = Usage(
            input_tokens=u.prompt_tokens - cached,
            output_tokens=u.completion_tokens,
            cache_read_tokens=cached,
        )
        return decision, usage, "live"


def get_provider(name: str | None = None) -> ModelProvider:
    choice = (name or os.environ.get("MODEL_PROVIDER", "mock")).lower()
    if choice == "mock":
        return MockProvider()
    if choice == "anthropic":
        return AnthropicProvider()
    if choice == "openai":
        return OpenAIProvider()
    raise ValueError(f"unknown MODEL_PROVIDER {choice!r}")
