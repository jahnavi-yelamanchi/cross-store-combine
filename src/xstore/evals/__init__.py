"""Evals: baseline detector, comparison matrix and pricing over the frozen snapshot."""

from xstore.evals.detection import Detection, Detector, pair_duplicates
from xstore.evals.matrix import CONFIGS, Config, run_matrix, score
from xstore.evals.snapshot import Snapshot, SnapshotStore, load_snapshot

__all__ = [
    "CONFIGS",
    "Config",
    "Detection",
    "Detector",
    "Snapshot",
    "SnapshotStore",
    "load_snapshot",
    "pair_duplicates",
    "run_matrix",
    "score",
]
