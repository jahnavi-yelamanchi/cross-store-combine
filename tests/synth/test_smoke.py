from pathlib import Path

import pytest

from xstore.synth.freeze import verify
from xstore.synth.generator import generate
from xstore.synth.injector import TYPES, inject

FROZEN = Path(__file__).resolve().parents[2] / "evals" / "frozen" / "v1"


@pytest.mark.smoke
def test_small_run_is_deterministic_and_frozen_hash_matches() -> None:
    runs = []
    for _ in range(2):
        data = generate(7, customers=80, orders=80, batches=40)
        runs.append((data, inject(data, n=16, seed=7)))
    assert runs[0] == runs[1]
    assert {lab.type for lab in runs[0][1]} == set(TYPES)
    assert verify(FROZEN)
