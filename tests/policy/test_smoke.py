from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from xstore.policy import (
    PolicyDiffError,
    PolicyEngine,
    ReviewOutcome,
    apply_diff,
    check_policy,
    load_policy,
    propose_diffs,
    render_diff,
)

POLICY = Path(__file__).resolve().parents[2] / "policy" / "policy.yaml"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 5, tzinfo=UTC)


@pytest.mark.smoke
def test_resolve_priority_coercion_recency() -> None:
    policy = load_policy(POLICY)
    assert check_policy(policy) == []
    engine = PolicyEngine(policy)

    cust = engine.resolve(
        "customer",
        "c1",
        {
            "postgres": {
                "name": "Ann Lee",
                "phone": "555-0100",
                "email": "ANN@x.io ",
                "updated_at": T1,
            },
            "hubspot": {
                "name": "Ann  Lee",
                "phone": "555-0199",
                "email": "ann@y.io",
                "updated_at": T1,
            },
            "mongodb": {
                "name": "A. Lee",
                "phone": "555-0100",
                "email": "ann@x.io",
                "updated_at": T1,
            },
        },
    )
    assert cust.decisions["name"].source_store == "hubspot"
    assert cust.decisions["name"].rule == "source_priority"
    assert cust.decisions["phone"].value == "555-0199"
    assert cust.decisions["phone"].requires_approval
    assert cust.decisions["email"].value == "ann@x.io"
    assert cust.decisions["email"].rule == "field_override"
    assert set(cust.conflicts) == {"name", "phone", "email"}
    assert cust.policy_digest == policy.digest()

    order = engine.resolve(
        "order",
        "o1",
        {"postgres": {"amount": 12.34, "updated_at": T1}, "hubspot": {"amount": "12.34"}},
    )
    assert order.decisions["amount"].rule == "agreement"
    assert order.record["amount"] == Decimal("12.34")
    assert order.conflicts == []
    assert engine.to_canonical_unit("postgres", "order", "amount", 1234) == Decimal("12.34")

    batch = engine.resolve(
        "batch",
        "b1",
        {
            "postgres": {"warehouse": "W2", "quantity": "10", "updated_at": T2},
            "cosmosdb": {"warehouse": "W3", "quantity": 10, "updated_at": T2.isoformat()},
            "adls_parquet": {"warehouse": "W1", "quantity": 10, "updated_at": T1},
        },
    )
    assert batch.decisions["quantity"].rule == "agreement"
    assert batch.decisions["warehouse"].value == "W3"  # newest tie -> cosmosdb by priority
    assert batch.decisions["warehouse"].rule == "source_priority"


@pytest.mark.smoke
def test_propose_and_apply_diff() -> None:
    policy = load_policy(POLICY)
    log = [
        ReviewOutcome(
            entity_type="customer",
            field="phone",
            chosen_store="postgres",
            policy_store="hubspot",
            approved=True,
            review_id=f"r{i}",
        )
        for i in range(5)
    ]
    diffs = propose_diffs(policy, log)
    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.path == "field_overrides.customer.phone"
    assert diff.new["source_priority"][0] == "postgres"
    assert "+      - postgres" in render_diff(diff)
    with pytest.raises(PolicyDiffError):
        apply_diff(policy, diff, approved_by="ops")
    new = apply_diff(policy, diff.model_copy(update={"status": "approved"}), approved_by="ops")
    assert (new.version, policy.version) == (2, 1)
    assert "phone" not in policy.field_overrides["customer"]
    assert new.digest() != policy.digest()
    assert (
        PolicyEngine(new)
        .resolve("customer", "c1", {"postgres": {"phone": "1"}, "hubspot": {"phone": "2"}})
        .record["phone"]
        == "1"
    )
