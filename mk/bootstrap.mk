.PHONY: bootstrap local

bootstrap:  ## local -> real: check env vars, write .env (STORE_BACKEND=real)
	uv run python scripts/bootstrap.py

local:  ## real -> local
	rm -f .env
