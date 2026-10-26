"""Allowed values for status-like columns. Stored as plain strings in the database."""

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    STAFF = "staff"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    WEBCHAT = "webchat"
    REPLAY = "replay"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    INTERACTIVE = "interactive"
    DOCUMENT = "document"
    UNSUPPORTED = "unsupported"


class MessageStatus(StrEnum):
    RECEIVED = "received"  # inbound, saved, waiting for the worker
    PROCESSED = "processed"  # inbound, handled by the worker
    FAILED = "failed"  # inbound or outbound, gave up
    QUEUED = "queued"  # outbound, not yet accepted by WhatsApp
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    SKIPPED = "skipped"  # outbound, replaced by a newer reply before it was sent


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    AWAITING_CUSTOMER = "awaiting_customer"
    CONFIRMED = "confirmed"
    INVOICED = "invoiced"
    CANCELLED = "cancelled"


class MatchMethod(StrEnum):
    ALIAS = "alias"
    FUZZY = "fuzzy"
    LLM = "llm"
    STAFF = "staff"


class Actor(StrEnum):
    AGENT = "agent"
    STAFF = "staff"
    CUSTOMER = "customer"
    SYSTEM = "system"


class AliasSource(StrEnum):
    SEED = "seed"
    STAFF = "staff"
    LEARNED = "learned"


class LedgerType(StrEnum):
    INVOICE = "invoice"
    PAYMENT = "payment"
    ADJUSTMENT = "adjustment"


class ReplyKind(StrEnum):
    """What an outbound message is, so a burst of replies can be reduced to the latest."""

    CONFIRMATION = "confirmation"  # order summary with Confirm / Edit / Cancel buttons
    REVIEW_HOLD = "review_hold"
    EDIT_PROMPT = "edit_prompt"
    CANCELLED = "cancelled"
    ANSWER = "answer"
    HANDOFF = "handoff"
    ORDER_CONFIRMED = "order_confirmed"
    INVOICE = "invoice"
    INFO = "info"


# Replies about the state of the open order: only the newest one is worth sending.
ORDER_STATE_REPLIES = {
    ReplyKind.CONFIRMATION,
    ReplyKind.REVIEW_HOLD,
    ReplyKind.EDIT_PROMPT,
    ReplyKind.CANCELLED,
}
