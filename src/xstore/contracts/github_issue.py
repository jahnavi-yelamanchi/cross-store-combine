"""Open a GitHub issue per drifted store.entity; dry-run to JSON unless explicitly enabled."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from xstore.contracts.check import EntityReport

API_URL = "https://api.github.com"
LABELS = ["area:contracts", "contract-drift"]
DEFAULT_OUT = Path("runs/contract_issues")


class IssuePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    body: str
    labels: list[str]


class IssueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["dry_run", "duplicate", "created"]
    title: str
    path: str | None = None
    url: str | None = None
    number: int | None = None


def build_issue(rep: EntityReport) -> IssuePayload:
    fields = sorted({v.field for v in rep.violations})
    rows = "\n".join(
        f"| {v.kind} | `{v.field}` | {v.count} / {v.total} | {v.severity} | "
        f"{', '.join(v.sample_keys)} |"
        for v in rep.violations
    )
    body = (
        f"Contract check found drift in `{rep.store}.{rep.entity_type}` "
        f"(owner: `{rep.owner}`, {rep.records} records scanned).\n\n"
        "| kind | field | records | severity | sample keys |\n"
        "|---|---|---|---|---|\n"
        f"{rows}\n\n"
        f"Update the producer or `contracts/{rep.store}.yaml` (a contract version bump, "
        "reviewed by the owning team). Records are not coerced in the meantime.\n"
    )
    return IssuePayload(
        title=f"Contract drift: {rep.store}.{rep.entity_type} ({len(fields)} fields)",
        body=body,
        labels=list(LABELS),
    )


def _live(env: Mapping[str, str]) -> bool:
    return (
        env.get("XSTORE_OPEN_ISSUES") == "1"
        and bool(env.get("GITHUB_TOKEN"))
        and bool(env.get("GITHUB_REPOSITORY"))
    )


def _dry_run(payload: IssuePayload, out_dir: Path) -> IssueResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", payload.title.lower()).strip("-")
    path = out_dir / f"{slug}.json"
    text = json.dumps(payload.model_dump(), indent=2) + "\n"
    path.write_text(text)
    print(text, end="")
    return IssueResult(mode="dry_run", title=payload.title, path=str(path))


def open_issue(
    payload: IssuePayload,
    *,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    out_dir: Path = DEFAULT_OUT,
) -> IssueResult:
    """POST /repos/{repo}/issues unless an open issue with the same title exists.

    Live only when XSTORE_OPEN_ISSUES=1 and GITHUB_TOKEN + GITHUB_REPOSITORY are set;
    otherwise writes the payload to `out_dir` and prints it.
    """
    env = os.environ if env is None else env
    if not _live(env):
        return _dry_run(payload, out_dir)
    repo = env["GITHUB_REPOSITORY"]
    headers = {
        "Authorization": f"Bearer {env['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    own = client is None
    http = client or httpx.Client(base_url=API_URL, timeout=30)
    try:
        resp = http.get(
            f"/repos/{repo}/issues",
            params={"labels": "contract-drift", "state": "open", "per_page": 100},
            headers=headers,
        )
        resp.raise_for_status()
        existing: list[dict[str, Any]] = resp.json()
        for issue in existing:
            if issue.get("title") == payload.title:
                return IssueResult(
                    mode="duplicate",
                    title=payload.title,
                    url=issue.get("html_url"),
                    number=issue.get("number"),
                )
        resp = http.post(f"/repos/{repo}/issues", json=payload.model_dump(), headers=headers)
        resp.raise_for_status()
        created: dict[str, Any] = resp.json()
        return IssueResult(
            mode="created",
            title=payload.title,
            url=created.get("html_url"),
            number=created.get("number"),
        )
    finally:
        if own:
            http.close()
