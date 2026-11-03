"""Seeded synthetic company: canonical entities rendered into each store's native records."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from xstore.entities import Batch, Customer, Entity, Order, all_stores, load_mapping
from xstore.entities.models import OrderStatus

EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
SPAN_SECONDS = 365 * 24 * 3600
EXCLUDED_STORES = frozenset({"snowflake"})

FIRST = ["ada", "ben", "chloe", "dev", "elena", "farah", "gus", "hana", "ivan", "jun", "kofi",
         "lena", "mateo", "nia", "omar", "priya", "quinn", "rosa", "sven", "tara", "uma", "viktor",
         "wen", "ximena", "yusuf", "zoe"]  # fmt: skip
LAST = ["abbott", "baker", "chen", "diaz", "eriksen", "fischer", "garcia", "haddad", "ito",
        "jensen", "kim", "lopez", "mensah", "novak", "okafor", "patel", "quist", "rossi", "singh",
        "tanaka", "usman", "varga", "weber", "xu", "young", "zhou"]  # fmt: skip
DOMAINS = ["example.com", "example.org", "example.net", "corp.example.com"]
COUNTRIES = ["US", "GB", "DE", "FR", "IN", "JP", "BR", "CA", "AU", "NG"]
STATUSES: list[OrderStatus] = ["pending", "paid", "shipped", "cancelled", "refunded"]
WAREHOUSES = ["WH-NORTH", "WH-SOUTH", "WH-EAST", "WH-WEST"]

Record = dict[str, Any]
Native = dict[str, dict[str, list[Record]]]


def _ts(rng: random.Random, after: datetime = EPOCH) -> datetime:
    span = SPAN_SECONDS - int((after - EPOCH).total_seconds())
    return after + timedelta(seconds=rng.randint(0, max(span, 0)))


def _customer(rng: random.Random, i: int) -> Customer:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    return Customer(
        customer_id=f"C{i:06d}",
        name=f"{first.title()} {last.title()}",
        email=f"{first}.{last}{i}@{rng.choice(DOMAINS)}",
        phone=f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
        country=rng.choice(COUNTRIES),
        updated_at=_ts(rng),
    )


def _order(rng: random.Random, i: int, customer_ids: list[str]) -> Order:
    ordered_at = _ts(rng)
    return Order(
        order_id=f"O{i:06d}",
        customer_id=rng.choice(customer_ids),
        amount=Decimal(rng.randint(100, 500_000)) / 100,
        status=rng.choice(STATUSES),
        ordered_at=ordered_at,
        updated_at=_ts(rng, ordered_at),
    )


def _batch(rng: random.Random, i: int) -> Batch:
    produced_at = _ts(rng)
    expires = produced_at + timedelta(days=rng.randint(30, 720)) if rng.random() < 0.7 else None
    return Batch(
        batch_id=f"B{i:06d}",
        sku=f"SKU-{rng.randint(1, 400):04d}",
        quantity=rng.randint(1, 5000),
        weight_kg=round(rng.uniform(0.5, 2000.0), 3),
        warehouse=rng.choice(WAREHOUSES),
        produced_at=produced_at,
        expires_at=expires,
        updated_at=_ts(rng, produced_at),
    )


def generate_entities(
    seed: int, customers: int = 4000, orders: int = 4000, batches: int = 2000
) -> dict[str, list[Entity]]:
    rng = random.Random(seed)
    cs: list[Entity] = [_customer(rng, i) for i in range(1, customers + 1)]
    ids = [c.key for c in cs]
    return {
        "customer": cs,
        "order": [_order(rng, i, ids) for i in range(1, orders + 1)],
        "batch": [_batch(rng, i) for i in range(1, batches + 1)],
    }


def local_stores() -> list[str]:
    return [s for s in all_stores() if s not in EXCLUDED_STORES]


def render(entities: dict[str, list[Entity]]) -> Native:
    out: Native = {}
    for store in local_stores():
        mapping = load_mapping(store)
        out[store] = {
            etype: [mapping.for_entity(etype).from_canonical(e) for e in ents]
            for etype, ents in entities.items()
            if etype in mapping.entities
        }
    return out


def generate(seed: int, customers: int = 4000, orders: int = 4000, batches: int = 2000) -> Native:
    return render(generate_entities(seed, customers, orders, batches))
