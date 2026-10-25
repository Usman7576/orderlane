"""Background tasks run by the arq worker."""

from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import Message
from app.models.enums import Channel, JobStatus, MessageStatus
from app.services.inbound import get_or_create_customer, mark_job

log = structlog.get_logger()

MAX_TRIES = 3


async def handle_message(session: AsyncSession, message_id: int) -> Message:
    """Day 1 processing: link the message to a customer and mark it processed.
    The agent (Day 3) will plug in here."""
    message = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
    if message.status == MessageStatus.PROCESSED:
        return message  # already done by an earlier attempt
    if message.sender:
        customer = await get_or_create_customer(
            session, message.business_id, message.sender, message.sender_name
        )
        message.customer_id = customer.id
    message.status = MessageStatus.PROCESSED
    return message


async def process_inbound_message(ctx: dict[str, Any], message_id: int, job_id: int) -> None:
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    whatsapp: WhatsAppClient | None = ctx.get("whatsapp")
    attempt: int = ctx.get("job_try", 1)
    bound = log.bind(message_id=message_id, job_id=job_id, attempt=attempt)

    async with factory() as session:
        await mark_job(session, job_id, JobStatus.RUNNING)
        await session.commit()

    try:
        async with factory() as session:
            message = await handle_message(session, message_id)
            await mark_job(session, job_id, JobStatus.DONE)
            await session.commit()
            channel, external_id = message.channel, message.external_id
    except Exception as exc:
        bound.exception("job.failed")
        async with factory() as session:
            if attempt < MAX_TRIES:
                await mark_job(session, job_id, JobStatus.QUEUED, error=str(exc)[:1000])
                await session.commit()
                raise Retry(defer=2**attempt) from exc
            await mark_job(session, job_id, JobStatus.FAILED, error=str(exc)[:1000])
            message = (
                await session.execute(select(Message).where(Message.id == message_id))
            ).scalar_one()
            message.status = MessageStatus.FAILED
            message.error = str(exc)[:1000]
            await session.commit()
        return

    bound.info("job.done")
    # Blue ticks are a nicety; a failure here must not fail the job.
    if whatsapp and channel == Channel.WHATSAPP and external_id and ctx.get("mark_as_read"):
        try:
            await whatsapp.mark_as_read(external_id)
        except WhatsAppError as exc:
            bound.warning("whatsapp.mark_as_read_failed", error=str(exc))
