from datetime import UTC, datetime

import pytest

from xstore.agent import EntityRef, MockProvider, TiebreakAgent, TiebreakRequest
from xstore.entities import Customer, load_mapping
from xstore.stores import Store, get_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ANA = Customer(
    customer_id="tb-c1",
    name="Ana Lee",
    email="ana.lee@example.com",
    phone="+1 (415) 555-0101",
    country="US",
    updated_at=NOW,
)
ANA_MONGO = ANA.model_copy(update={"name": "Lee, Ana", "phone": "4155550101"})
BOB = Customer(
    customer_id="tb-c2",
    name="Bob Ortiz",
    email="bob.ortiz@other.org",
    phone="+44 20 7946 0958",
    country="GB",
    updated_at=NOW,
)


def seeded_stores() -> dict[str, Store]:
    stores = {"postgres": get_store("postgres"), "mongodb": get_store("mongodb")}
    for name, customers in {"postgres": [ANA], "mongodb": [ANA_MONGO, BOB]}.items():
        m = load_mapping(name).for_entity("customer")
        for c in customers:
            stores[name].write("customer", m.from_canonical(c), f"seed-{c.customer_id}")
    return stores


def pair(pair_id: str, right_key: str) -> TiebreakRequest:
    return TiebreakRequest(
        pair_id=pair_id,
        left=EntityRef(store="postgres", entity_type="customer", key="tb-c1"),
        right=EntityRef(store="mongodb", entity_type="customer", key=right_key),
        match_probability=0.55,
    )


@pytest.mark.smoke
def test_tiebreak_agent_mcp_evidence_and_fixture_replay() -> None:
    agent = TiebreakAgent(seeded_stores(), provider=MockProvider())
    assert set(agent.graph.get_graph().nodes) >= {"fetch_evidence", "decide", "validate"}

    same, diff = agent.run_many([pair("tb-same", "tb-c1"), pair("tb-diff", "tb-c2")])
    assert same.decision.same_entity is True
    assert diff.decision.same_entity is False
    assert all(r.usage.input_tokens > 0 and r.usage.output_tokens > 0 for r in (same, diff))
    assert any(r.source == "fixture" for r in (same, diff))
    assert same.model == "claude-haiku-4-5" and same.provider == "mock"
