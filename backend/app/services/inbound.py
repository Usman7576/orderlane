"""Saving inbound messages and delivery statuses. Shared by every channel."""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Customer, Job, Message
from app.models.enums import Direction, JobStatus, MessageStatus
from app.schemas.inbound import InboundMessage, StatusUpdate
from app.services.queue import PROCESS_MESSAGE

# Order of WhatsApp delivery statuses; a late "sent" must not overwrite "read".
_STATUS_RANK = {
    MessageStatus.QUEUED: 0,
    MessageStatus.SENT: 1,
    MessageStatus.DELIVERED: 2,
    MessageStatus.READ: 3,
    MessageStatus.FAILED: 4,
}


@dataclass(frozen=True)
class SavedMessage:
    message_id: int
    job_id: int


async def find_business_by_phone_number_id(
    session: AsyncSession, phone_number_id: str
) -> Business | None:
    result = await session.execute(
        select(Business).where(Business.wa_phone_number_id == phone_number_id)
    )
    return result.scalar_one_or_none()


async def save_inbound(
    session: AsyncSession, business_id: int, msg: InboundMessage
) -> SavedMessage | None:
    """Insert the message and a job row. Returns None if this message was already saved."""
    stmt = (
        insert(Message)
        .values(
            business_id=business_id,
            channel=msg.channel,
            external_id=msg.external_id,
            direction=Direction.IN,
            type=msg.type,
            sender=msg.sender,
            sender_name=msg.sender_name,
            body=msg.body,
            media_ref=msg.media_ref,
            status=MessageStatus.RECEIVED,
            raw=msg.raw,
        )
        .on_conflict_do_nothing(index_elements=["channel", "external_id"])
        .returning(Message.id)
    )
    message_id = (await session.execute(stmt)).scalar_one_or_none()
    if message_id is None:
        return None
    job = Job(message_id=message_id, kind=PROCESS_MESSAGE, status=JobStatus.QUEUED)
    session.add(job)
    await session.flush()
    return SavedMessage(message_id=message_id, job_id=job.id)


async def apply_status(session: AsyncSession, update_: StatusUpdate) -> None:
    try:
        new_status = MessageStatus(update_.status)
    except ValueError:
        return
    message = (
        await session.execute(
            select(Message).where(
                Message.external_id == update_.external_id,
                Message.direction == Direction.OUT,
            )
        )
    ).scalar_one_or_none()
    if message is None:
        return
    current = _STATUS_RANK.get(MessageStatus(message.status), 0)
    if _STATUS_RANK.get(new_status, 0) >= current:
        message.status = new_status
        if update_.error:
            message.error = update_.error


async def get_or_create_customer(
    session: AsyncSession, business_id: int, wa_phone: str, name: str | None
) -> Customer:
    await session.execute(
        insert(Customer)
        .values(
            business_id=business_id,
            wa_phone=wa_phone,
            name=name or f"Customer {wa_phone[-4:]}",
            price_tier="A",
            credit_limit=0,
            is_new=True,
        )
        .on_conflict_do_nothing(index_elements=["business_id", "wa_phone"])
    )
    result = await session.execute(
        select(Customer).where(Customer.business_id == business_id, Customer.wa_phone == wa_phone)
    )
    return result.scalar_one()


async def mark_job(
    session: AsyncSession, job_id: int, status: JobStatus, error: str | None = None
) -> None:
    values: dict[str, object] = {"status": status, "last_error": error}
    if status == JobStatus.RUNNING:
        values["attempts"] = Job.attempts + 1
    await session.execute(update(Job).where(Job.id == job_id).values(**values))
