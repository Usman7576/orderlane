"""Customers for the dashboard: balances, credit limits, ledger and order history."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.deps import CurrentUser, SessionDep, require_csrf
from app.models import Customer, LedgerEntry, Order

router = APIRouter(prefix="/api", tags=["customers"], dependencies=[Depends(require_csrf)])


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str
    price_tier: str
    credit_limit: Decimal
    balance: Decimal
    is_new: bool
    order_count: int
    last_order_at: datetime | None


class CustomerPage(BaseModel):
    items: list[CustomerOut]
    total: int


class LedgerOut(BaseModel):
    id: int
    type: str
    amount: Decimal
    ref: str | None
    created_at: datetime


class CustomerOrderOut(BaseModel):
    id: int
    number: int
    status: str
    total: Decimal
    created_at: datetime


class CustomerDetail(CustomerOut):
    ledger: list[LedgerOut]
    orders: list[CustomerOrderOut]


def _stats_query(business_id: int):  # type: ignore[no-untyped-def]
    balance = (
        select(LedgerEntry.customer_id, func.sum(LedgerEntry.amount).label("balance"))
        .where(LedgerEntry.business_id == business_id)
        .group_by(LedgerEntry.customer_id)
        .subquery()
    )
    orders = (
        select(
            Order.customer_id,
            func.count().label("order_count"),
            func.max(Order.created_at).label("last_order_at"),
        )
        .where(Order.business_id == business_id)
        .group_by(Order.customer_id)
        .subquery()
    )
    return (
        select(
            Customer,
            func.coalesce(balance.c.balance, 0),
            func.coalesce(orders.c.order_count, 0),
            orders.c.last_order_at,
        )
        .outerjoin(balance, balance.c.customer_id == Customer.id)
        .outerjoin(orders, orders.c.customer_id == Customer.id)
        .where(Customer.business_id == business_id)
    )


def _out(row: tuple[Customer, Decimal, int, datetime | None]) -> CustomerOut:
    c, balance, count, last = row
    return CustomerOut(
        id=c.id,
        name=c.name,
        phone=c.wa_phone,
        price_tier=c.price_tier,
        credit_limit=c.credit_limit,
        balance=Decimal(balance),
        is_new=c.is_new,
        order_count=int(count),
        last_order_at=last,
    )


@router.get("/customers", response_model=CustomerPage)
async def list_customers(
    user: CurrentUser,
    session: SessionDep,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CustomerPage:
    query = _stats_query(user.business_id)
    if q:
        query = query.where(or_(Customer.name.ilike(f"%{q}%"), Customer.wa_phone.ilike(f"%{q}%")))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (await session.execute(query.order_by(Customer.name).limit(limit).offset(offset))).all()
    return CustomerPage(items=[_out(tuple(r)) for r in rows], total=total)


@router.get("/customers/{customer_id}", response_model=CustomerDetail)
async def get_customer(customer_id: int, user: CurrentUser, session: SessionDep) -> CustomerDetail:
    row = (
        await session.execute(_stats_query(user.business_id).where(Customer.id == customer_id))
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    ledger = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.customer_id == customer_id)
            .order_by(LedgerEntry.id.desc())
            .limit(50)
        )
    ).scalars()
    orders = (
        await session.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.id.desc())
            .limit(25)
        )
    ).scalars()
    return CustomerDetail(
        **_out(tuple(row)).model_dump(),
        ledger=[
            LedgerOut(id=e.id, type=e.type, amount=e.amount, ref=e.ref, created_at=e.created_at)
            for e in ledger
        ],
        orders=[
            CustomerOrderOut(
                id=o.id, number=o.number, status=o.status, total=o.total, created_at=o.created_at
            )
            for o in orders
        ],
    )
