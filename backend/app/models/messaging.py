from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import JobStatus, MessageStatus


class Message(TimestampMixin, Base):
    """Every inbound and outbound message, on any channel."""

    __tablename__ = "messages"
    # WhatsApp may deliver the same webhook more than once; this makes saving idempotent.
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(16))
    sender: Mapped[str | None] = mapped_column(String(32))
    sender_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    media_ref: Mapped[str | None] = mapped_column(String(256))
    transcript: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=MessageStatus.RECEIVED)
    error: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Job(TimestampMixin, Base):
    """Tracks background work so failures are visible and retryable from the dashboard."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
