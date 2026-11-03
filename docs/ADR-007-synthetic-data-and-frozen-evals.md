# ADR-007: Seeded synthetic data and hashed frozen evals

- Context: matching and drift detection need data with known inconsistencies across 7 local stores, reproducible across branches and runs.
- Decision: stdlib-seeded generator renders canonical entities through store mappings; injector adds 500 balanced labelled faults of 8 types; output frozen as JSONL in `evals/frozen/v1` with a 400/100 label/holdout split and a sha256 manifest checked by `make verify-frozen`.
- Alternative rejected: Faker-based generation regenerated at test time.
- Why: no extra dependency, byte-identical output per seed, and a committed snapshot means eval numbers stay comparable even if generator code changes.
- Consequence: ~9 MB of JSONL in git; generator changes require a new `v2` rather than editing `v1`.
