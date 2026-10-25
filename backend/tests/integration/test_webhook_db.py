"""End-to-end webhook behavior against a real PostgreSQL database."""

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Business, Job, Message
from app.models.enums import Channel, Direction, JobStatus, MessageStatus, MessageType
from tests.conftest import FakeQueue
from tests.factories import PHONE_NUMBER_ID, audio_payload, signed, status_payload, text_payload

pytestmark = pytest.mark.db

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def business(session_factory: Factory) -> Business:
    async with session_factory() as session:
        business = Business(name="Test Wholesale", wa_phone_number_id=PHONE_NUMBER_ID, settings={})
        session.add(business)
        await session.commit()
        return business


async def _post(client: httpx.AsyncClient, payload: dict[str, object]) -> httpx.Response:
    body, headers = signed(payload)  # type: ignore[arg-type]
    return await client.post("/webhooks/whatsapp", content=body, headers=headers)


async def test_text_message_is_saved_and_queued(
    db_client: httpx.AsyncClient, queue: FakeQueue, session_factory: Factory, business: Business
) -> None:
    response = await _post(db_client, text_payload("5 ctn tapal 950"))
    assert response.status_code == 200

    async with session_factory() as session:
        message = (await session.execute(select(Message))).scalar_one()
        job = (await session.execute(select(Job))).scalar_one()
    assert message.business_id == business.id
    assert message.channel == Channel.WHATSAPP
    assert message.direction == Direction.IN
    assert message.body == "5 ctn tapal 950"
    assert message.status == MessageStatus.RECEIVED
    assert job.status == JobStatus.QUEUED
    assert queue.calls == [(message.id, job.id)]


async def test_duplicate_delivery_is_saved_and_queued_once(
    db_client: httpx.AsyncClient, queue: FakeQueue, session_factory: Factory, business: Business
) -> None:
    payload = text_payload("2 dozen surf 500", message_id="wamid.DUP")
    for _ in range(3):
        assert (await _post(db_client, payload)).status_code == 200

    async with session_factory() as session:
        count = (await session.execute(select(func.count()).select_from(Message))).scalar_one()
    assert count == 1
    assert len(queue.calls) == 1


async def test_audio_message_keeps_media_reference(
    db_client: httpx.AsyncClient, session_factory: Factory, business: Business
) -> None:
    await _post(db_client, audio_payload("MEDIA-7"))
    async with session_factory() as session:
        message = (await session.execute(select(Message))).scalar_one()
    assert message.type == MessageType.AUDIO
    assert message.media_ref == "MEDIA-7"


async def test_unknown_business_number_is_ignored(
    db_client: httpx.AsyncClient, queue: FakeQueue, session_factory: Factory, business: Business
) -> None:
    response = await _post(db_client, text_payload("hi", phone_number_id="999"))
    assert response.status_code == 200
    async with session_factory() as session:
        count = (await session.execute(select(func.count()).select_from(Message))).scalar_one()
    assert count == 0
    assert queue.calls == []


async def test_status_updates_only_move_forward(
    db_client: httpx.AsyncClient, session_factory: Factory, business: Business
) -> None:
    async with session_factory() as session:
        session.add(
            Message(
                business_id=business.id,
                channel=Channel.WHATSAPP,
                external_id="wamid.OUT1",
                direction=Direction.OUT,
                type=MessageType.TEXT,
                body="Your order",
                status=MessageStatus.SENT,
            )
        )
        await session.commit()

    await _post(db_client, status_payload("wamid.OUT1", "read"))
    await _post(db_client, status_payload("wamid.OUT1", "delivered"))  # arrives late

    async with session_factory() as session:
        message = (await session.execute(select(Message))).scalar_one()
    assert message.status == MessageStatus.READ
