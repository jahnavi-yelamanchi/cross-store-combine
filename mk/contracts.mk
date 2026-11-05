.PHONY: contracts-gen contracts-check

# Bootstrap only: committed contracts/*.yaml are the source of truth (use FORCE=1 to regenerate).
contracts-gen:
	uv run python scripts/gen_contracts.py $(if $(FORCE),--force,)

# Fails (exit 1) on the frozen snapshot: it carries injected schema drift by design.
contracts-check:
	uv run python -m xstore.contracts check --snapshot evals/frozen/v1
