# ADR-026: `make bootstrap` flips local to real via .env

- Context: every run defaults to local backends and the mock model; real mode needs per-layer credentials.
- Decision: `scripts/bootstrap.py` checks required env vars per layer and writes a gitignored `.env` with `STORE_BACKEND=real` and `MODEL_PROVIDER`; the root Makefile includes it; secrets stay in the environment.
- Alternative rejected: a committed config file with credentials; per-command flags.
- Why: one switch for every make target; refuses to flip with missing vars; nothing secret on disk.
- Consequence: `make local` deletes `.env`; adapters still read their own env vars at call time.
