"""CLI: `python -m xstore.contracts check --snapshot evals/frozen/v1 [--open-issues]`."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from xstore.contracts.check import EntityReport, check_native, check_snapshot
from xstore.contracts.github_issue import DEFAULT_OUT, build_issue, open_issue
from xstore.contracts.models import CONTRACTS_DIR

KINDS = ("schema_drift", "type_drift", "missing_field", "enum")


def summarize(reports: list[EntityReport]) -> str:
    lines: list[str] = []
    for store in sorted({r.store for r in reports}):
        rows = [r for r in reports if r.store == store]
        status = "FAIL" if any(r.failed for r in rows) else "ok"
        lines.append(f"{store} (owner {rows[0].owner}): {status}")
        for r in rows:
            counts = Counter[str]()
            for v in r.violations:
                counts[v.kind] += v.count
            kinds = " ".join(f"{k}={counts[k]}" for k in KINDS)
            lines.append(f"  {r.entity_type:<9} {r.records:>6} records  {kinds}")
            for v in r.violations:
                lines.append(
                    f"    [{v.severity}] {v.kind} {v.field} x{v.count} "
                    f"e.g. {', '.join(v.sample_keys[:3])}"
                )
    return "\n".join(lines)


def run_check(
    reports: list[EntityReport], *, open_issues: bool = False, issues_dir: Path = DEFAULT_OUT
) -> int:
    """Print the summary, optionally file issues for failing entities; exit code 1 on failure."""
    print(summarize(reports))
    failing = [r for r in reports if r.failed]
    if open_issues:
        for r in failing:
            res = open_issue(build_issue(r), out_dir=issues_dir)
            print(f"issue {res.mode}: {res.title} {res.url or res.path or ''}".rstrip())
    print(f"contracts: {len(failing)} failing of {len(reports)} entity checks")
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m xstore.contracts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="check native records against contracts/")
    src = chk.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", type=Path, help="frozen snapshot dir, e.g. evals/frozen/v1")
    src.add_argument("--synthetic", type=int, metavar="N", help="clean generated company size")
    chk.add_argument("--seed", type=int, default=7)
    chk.add_argument("--contracts", type=Path, default=CONTRACTS_DIR)
    chk.add_argument("--open-issues", action="store_true")
    chk.add_argument("--issues-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if args.snapshot is not None:
        reports = check_snapshot(args.snapshot, args.contracts)
    else:
        from xstore.synth.generator import generate

        n = args.synthetic
        reports = check_native(generate(args.seed, n, n, max(n // 2, 1)), args.contracts)
    return run_check(reports, open_issues=args.open_issues, issues_dir=args.issues_dir)


if __name__ == "__main__":
    raise SystemExit(main())
