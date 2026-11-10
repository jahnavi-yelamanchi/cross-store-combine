import json
from pathlib import Path

import httpx
import pytest

from xstore.contracts import (
    build_issue,
    check_native,
    check_records,
    check_store,
    contract_for,
    open_issue,
)
from xstore.contracts.__main__ import main, run_check
from xstore.stores import get_store
from xstore.synth.generator import generate


@pytest.mark.smoke
def test_clean_passes_renamed_field_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = generate(seed=3, customers=40, orders=40, batches=20)
    assert not any(r.failed or r.violations for r in check_native(data))
    assert main(["check", "--synthetic", "20"]) == 0

    pg = contract_for("postgres")
    store = get_store("postgres")
    for rec in data["postgres"]["customer"][:5]:
        store.write("customer", rec, f"contracts-{rec['id']}")
    live = {r.entity_type: r for r in check_store(store, pg)}
    assert live["customer"].records == 5 and not live["customer"].violations

    bad = [dict(r) for r in data["postgres"]["customer"][:3]]
    bad[0]["phone_number"] = bad[0].pop("phone")
    bad[1]["amount"] = "x"
    viol = check_records(pg, "customer", bad)
    kinds = {(v.kind, v.field, v.severity) for v in viol}
    assert ("schema_drift", "phone->phone_number", "fail") in kinds
    assert ("schema_drift", "amount", "fail") in kinds

    rep = check_native({"postgres": {"customer": bad}})
    assert run_check(rep, open_issues=True, issues_dir=tmp_path) == 1  # dry-run without env
    written = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert written["title"] == "Contract drift: postgres.customer (2 fields)"
    assert "Contract drift" in capsys.readouterr().out


@pytest.mark.smoke
def test_github_issue_posts_and_dedupes(tmp_path: Path) -> None:
    pg = contract_for("postgres")
    rec = dict(generate(seed=3, customers=1, orders=1, batches=1)["postgres"]["customer"][0])
    rec["email_address"] = rec.pop("email")
    payload = build_issue(check_native({"postgres": {"customer": [rec]}})[0])
    assert payload.labels == ["area:contracts", "contract-drift"]
    assert pg.info.owner in payload.body and "email->email_address" in payload.body

    seen: list[httpx.Request] = []
    existing: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=existing)
        return httpx.Response(201, json={"number": 7, "html_url": "https://gh/i/7"})

    env = {"XSTORE_OPEN_ISSUES": "1", "GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test")
    res = open_issue(payload, env=env, client=client, out_dir=tmp_path)
    assert (res.mode, res.number) == ("created", 7)
    get, post = seen
    assert get.url.params["labels"] == "contract-drift"
    assert post.url.path == "/repos/o/r/issues"
    assert post.headers["Authorization"] == "Bearer t"
    assert json.loads(post.content) == payload.model_dump()

    existing.append({"title": payload.title, "number": 7, "html_url": "https://gh/i/7"})
    assert open_issue(payload, env=env, client=client, out_dir=tmp_path).mode == "duplicate"
    assert len(seen) == 3  # GET only, no second POST

    dry = open_issue(payload, env={"XSTORE_OPEN_ISSUES": "1"}, out_dir=tmp_path)
    assert dry.mode == "dry_run" and dry.path and Path(dry.path).exists()
