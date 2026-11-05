from typing import Any

import pytest
from fastapi.testclient import TestClient

from xstore.proposals import Proposal, SqlRepository
from xstore.proposals.app import create_app


def _payload(action: str, confidence: float, detection_type: str) -> dict[str, Any]:
    record = {"customer_id": "c1", "email": "a@x.io"} if action == "upsert" else None
    return {
        "fix": {
            "target_store": "postgres",
            "entity_type": "customer",
            "key": "c1",
            "action": action,
            "record": record,
        },
        "evidence": [
            {
                "store": "mongodb",
                "entity_type": "customer",
                "key": "c1",
                "field": "email",
                "value": "a@x.io",
                "source": "store",
            }
        ],
        "confidence": confidence,
        "lineage": {"run_id": "r1", "detector": "field-diff", "detection_type": detection_type},
    }


@pytest.mark.smoke
def test_proposal_lifecycle_idempotent() -> None:
    applied: list[Proposal] = []
    client = TestClient(create_app(SqlRepository.from_env("sqlite://"), applied.append))
    assert client.get("/healthz").json() == {"status": "ok"}

    r = client.post("/proposals", json=_payload("upsert", 0.95, "stale"))
    assert r.status_code == 201 and r.json()["safe"] is True
    safe_id = r.json()["id"]
    r = client.post("/proposals", json=_payload("delete", 0.99, "duplicate"))
    assert r.status_code == 201 and r.json()["safe"] is False
    delete_id = r.json()["id"]
    again = client.post("/proposals", json=_payload("upsert", 0.95, "stale"))
    assert again.status_code == 200 and again.json()["id"] == safe_id

    assert [p["id"] for p in client.get("/proposals", params={"safe": True}).json()] == [safe_id]
    by_type = client.get("/proposals", params={"detection_type": "duplicate"}).json()
    assert [p["id"] for p in by_type] == [delete_id]
    assert len(client.get("/proposals", params={"status": "pending"}).json()) == 2

    who = {"decided_by": "ops"}
    assert client.post("/proposals/approve-safe", json=who).json() == {"approved": [safe_id]}
    assert [p.id for p in applied] == [safe_id]
    r = client.post(f"/proposals/{safe_id}/approve", json=who)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert len(applied) == 1

    hdr = {"Idempotency-Key": "rej-1"}
    r = client.post(f"/proposals/{delete_id}/reject", json=who, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    replay = client.post(f"/proposals/{delete_id}/reject", json=who, headers=hdr)
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json() == r.json()
    assert client.post(f"/proposals/{delete_id}/approve", json=who).status_code == 409
    assert client.get(f"/proposals/{delete_id}").json()["status"] == "rejected"
    assert len(applied) == 1
