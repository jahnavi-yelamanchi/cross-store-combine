"""`python -m xstore.policy check policy/policy.yaml`: validate and print version + digest."""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from xstore.policy.engine import check_policy, load_policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m xstore.policy")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="validate a policy file")
    check.add_argument("path")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.path)
    except (OSError, ValidationError) as exc:
        print(f"invalid policy: {exc}", file=sys.stderr)
        return 1
    problems = check_policy(policy)
    for p in problems:
        print(f"problem: {p}", file=sys.stderr)
    print(f"version {policy.version} digest {policy.digest()}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
