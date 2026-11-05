"""Unit prices from evals/prices.yaml: token cost (cache reads/writes priced separately), ledger."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from xstore.agent import Usage

PRICES_PATH = Path(__file__).resolve().parents[3] / "evals" / "prices.yaml"


@cache
def load_prices(path: Path = PRICES_PATH) -> dict[str, Any]:
    prices: dict[str, Any] = yaml.safe_load(path.read_text())
    return prices


def token_cost(usage: Usage, model_id: str, prices: dict[str, Any] | None = None) -> float:
    """USD for one Usage. input_tokens excludes cached tokens (Anthropic usage semantics)."""
    p = (prices or load_prices())["models"][model_id]
    return (
        usage.input_tokens * p["input"]
        + usage.output_tokens * p["output"]
        + usage.cache_write_tokens * p["cache_write_5m"]
        + usage.cache_read_tokens * p["cache_read"]
    ) / 1e6


def ledger_write_cost(writes: int, prices: dict[str, Any] | None = None) -> dict[str, float]:
    """USD to append `writes` ~1 KB ledger items on DynamoDB on-demand vs Cosmos DB serverless."""
    s = (prices or load_prices())["storage"]
    ddb, cosmos = s["dynamodb_on_demand"], s["cosmosdb_serverless"]
    return {
        "dynamodb": writes
        * ddb["write_units_per_1kb_write"]
        * ddb["usd_per_million_write_units"]
        / 1e6,
        "cosmosdb": writes
        * cosmos["request_units_per_1kb_write"]
        * cosmos["usd_per_million_request_units"]
        / 1e6,
    }


def splink_cost_per_1k(prices: dict[str, Any] | None = None) -> float:
    return float((prices or load_prices())["compute"]["splink_compute_per_1k_entities"])
