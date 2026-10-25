"""Background tasks run by the arq worker."""

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import Message
from app.models.enums import Channel, JobStatus, MessageStatus
from app.services.inbound import mark_job
from app.services.processing import ProcessResult, Runtime, process_message

log = structlog.get_logger()

MAX_TRIES = 3
LOCK_TIMEOUT_SECONDS = 180


def _customer_lock(ctx: dict[str, Any], sender: str | None) -> AbstractAsyncContextManager[Any]:
    """One message at a time per customer, so two messages never edit the same draft at once."""
    redis = ctx.get("redis")
    if redis is None or sender is None:
        return nullcontext()
    lock: AbstractAsyncContextManager[Any] = redis.lock(
        f"lock:sender:{sender}", timeout=LOCK_TIMEOUT_SECONDS, blocking_timeout=LOCK_TIMEOUT_SECONDS
    )
    return lock


async def _sender(factory: async_sessionmaker[AsyncSession], message_id: int) -> str | None:
    async with factory() as session:
        result = await session.execute(select(Message.sender).where(Message.id == message_id))
        return result.scalar_one_or_none()


async def process_inbound_message(ctx: dict[str, Any], message_id: int, job_id: int) -> None:
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    runtime: Runtime = ctx["runtime"]
    whatsapp: WhatsAppClient | None = ctx.get("whatsapp")
    attempt: int = ctx.get("job_try", 1)
    bound = log.bind(message_id=message_id, job_id=job_id, attempt=attempt)

    async with factory() as session:
        await mark_job(session, job_id, JobStatus.RUNNING)
        await session.commit()

    try:
        async with _customer_lock(ctx, await _sender(factory, message_id)), factory() as session:
            result: ProcessResult = await process_message(session, message_id, runtime)
            await mark_job(session, job_id, JobStatus.DONE)
            await session.commit()
            message = await session.get_one(Message, message_id)
            channel, external_id = message.channel, message.external_id
    except Exception as exc:
        bound.exception("job.failed")
        async with factory() as session:
            if attempt < MAX_TRIES:
                await mark_job(session, job_id, JobStatus.QUEUED, error=str(exc)[:1000])
                await session.commit()
                raise Retry(defer=2**attempt) from exc
            await mark_job(session, job_id, JobStatus.FAILED, error=str(exc)[:1000])
            message = await session.get_one(Message, message_id)
            message.status = MessageStatus.FAILED
            message.error = str(exc)[:1000]
            await session.commit()
        return

    bound.info("job.done", skipped=result.skipped, paused=result.paused)
    # Blue ticks are a nicety; a failure here must not fail the job.
    if whatsapp and channel == Channel.WHATSAPP and external_id and ctx.get("mark_as_read"):
        try:
            await whatsapp.mark_as_read(external_id)
        except WhatsAppError as exc:
            bound.warning("whatsapp.mark_as_read_failed", error=str(exc))
