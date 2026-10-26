"""Queuing and sending replies.

Replies are saved as queued outbound messages first, then sent by a delivery job a few
seconds later. If the customer sends another message in the meantime, that message's job
takes over, and replies about the order's state are reduced to the newest one, so a
burst of three messages gets one confirmation, not three."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.models import Customer, Message, Order
from app.models.enums import (
    ORDER_STATE_REPLIES,
    Channel,
    Direction,
    MessageStatus,
    MessageType,
    OrderStatus,
    ReplyKind,
)

log = structlog.get_logger()


def queue_reply(
    session: AsyncSession,
    *,
    business_id: int,
    customer_id: int,
    channel: str,
    kind: ReplyKind,
    body: str,
    order_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        business_id=business_id,
        customer_id=customer_id,
        channel=channel,
        direction=Direction.OUT,
        type=MessageType.DOCUMENT if kind is ReplyKind.INVOICE else MessageType.TEXT,
        body=body,
        status=MessageStatus.QUEUED,
        raw={"kind": kind.value, "order_id": order_id, **(extra or {})},
    )
    session.add(message)
    return message


def kind_of(message: Message) -> ReplyKind:
    return ReplyKind((message.raw or {}).get("kind", ReplyKind.INFO))


def select_replies(queued: list[Message]) -> tuple[list[Message], list[Message]]:
    """Split queued replies (oldest first) into (send, skip): of the replies about the
    order's state, only the newest is sent; answers and invoices are always sent."""
    state_replies = [m for m in queued if kind_of(m) in ORDER_STATE_REPLIES]
    newest_state = state_replies[-1] if state_replies else None
    send = [m for m in queued if kind_of(m) not in ORDER_STATE_REPLIES or m is newest_state]
    skip = [m for m in queued if m not in send]
    return send, skip


class Sender(Protocol):
    async def send(self, message: Message, to: str) -> str | None: ...


@dataclass
class WhatsAppSender:
    client: WhatsAppClient
    storage_dir: Path

    async def send(self, message: Message, to: str) -> str | None:
        kind = kind_of(message)
        raw = message.raw or {}
        body = message.body or ""
        if kind is ReplyKind.CONFIRMATION and raw.get("order_id"):
            order_id = raw["order_id"]
            return await self.client.send_buttons(
                to,
                body,
                [
                    (f"confirm:{order_id}", "Confirm"),
                    (f"edit:{order_id}", "Edit"),
                    (f"cancel:{order_id}", "Cancel"),
                ],
            )
        if kind is ReplyKind.INVOICE:
            path = self.storage_dir / raw["path"]
            media_id = await self.client.upload_media(
                path.read_bytes(), "application/pdf", raw["filename"]
            )
            return await self.client.send_document(to, media_id, raw["filename"], body)
        return await self.client.send_text(to, body)


class WebchatSender:
    """The web chat simulator reads replies from the database; nothing to send."""

    async def send(self, message: Message, to: str) -> str | None:
        return None


@dataclass
class DeliveryResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    superseded: bool = False


async def _still_valid(session: AsyncSession, message: Message) -> bool:
    """A confirmation is only worth sending while its order still waits for the customer."""
    if kind_of(message) is not ReplyKind.CONFIRMATION:
        return True
    order_id = (message.raw or {}).get("order_id")
    order = await session.get(Order, order_id) if order_id else None
    return order is not None and order.status == OrderStatus.AWAITING_CUSTOMER


async def deliver_replies(
    session: AsyncSession,
    customer_id: int,
    after_message_id: int,
    senders: dict[str, Sender],
) -> DeliveryResult:
    """Send the customer's queued replies, unless a newer inbound message will do it."""
    newer = await session.scalar(
        select(
            exists().where(
                Message.customer_id == customer_id,
                Message.direction == Direction.IN,
                Message.id > after_message_id,
            )
        )
    )
    if newer:
        return DeliveryResult(superseded=True)

    customer = await session.get_one(Customer, customer_id)
    queued = list(
        (
            await session.execute(
                select(Message)
                .where(
                    Message.customer_id == customer_id,
                    Message.direction == Direction.OUT,
                    Message.status == MessageStatus.QUEUED,
                )
                .order_by(Message.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    send, skip = select_replies(queued)
    result = DeliveryResult()
    for message in skip:
        message.status = MessageStatus.SKIPPED
        result.skipped += 1
    for message in send:
        if not await _still_valid(session, message):
            message.status = MessageStatus.SKIPPED
            result.skipped += 1
            continue
        sender = senders.get(message.channel)
        if sender is None:
            message.status, message.error = MessageStatus.FAILED, f"no sender for {message.channel}"
            result.failed += 1
            continue
        try:
            message.external_id = await sender.send(message, customer.wa_phone)
        except WhatsAppError as exc:
            if exc.retryable:
                await session.commit()  # keep what was already sent; retry the rest
                raise
            message.status, message.error = MessageStatus.FAILED, str(exc)[:1000]
            result.failed += 1
            log.warning("reply.failed", message_id=message.id, error=str(exc)[:200])
            continue
        message.status = MessageStatus.SENT
        result.sent += 1
    return result


async def fail_queued(session: AsyncSession, customer_id: int, error: str) -> None:
    """After the last retry: mark the customer's unsent replies as failed (visible to staff)."""
    queued = (
        await session.execute(
            select(Message).where(
                Message.customer_id == customer_id,
                Message.direction == Direction.OUT,
                Message.status == MessageStatus.QUEUED,
            )
        )
    ).scalars()
    for message in queued:
        message.status, message.error = MessageStatus.FAILED, error[:1000]


def senders_for(whatsapp: WhatsAppClient | None, storage_dir: Path) -> dict[str, Sender]:
    senders: dict[str, Sender] = {Channel.WEBCHAT: WebchatSender(), Channel.REPLAY: WebchatSender()}
    if whatsapp is not None:
        senders[Channel.WHATSAPP] = WhatsAppSender(whatsapp, storage_dir)
    return senders
