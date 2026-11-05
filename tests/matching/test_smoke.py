from collections import Counter
from pathlib import Path

import pytest

from xstore.matching import cluster, load_customers, load_settings, train_and_predict

DATA = Path(__file__).resolve().parents[2] / "evals" / "frozen" / "v1"


@pytest.mark.smoke
def test_splink_matcher_bands_and_determinism(tmp_path: Path) -> None:
    table = load_customers(DATA, limit_per_store=800)
    assert table.num_rows > 4000
    pairs = train_and_predict(table, model_path=tmp_path / "model.json")
    assert (tmp_path / "model.json").exists()

    bands = Counter(p.band for p in pairs)
    assert bands["match"] > 0 and bands["tiebreak"] + bands["non_match"] > 0

    pg_mongo = [
        p
        for p in pairs
        if {p.left_store, p.right_store} == {"mongodb", "postgres"} and p.left_key == p.right_key
    ]
    assert len(pg_mongo) > 700
    assert sum(p.band == "match" for p in pg_mongo) / len(pg_mongo) > 0.9

    clusters = cluster(pairs, load_settings().bands.match_at)
    assert clusters["postgres:C000001"] == clusters["mongodb:C000001"]

    again = train_and_predict(table)
    assert [(p.pair_id, p.band, p.match_probability) for p in again] == [
        (p.pair_id, p.band, p.match_probability) for p in pairs
    ]
