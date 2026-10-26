"""Orders for the dashboard: inbox, detail (with conversation and agent trace), staff review."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, QueueDep, SessionDep, SettingsDep, client_ip, require_csrf
from app.models import (
    AgentRun,
    AgentStep,
    Customer,
    Invoice,
    Message,
    Order,
    OrderEvent,
    OrderLine,
    Product,
    ProductUnit,
)
from app.models.enums import OrderStatus
from app.services.auth import audit
from app.services.review import LineEdit, ReviewError, edit_lines

router = APIRouter(prefix="/api", tags=["orders"], dependencies=[Depends(require_csrf)])

CONVERSATION_LIMIT = 40


# --- Response shapes ---


class OrderSummary(BaseModel):
    id: int
    number: int
    status: str
    total: Decimal
    customer_id: int
    customer_name: str
    line_count: int
    issues: list[str]
    created_at: datetime
    updated_at: datetime


class OrderPage(BaseModel):
    items: list[OrderSummary]
    total: int
    counts: dict[str, int]


class Alternative(BaseModel):
    sku: str
    name: str


class LineOut(BaseModel):
    id: int
    sku: str | None
    product_name: str | None
    unit: str | None
    units: list[str]
    qty: Decimal
    unit_price: Decimal | None
    line_total: Decimal | None
    source_text: str | None
    match_method: str | None
    match_confidence: float | None
    needs_review: bool
    alternatives: list[Alternative]


class MessageOut(BaseModel):
    id: int
    direction: str
    type: str
    body: str | None
    transcript: str | None
    status: str
    has_audio: bool
    kind: str | None
    created_at: datetime


class EventOut(BaseModel):
    actor: str
    type: str
    payload: dict[str, Any]
    created_at: datetime


class StepOut(BaseModel):
    node: str
    duration_ms: int | None
    output: dict[str, Any] | None


class RunOut(BaseModel):
    id: int
    message_id: int | None
    status: str
    intent: str | None
    route: str | None
    prompt_version: str | None
    latency_ms: int | None
    tokens_in: int
    tokens_out: int
    cost_estimate: Decimal
    error: str | None
    created_at: datetime
    steps: list[StepOut]


class OrderDetail(BaseModel):
    id: int
    number: int
    status: str
    total: Decimal
    issues: list[str]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    customer: dict[str, Any]
    lines: list[LineOut]
    messages: list[MessageOut]
    events: list[EventOut]
    runs: list[RunOut]
    invoice: dict[str, str] | None
    can_approve: bool
    review_blockers: list[str]


# --- Helpers ---


def _issue_codes(issues: list[dict[str, Any]]) -> list[str]:
    return [str(i.get("code")) for i in issues if i.get("code")]


async def _order_for(session: AsyncSession, business_id: int, order_id: int) -> Order:
    order = await session.scalar(
        select(Order).where(Order.id == order_id, Order.business_id == business_id)
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return order


def _review_blockers(order: Order, lines: list[LineOut]) -> list[str]:
    if order.status != OrderStatus.PENDING_REVIEW:
        return [f"Order is {order.status.replace('_', ' ')}, not waiting for review."]
    blockers = []
    if not lines:
        blockers.append("The order has no lines.")
    for line in lines:
        if line.sku is None:
            blockers.append(f"Choose a product for “{line.source_text or 'a line'}”.")
        elif line.unit_price is None:
            blockers.append(f"{line.product_name}: no price for the {line.unit} unit.")
    return blockers


# --- Endpoints ---


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    user: CurrentUser,
    session: SessionDep,
    status_: str | None = Query(None, alias="status", description="comma-separated statuses"),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> OrderPage:
    base = (
        select(Order, Customer.name)
        .join(Customer, Customer.id == Order.customer_id)
        .where(Order.business_id == user.business_id)
    )
    if status_:
        base = base.where(Order.status.in_([s.strip() for s in status_.split(",") if s.strip()]))
    if q:
        term = q.strip().lstrip("#")
        conditions: list[ColumnElement[bool]] = [Customer.name.ilike(f"%{term}%")]
        if term.isdigit():
            conditions.append(Order.number == int(term))
        base = base.where(or_(*conditions))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await session.execute(base.order_by(Order.updated_at.desc()).limit(limit).offset(offset))
    ).all()
    line_stats = {
        order_id: (int(total_lines), int(unchecked))
        for order_id, total_lines, unchecked in (
            await session.execute(
                select(
                    OrderLine.order_id,
                    func.count(),
                    # Lines the agent wasn't sure about have no match method yet.
                    func.count().filter(OrderLine.match_method.is_(None)),
                )
                .where(OrderLine.order_id.in_([o.id for o, _ in rows]))
                .group_by(OrderLine.order_id)
            )
        ).all()
    }
    counts = dict(
        (
            await session.execute(
                select(Order.status, func.count())
                .where(Order.business_id == user.business_id)
                .group_by(Order.status)
            )
        ).all()
    )
    return OrderPage(
        items=[
            OrderSummary(
                id=o.id,
                number=o.number,
                status=o.status,
                total=o.total,
                customer_id=o.customer_id,
                customer_name=name,
                line_count=line_stats.get(o.id, (0, 0))[0],
                issues=_issue_codes(o.issues)
                + (["lines_to_check"] if line_stats.get(o.id, (0, 0))[1] else []),
                created_at=o.created_at,
                updated_at=o.updated_at,
            )
            for o, name in rows
        ],
        total=total,
        counts={str(k): int(v) for k, v in counts.items()},
    )


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(order_id: int, user: CurrentUser, session: SessionDep) -> OrderDetail:
    order = await _order_for(session, user.business_id, order_id)
    customer = await session.get_one(Customer, order.customer_id)

    line_rows = (
        await session.execute(
            select(OrderLine, Product)
            .outerjoin(Product, Product.id == OrderLine.product_id)
            .where(OrderLine.order_id == order.id)
            .order_by(OrderLine.id)
        )
    ).all()
    alt_keys = {a["key"] for line, _ in line_rows for a in line.alternatives if "key" in a}
    products = {
        p.sku: p
        for p in (
            await session.execute(
                select(Product).where(
                    Product.business_id == user.business_id, Product.sku.in_(alt_keys)
                )
            )
        ).scalars()
    }
    product_ids = [p.id for _, p in line_rows if p is not None]
    units: dict[int, list[str]] = {}
    for pid, unit in (
        await session.execute(
            select(ProductUnit.product_id, ProductUnit.unit_name)
            .where(ProductUnit.product_id.in_(product_ids))
            .order_by(ProductUnit.base_qty)
        )
    ).all():
        units.setdefault(pid, []).append(unit)

    lines = [
        LineOut(
            id=line.id,
            sku=product.sku if product else None,
            product_name=product.name if product else None,
            unit=line.unit_name,
            units=units.get(product.id, []) if product else [],
            qty=line.qty,
            unit_price=line.unit_price,
            line_total=line.line_total,
            source_text=line.source_text,
            match_method=line.match_method,
            match_confidence=line.match_confidence,
            needs_review=line.match_method is None,
            alternatives=[
                Alternative(sku=a["key"], name=products[a["key"]].name)
                for a in line.alternatives
                if a.get("key") in products and (product is None or a["key"] != product.sku)
            ],
        )
        for line, product in line_rows
    ]

    messages = list(
        (
            await session.execute(
                select(Message)
                .where(Message.customer_id == customer.id)
                .order_by(Message.id.desc())
                .limit(CONVERSATION_LIMIT)
            )
        ).scalars()
    )[::-1]
    events = list(
        (
            await session.execute(
                select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.id)
            )
        ).scalars()
    )
    message_ids = {e.payload.get("message_id") for e in events if e.payload.get("message_id")}
    runs = list(
        (
            await session.execute(
                select(AgentRun).where(AgentRun.message_id.in_(message_ids)).order_by(AgentRun.id)
            )
        ).scalars()
    )
    steps: dict[int, list[StepOut]] = {}
    for step in (
        await session.execute(
            select(AgentStep)
            .where(AgentStep.run_id.in_([r.id for r in runs]))
            .order_by(AgentStep.id)
        )
    ).scalars():
        duration = (
            int((step.ended_at - step.started_at).total_seconds() * 1000) if step.ended_at else None
        )
        steps.setdefault(step.run_id, []).append(
            StepOut(node=step.node, duration_ms=duration, output=step.output)
        )
    invoice = await session.scalar(select(Invoice).where(Invoice.order_id == order.id))
    blockers = _review_blockers(order, lines)

    return OrderDetail(
        id=order.id,
        number=order.number,
        status=order.status,
        total=order.total,
        issues=_issue_codes(order.issues),
        created_at=order.created_at,
        updated_at=order.updated_at,
        confirmed_at=order.confirmed_at,
        customer={
            "id": customer.id,
            "name": customer.name,
            "phone": customer.wa_phone,
            "price_tier": customer.price_tier,
            "credit_limit": str(customer.credit_limit),
            "is_new": customer.is_new,
        },
        lines=lines,
        messages=[
            MessageOut(
                id=m.id,
                direction=m.direction,
                type=m.type,
                body=m.body,
                transcript=m.transcript,
                status=m.status,
                has_audio=bool(m.media_ref) and m.type == "audio",
                kind=(m.raw or {}).get("kind") if m.direction == "out" else None,
                created_at=m.created_at,
            )
            for m in messages
        ],
        events=[
            EventOut(actor=e.actor, type=e.type, payload=e.payload, created_at=e.created_at)
            for e in events
        ],
        runs=[
            RunOut(
                id=r.id,
                message_id=r.message_id,
                status=r.status,
                intent=r.intent,
                route=r.route,
                prompt_version=r.prompt_version,
                latency_ms=r.latency_ms,
                tokens_in=r.tokens_in,
                tokens_out=r.tokens_out,
                cost_estimate=r.cost_estimate,
                error=r.error,
                created_at=r.created_at,
                steps=steps.get(r.id, []),
            )
            for r in runs
        ],
        invoice={"number": invoice.number, "url": f"/api/orders/{order.id}/invoice.pdf"}
        if invoice
        else None,
        can_approve=not blockers,
        review_blockers=blockers,
    )


class LineEditIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    unit: str = Field(min_length=1, max_length=32)
    qty: Decimal = Field(gt=0, le=100000)
    source_text: str | None = Field(None, max_length=500)
    save_alias: bool = False


class LinesIn(BaseModel):
    lines: list[LineEditIn] = Field(min_length=1, max_length=100)


@router.put("/orders/{order_id}/lines", response_model=OrderDetail)
async def replace_lines(
    order_id: int, body: LinesIn, request: Request, user: CurrentUser, session: SessionDep
) -> OrderDetail:
    await _order_for(session, user.business_id, order_id)
    try:
        await edit_lines(
            session,
            user.business_id,
            order_id,
            user.id,
            [LineEdit(e.sku, e.unit, e.qty, e.source_text, e.save_alias) for e in body.lines],
        )
    except ReviewError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(
        session,
        "order_lines_edited",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        order_id=order_id,
    )
    await session.commit()
    return await get_order(order_id, user, session)


class ReviewQueued(BaseModel):
    status: Literal["queued"]
    action: str


@router.post(
    "/orders/{order_id}/{action}",
    response_model=ReviewQueued,
    status_code=status.HTTP_202_ACCEPTED,
)
async def review(
    order_id: int,
    action: Literal["approve", "reject"],
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
) -> ReviewQueued:
    """Checks the order can be reviewed, then hands the decision to the worker, which resumes
    the paused agent run and messages the customer."""
    order = await _order_for(session, user.business_id, order_id)
    if order.status != OrderStatus.PENDING_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, f"order is {order.status}, not in review")
    if action == "approve":
        detail = await get_order(order_id, user, session)
        if detail.review_blockers:
            raise HTTPException(status.HTTP_409_CONFLICT, " ".join(detail.review_blockers))
    await queue.enqueue_review(user.business_id, order_id, action, user.id)
    audit(
        session,
        f"order_{action}_requested",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        order_id=order_id,
    )
    await session.commit()
    return ReviewQueued(status="queued", action=action)


@router.get("/orders/{order_id}/invoice.pdf")
async def invoice_pdf(
    order_id: int, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    await _order_for(session, user.business_id, order_id)
    invoice = await session.scalar(select(Invoice).where(Invoice.order_id == order_id))
    path = settings.storage_dir / invoice.pdf_path if invoice else None
    if invoice is None or path is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no invoice for this order")
    return FileResponse(path, media_type="application/pdf", filename=f"{invoice.number}.pdf")


@router.get("/messages/{message_id}/audio")
async def message_audio(
    message_id: int, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    message = await session.scalar(
        select(Message).where(Message.id == message_id, Message.business_id == user.business_id)
    )
    path = Path(settings.storage_dir) / "audio" / f"{message_id}.ogg"
    if message is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no audio for this message")
    return FileResponse(path, media_type="audio/ogg")
