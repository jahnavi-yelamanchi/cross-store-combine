"""Langfuse generations for tiebreak calls (langfuse 5.x). No-op without credentials.

Enabled when LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST (or LANGFUSE_BASE_URL)
are set. Only the request refs and the decision are sent, never the store evidence.
"""

from __future__ import annotations

import os
from functools import cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xstore.agent.models import TiebreakResult

ENV_VARS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


def enabled() -> bool:
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY") and host
    )


@cache
def _client() -> Any:
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL"),
            environment=os.environ.get("XSTORE_ENV", "local"),
        )
    except Exception:
        return None


def record_generation(result: TiebreakResult, cost_usd: float, run_id: str | None) -> bool:
    """Send one generation; returns True if handed to the Langfuse client."""
    if not enabled():
        return False
    try:
        client = _client()
        if client is None:
            return False
        u = result.usage
        gen = client.start_observation(
            name="tiebreak",
            as_type="generation",
            model=result.model,
            input=result.request.model_dump(mode="json"),
            output=result.decision.model_dump(mode="json"),
            usage_details={
                "input": u.input_tokens,
                "output": u.output_tokens,
                "cache_read_input_tokens": u.cache_read_tokens,
                "cache_creation_input_tokens": u.cache_write_tokens,
            },
            cost_details={"total": cost_usd},
            metadata={
                "run_id": run_id,
                "provider": result.provider,
                "source": result.source,
                "latency_ms": result.latency_ms,
                "needs_human": result.needs_human,
            },
        )
        gen.end()
        return True
    except Exception:  # telemetry must never break the agent
        return False


def flush() -> None:
    if enabled():
        try:
            client = _client()
            if client is not None:
                client.flush()
        except Exception:
            pass
