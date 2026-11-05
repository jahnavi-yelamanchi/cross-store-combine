"""Record live tiebreak calls as MockProvider fixtures.

python -m xstore.agent.record_fixtures --pairs pairs.jsonl [--stores a,b] [--model haiku]

Each line of --pairs is a TiebreakRequest JSON object. Real mode only: refuses MODEL_PROVIDER=mock.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from xstore.agent.graph import TiebreakAgent
from xstore.agent.models import TiebreakDecision, TiebreakRequest, Usage
from xstore.agent.providers import FIXTURES_DIR, ModelProvider, get_provider, write_fixture
from xstore.stores import get_store


class RecordingProvider:
    """Wraps a live provider and writes every response as a fixture."""

    def __init__(self, inner: ModelProvider, out_dir: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.out_dir = out_dir

    def decide(
        self, model: str, system: str, prompt: str, evidence: dict[str, Any]
    ) -> tuple[TiebreakDecision, Usage, str]:
        decision, usage, source = self.inner.decide(model, system, prompt, evidence)
        write_fixture(model, system, prompt, decision, usage, self.out_dir)
        return decision, usage, source


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pairs", type=Path, required=True)
    p.add_argument("--stores", default="postgres,mongodb")
    p.add_argument("--model", default=None)
    p.add_argument("--out", type=Path, default=FIXTURES_DIR)
    args = p.parse_args(argv)

    if os.environ.get("MODEL_PROVIDER", "mock").lower() == "mock":
        print(
            "record_fixtures needs a live provider (MODEL_PROVIDER=anthropic|openai)",
            file=sys.stderr,
        )
        return 2
    requests = [
        TiebreakRequest.model_validate_json(line)
        for line in args.pairs.read_text().splitlines()
        if line.strip()
    ]
    stores = {n: get_store(n) for n in args.stores.split(",") if n}
    provider = RecordingProvider(get_provider(), args.out)
    agent = TiebreakAgent(stores, provider=provider, model=args.model)
    for result in agent.run_many(requests):
        print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
