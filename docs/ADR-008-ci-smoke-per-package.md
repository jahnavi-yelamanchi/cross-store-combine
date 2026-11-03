# ADR-008: CI runs lint, types and one smoke test per package

- Context: 8 parallel branches; each adds a package that must stay importable and runnable on local backends.
- Decision: one GitHub Actions job: ruff, mypy, `pytest -m smoke`; each package ships its smoke test in its own PR; conftest pins STORE_BACKEND=local, MODEL_PROVIDER=mock.
- Alternative rejected: one CI PR carrying every package's smoke test.
- Why: a test lands with the code it covers; CI on this branch cannot import packages that are not merged yet.
- Consequence: smoke exit code 5 (no tests) is tolerated until package PRs merge.
