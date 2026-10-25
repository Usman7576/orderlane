from typing import Any

import pytest
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Business, Customer, Job, Message
from app.models.enums import Channel, Direction, JobStatus, MessageStatus, MessageType
from app.schemas.inbound import InboundMessage
from app.services.inbound import save_inbound
from app.workers import tasks
from app.workers.tasks import process_inbound_message

pytestmark = pytest.mark.db

Factory = async_sessionmaker[AsyncSession]


async def _saved_message(factory: Factory, sender: str = "923001234567") -> tuple[int, int]:
    async with factory() as session:
        business = Business(name="Worker Test", wa_phone_number_id="PN-W", settings={})
        session.add(business)
        await session.flush()
        saved = await save_inbound(
            session,
            business.id,
            InboundMessage(
                channel=Channel.WHATSAPP,
                external_id="wamid.W1",
                sender=sender,
                sender_name="Noor Store",
                type=MessageType.TEXT,
                body="5 ctn tapal",
            ),
        )
        await session.commit()
    assert saved is not None
    return saved.message_id, saved.job_id


def _ctx(factory: Factory, job_try: int = 1) -> dict[str, Any]:
    return {"session_factory": factory, "whatsapp": None, "job_try": job_try}


async def test_creates_customer_and_marks_processed(session_factory: Factory) -> None:
    message_id, job_id = await _saved_message(session_factory)
    await process_inbound_message(_ctx(session_factory), message_id, job_id)

    async with session_factory() as session:
        message = await session.get(Message, message_id)
        job = await session.get(Job, job_id)
        customer = (await session.execute(select(Customer))).scalar_one()
    assert message is not None and job is not None
    assert message.status == MessageStatus.PROCESSED
    assert message.customer_id == customer.id
    assert customer.name == "Noor Store"
    assert customer.is_new is True
    assert (job.status, job.attempts) == (JobStatus.DONE, 1)


async def test_reprocessing_is_harmless(session_factory: Factory) -> None:
    message_id, job_id = await _saved_message(session_factory)
    await process_inbound_message(_ctx(session_factory), message_id, job_id)
    await process_inbound_message(_ctx(session_factory), message_id, job_id)

    async with session_factory() as session:
        customers = (await session.execute(select(Customer))).scalars().all()
    assert len(customers) == 1


async def test_failure_retries_then_gives_up(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    message_id, job_id = await _saved_message(session_factory)

    async def boom(session: AsyncSession, message_id: int) -> Message:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(tasks, "handle_message", boom)

    with pytest.raises(Retry):
        await process_inbound_message(_ctx(session_factory, job_try=1), message_id, job_id)
    async with session_factory() as session:
        job = await session.get(Job, job_id)
    assert job is not None and job.status == JobStatus.QUEUED

    await process_inbound_message(
        _ctx(session_factory, job_try=tasks.MAX_TRIES), message_id, job_id
    )
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        message = await session.get(Message, message_id)
    assert job is not None and message is not None
    assert job.status == JobStatus.FAILED
    assert "database exploded" in (job.last_error or "")
    assert message.status == MessageStatus.FAILED


async def test_saved_message_is_inbound(session_factory: Factory) -> None:
    message_id, _ = await _saved_message(session_factory)
    async with session_factory() as session:
        message = await session.get(Message, message_id)
    assert message is not None and message.direction == Direction.IN
