"""Staff review of orders the agent held back: fix lines, then approve or reject.

Approving resumes the agent's paused LangGraph run, which re-reads the (possibly edited)
order, marks it approved and composes the confirmation for the customer. Orders without
a paused run (for example ones sent back after a failed stock check) take a direct path."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import replies
from app.agent.graph import resume_review
from app.agent.state import AgentDeps
from app.agent.tools import DbOrderTools
from app.matching.matcher import Matcher
from app.matching.normalize import normalize
from app.models import (
    Customer,
    Message,
    Order,
    OrderEvent,
    OrderLine,
    PriceList,
    Product,
    ProductAlias,
    ProductUnit,
)
from app.models.enums import (
    Actor,
    AliasSource,
    Channel,
    Direction,
    MatchMethod,
    OrderStatus,
    ReplyKind,
)
from app.services.outbound import queue_reply
from app.services.processing import Runtime


class ReviewError(Exception):
    """The request can't be applied (wrong state, unknown product...). Safe to show staff."""


@dataclass(frozen=True)
class LineEdit:
    sku: str
    unit: str
    qty: Decimal
    source_text: str | None = None  # what the customer wrote, kept for the audit trail
    save_alias: bool = False  # teach the matcher: source_text means this product


async def _locked_order(session: AsyncSession, business_id: int, order_id: int) -> Order:
    order = (
        await session.execute(
            select(Order)
            .where(Order.id == order_id, Order.business_id == business_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise ReviewError("order not found")
    if order.status != OrderStatus.PENDING_REVIEW:
        raise ReviewError(f"order is {order.status}, not waiting for review")
    return order


async def edit_lines(
    session: AsyncSession, business_id: int, order_id: int, user_id: int, edits: list[LineEdit]
) -> Order:
    """Replace the order's lines with staff-checked ones, priced from the price list."""
    if not edits:
        raise ReviewError("an order needs at least one line")
    order = await _locked_order(session, business_id, order_id)
    customer = await session.get_one(Customer, order.customer_id)
    products = {
        p.sku: p
        for p in (
            await session.execute(
                select(Product).where(
                    Product.business_id == business_id,
                    Product.sku.in_({e.sku for e in edits}),
                    Product.active.is_(True),
                )
            )
        ).scalars()
    }
    new_lines, total, aliases = [], Decimal(0), []
    for edit in edits:
        product = products.get(edit.sku)
        if product is None:
            raise ReviewError(f"unknown product {edit.sku}")
        if edit.qty <= 0:
            raise ReviewError(f"quantity for {edit.sku} must be positive")
        sold = await session.scalar(
            select(ProductUnit.id).where(
                ProductUnit.product_id == product.id, ProductUnit.unit_name == edit.unit
            )
        )
        price = await session.scalar(
            select(PriceList.price).where(
                PriceList.product_id == product.id,
                PriceList.tier == customer.price_tier,
                PriceList.unit_name == edit.unit,
            )
        )
        if sold is None or price is None:
            raise ReviewError(f"{edit.sku} is not sold by the {edit.unit}")
        line_total = (price * edit.qty).quantize(Decimal("1"))
        total += line_total
        new_lines.append(
            OrderLine(
                order_id=order.id,
                product_id=product.id,
                unit_name=edit.unit,
                qty=edit.qty,
                unit_price=price,
                line_total=line_total,
                source_text=edit.source_text,
                match_method=MatchMethod.STAFF,
                match_confidence=1.0,
                alternatives=[],
            )
        )
        if edit.save_alias and edit.source_text and normalize(edit.source_text):
            aliases.append((product.id, edit.source_text))

    await session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
    session.add_all(new_lines)
    for product_id, text in aliases:
        exists = await session.scalar(
            select(ProductAlias.id).where(
                ProductAlias.business_id == business_id,
                ProductAlias.product_id == product_id,
                ProductAlias.normalized_text == normalize(text),
            )
        )
        if exists is None:
            session.add(
                ProductAlias(
                    business_id=business_id,
                    product_id=product_id,
                    alias_text=text,
                    normalized_text=normalize(text),
                    source=AliasSource.STAFF,
                )
            )
    order.total = total
    session.add(
        OrderEvent(
            order_id=order.id,
            actor=Actor.STAFF,
            type="lines_edited",
            payload={
                "user_id": user_id,
                "lines": [{"sku": e.sku, "unit": e.unit, "qty": str(e.qty)} for e in edits],
                "aliases_learned": len(aliases),
            },
        )
    )
    await session.flush()
    return order


async def _reply_channel(session: AsyncSession, customer_id: int) -> str:
    channel = await session.scalar(
        select(Message.channel)
        .where(Message.customer_id == customer_id, Message.direction == Direction.IN)
        .order_by(Message.id.desc())
        .limit(1)
    )
    return channel or Channel.WHATSAPP


async def review_order(
    session: AsyncSession, rt: Runtime, business_id: int, order_id: int, action: str, user_id: int
) -> Message:
    """Approve or reject a held order, and queue the customer's reply."""
    if action not in ("approve", "reject"):
        raise ReviewError("action must be approve or reject")
    order = await _locked_order(session, business_id, order_id)
    lines = list(
        (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars()
    )
    if action == "approve":
        unresolved = [
            line.id for line in lines if line.product_id is None or line.unit_price is None
        ]
        if unresolved or not lines:
            raise ReviewError("fix every line (product, unit and quantity) before approving")

    decision: dict[str, Any] = {"action": action, "user_id": user_id}
    index = await rt.catalog.get(session, business_id)
    tools = DbOrderTools(session, business_id, index)
    if order.review_thread_id:
        deps = AgentDeps(tools=tools, matcher=Matcher(index), llm=rt.llm, settings=rt.settings)
        outcome = await resume_review(
            rt.graph, deps, thread_id=order.review_thread_id, decision=decision
        )
        body = outcome.reply or (replies.CANCELLED if action == "reject" else "")
    else:
        body = await _direct_review(session, order, lines, action, index, tools)

    await session.refresh(order)
    order.review_thread_id = None
    session.add(
        OrderEvent(order_id=order.id, actor=Actor.STAFF, type=f"{action}d", payload=decision)
    )
    return queue_reply(
        session,
        business_id=business_id,
        customer_id=order.customer_id,
        channel=await _reply_channel(session, order.customer_id),
        kind=ReplyKind.CONFIRMATION if action == "approve" else ReplyKind.CANCELLED,
        body=body,
        order_id=order.id,
    )


async def _direct_review(
    session: AsyncSession,
    order: Order,
    lines: list[OrderLine],
    action: str,
    index: Any,
    tools: DbOrderTools,
) -> str:
    if action == "reject":
        order.status = OrderStatus.CANCELLED
        return replies.CANCELLED
    for line in lines:
        line.match_method = line.match_method or MatchMethod.STAFF
    order.status = OrderStatus.AWAITING_CUSTOMER
    order.issues = []
    await session.flush()
    draft = await tools.load_draft(order.customer_id)
    summary = replies.order_summary(draft, index, order.total)
    return replies.confirmation(order.number, summary)
