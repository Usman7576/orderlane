"""Web chat simulator: chat as a shop from the dashboard. Messages go through exactly the same
pipeline as WhatsApp (queue, worker, agent); only the channel differs, so replies are stored
instead of being sent. Useful for demos, where WhatsApp's test number can't reach everyone."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CurrentUser, QueueDep, SessionDep, require_csrf
from app.models import Customer, Message
from app.models.enums import Channel, Direction, MessageStatus, MessageType
from app.schemas.inbound import InboundMessage
from app.services.inbound import save_inbound

router = APIRouter(
    prefix="/api/simulator", tags=["simulator"], dependencies=[Depends(require_csrf)]
)

PHONE = r"^\d{8,15}$"
CONVERSATION_LIMIT = 60


class SimMessageIn(BaseModel):
    phone: str = Field(pattern=PHONE, description="The shop's WhatsApp ID (digits only)")
    name: str | None = Field(None, max_length=120)
    text: str | None = Field(None, min_length=1, max_length=1000)
    button_id: str | None = Field(None, pattern=r"^(confirm|edit|cancel):\d+$")
    button_title: str | None = Field(None, max_length=20)


class SimQueued(BaseModel):
    message_id: int


class SimMessage(BaseModel):
    id: int
    direction: str
    type: str
    body: str | None
    status: str
    kind: str | None
    order_id: int | None
    created_at: datetime


@router.post("/messages", response_model=SimQueued, status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    body: SimMessageIn, user: CurrentUser, session: SessionDep, queue: QueueDep
) -> SimQueued:
    if (body.text is None) == (body.button_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "send either text or a button")
    if body.button_id is not None:
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            external_id=f"sim-{uuid.uuid4()}",
            sender=body.phone,
            sender_name=body.name,
            type=MessageType.INTERACTIVE,
            body=body.button_title or body.button_id.split(":")[0].title(),
            interactive_id=body.button_id,
            # Same shape WhatsApp sends, so the worker handles it the same way.
            raw={"interactive": {"type": "button_reply", "button_reply": {"id": body.button_id}}},
        )
    else:
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            external_id=f"sim-{uuid.uuid4()}",
            sender=body.phone,
            sender_name=body.name,
            type=MessageType.TEXT,
            body=body.text,
        )
    saved = await save_inbound(session, user.business_id, inbound)
    await session.commit()
    assert saved is not None  # external IDs are random, so never a duplicate
    await queue.enqueue_message(saved.message_id, saved.job_id)
    return SimQueued(message_id=saved.message_id)


@router.get("/conversation", response_model=list[SimMessage])
async def conversation(
    user: CurrentUser,
    session: SessionDep,
    phone: str = Query(pattern=PHONE),
) -> list[SimMessage]:
    """The shop's messages on every channel, oldest first. Replies replaced by a newer one
    before sending are left out, just as the customer would never see them."""
    customer = await session.scalar(
        select(Customer).where(Customer.business_id == user.business_id, Customer.wa_phone == phone)
    )
    sender_filter = (
        Message.customer_id == customer.id
        if customer
        else (Message.sender == phone) & (Message.business_id == user.business_id)
    )
    rows = (
        await session.execute(
            select(Message)
            .where(sender_filter, Message.status != MessageStatus.SKIPPED)
            .order_by(Message.id.desc())
            .limit(CONVERSATION_LIMIT)
        )
    ).scalars()
    return [
        SimMessage(
            id=m.id,
            direction=m.direction,
            type=m.type,
            body=m.body or m.transcript,
            status=m.status,
            kind=(m.raw or {}).get("kind") if m.direction == Direction.OUT else None,
            order_id=(m.raw or {}).get("order_id") if m.direction == Direction.OUT else None,
            created_at=m.created_at,
        )
        for m in list(rows)[::-1]
    ]
