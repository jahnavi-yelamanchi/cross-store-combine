"""promptfoo prompt function: system = src/xstore/agent/prompts/tiebreak.md, user = evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SYSTEM = (
    Path(__file__).resolve().parents[2] / "src" / "xstore" / "agent" / "prompts" / "tiebreak.md"
)
JSON_ONLY = (
    "\n\nRespond with only a JSON object with keys same_entity (boolean), confidence (number), "
    "rationale (string) and evidence_fields (array of strings)."
)


def tiebreak(context: dict[str, Any]) -> str:
    evidence = context["vars"]["evidence"]
    if not isinstance(evidence, str):
        evidence = json.dumps(evidence, indent=2, sort_keys=True)
    return json.dumps(
        [
            {"role": "system", "content": SYSTEM.read_text()},
            {"role": "user", "content": "Evidence:\n" + evidence + JSON_ONLY},
        ]
    )
