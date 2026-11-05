"""Render runs/*.jsonl into results/results.md and results/f1_vs_cost.png.

uv run python scripts/report.py --runs runs --out results
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Validated categorical slots 1-3 (blue, orange, aqua): colorblind-safe for all pairs.
COLORS = {"splink_only": "#2a78d6", "llm_only": "#eb6834", "hybrid": "#1baf7a"}
MARKERS = {
    ("haiku", "batch"): "o",
    ("haiku", "streaming"): "s",
    ("sonnet", "batch"): "D",
    ("sonnet", "streaming"): "P",
    ("none", "batch"): "o",
    ("none", "streaming"): "s",
}
ZERO_COST_AT = 1e-5  # where $0 configs sit on the log axis
TYPES = (
    "duplicate",
    "stale",
    "typo",
    "missing",
    "type_drift",
    "orphaned",
    "schema_drift",
    "unit_mismatch",
)


def load_latest(runs: Path) -> dict[str, list[dict[str, Any]]]:
    """Rows of the newest run_id per split."""
    rows = [
        json.loads(line)
        for f in sorted(runs.glob("*.jsonl"))
        for line in f.read_text().splitlines()
        if line.strip()
    ]
    by_split: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_split[r["split"]][r["run_id"]].append(r)
    return {s: runs_[max(runs_)] for s, runs_ in sorted(by_split.items())}


def _label(r: dict[str, Any]) -> str:
    return f"{r['config']} / {r['model']} / {r['mode']}"


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def gate_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    hyb = [r for r in rows if r["config"] == "hybrid"]
    return hyb[-1] if hyb else None


def markdown(latest: dict[str, list[dict[str, Any]]], png: str) -> str:
    lines = ["# Eval results", ""]
    any_sim = any(r.get("note") == "simulated" for rows in latest.values() for r in rows)
    if any_sim:
        lines += [
            "> Agent verdicts and token counts marked `simulated` come from the offline "
            "MockProvider (rule-based verdict with a seeded per-model error rate, tokens "
            "estimated as chars/4), not from Claude. They measure the harness, not the model.",
            "",
        ]
    for split, rows in latest.items():
        r0 = rows[0]
        lines += [
            f"## Matrix: {split} split",
            "",
            f"run `{r0['run_id']}`; {r0['n_entities']} entities, {r0['n_labels']} labels, "
            f"{r0['candidate_pairs']} Splink candidate pairs ({r0['tiebreak_pairs']} in the "
            f"tiebreak band); prices as of {r0['prices_as_of']}.",
            "",
        ]
        lines += _table(
            [
                "config",
                "model",
                "mode",
                "precision",
                "recall",
                "F1",
                "$/1k entities",
                "correct fix",
                "bad write",
                "approvals",
                "note",
            ],
            [
                [
                    r["config"],
                    r["model"],
                    r["mode"],
                    f"{r['precision']:.3f}",
                    f"{r['recall']:.3f}",
                    f"{r['f1']:.3f}",
                    f"{r['cost_per_1k']:.5f}",
                    f"{r['correct_fix_rate']:.3f}",
                    f"{r['bad_write_rate']:.3f}",
                    r["approvals_needed"],
                    r["note"],
                ]
                for r in rows
            ],
        )
        lines.append("")
    if "holdout" in latest and (g := gate_row(latest["holdout"])):
        lines += [f"## Per-type precision / recall: hybrid / {g['model']} / {g['mode']}", ""]
        heads = ["type"] + [f"{s} P" for s in latest] + [f"{s} R" for s in latest]
        gates = {s: gate_row(rows) for s, rows in latest.items()}
        body = []
        for t in TYPES:
            pt = {s: (g_ or {}).get("per_type", {}).get(t, {}) for s, g_ in gates.items()}
            body.append(
                [t]
                + [f"{pt[s].get('precision', 0):.3f}" for s in latest]
                + [f"{pt[s].get('recall', 0):.3f}" for s in latest]
            )
        lines += [*_table(heads, body), ""]
    lines += ["## Cost", ""]
    cost_rows = []
    for split, rows in latest.items():
        for r in rows:
            u = r["usage"]
            ledger = r["ledger_write_cost_usd"]
            cost_rows.append(
                [
                    split,
                    _label(r),
                    r["agent_calls"],
                    u["input_tokens"],
                    u["output_tokens"],
                    u["cache_read_tokens"],
                    u["cache_write_tokens"],
                    f"{r['llm_cost_usd']:.4f}",
                    f"{r['cost_per_1k']:.5f}",
                    f"{ledger['dynamodb']:.6f}",
                    f"{ledger['cosmosdb']:.6f}",
                ]
            )
    lines += _table(
        [
            "split",
            "config",
            "agent calls",
            "input tok",
            "output tok",
            "cache read tok",
            "cache write tok",
            "LLM $",
            "$/1k entities",
            "ledger $ DynamoDB",
            "ledger $ Cosmos DB",
        ],
        cost_rows,
    )
    notes = sorted(
        {r["sample_note"] for rows in latest.values() for r in rows if "sample_note" in r}
    )
    lines += [
        "",
        "Token prices: evals/prices.yaml (cache reads and 5-minute cache writes priced "
        "separately). Ledger prices are placeholders marked `verify` in that file.",
    ]
    lines += [f"- {n}" for n in notes]
    lines += ["", "## Latency", ""]
    lines += _table(
        ["split", "config", "p50 ms", "p95 ms", "measured as"],
        [
            [
                split,
                _label(r),
                f"{r['latency_p50_ms']:.1f}",
                f"{r['latency_p95_ms']:.1f}",
                "CDC event -> detection" if r["mode"] == "streaming" else "full run (incl. Splink)",
            ]
            for split, rows in latest.items()
            for r in rows
        ],
    )
    lines += ["", f"![F1 vs cost]({png})", ""]
    return "\n".join(lines)


def plot(rows: list[dict[str, Any]], split: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    groups: dict[tuple[float, float], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        x = r["cost_per_1k"] if r["cost_per_1k"] > 0 else ZERO_COST_AT
        groups[(round(x, 8), r["f1"])].append(r)
    for (x, y), rs in groups.items():
        for r in rs:
            family = "hybrid" if r["config"].startswith("hybrid") else r["config"]
            hollow = r["config"] == "hybrid_newest_wins"
            ax.scatter(
                x,
                y,
                s=70,
                marker=MARKERS.get((r["model"], r["mode"]), "o"),
                facecolors="none" if hollow else COLORS[family],
                edgecolors=COLORS[family] if hollow else "white",
                linewidths=1.5,
                zorder=3,
            )
        text = "\n".join(f"{r['config']} {r['model']} {r['mode']}".replace(" none", "") for r in rs)
        ax.annotate(
            text,
            (x, y),
            xytext=(7, -4),
            textcoords="offset points",
            fontsize=7,
            color="#52514e",
            va="top",
        )
    ax.set_xscale("log")
    ax.set_xlabel("Cost, USD per 1k entities (log scale; $0 plotted at 1e-5)")
    ax.set_ylabel("F1 vs ground truth")
    ax.set_title(f"Detection F1 vs cost per configuration ({split} split, frozen v1)")
    ys = [r["f1"] for r in rows]
    ax.set_ylim(max(0.0, min(ys) - 0.05), min(1.0, max(ys) + 0.03))
    ax.grid(True, color="#e6e5e0", linewidth=0.6, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    handles = [
        plt.Line2D([], [], marker="o", linestyle="", color=c, label=k) for k, c in COLORS.items()
    ]
    handles.append(
        plt.Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            markerfacecolor="none",
            color=COLORS["hybrid"],
            label="hybrid, newest-wins policy",
        )
    )
    ax.legend(handles=handles, loc="lower right", fontsize=8, frameon=False)
    if any(r.get("note") == "simulated" for r in rows):
        fig.text(
            0.01,
            0.01,
            "LLM verdicts and tokens simulated by MockProvider",
            fontsize=7,
            color="#52514e",
        )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="report")
    ap.add_argument("--runs", type=Path, default=Path("runs"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args(argv)
    latest = load_latest(args.runs)
    if not latest:
        print(f"no runs in {args.runs}", file=sys.stderr)
        return 1
    split = "holdout" if "holdout" in latest else next(iter(latest))
    png = args.out / "f1_vs_cost.png"
    plot(latest[split], split, png)
    md = args.out / "results.md"
    md.write_text(markdown(latest, png.name))
    print(f"-> {md}, {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
