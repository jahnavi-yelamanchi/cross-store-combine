"""Customer entity resolution across stores (Splink, Fellegi-Sunter, DuckDB)."""

from xstore.matching.canonical import customers_from_stores, load_customers
from xstore.matching.models import CandidatePair, MatchBands, MatcherSettings, load_settings
from xstore.matching.splink_matcher import cluster, pairs_to_table, train_and_predict

__all__ = [
    "CandidatePair",
    "MatchBands",
    "MatcherSettings",
    "cluster",
    "customers_from_stores",
    "load_customers",
    "load_settings",
    "pairs_to_table",
    "train_and_predict",
]
