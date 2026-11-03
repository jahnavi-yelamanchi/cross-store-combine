# ADR-006: In-memory FastAPI stub for HubSpot CRM

- Context: the HubSpot store needs a SaaS CRM in tests and demos without an account, token or network.
- Decision: FastAPI app shaped like CRM v3 objects (contacts, deals) with `Idempotency-Key` replay and a webhook-shaped `/xstore/changes` log; the adapter talks to it in-process through TestClient and to api.hubapi.com in real mode.
- Alternative rejected: recorded HTTP fixtures (VCR-style); mocking httpx calls in the adapter.
- Why: one adapter code path for stub and real API; stateful writes, paging and replay can be exercised; no port is opened.
- Consequence: the stub accepts client-supplied ids and exposes a pull change log that real HubSpot lacks; real mode needs a webhook receiver for `stream_changes`.
