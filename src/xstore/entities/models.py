"""Canonical entity models. Every store maps to and from these."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

OrderStatus = Literal["pending", "paid", "shipped", "cancelled", "refunded"]


class Entity(BaseModel):
    """Base for canonical entities; `key_field` names the business key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: ClassVar[str]
    key_field: ClassVar[str]
    updated_at: datetime

    @property
    def key(self) -> str:
        return str(getattr(self, self.key_field))


class Customer(Entity):
    entity_type: ClassVar[str] = "customer"
    key_field: ClassVar[str] = "customer_id"

    customer_id: str = Field(min_length=1)
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)


class Order(Entity):
    entity_type: ClassVar[str] = "order"
    key_field: ClassVar[str] = "order_id"

    order_id: str = Field(min_length=1)
    customer_id: str
    amount: Decimal = Field(ge=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: OrderStatus
    ordered_at: datetime


class Batch(Entity):
    entity_type: ClassVar[str] = "batch"
    key_field: ClassVar[str] = "batch_id"

    batch_id: str = Field(min_length=1)
    sku: str
    quantity: int = Field(ge=0)
    weight_kg: float = Field(ge=0)
    warehouse: str
    produced_at: datetime
    expires_at: datetime | None = None


ENTITY_TYPES: dict[str, type[Entity]] = {
    cls.entity_type: cls for cls in (Customer, Order, Batch)
}
