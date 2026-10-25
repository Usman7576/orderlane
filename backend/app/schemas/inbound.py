from typing import Any

from pydantic import BaseModel

from app.models.enums import Channel, MessageType


class InboundMessage(BaseModel):
    """A customer message from any channel, in one shape the pipeline understands."""

    channel: Channel
    external_id: str
    sender: str
    sender_name: str | None = None
    type: MessageType
    body: str | None = None
    # WhatsApp media ID for audio; a local file path for uploads from the web chat.
    media_ref: str | None = None
    # ID of the button the customer tapped, for interactive replies.
    interactive_id: str | None = None
    raw: dict[str, Any] | None = None


class StatusUpdate(BaseModel):
    """Delivery status for a message we sent (sent, delivered, read, failed)."""

    external_id: str
    status: str
    error: str | None = None
