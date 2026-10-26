"""Day 4 flows end to end against PostgreSQL: replies, confirmation, invoices, staff review."""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.integrations.whatsapp import WhatsAppError
from app.models import (
    Customer,
    Inventory,
    Invoice,
    LedgerEntry,
    Message,
    Order,
    OrderLine,
    Product,
    ProductAlias,
)
from app.models.enums import Direction, MessageStatus, OrderStatus, ReplyKind
from app.services.outbound import deliver_replies, senders_for
from app.services.processing import Runtime, process_message
from app.services.review import LineEdit, ReviewError, edit_lines, review_order
from app.workers.tasks import process_inbound_message
from tests.integration.helpers import (
    NEW_PHONE,
    Factory,
    fake_whatsapp,
    make_runtime,
    save_message,
    seeded_business,
    worker_ctx,
)

pytestmark = pytest.mark.db


async def _send(factory: Factory, runtime: Runtime, business_id: int, **kwargs: object) -> int:
    """Save an inbound message and run the worker task on it (replies delivered inline)."""
    message_id, job_id = await save_message(factory, business_id, **kwargs)  # type: ignore[arg-type]
    await process_inbound_message(worker_ctx(factory, runtime), message_id, job_id)
    return message_id


async def _only_order(factory: Factory) -> Order:
    async with factory() as session:
        return (await session.execute(select(Order))).scalar_one()


async def _reserved(factory: Factory, sku: str) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(Inventory.reserved_base)
                .join(Product, Product.id == Inventory.product_id)
                .where(Product.sku == sku)
            )
            or 0
        )


async def test_confirm_button_reserves_stock_bills_and_sends_invoice(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="2 ctn tapal 950")
    order = await _only_order(session_factory)
    assert order.status == OrderStatus.AWAITING_CUSTOMER

    await _send(session_factory, runtime, business_id, button_id=f"confirm:{order.id}")

    order = await _only_order(session_factory)
    async with session_factory() as session:
        invoice = (await session.execute(select(Invoice))).scalar_one()
        ledger = (
            await session.execute(select(LedgerEntry).where(LedgerEntry.ref == invoice.number))
        ).scalar_one()
        customer = await session.get_one(Customer, order.customer_id)
    assert order.status == OrderStatus.INVOICED and order.confirmed_at is not None
    assert invoice.number == f"INV-{business_id}-{order.number}"
    assert (tmp_path / invoice.pdf_path).read_bytes().startswith(b"%PDF")
    assert ledger.amount == order.total == Decimal(42206)  # 2 x 21,103 (tier B carton)
    assert await _reserved(session_factory, "TAP-DAN-950") == 24  # 2 cartons of 12
    assert customer.is_new is False
    kinds = [item["kind"] for item in fake_whatsapp(runtime).sent]
    assert kinds == ["buttons", "text", "document"]  # order, "confirmed", invoice PDF


async def test_double_tap_confirms_once(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="1 ctn tapal 950")
    order = await _only_order(session_factory)
    for _ in range(2):
        await _send(session_factory, runtime, business_id, button_id=f"confirm:{order.id}")

    async with session_factory() as session:
        invoices = await session.scalar(select(func.count()).select_from(Invoice))
    assert invoices == 1
    assert await _reserved(session_factory, "TAP-DAN-950") == 12
    assert "already confirmed" in fake_whatsapp(runtime).sent[-1]["text"]


async def test_typed_reply_confirms_waiting_order(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="1 ctn tapal 950")
    await _send(session_factory, runtime, business_id, body="1")
    assert (await _only_order(session_factory)).status == OrderStatus.INVOICED


async def test_edit_then_change_then_confirm(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="2 ctn olpers")
    order = await _only_order(session_factory)
    await _send(session_factory, runtime, business_id, button_id=f"edit:{order.id}")
    assert (await _only_order(session_factory)).status == OrderStatus.DRAFT
    assert "send your changes" in fake_whatsapp(runtime).sent[-1]["text"]

    await _send(session_factory, runtime, business_id, body="olpers 5 carton kar do")
    order = await _only_order(session_factory)
    async with session_factory() as session:
        line = (await session.execute(select(OrderLine))).scalar_one()
    assert order.status == OrderStatus.AWAITING_CUSTOMER and line.qty == Decimal(5)


async def test_cancel_button(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="2 ctn sprite")
    order = await _only_order(session_factory)
    await _send(session_factory, runtime, business_id, button_id=f"cancel:{order.id}")
    assert (await _only_order(session_factory)).status == OrderStatus.CANCELLED
    assert await _reserved(session_factory, "SPR-1500") == 0


