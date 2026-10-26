from decimal import Decimal
from pathlib import Path

import pytest
from arq import Retry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    AgentStep,
    Business,
    Customer,
    Job,
    Message,
    Order,
    OrderLine,
    Product,
)
from app.models.enums import Direction, JobStatus, MessageStatus, OrderStatus
from app.services.processing import ProcessResult, Runtime
from app.workers import tasks
from app.workers.tasks import process_inbound_message
from tests.integration.helpers import (
    Factory,
    FakeSpeech,
    fake_whatsapp,
    make_runtime,
    save_message,
    seeded_business,
    worker_ctx,
)

pytestmark = pytest.mark.db


async def test_text_order_creates_draft_trace_and_reply(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    message_id, job_id = await save_message(
        session_factory, business_id, body="2 ctn tapal 950", external_id="wamid.W1"
    )
    inbound_external_id = "wamid.W1"
    runtime = make_runtime(tmp_path)

    await process_inbound_message(worker_ctx(session_factory, runtime), message_id, job_id)

    async with session_factory() as session:
        order = (await session.execute(select(Order))).scalar_one()
        line = (await session.execute(select(OrderLine))).scalar_one()
        product = await session.get_one(Product, line.product_id)
        run = (await session.execute(select(AgentRun))).scalar_one()
        steps = (
            (await session.execute(select(AgentStep.node).order_by(AgentStep.id))).scalars().all()
        )
        reply = (
            await session.execute(select(Message).where(Message.direction == Direction.OUT))
        ).scalar_one()
        job = await session.get_one(Job, job_id)

    assert (product.sku, line.qty, line.unit_name) == ("TAP-DAN-950", Decimal(2), "carton")
    assert order.number == 1001 and order.status == OrderStatus.AWAITING_CUSTOMER
    assert line.unit_price == Decimal(21103)  # tier B carton price
    assert (run.status, run.intent, run.route, run.prompt_version) == (
        "done",
        "new_order",
        "ask_customer",
        "rules",
    )
    assert steps[0] == "interpret" and "save_order" in steps
    assert reply.status == MessageStatus.SENT and "Order #1001" in (reply.body or "")
    assert job.status == JobStatus.DONE
    assert fake_whatsapp(runtime).read == [inbound_external_id]
    assert fake_whatsapp(runtime).sent[0]["kind"] == "buttons"  # Confirm / Edit / Cancel


async def test_voice_note_is_transcribed_then_understood(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    message_id, job_id = await save_message(session_factory, business_id, audio="MEDIA-1")
    runtime = make_runtime(tmp_path, FakeSpeech("teen carton olpers"))

    await process_inbound_message(worker_ctx(session_factory, runtime), message_id, job_id)

    async with session_factory() as session:
        message = await session.get_one(Message, message_id)
        line = (await session.execute(select(OrderLine))).scalar_one()
    assert message.transcript == "teen carton olpers"
    assert (line.qty, line.unit_name) == (Decimal(3), "carton")
    assert (tmp_path / "audio" / f"{message_id}.ogg").exists()


async def test_voice_note_without_speech_is_skipped_not_failed(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    message_id, job_id = await save_message(session_factory, business_id, audio="MEDIA-2")
    await process_inbound_message(
        worker_ctx(session_factory, make_runtime(tmp_path)), message_id, job_id
    )
    async with session_factory() as session:
        message = await session.get_one(Message, message_id)
    assert message.status == MessageStatus.PROCESSED
    assert "not configured" in (message.error or "")


async def test_new_sender_becomes_customer_and_first_order_is_reviewed(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    message_id, job_id = await save_message(
        session_factory, business_id, body="1 ctn tapal 950", sender="923009999999"
    )
    await process_inbound_message(
        worker_ctx(session_factory, make_runtime(tmp_path)), message_id, job_id
    )
    async with session_factory() as session:
        customer = (
            await session.execute(select(Customer).where(Customer.wa_phone == "923009999999"))
        ).scalar_one()
        order = (await session.execute(select(Order))).scalar_one()
        run = (await session.execute(select(AgentRun))).scalar_one()
    assert customer.is_new and order.customer_id == customer.id
    assert order.status == OrderStatus.PENDING_REVIEW
    assert run.status == "paused" and {"code": "first_order"} in order.issues


async def test_reprocessing_is_harmless(session_factory: Factory, tmp_path: Path) -> None:
    business_id = await seeded_business(session_factory)
    message_id, job_id = await save_message(session_factory, business_id, body="2 ctn tapal 950")
    runtime = make_runtime(tmp_path)
    await process_inbound_message(worker_ctx(session_factory, runtime), message_id, job_id)
    await process_inbound_message(worker_ctx(session_factory, runtime), message_id, job_id)
    async with session_factory() as session:
        orders = (await session.execute(select(func.count()).select_from(Order))).scalar_one()
        runs = (await session.execute(select(func.count()).select_from(AgentRun))).scalar_one()
    assert (orders, runs) == (1, 1)


async def test_failure_retries_then_gives_up(
    session_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        business = Business(name="Worker Test", wa_phone_number_id="PN-X", settings={})
        session.add(business)
        await session.commit()
        business_id = business.id
    message_id, job_id = await save_message(session_factory, business_id, body="hi")

    async def boom(session: AsyncSession, message_id: int, runtime: Runtime) -> ProcessResult:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(tasks, "process_message", boom)
    runtime = make_runtime(tmp_path)

    with pytest.raises(Retry):
        await process_inbound_message(
            worker_ctx(session_factory, runtime, job_try=1), message_id, job_id
        )
    async with session_factory() as session:
        job = await session.get_one(Job, job_id)
    assert job.status == JobStatus.QUEUED

    await process_inbound_message(
        worker_ctx(session_factory, runtime, job_try=tasks.MAX_TRIES), message_id, job_id
    )
    async with session_factory() as session:
        job = await session.get_one(Job, job_id)
        message = await session.get_one(Message, message_id)
    assert job.status == JobStatus.FAILED and "database exploded" in (job.last_error or "")
    assert message.status == MessageStatus.FAILED
