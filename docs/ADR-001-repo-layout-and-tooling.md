# ADR-001: Repo layout and tooling

- Context: one Python codebase spanning 8 store adapters, CDC, matching, agent, API; built in parallel branches.
- Decision: single `src/xstore` package, uv for env + lock, ruff + mypy via pre-commit, pytest markers `smoke`/`e2e`; all Day 1 runtime deps declared on main up front.
- Alternative rejected: uv workspace with one package per layer; per-branch dependency additions.
- Why: one package keeps imports simple; deps on main avoid pyproject/uv.lock merge conflicts across parallel branches.
- Consequence: install pulls all adapters' deps even when one store is used.
