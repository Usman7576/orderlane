from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from arq import Retry
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_graph
from app.agent.state import AgentSettings
from app.integrations.speech import Transcript
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
from app.models.enums import Channel, Direction, JobStatus, MessageStatus, MessageType, OrderStatus
from app.schemas.inbound import InboundMessage
from app.services.inbound import save_inbound
from app.services.processing import CatalogCache, ProcessResult, Runtime
from app.services.seeding import seed
from app.workers import tasks
from app.workers.tasks import process_inbound_message

pytestmark = pytest.mark.db

Factory = async_sessionmaker[AsyncSession]
SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
TRUSTED_PHONE = "923000000106"  # City Cash & Carry in customers.csv


class FakeWhatsApp:
    def __init__(self) -> None:
        self.read: list[str] = []

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        return b"OggS-fake", "audio/ogg; codecs=opus"

    async def mark_as_read(self, message_id: str) -> None:
        self.read.append(message_id)


class FakeSpeech:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcript | None:
        return Transcript(self.text, "ur-PK", 0.9)


def _runtime(tmp_path: Path, speech: FakeSpeech | None = None) -> Runtime:
    whatsapp = FakeWhatsApp()
    return Runtime(
        graph=build_graph(InMemorySaver()),
        catalog=CatalogCache(),
        llm=None,
        speech=speech,
        whatsapp=whatsapp,  # type: ignore[arg-type]
        storage_dir=tmp_path,
        settings=AgentSettings(),
    )


async def _seeded_business(factory: Factory) -> int:
    async with factory() as session:
        result = await seed(
            session,
            SEED_DIR,
            wa_phone_number_id="PN-W",
            admin_email="a@test.test",
            admin_password_hash=None,
        )
    return result.business_id


async def _save(
    factory: Factory,
    business_id: int,
    *,
    body: str | None = None,
    audio: str | None = None,
    sender: str = TRUSTED_PHONE,
    external_id: str = "wamid.W1",
) -> tuple[int, int]:
    async with factory() as session:
        saved = await save_inbound(
            session,
            business_id,
            InboundMessage(
                channel=Channel.WHATSAPP,
                external_id=external_id,
                sender=sender,
                sender_name="Shop",
                type=MessageType.AUDIO if audio else MessageType.TEXT,
                body=body,
                media_ref=audio,
            ),
        )
        await session.commit()
    assert saved is not None
    return saved.message_id, saved.job_id


def _ctx(factory: Factory, runtime: Runtime, job_try: int = 1) -> dict[str, Any]:
    return {
        "session_factory": factory,
        "runtime": runtime,
        "whatsapp": runtime.whatsapp,
        "mark_as_read": True,
        "job_try": job_try,
    }


async def test_text_order_creates_draft_trace_and_reply(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await _seeded_business(session_factory)
    message_id, job_id = await _save(session_factory, business_id, body="2 ctn tapal 950")
    runtime = _runtime(tmp_path)

    await process_inbound_message(_ctx(session_factory, runtime), message_id, job_id)

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
    assert reply.status == MessageStatus.QUEUED and "Order #1001" in (reply.body or "")
    assert job.status == JobStatus.DONE
    assert runtime.whatsapp.read == ["wamid.W1"]  # type: ignore[union-attr]


async def test_voice_note_is_transcribed_then_understood(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await _seeded_business(session_factory)
    message_id, job_id = await _save(session_factory, business_id, audio="MEDIA-1")
    runtime = _runtime(tmp_path, FakeSpeech("teen carton olpers"))

    await process_inbound_message(_ctx(session_factory, runtime), message_id, job_id)

    async with session_factory() as session:
        message = await session.get_one(Message, message_id)
        line = (await session.execute(select(OrderLine))).scalar_one()
    assert message.transcript == "teen carton olpers"
    assert (line.qty, line.unit_name) == (Decimal(3), "carton")
    assert (tmp_path / "audio" / f"{message_id}.ogg").exists()


async def test_voice_note_without_speech_is_skipped_not_failed(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await _seeded_business(session_factory)
    message_id, job_id = await _save(session_factory, business_id, audio="MEDIA-2")
    await process_inbound_message(_ctx(session_factory, _runtime(tmp_path)), message_id, job_id)
    async with session_factory() as session:
        message = await session.get_one(Message, message_id)
    assert message.status == MessageStatus.PROCESSED
    assert "not configured" in (message.error or "")


async def test_new_sender_becomes_customer_and_first_order_is_reviewed(
    session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await _seeded_business(session_factory)
    message_id, job_id = await _save(
        session_factory, business_id, body="1 ctn tapal 950", sender="923009999999"
    )
    await process_inbound_message(_ctx(session_factory, _runtime(tmp_path)), message_id, job_id)
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
    business_id = await _seeded_business(session_factory)
    message_id, job_id = await _save(session_factory, business_id, body="2 ctn tapal 950")
    runtime = _runtime(tmp_path)
    await process_inbound_message(_ctx(session_factory, runtime), message_id, job_id)
    await process_inbound_message(_ctx(session_factory, runtime), message_id, job_id)
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
    message_id, job_id = await _save(session_factory, business_id, body="hi")

    async def boom(session: AsyncSession, message_id: int, runtime: Runtime) -> ProcessResult:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(tasks, "process_message", boom)
    runtime = _runtime(tmp_path)

    with pytest.raises(Retry):
        await process_inbound_message(_ctx(session_factory, runtime, job_try=1), message_id, job_id)
    async with session_factory() as session:
        job = await session.get_one(Job, job_id)
    assert job.status == JobStatus.QUEUED

    await process_inbound_message(
        _ctx(session_factory, runtime, job_try=tasks.MAX_TRIES), message_id, job_id
    )
    async with session_factory() as session:
        job = await session.get_one(Job, job_id)
        message = await session.get_one(Message, message_id)
    assert job.status == JobStatus.FAILED and "database exploded" in (job.last_error or "")
    assert message.status == MessageStatus.FAILED
