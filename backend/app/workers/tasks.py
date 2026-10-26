"""Background tasks run by the arq worker."""

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import Customer, Message
from app.models.enums import Channel, Direction, JobStatus, MessageStatus
from app.services.inbound import mark_job
from app.services.outbound import deliver_replies as deliver_queued
from app.services.outbound import fail_queued, senders_for
from app.services.processing import ProcessResult, Runtime, process_message
from app.services.review import ReviewError, review_order

log = structlog.get_logger()

MAX_TRIES = 3
DELIVER_REPLIES = "deliver_replies"
REVIEW_ORDER = "review_order_task"
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
    if result.customer_id is not None and result.deliver_after_s is not None:
        await schedule_delivery(ctx, result.customer_id, message_id, result.deliver_after_s)


async def schedule_delivery(
    ctx: dict[str, Any], customer_id: int, after_message_id: int, delay_s: float
) -> None:
    """Send the customer's queued replies after `delay_s`. Without a queue (tests, scripts)
    they are delivered right away."""
    redis = ctx.get("redis")
    if redis is None:
        await deliver_replies(ctx, customer_id, after_message_id)
        return
    await redis.enqueue_job(
        DELIVER_REPLIES,
        customer_id,
        after_message_id,
        _job_id=f"deliver:{customer_id}:{after_message_id}",
        _defer_by=delay_s,
    )


async def deliver_replies(ctx: dict[str, Any], customer_id: int, after_message_id: int) -> str:
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    runtime: Runtime = ctx["runtime"]
    attempt: int = ctx.get("job_try", 1)
    senders = senders_for(runtime.whatsapp, runtime.storage_dir)
    async with factory() as session:
        phone = await session.scalar(select(Customer.wa_phone).where(Customer.id == customer_id))
    try:
        async with _customer_lock(ctx, phone), factory() as session:
            result = await deliver_queued(session, customer_id, after_message_id, senders)
            await session.commit()
    except WhatsAppError as exc:
        if attempt < MAX_TRIES:
            raise Retry(defer=2**attempt) from exc
        async with factory() as session:
            await fail_queued(session, customer_id, str(exc))
            await session.commit()
        log.error("replies.gave_up", customer_id=customer_id, error=str(exc)[:200])
        return "failed"
    log.info("replies.delivered", customer_id=customer_id, **result.__dict__)
    return "superseded" if result.superseded else f"sent {result.sent}, skipped {result.skipped}"


async def review_order_task(
    ctx: dict[str, Any], business_id: int, order_id: int, action: str, user_id: int
) -> str:
    """Staff approved or rejected a held order (enqueued by the dashboard API)."""
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    runtime: Runtime = ctx["runtime"]
    async with factory() as session:
        try:
            reply = await review_order(session, runtime, business_id, order_id, action, user_id)
        except ReviewError as exc:
            log.warning("review.rejected_request", order_id=order_id, error=str(exc))
            return f"error: {exc}"
        await session.flush()
        customer_id = reply.customer_id
        last_inbound = await session.scalar(
            select(func.max(Message.id)).where(
                Message.customer_id == customer_id, Message.direction == Direction.IN
            )
        )
        await session.commit()
    if customer_id is not None:
        await schedule_delivery(ctx, customer_id, last_inbound or 0, 0)
    return action
