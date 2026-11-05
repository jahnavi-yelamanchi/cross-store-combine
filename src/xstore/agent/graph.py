"""LangGraph tiebreak agent: fetch_evidence (MCP) -> decide (Instructor) -> validate."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from mcp.client import Client

from xstore.agent.config import AgentConfig, load_config
from xstore.agent.evidence_mcp import build_server
from xstore.agent.models import (
    EntityRef,
    Source,
    TiebreakDecision,
    TiebreakRequest,
    TiebreakResult,
    Usage,
)
from xstore.agent.providers import ModelProvider, get_provider
from xstore.stores import Store

SYSTEM_PROMPT_PATH = Path(__file__).with_name("prompts") / "tiebreak.md"
MIN_CONFIDENCE = 0.6
NODES = ("fetch_evidence", "decide", "validate")


class TiebreakState(TypedDict, total=False):
    request: TiebreakRequest
    evidence: dict[str, Any]
    decision: TiebreakDecision
    usage: Usage
    source: Source
    needs_human: bool


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text()


def build_prompt(evidence: dict[str, Any]) -> str:
    """User turn: the evidence JSON only. Deterministic so fixtures key on it."""
    return "Evidence:\n" + json.dumps(evidence, indent=2, sort_keys=True, default=str)


class TiebreakAgent:
    def __init__(
        self,
        stores: Mapping[str, Store],
        provider: ModelProvider | None = None,
        model: str | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        """`model` is an alias from config.yaml or a model id; None means XSTORE_TIEBREAK_MODEL
        or the configured default (haiku)."""
        self.config = config or load_config()
        self.model_alias, spec = self.config.resolve(model)
        self.model_id = spec.id
        self.provider = provider or get_provider()
        self.server = build_server(stores)
        self.system = load_system_prompt()
        self.graph = self._build().compile()

    def _build(self) -> StateGraph[TiebreakState]:
        g: StateGraph[TiebreakState] = StateGraph(TiebreakState)
        g.add_node("fetch_evidence", self._fetch_evidence)
        g.add_node("decide", self._decide)
        g.add_node("validate", self._validate)
        g.add_edge(START, "fetch_evidence")
        g.add_edge("fetch_evidence", "decide")
        g.add_edge("decide", "validate")
        g.add_edge("validate", END)
        return g

    async def _call_evidence(self, client: Client, ref: EntityRef) -> dict[str, Any]:
        result = await client.call_tool("get_evidence", ref.model_dump())
        if result.is_error or result.structured_content is None:
            raise RuntimeError(f"get_evidence failed for {ref}: {result.content}")
        return dict(result.structured_content)

    async def _fetch_evidence(self, state: TiebreakState) -> dict[str, Any]:
        req = state["request"]
        async with Client(self.server) as client:
            left = await self._call_evidence(client, req.left)
            right = await self._call_evidence(client, req.right)
        evidence = {
            "pair_id": req.pair_id,
            "match_probability": req.match_probability,
            "left": left,
            "right": right,
        }
        return {"evidence": evidence}

    def _decide(self, state: TiebreakState) -> dict[str, Any]:
        evidence = state["evidence"]
        decision, usage, source = self.provider.decide(
            self.model_id, self.system, build_prompt(evidence), evidence
        )
        return {"decision": decision, "usage": usage, "source": source}

    def _validate(self, state: TiebreakState) -> dict[str, Any]:
        decision = state["decision"]
        if decision.confidence < MIN_CONFIDENCE:
            return {
                "decision": decision.model_copy(update={"same_entity": False}),
                "needs_human": True,
            }
        return {"needs_human": False}

    async def arun(self, request: TiebreakRequest) -> TiebreakResult:
        start = time.perf_counter()
        state: dict[str, Any] = await self.graph.ainvoke({"request": request})
        return TiebreakResult(
            request=request,
            decision=state["decision"],
            usage=state["usage"],
            model=self.model_id,
            provider=self.provider.name,
            source=state["source"],
            latency_ms=round((time.perf_counter() - start) * 1000, 3),
            needs_human=state["needs_human"],
        )

    def run(self, request: TiebreakRequest) -> TiebreakResult:
        return asyncio.run(self.arun(request))

    def run_many(self, requests: Iterable[TiebreakRequest]) -> list[TiebreakResult]:
        async def _all() -> list[TiebreakResult]:
            return [await self.arun(r) for r in requests]

        return asyncio.run(_all())
