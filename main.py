from datetime import datetime, UTC
from uuid import UUID, uuid4
from typing import Literal, Annotated, Any, Self
from decimal import Decimal, ROUND_HALF_UP
from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    AliasChoices,
    field_serializer,
    field_validator,
    computed_field,
    model_validator,
)


Currency = Literal["PLN", "USD", "EUR"]
PartnerCode = Annotated[str, Field(pattern=r"^[A-Z0-9_]{3,20}$")]
Sku = Annotated[
    str, Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
]
Quantity = Annotated[int, Field(gt=0, le=999)]


class IngressMoney(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency


# m = IngressMoney(amount="12.50", currency="PLN")
# print(m.currency)
# print(m.amount)


class StrictEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    event_id: UUID
    version: Literal[1]
    partner_code: Annotated[str, Field(pattern=r"^[A-Z0-9_]{3,20}$")]
    event_time: datetime


# StrictEnvelope(
#     event_id=uuid4(), version=1, partner_code="AAA", event_time=datetime.now(tz=UTC)
# )

# Alias


class PartnerOrder(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    order_id: str = Field(
        validation_alias=AliasChoices("orderId", "order_id", "id"),
        serialization_alias="orderId",
    )
    partner: PartnerCode = Field(validation_alias=AliasChoices("merchant", "partner"))


# data = {"id": "EXT-42", "merchant": "MPK_EU"}

# ref = PartnerOrder.model_validate(data)
# ref = PartnerOrder(**data)
# print(ref.order_id)
# print(ref.partner)

# serializacja danych
# print(ref.model_dump(by_alias=True))

# kompozycja modeli


class Customer(BaseModel):
    customer_id: Annotated[str, Field(pattern=r"^CUST-\d{4,}$")]
    loyalty_tier: Literal["standard", "gold", "vip"] = "standard"


class ShippingAddress(BaseModel):
    country: Literal["PL", "DE", "CZ"]
    city: Annotated[str, Field(min_length=2, max_length=80)]
    postal_code: Annotated[str, Field(pattern=r"^\d{2}-\d{3}$")]
    line1: Annotated[str, Field(min_length=5, max_length=120)]


class Money(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, amount: Decimal) -> str:
        return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


class OrderItem(BaseModel):
    sku: Sku
    quantity: Quantity
    unit_price: Money

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper().replace(" ", "_")
        return value

    @computed_field
    @property
    def line_total(self) -> Decimal:
        return (self.unit_price.amount * self.quantity).quantize(Decimal("0.01"))


class OrderTotals(BaseModel):
    items: list[OrderItem] = Field(min_length=1)
    declared_total: Money

    @model_validator(mode="after")
    def total_must_match_items(self) -> Self:
        currencies = {item.unit_price.currency for item in self.items} | {
            self.declared_total.currency
        }

        if len(currencies) != 1:
            raise ValueError(
                f"all order money values must share one currency, got {sorted(currencies)}"
            )

        computed = sum(
            (item.line_total for item in self.items), Decimal("0.00")
        ).quantize(Decimal("0.01"))
        declared = self.declared_total.amount.quantize(Decimal("0.01"))

        if computed != declared:
            raise ValueError(
                f"declared_total={declared} does not match computed_total={computed}"
            )

        return self


class CanonicalOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: Annotated[str, Field(min_length=6, max_length=40)]
    partner: PartnerCode
    customer: Customer
    shipping: ShippingAddress
    totals: OrderTotals
    received_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


data = {
    "order_id": "ORD-2026-0001",
    "partner": "MKP_EU",
    "customer": {"customer_id": "CUST-1001", "loyalty_tier": "gold"},
    "shipping": {
        "country": "PL",
        "city": "Warszawa",
        "postal_code": "00-001",
        "line1": "Prosta 1",
    },
    "totals": {
        "items": [
            {
                "sku": " sku-001 ",
                "quantity": "2",
                "unit_price": {"amount": "19.99", "currency": "PLN"},
            },
            {
                "sku": "sku 002",
                "quantity": 1,
                "unit_price": {"amount": Decimal("5.00"), "currency": "PLN"},
            },
        ],
        "declared_total": {"amount": "44.98", "currency": "PLN"},
    },
}
invalid = data.copy()
# invalid["shipping"]["postal_code"] = "00001"

order = CanonicalOrder.model_validate(data)
print(order.totals.items[0].sku)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    partner_code: Annotated[str, Field(pattern=r"^[A-Z0-9_]{3,20}$")]
    event_time: datetime


class OrderCreatedEvent(EventEnvelope):
    type: Literal["order.created"]
    order: CanonicalOrder


class LegacyOrCanonicalCreatedEvent(OrderCreatedEvent):
    @model_validator(mode="before")
    @classmethod
    def accept_legacy_event_names(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("eventType") == "orderCreated":
            patched = dict(data)
            patched["type"] = "order.created"
            patched.pop("eventType", None)
            patched["event_time"] = patched.pop("timestamp")
            patched["event_id"] = patched.pop("eventId")
            return patched
        return data


legacy = {
    "event_id": str(uuid4()),
    "type": "order.created",
    "partner_code": "MKP_EU",
    "event_time": "2026-06-08T09:15:00+00:00",
    "order": data,
}

legacy["eventType"] = "orderCreated"
legacy["timestamp"] = legacy.pop("event_time")
legacy["eventId"] = legacy.pop("event_id")
legacy.pop("type")


event = LegacyOrCanonicalCreatedEvent.model_validate(legacy)
print(event.type)
print(event.order.order_id)
