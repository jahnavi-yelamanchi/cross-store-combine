# ADR-014: Deterministic policy engine with agent-proposed, human-approved diffs

- Context: stores disagree on the same field after matching; we need a repeatable winner per field and a way for review feedback to change the rules.
- Decision: `policy/policy.yaml` (Pydantic-validated, versioned, sha256 digest) drives a pure `PolicyEngine.resolve` that records the rule per field; agents only emit `PolicyDiff`s, which `apply_diff` accepts after human approval and bumps the version.
- Alternative rejected: letting the LLM pick the winning value per conflict at write time.
- Why: same inputs give the same record and an audit trail (rule, candidates, policy digest); LLM picks are non-reproducible, costly per record and hard to review; rule changes stay small diffs a human can read.
- Consequence: new resolution rules need engine code; the LLM agent (#27) plugs in via the `DiffProposer` protocol, with the count heuristic as default.
