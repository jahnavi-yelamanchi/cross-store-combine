"""Fellegi-Sunter customer matcher: Splink on DuckDB, EM-trained m, sampled u."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
import splink.comparison_level_library as cll
import splink.comparison_library as cl
from splink import ColumnExpression, DuckDBAPI, Linker, SettingsCreator, block_on

from xstore.matching.models import CandidatePair, MatchBands, MatcherSettings, load_settings

PAIR_SCHEMA = pa.schema(
    [
        ("pair_id", pa.string()),
        ("entity_type", pa.string()),
        ("left_store", pa.string()),
        ("left_key", pa.string()),
        ("right_store", pa.string()),
        ("right_key", pa.string()),
        ("match_probability", pa.float64()),
        ("match_weight", pa.float64()),
        ("band", pa.string()),
    ]
)


def email_comparison(col: str = "email_norm") -> cl.CustomComparison:
    """Exact, same username (domain drift), edit distance 1 (typo), else.

    Splink's EmailComparison uses Jaro-Winkler 0.88, which scores shared
    `first.last<id>@` prefixes of different people as near-matches.
    """
    full = ColumnExpression(col)
    user = ColumnExpression(col).regex_extract("^[^@]+")
    return cl.CustomComparison(
        [
            cll.NullLevel(col),
            cll.ExactMatchLevel(full),
            cll.ExactMatchLevel(user).configure(label_for_charts="Exact match on username"),
            cll.LevenshteinLevel(full, 1),
            cll.ElseLevel(),
        ],
        output_column_name=col,
    )


def splink_settings(cfg: MatcherSettings) -> SettingsCreator:
    """Comparisons + prediction blocking rules for the union of all stores."""
    return SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="unique_id",
        comparisons=[
            cl.JaroWinklerAtThresholds("name_norm", [0.95, 0.88]),
            email_comparison("email_norm"),
            # Prior m for the phone mismatch level, which EM may not observe.
            cl.ExactMatch("phone_norm").configure(m_probabilities=[0.998, 0.002]),
            cl.ExactMatch("country"),
        ],
        blocking_rules_to_generate_predictions=[block_on(*r) for r in cfg.blocking_rules],
        additional_columns_to_retain=["source_store", "key"],
        retain_intermediate_calculation_columns=False,
    )


def build_linker(table: pa.Table, cfg: MatcherSettings) -> Linker:
    db_api = DuckDBAPI()
    sdf = db_api.register(table, dataset_display_name="customers")
    return Linker(sdf, splink_settings(cfg), log_level=logging.WARNING)


def train(linker: Linker, cfg: MatcherSettings) -> None:
    """Prior from deterministic rules, u by random sampling, m by EM."""
    t = linker.training
    t.estimate_probability_two_random_records_match(
        list(cfg.deterministic_rules), recall=cfg.deterministic_recall
    )
    t.estimate_u_using_random_sampling(max_pairs=cfg.u_max_pairs, seed=cfg.seed)
    for rule in cfg.em_training_rules:
        t.estimate_parameters_using_expectation_maximisation(block_on(*rule))


def _to_pair(row: dict[str, Any], bands: MatchBands) -> CandidatePair:
    left = (str(row["source_store_l"]), str(row["key_l"]))
    right = (str(row["source_store_r"]), str(row["key_r"]))
    if right < left:
        left, right = right, left
    p = round(float(row["match_probability"]), 6)
    return CandidatePair(
        pair_id=f"{left[0]}:{left[1]}|{right[0]}:{right[1]}",
        left_store=left[0],
        left_key=left[1],
        right_store=right[0],
        right_key=right[1],
        match_probability=p,
        match_weight=round(float(row["match_weight"]), 4),
        band=bands.band(p),
    )


def train_and_predict(
    table: pa.Table,
    bands: MatchBands | None = None,
    *,
    settings: MatcherSettings | None = None,
    model_path: Path | None = None,
) -> list[CandidatePair]:
    """Train the model on `table`, score blocked pairs, band them. Sorted by pair_id."""
    cfg = settings or load_settings()
    bands = bands or cfg.bands
    linker = build_linker(table, cfg)
    train(linker, cfg)
    if model_path is not None:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        linker.misc.save_model_to_json(str(model_path), overwrite=True)
    scored = linker.inference.predict(threshold_match_probability=cfg.predict_threshold)
    pairs = [_to_pair(r, bands) for r in scored.as_pyarrow_table().to_pylist()]
    return sorted(pairs, key=lambda p: p.pair_id)


def cluster(pairs: Iterable[CandidatePair], threshold: float) -> dict[str, str]:
    """unique_id -> cluster id (smallest member) over pairs with p >= threshold."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        a, b = find(p.left_id), find(p.right_id)
        if p.match_probability >= threshold and a != b:
            parent[max(a, b)] = min(a, b)
    return {x: find(x) for x in sorted(parent)}


def pairs_to_table(pairs: Iterable[CandidatePair]) -> pa.Table:
    return pa.Table.from_pylist([p.model_dump() for p in pairs], schema=PAIR_SCHEMA)
