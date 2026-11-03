# ADR-010: Bot-authored PRs via GITHUB_TOKEN

- Context: PRs opened with the owner's token or the Claude app show the owner as author, so owner reviews are self-reviews.
- Decision: pushes to `bot/<issue>-<slug>` trigger a workflow that opens the PR as github-actions[bot] and runs ruff, mypy, smoke on the push.
- Alternative rejected: machine-user account with its own PAT; Claude Code GitHub Action with a model key.
- Why: no extra account or secret; built-in token is scoped to this repo.
- Consequence: GITHUB_TOKEN-created PRs do not trigger `ci.yml`; checks are duplicated in `bot-pr.yml`; PR body is edited after creation.
