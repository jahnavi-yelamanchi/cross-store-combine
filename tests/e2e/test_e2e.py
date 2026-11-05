from pathlib import Path

import pytest

from xstore.pipeline.demo import run_demo


@pytest.mark.e2e
def test_inject_detect_propose_approve_apply_replay(tmp_path: Path) -> None:
    report = run_demo(n_customers=120, n_injections=15, seed=3, workdir=tmp_path)

    assert report.observable_injections > 0
    assert report.undetected == [] and report.recall == 1.0
    assert report.safe_proposals > 0 and report.approved == report.safe_proposals
    assert report.applied == report.approved and report.failed == 0
    assert report.reapplied == 0  # second apply_pending is a no-op
    assert report.bad_writes == 0
    assert report.replay_ok and report.replay_fixes == report.applied
    assert report.remaining_conflicts == 0