async def test_burst_of_messages_gets_one_confirmation(
    session_factory: Factory, tmp_path: Path
) -> None:
    """Two quick messages: the first reply job sees a newer message and steps aside;
    the second sends one confirmation and drops the outdated one."""
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    first, _ = await save_message(session_factory, business_id, body="2 ctn pepsi 1.5")
    second, _ = await save_message(session_factory, business_id, body="aur 1 ctn pepsi 500 bhi")
    for message_id in (first, second):
        async with session_factory() as session:
            await process_message(session, message_id, runtime)
            await session.commit()

    senders = senders_for(runtime.whatsapp, tmp_path)
    async with session_factory() as session:
        customer_id = (await session.get_one(Message, first)).customer_id
        assert customer_id is not None
        stale = await deliver_replies(session, customer_id, first, senders)
        fresh = await deliver_replies(session, customer_id, second, senders)
        await session.commit()
    assert stale.superseded
    assert (fresh.sent, fresh.skipped) == (1, 1)
    [sent] = fake_whatsapp(runtime).sent
    assert "Pepsi 1.5 Litre" in sent["text"] and "Pepsi 500ml" in sent["text"]


async def test_stock_gone_at_confirm_sends_order_back_to_review(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="2 ctn tapal 950")
    order = await _only_order(session_factory)
    async with session_factory() as session:  # someone else bought the stock meanwhile
        await session.execute(update(Inventory).values(reserved_base=Inventory.on_hand_base))
        await session.commit()

    await _send(session_factory, runtime, business_id, button_id=f"confirm:{order.id}")

    order = await _only_order(session_factory)
    assert order.status == OrderStatus.PENDING_REVIEW
    assert any(issue["code"] == "out_of_stock_at_confirm" for issue in order.issues)
    assert "out of stock" in fake_whatsapp(runtime).sent[-1]["text"]


async def test_staff_edit_learn_alias_and_approve_resumes_agent(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="1 dz ariel", sender=NEW_PHONE)
    order = await _only_order(session_factory)
    assert order.status == OrderStatus.PENDING_REVIEW and order.review_thread_id

    async with session_factory() as session:
        await edit_lines(
            session,
            business_id,
            order.id,
            user_id=1,
            edits=[
                LineEdit("ARL-DET-500", "dozen", Decimal(2), source_text="ariel", save_alias=True)
            ],
        )
        await session.commit()
    async with session_factory() as session:
        reply = await review_order(session, runtime, business_id, order.id, "approve", user_id=1)
        await session.commit()

    order = await _only_order(session_factory)
    async with session_factory() as session:
        alias = (
            await session.execute(select(ProductAlias).where(ProductAlias.source == "staff"))
        ).scalar_one()
    assert order.status == OrderStatus.AWAITING_CUSTOMER and order.review_thread_id is None
    assert order.total == Decimal(8880)  # 2 dozen x 4,440 (tier A)
    assert (reply.raw or {})["kind"] == ReplyKind.CONFIRMATION
    assert "Ariel Detergent 500g" in (reply.body or "") and "Rs 8,880" in (reply.body or "")
    assert alias.normalized_text == "ariel"


async def test_staff_reject(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="1 ctn tapal 950", sender=NEW_PHONE)
    order = await _only_order(session_factory)
    async with session_factory() as session:
        reply = await review_order(session, runtime, business_id, order.id, "reject", user_id=1)
        await session.commit()
    order = await _only_order(session_factory)
    async with session_factory() as session:
        lines = await session.scalar(select(func.count()).select_from(OrderLine))
    assert order.status == OrderStatus.CANCELLED and lines == 1  # lines kept for the audit trail
    assert (reply.raw or {})["kind"] == ReplyKind.CANCELLED


async def test_review_rejects_invalid_requests(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    await _send(session_factory, runtime, business_id, body="2 ctn tapal 950")  # not held
    order = await _only_order(session_factory)
    async with session_factory() as session:
        with pytest.raises(ReviewError, match="not waiting for review"):
            await review_order(session, runtime, business_id, order.id, "approve", user_id=1)
    async with session_factory() as session:
        with pytest.raises(ReviewError, match="order not found"):
            await review_order(session, runtime, business_id + 99, order.id, "approve", user_id=1)


async def test_permanent_send_failure_is_recorded(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    fake_whatsapp(runtime).fail_with = WhatsAppError(
        "WhatsApp API 400: bad number", retryable=False
    )
    await _send(session_factory, runtime, business_id, body="2 ctn tapal 950")
    async with session_factory() as session:
        reply = (
            await session.execute(select(Message).where(Message.direction == Direction.OUT))
        ).scalar_one()
    assert reply.status == MessageStatus.FAILED and "bad number" in (reply.error or "")


async def test_temporary_send_failure_is_retried(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    runtime = make_runtime(tmp_path)
    message_id, _ = await save_message(session_factory, business_id, body="2 ctn tapal 950")
    async with session_factory() as session:
        await process_message(session, message_id, runtime)
        await session.commit()
        customer_id = (await session.get_one(Message, message_id)).customer_id
    assert customer_id is not None
    fake_whatsapp(runtime).fail_with = WhatsAppError("WhatsApp API 503", retryable=True)
    async with session_factory() as session:
        with pytest.raises(WhatsAppError):
            await deliver_replies(
                session, customer_id, message_id, senders_for(runtime.whatsapp, tmp_path)
            )
    async with session_factory() as session:
        reply = (
            await session.execute(select(Message).where(Message.direction == Direction.OUT))
        ).scalar_one()
    assert reply.status == MessageStatus.QUEUED  # still waiting for the retry
