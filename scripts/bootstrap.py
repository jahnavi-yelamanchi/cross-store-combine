"""Flip local -> real: check real-mode env vars per layer, write .env for make.

Usage: python scripts/bootstrap.py [--layers stores,model,...] [--force]
Writes .env with STORE_BACKEND=real and MODEL_PROVIDER=<provider>; the root
Makefile includes .env, so every make target then runs against real backends.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

LAYERS: dict[str, list[str]] = {
    "model": ["ANTHROPIC_API_KEY"],
    "postgres": ["DATABASE_URL"],
    "mongodb": ["MONGO_URI"],
    "aws": ["AWS_REGION", "XSTORE_S3_BUCKET"],
    "azure": ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "COSMOS_ENDPOINT", "AZURE_STORAGE_ACCOUNT"],
    "snowflake": ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE"],
    "crm": ["HUBSPOT_TOKEN"],
}
OPTIONAL = ["OPENAI_API_KEY", "MONGO_DB", "LANGFUSE_PUBLIC_KEY", "OTEL_EXPORTER_OTLP_ENDPOINT"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", default=",".join(LAYERS))
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--force", action="store_true", help="write .env even if vars are missing")
    ap.add_argument("--out", default=".env")
    args = ap.parse_args(argv)
    missing = [
        (layer, var)
        for layer in args.layers.split(",")
        for var in LAYERS[layer]
        if not os.environ.get(var)
    ]
    for layer, var in missing:
        print(f"missing {var} ({layer})")
    if missing and not args.force:
        print("not switching to real mode; set the vars above or pass --force")
        return 1
    lines = ["STORE_BACKEND=real", f"MODEL_PROVIDER={args.provider}"]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out}: {', '.join(lines)}; secrets stay in your environment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
