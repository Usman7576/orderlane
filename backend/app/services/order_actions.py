"""What customers do with an order that waits for them: confirm, edit or cancel.

A confirmation reserves stock, adds the invoice to the customer's ledger and creates the
invoice PDF, all in one database transaction with the order row locked, so a double tap on
"Confirm" can never invoice twice."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import replies
from app.matching.normalize import clean
from app.models import (
    Business,
    Customer,
    Inventory,
    Invoice,
    LedgerEntry,
    Message,
    Order,
    OrderEvent,
    OrderLine,
    Product,
    ProductUnit,
)
from app.models.enums import Actor, LedgerType, MessageType, OrderStatus, ReplyKind
from app.services.invoice_pdf import InvoiceData, InvoiceLine, render_invoice
from app.services.outbound import queue_reply

CONFIRM_WORDS = {
    "1", "confirm", "confirmed", "ok", "okay", "yes", "haan", "han", "ji", "jee", "done",
    "theek", "theek hai", "thik hai", "sahi hai", "bhej do", "go ahead",
}  # fmt: skip
EDIT_WORDS = {"2", "edit", "change", "changes", "tabdeeli", "badlo"}
CANCEL_WORDS = {"3", "cancel", "cancel order"}


@dataclass(frozen=True)
class CustomerAction:
    action: str  # confirm | edit | cancel
    order_id: int | None  # from a button; typed replies apply to the waiting order


def parse_action(message: Message) -> CustomerAction | None:
    """A button tap, or a short typed reply like "1" / "ok" / "edit"."""
    if message.type == MessageType.INTERACTIVE:
        reply = ((message.raw or {}).get("interactive") or {}).get("button_reply") or {}
        action, _, order_id = str(reply.get("id", "")).partition(":")
        if action in ("confirm", "edit", "cancel") and order_id.isdigit():
            return CustomerAction(action, int(order_id))
        return None
    if message.type != MessageType.TEXT or not message.body:
        return None
    text = clean(message.body).replace("?", "").strip()
    for action, words in (
        ("confirm", CONFIRM_WORDS),
        ("edit", EDIT_WORDS),
        ("cancel", CANCEL_WORDS),
    ):
        if text in words:
            return CustomerAction(action, None)
    return None


async def waiting_order(session: AsyncSession, customer_id: int) -> Order | None:
    return (
        await session.execute(
            select(Order)
            .where(Order.customer_id == customer_id, Order.status == OrderStatus.AWAITING_CUSTOMER)
            .order_by(Order.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _event(order_id: int, actor: Actor, type_: str, payload: dict[str, Any]) -> OrderEvent:
    return OrderEvent(order_id=order_id, actor=actor, type=type_, payload=payload)


async def handle_customer_action(
    session: AsyncSession, message: Message, action: CustomerAction, storage_dir: Path
) -> bool:
    """Apply the action and queue the reply. Returns False if a typed reply doesn't refer to
    any waiting order (so the message should go to the agent instead)."""
    assert message.customer_id is not None
    if action.order_id is None:
        order = await waiting_order(session, message.customer_id)
        if order is None:
            return False
    else:
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == action.order_id, Order.customer_id == message.customer_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    def reply(kind: ReplyKind, body: str, **extra: Any) -> None:
        queue_reply(
            session,
            business_id=message.business_id,
            customer_id=message.customer_id or 0,
            channel=message.channel,
            kind=kind,
            body=body,
            order_id=order.id if order else None,
            extra=extra or None,
        )

    if order is None:
        reply(ReplyKind.INFO, "Sorry, we couldn't find that order.")
        return True
    if order.status != OrderStatus.AWAITING_CUSTOMER:
        reply(ReplyKind.INFO, _stale_text(order))
        return True

    if action.action == "edit":
        order.status = OrderStatus.DRAFT
        session.add(_event(order.id, Actor.CUSTOMER, "edit_requested", {"message_id": message.id}))
        reply(ReplyKind.EDIT_PROMPT, replies.EDIT_PROMPT)
    elif action.action == "cancel":
        order.status = OrderStatus.CANCELLED
        session.add(_event(order.id, Actor.CUSTOMER, "cancelled", {"message_id": message.id}))
        reply(ReplyKind.CANCELLED, replies.CANCELLED)
    else:
        invoice = await confirm_and_invoice(session, order, storage_dir, message.id)
        if invoice is None:
            reply(ReplyKind.REVIEW_HOLD, replies.STOCK_CHANGED)
        else:
            reply(ReplyKind.ORDER_CONFIRMED, replies.order_confirmed(order.number))
            reply(
                ReplyKind.INVOICE,
                f"Invoice {invoice.number}",
                path=invoice.pdf_path,
                filename=f"{invoice.number}.pdf",
            )
    return True


def _stale_text(order: Order) -> str:
    if order.status in (OrderStatus.CONFIRMED, OrderStatus.INVOICED):
        return f"Order #{order.number} is already confirmed."
    if order.status == OrderStatus.CANCELLED:
        return f"Order #{order.number} was cancelled."
    return f"Order #{order.number} has changed. We'll send you the updated order to confirm."


async def confirm_and_invoice(
    session: AsyncSession, order: Order, storage_dir: Path, message_id: int | None = None
) -> Invoice | None:
    """Reserve stock, post to the ledger and create the invoice. Returns None (and sends
    the order back to staff review) if stock ran out since the order was checked."""
    lines = list(
        (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars()
    )
    needed: dict[int, int] = {}
    for line in lines:
        if line.product_id is None or line.unit_name is None:
            raise ValueError(f"order {order.id} has an unresolved line")
        per_unit = await session.scalar(
            select(ProductUnit.base_qty).where(
                ProductUnit.product_id == line.product_id, ProductUnit.unit_name == line.unit_name
            )
        )
        needed[line.product_id] = needed.get(line.product_id, 0) + int(line.qty * (per_unit or 1))

    stock = {
        row.product_id: row
        for row in (
            await session.execute(
                select(Inventory)
                .where(Inventory.product_id.in_(needed))
                .order_by(Inventory.product_id)  # consistent lock order avoids deadlocks
                .with_for_update()
            )
        ).scalars()
    }
    short = [
        pid for pid, qty in needed.items()
        if pid in stock and stock[pid].on_hand_base - stock[pid].reserved_base < qty
    ]  # fmt: skip
    if short:
        order.status = OrderStatus.PENDING_REVIEW
        order.issues = [*order.issues, {"code": "out_of_stock_at_confirm", "products": short}]
        session.add(_event(order.id, Actor.SYSTEM, "stock_check_failed", {"products": short}))
        return None
    for pid, qty in needed.items():
        if pid in stock:
            stock[pid].reserved_base += qty

    customer = await session.get_one(Customer, order.customer_id)
    business = await session.get_one(Business, order.business_id)
    previous_balance = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.customer_id == customer.id
            )
        )
        or 0
    )
    number = f"INV-{order.business_id}-{order.number}"
    now = datetime.now(UTC)
    order.status = OrderStatus.INVOICED
    order.confirmed_at = now
    customer.is_new = False
    session.add(
        LedgerEntry(
            business_id=order.business_id,
            customer_id=customer.id,
            type=LedgerType.INVOICE,
            amount=order.total,
            ref=number,
        )
    )

    names = {
        p.id: p.name
        for p in (await session.execute(select(Product).where(Product.id.in_(needed)))).scalars()
    }
    pdf = render_invoice(
        InvoiceData(
            number=number,
            order_number=order.number,
            issued_at=now,
            business_name=business.name,
            customer_name=customer.name,
            currency=business.currency,
            lines=[
                InvoiceLine(
                    names.get(line.product_id or 0, "Item"),
                    line.qty,
                    line.unit_name or "",
                    line.unit_price or Decimal(0),
                    line.line_total or Decimal(0),
                )
                for line in lines
            ],
            total=order.total,
            previous_balance=previous_balance,
        )
    )
    relative = Path("invoices") / f"{number}.pdf"
    (storage_dir / relative).parent.mkdir(parents=True, exist_ok=True)
    (storage_dir / relative).write_bytes(pdf)
    invoice = Invoice(order_id=order.id, number=number, pdf_path=relative.as_posix())
    session.add(invoice)
    session.add(_event(order.id, Actor.CUSTOMER, "confirmed", {"message_id": message_id}))
    session.add(_event(order.id, Actor.SYSTEM, "invoiced", {"invoice": number}))
    await session.flush()
    return invoice
