"""FastAPI approval API over fix proposals.

Create is idempotent by content-hash id (201 new, 200 existing). Approve and reject
only leave `pending`; `on_approve` runs once per proposal. `Idempotency-Key` on
approve/reject replays the stored response with `Idempotent-Replayed: true`.
Run: `uvicorn xstore.proposals.app:app`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse

from xstore.proposals.models import Decision, DetectionType, Proposal, ProposalCreate, Status
from xstore.proposals.repository import ProposalRepository, Reply, SqlRepository

OnApprove = Callable[[Proposal], None]


def create_app(
    repo: ProposalRepository | None = None, on_approve: OnApprove | None = None
) -> FastAPI:
    app = FastAPI(title="xstore proposals")
    store: ProposalRepository = repo if repo is not None else SqlRepository.from_env()
    apply: OnApprove = on_approve or (lambda _p: None)
    app.state.repo = store

    def body(p: Proposal) -> Any:
        return p.model_dump(mode="json")

    def load(proposal_id: str) -> Proposal:
        p = store.get(proposal_id)
        if p is None:
            raise HTTPException(404, "proposal not found")
        return p

    def approve(p: Proposal, decision: Decision) -> tuple[Reply, bool]:
        """Returns the reply and whether this call moved the proposal to approved."""
        while True:
            if p.status == "approved":
                return (200, body(p)), False
            if p.status == "rejected":
                return (409, {"detail": "proposal already rejected"}), False
            decided = p.decide("approved", decision)
            if store.transition("pending", decided):
                try:
                    apply(decided)
                except Exception:
                    store.transition("approved", p)  # back to pending; fix not applied
                    raise
                return (200, body(decided)), True
            p = load(p.id)  # lost a race; re-read and resolve

    def reject(p: Proposal, decision: Decision) -> Reply:
        decided = p.decide("rejected", decision)
        if p.status == "pending" and store.transition("pending", decided):
            return 200, body(decided)
        return 409, {"detail": f"proposal is {load(p.id).status}"}

    def idempotent(scope: str, key: str | None, run: Callable[[], Reply]) -> Response:
        full_key = f"{scope}:{key}"
        if key is not None and (stored := store.get_reply(full_key)) is not None:
            return JSONResponse(stored[1], stored[0], headers={"Idempotent-Replayed": "true"})
        status, content = run()
        if key is not None and status < 400:
            store.put_reply(full_key, (status, content))
        return JSONResponse(content, status)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/proposals", status_code=201)
    def create(payload: ProposalCreate, response: Response) -> Proposal:
        p, created = store.add(Proposal.new(payload))
        if not created:
            response.status_code = 200
        return p

    @app.get("/proposals")
    def list_proposals(
        status: Status | None = None,
        safe: bool | None = None,
        detection_type: DetectionType | None = None,
    ) -> list[Proposal]:
        return store.find(status=status, safe=safe, detection_type=detection_type)

    @app.post("/proposals/approve-safe")
    def approve_safe(decision: Decision) -> dict[str, list[str]]:
        approved = []
        for p in store.find(status="pending", safe=True):
            if approve(p, decision)[1]:
                approved.append(p.id)
        return {"approved": approved}

    @app.get("/proposals/{proposal_id}")
    def get(proposal_id: str) -> Proposal:
        return load(proposal_id)

    @app.post("/proposals/{proposal_id}/approve")
    def approve_one(
        proposal_id: str,
        decision: Decision,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        p = load(proposal_id)
        return idempotent(
            f"approve:{proposal_id}", idempotency_key, lambda: approve(p, decision)[0]
        )

    @app.post("/proposals/{proposal_id}/reject")
    def reject_one(
        proposal_id: str,
        decision: Decision,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        p = load(proposal_id)
        return idempotent(f"reject:{proposal_id}", idempotency_key, lambda: reject(p, decision))

    return app


app = create_app()
