"""Staff inbox: messages the agent handed over (complaints, questions it couldn't answer,
photos), with replies from the dashboard.

Replying or marking one message done also closes the customer's earlier open messages: staff
answer a conversation, not each line of it."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, QueueDep, SessionDep, client_ip, require_csrf
from app.models import Customer, Message, User
from app.models.enums import Channel, Direction, MessageStatus, ReplyKind
from app.services.auth import audit
from app.services.outbound import queue_reply

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(require_csrf)])

# WhatsApp accepts free-form replies only within 24 hours of the customer's last message;
# after that a pre-approved template is needed.
REPLY_WINDOW = timedelta(hours=24)
CONVERSATION_LIMIT = 40


class InboxCustomer(BaseModel):
    id: int
    name: str
    phone: str


class InboxItem(BaseModel):
    id: int
    customer: InboxCustomer
    channel: str
    type: str
    body: str
    attention: str
    created_at: datetime
    handled_at: datetime | None
    handled_by: str | None


class ConversationMessage(BaseModel):
    id: int
    direction: str
    body: str
    status: str
    kind: str | None
    created_at: datetime


class InboxDetail(InboxItem):
    conversation: list[ConversationMessage]
    reply_window_open: bool


class InboxPage(BaseModel):
    items: list[InboxItem]
    open: int


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


def _text(message: Message) -> str:
    """What the message says; for a photo or document, what it was."""
    if message.body or message.transcript:
        return message.body or message.transcript or ""
    kind = (message.raw or {}).get("type") or message.type
    return f"[{kind}]"


def _item(message: Message, customer: Customer, handler: User | None) -> InboxItem:
    return InboxItem(
        id=message.id,
        customer=InboxCustomer(id=customer.id, name=customer.name, phone=customer.wa_phone),
        channel=message.channel,
        type=message.type,
        body=_text(message),
        attention=message.attention or "",
        created_at=message.created_at,
        handled_at=message.handled_at,
        handled_by=(handler.name or handler.email) if handler else None,
    )


async def _load(session: AsyncSession, business_id: int, message_id: int) -> Message:
    message = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.business_id == business_id,
            Message.attention.is_not(None),
        )
    )
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    return message


async def _close_conversation(session: AsyncSession, message: Message, user_id: int | None) -> None:
    """Mark this message and the customer's earlier open ones as handled (None reopens it)."""
    if user_id is None:
        message.handled_at, message.handled_by_id = None, None
        return
    await session.execute(
        update(Message)
        .where(
            Message.business_id == message.business_id,
            Message.customer_id == message.customer_id,
            Message.attention.is_not(None),
            Message.handled_at.is_(None),
            Message.id <= message.id,
        )
        .values(handled_at=func.now(), handled_by_id=user_id)
    )


@router.get("", response_model=InboxPage)
async def list_inbox(
    user: CurrentUser,
    session: SessionDep,
    state: str = Query("open", pattern="^(open|done)$"),
    limit: int = Query(50, ge=1, le=200),
) -> InboxPage:
    waiting = Message.handled_at.is_(None) if state == "open" else Message.handled_at.is_not(None)
    rows = (
        await session.execute(
            select(Message, Customer, User)
            .join(Customer, Customer.id == Message.customer_id)
            .outerjoin(User, User.id == Message.handled_by_id)
            .where(
                Message.business_id == user.business_id,
                Message.attention.is_not(None),
                waiting,
            )
            # Open: complaints first, then the longest waiting. Done: newest first.
            .order_by(
                *(
                    (Message.attention != "complaint", Message.id)
                    if state == "open"
                    else (Message.handled_at.desc(), Message.id.desc())
                )
            )
            .limit(limit)
        )
    ).all()
    open_count = await session.scalar(
        select(func.count()).where(
            Message.business_id == user.business_id,
            Message.attention.is_not(None),
            Message.handled_at.is_(None),
        )
    )
    return InboxPage(items=[_item(*row) for row in rows], open=int(open_count or 0))


@router.get("/{message_id}", response_model=InboxDetail)
async def get_inbox_message(message_id: int, user: CurrentUser, session: SessionDep) -> InboxDetail:
    message = await _load(session, user.business_id, message_id)
    customer = await session.get_one(Customer, message.customer_id)
    handler = await session.get(User, message.handled_by_id) if message.handled_by_id else None
    history = (
        await session.execute(
            select(Message)
            .where(Message.customer_id == customer.id, Message.status != MessageStatus.SKIPPED)
            .order_by(Message.id.desc())
            .limit(CONVERSATION_LIMIT)
        )
    ).scalars()
    return InboxDetail(
        **_item(message, customer, handler).model_dump(),
        conversation=[
            ConversationMessage(
                id=m.id,
                direction=m.direction,
                body=_text(m),
                status=m.status,
                kind=(m.raw or {}).get("kind") if m.direction == Direction.OUT else m.attention,
                created_at=m.created_at,
            )
            for m in list(history)[::-1]
        ],
        reply_window_open=await _window_open(session, message),
    )


async def _window_open(session: AsyncSession, message: Message) -> bool:
    if message.channel != Channel.WHATSAPP:
        return True
    last_inbound = await session.scalar(
        select(func.max(Message.created_at)).where(
            Message.customer_id == message.customer_id, Message.direction == Direction.IN
        )
    )
    return last_inbound is not None and datetime.now(UTC) - last_inbound < REPLY_WINDOW


@router.post("/{message_id}/reply", status_code=status.HTTP_202_ACCEPTED)
async def reply(
    message_id: int,
    body: ReplyIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
) -> dict[str, str]:
    message = await _load(session, user.business_id, message_id)
    if message.customer_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "message has no customer")
    if not await _window_open(session, message):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "WhatsApp only allows a reply within 24 hours of the customer's last message. "
            "Call the customer instead.",
        )
    queue_reply(
        session,
        business_id=user.business_id,
        customer_id=message.customer_id,
        channel=message.channel,
        kind=ReplyKind.STAFF,
        body=body.body.strip(),
        extra={"user_id": user.id, "in_reply_to": message.id},
    )
    await _close_conversation(session, message, user.id)
    audit(
        session,
        "inbox_replied",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        message_id=message.id,
    )
    last_inbound = await session.scalar(
        select(func.max(Message.id)).where(
            Message.customer_id == message.customer_id, Message.direction == Direction.IN
        )
    )
    await session.commit()
    await queue.enqueue_delivery(message.customer_id, last_inbound or message.id)
    return {"status": "queued"}


@router.post("/{message_id}/{action}", response_model=InboxItem)
async def set_done(
    message_id: int,
    action: str,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> InboxItem:
    if action not in ("done", "reopen"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown action")
    message = await _load(session, user.business_id, message_id)
    await _close_conversation(session, message, user.id if action == "done" else None)
    audit(
        session,
        f"inbox_{action}",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        message_id=message.id,
    )
    await session.commit()
    await session.refresh(message)
    customer = await session.get_one(Customer, message.customer_id)
    handler = await session.get(User, message.handled_by_id) if message.handled_by_id else None
    return _item(message, customer, handler)
