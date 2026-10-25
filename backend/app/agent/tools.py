"""Business tools the agent uses: customer info, prices, stock, and the draft order.

The LLM never calls these directly and never writes data. Agent steps call them after
checking every product and quantity. Two implementations share one interface:
DbOrderTools (PostgreSQL, used by the worker) and MemoryOrderTools (evaluation and tests).
"""

import csv
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.catalog import CatalogIndex
from app.models import (
    Business,
    Customer,
    Inventory,
    LedgerEntry,
    Order,
    OrderEvent,
    OrderLine,
    PriceList,
)
from app.models.enums import Actor, OrderStatus
from app.ordering.draft import Draft, DraftLine
from app.services.seeding import parse_units, unit_price

OPEN_STATUSES = (OrderStatus.DRAFT, OrderStatus.PENDING_REVIEW, OrderStatus.AWAITING_CUSTOMER)
FIRST_ORDER_NUMBER = 1001


@dataclass(frozen=True)
class CustomerInfo:
    id: int
    name: str
    price_tier: str
    credit_limit: Decimal
    balance: Decimal  # what the customer owes now
    is_new: bool


@dataclass(frozen=True)
class SavedDraft:
    order_id: int | None
    number: int | None


class OrderTools(Protocol):
    async def customer(self, customer_id: int) -> CustomerInfo: ...
    async def load_draft(self, customer_id: int) -> Draft: ...
    async def price(self, key: str, unit: str, tier: str) -> Decimal | None: ...
    async def available_base(self, key: str) -> int | None: ...
    async def save_draft(
        self,
        customer_id: int,
        draft: Draft,
        *,
        status: OrderStatus,
        total: Decimal,
        issues: list[str],
        event: dict[str, Any],
    ) -> SavedDraft: ...


# --- PostgreSQL ---


class DbOrderTools:
    def __init__(self, session: AsyncSession, business_id: int, index: CatalogIndex) -> None:
        self._session = session
        self._business_id = business_id
        self._index = index
        self._key_by_product_id = {
            p.product_id: p.key for p in index.products if p.product_id is not None
        }

    async def customer(self, customer_id: int) -> CustomerInfo:
        c = await self._session.get_one(Customer, customer_id)
        balance = (
            await self._session.execute(
                select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                    LedgerEntry.customer_id == customer_id
                )
            )
        ).scalar_one()
        return CustomerInfo(c.id, c.name, c.price_tier, c.credit_limit, Decimal(balance), c.is_new)

    async def _open_order(self, customer_id: int) -> Order | None:
        result = await self._session.execute(
            select(Order)
            .where(Order.customer_id == customer_id, Order.status.in_(OPEN_STATUSES))
            .order_by(Order.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def load_draft(self, customer_id: int) -> Draft:
        order = await self._open_order(customer_id)
        if order is None:
            return Draft()
        lines = (
            await self._session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.id)
            )
        ).scalars()
        return Draft(
            tuple(
                DraftLine(
                    key=self._key_by_product_id.get(line.product_id) if line.product_id else None,
                    qty=line.qty,
                    unit=line.unit_name,
                    source_text=line.source_text or "",
                    needs_review=bool(line.match_method is None),
                )
                for line in lines
            )
        )

    async def price(self, key: str, unit: str, tier: str) -> Decimal | None:
        product_id = self._index.by_key[key].product_id
        result = await self._session.execute(
            select(PriceList.price).where(
                PriceList.product_id == product_id,
                PriceList.tier == tier,
                PriceList.unit_name == unit,
            )
        )
        return result.scalar_one_or_none()

    async def available_base(self, key: str) -> int | None:
        product_id = self._index.by_key[key].product_id
        inventory = await self._session.get(Inventory, product_id)
        return None if inventory is None else inventory.on_hand_base - inventory.reserved_base

    async def _next_number(self) -> int:
        # Lock the business row so two orders never get the same number.
        await self._session.execute(
            select(Business.id).where(Business.id == self._business_id).with_for_update()
        )
        current = (
            await self._session.execute(
                select(func.max(Order.number)).where(Order.business_id == self._business_id)
            )
        ).scalar_one()
        return (current or FIRST_ORDER_NUMBER - 1) + 1

    async def save_draft(
        self,
        customer_id: int,
        draft: Draft,
        *,
        status: OrderStatus,
        total: Decimal,
        issues: list[str],
        event: dict[str, Any],
    ) -> SavedDraft:
        order = await self._open_order(customer_id)
        if order is None:
            if not draft.lines:
                return SavedDraft(None, None)
            order = Order(
                business_id=self._business_id,
                number=await self._next_number(),
                customer_id=customer_id,
                status=status,
                total=total,
                issues=[],
            )
            self._session.add(order)
            await self._session.flush()
        order.status = status
        order.total = total
        order.issues = [{"code": code} for code in issues]
        await self._session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
        for line in draft.lines:
            product = self._index.by_key.get(line.key) if line.key else None
            self._session.add(
                OrderLine(
                    order_id=order.id,
                    product_id=product.product_id if product else None,
                    unit_name=line.unit,
                    qty=line.qty if line.qty is not None else Decimal(0),
                    unit_price=line.unit_price,
                    line_total=line.line_total,
                    source_text=line.source_text,
                    match_method=None if line.needs_review else line.method,
                    match_confidence=line.confidence,
                    alternatives=[{"key": key} for key in line.candidates],
                )
            )
        self._session.add(
            OrderEvent(order_id=order.id, actor=Actor.AGENT, type="draft_updated", payload=event)
        )
        await self._session.flush()
        return SavedDraft(order.id, order.number)


# --- In memory (evaluation and tests) ---


@dataclass
class MemoryOrderTools:
    """Prices, stock and customers from the seed CSV files; drafts kept in a dict."""

    piece_prices: dict[str, Decimal]
    units: dict[str, dict[str, int]]
    stock: dict[str, int]
    customers: dict[int, CustomerInfo]
    drafts: dict[int, Draft] = field(default_factory=dict)
    saved: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_seed(cls, seed_dir: Path) -> "MemoryOrderTools":
        prices, units, stock = {}, {}, {}
        with (seed_dir / "catalog.csv").open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                prices[row["sku"]] = Decimal(row["price_a"])
                units[row["sku"]] = parse_units(row["units"])
                stock[row["sku"]] = int(row["stock_pieces"])
        customers = {}
        with (seed_dir / "customers.csv").open(encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=1):
                customers[i] = CustomerInfo(
                    i,
                    row["name"],
                    row["price_tier"],
                    Decimal(row["credit_limit"]),
                    Decimal(row["opening_balance"]),
                    row["is_new"] == "true",
                )
        return cls(prices, units, stock, customers)

    async def customer(self, customer_id: int) -> CustomerInfo:
        return self.customers[customer_id]

    async def load_draft(self, customer_id: int) -> Draft:
        return self.drafts.get(customer_id, Draft())

    async def price(self, key: str, unit: str, tier: str) -> Decimal | None:
        per_unit = self.units.get(key, {}).get(unit)
        if per_unit is None:
            return None
        return unit_price(self.piece_prices[key], unit, per_unit, tier)

    async def available_base(self, key: str) -> int | None:
        return self.stock.get(key)

    async def save_draft(
        self,
        customer_id: int,
        draft: Draft,
        *,
        status: OrderStatus,
        total: Decimal,
        issues: list[str],
        event: dict[str, Any],
    ) -> SavedDraft:
        self.drafts[customer_id] = Draft() if status == OrderStatus.CANCELLED else draft
        self.saved.append({"customer_id": customer_id, "status": status, "issues": issues})
        return SavedDraft(customer_id, None)
