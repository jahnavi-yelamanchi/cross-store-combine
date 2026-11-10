"""Render README figures from real runs: Splink band routing and the demo loop.

Usage: uv run python scripts/readme_figures.py [--out results]
"""

from __future__ import annotations

import argparse
import tempfile
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

from xstore.matching import load_customers, train_and_predict
from xstore.pipeline.demo import run_demo

BLUE = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def _axes(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _hbars(path: Path, labels: list[str], values: list[int], title: str, note: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 0.62 * len(labels) + 1.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    _axes(ax)
    y = list(range(len(labels)))[::-1]
    ax.barh(y, values, color=BLUE, height=0.56, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(y, labels, color=INK, fontsize=10)
    top = max(values)
    for yi, v in zip(y, values, strict=True):
        ax.text(v + top * 0.01, yi, f"{v:,}", va="center", color=INK, fontsize=10)
    ax.set_xlim(0, top * 1.15)
    ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=12)
    fig.text(0.01, 0.01, note, color=INK_2, fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def routing(out: Path, data: Path) -> dict[str, int]:
    pairs = train_and_predict(load_customers(data))
    bands = Counter(p.band for p in pairs)
    counts = {
        "candidate pairs (after blocking)": len(pairs),
        "auto-matched (p >= 0.95)": bands["match"],
        "sent to LLM tiebreak (0.50-0.95)": bands["tiebreak"],
    }
    _hbars(
        out / "llm_routing.png",
        list(counts),
        list(counts.values()),
        "Splink decides most pairs; the LLM sees the uncertain band only",
        f"frozen v1: {len(load_customers(data)):,} customer records across 6 stores; "
        f"{bands['non_match']} pairs fell below 0.50",
    )
    return counts


def demo_loop(out: Path) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmp:
        r = run_demo(200, 20, 7, Path(tmp))
    counts = {
        "fix proposals": r.proposals,
        "auto-safe": r.safe_proposals,
        "approved": r.approved,
        "applied": r.applied,
    }
    _hbars(
        out / "demo_loop.png",
        list(counts),
        list(counts.values()),
        "make demo-local: 20 injected faults -> approved, idempotent fixes",
        f"recall {r.recall:.2f} | bad writes {r.bad_writes} | "
        + ("replay verified" if r.replay_ok else "replay FAILED"),
    )
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    ap.add_argument("--data", default="evals/frozen/v1")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(routing(out, Path(args.data)))
    print(demo_loop(out))


if __name__ == "__main__":
    main()
