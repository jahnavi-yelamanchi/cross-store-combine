# ADR-011: Threshold eval gate on holdout, nightly full matrix

- Context: matcher, tiebreak prompt and policy changes can silently lower accuracy or cause bad writes.
- Decision: eval-gate.yml runs Inspect + holdout matrix per PR and fails below evals/thresholds.yaml (hybrid F1, recall, bad-write rate 0, $/1k); nightly runs train + holdout and publishes the report.
- Alternative rejected: report-only evals without a gate; full matrix on every PR.
- Why: a hard gate makes regressions block merges; holdout-only keeps PR runtime short.
- Consequence: thresholds live in one YAML; steps for dbt and Great Expectations activate when those layers land.
