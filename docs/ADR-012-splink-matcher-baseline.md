# ADR-012: Splink Fellegi-Sunter matcher on DuckDB as the entity resolution baseline

- Context: the same customer lives in six stores under drifted names, emails and missing fields, plus injected in-store duplicates; record linkage across stores and dedupe within them must run locally in seconds.
- Decision: Splink 5 on DuckDB, dedupe_only over the union of stores (source_store as a column); blocking rules on email, phone and name prefix + country; prior from deterministic rules, u by seeded random sampling, m by EM on two blocking rules; config in matching/settings.yaml.
- Alternative rejected: exact-key joins (miss typos, drifted fields, re-keyed duplicates); LLM-only matching (cost and latency on ~60k pairs, not reproducible).
- Why: Fellegi-Sunter gives a calibrated match probability per pair with explainable per-field weights, trains without labels, and is deterministic with a fixed seed.
- Consequence: pairs are banded (match >= 0.95, non_match < 0.50, tiebreak between); only the tiebreak band goes to the LLM tiebreak, so model spend scales with ambiguity, not volume.
