"""WhatsApp Cloud API: webhook signature check, payload parsing and an HTTP client."""

import hashlib
import hmac
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Channel, MessageType
from app.schemas.inbound import InboundMessage, StatusUpdate

GRAPH_BASE_URL = "https://graph.facebook.com"


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header: "sha256=" + HMAC-SHA256(app secret, raw body)."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


# --- Webhook payload (only the fields we use; everything else is ignored) ---


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Text(_Model):
    body: str


class _Media(_Model):
    id: str
    mime_type: str | None = None


class _ButtonReply(_Model):
    id: str
    title: str


class _Interactive(_Model):
    type: str
    button_reply: _ButtonReply | None = None


class _WaMessage(_Model):
    id: str
    from_: str = Field(alias="from")
    timestamp: str
    type: str
    text: _Text | None = None
    audio: _Media | None = None
    interactive: _Interactive | None = None


class _Profile(_Model):
    name: str | None = None


class _Contact(_Model):
    wa_id: str
    profile: _Profile | None = None


class _StatusError(_Model):
    code: int | None = None
    title: str | None = None


class _Status(_Model):
    id: str
    status: str
    errors: list[_StatusError] = []


class _Metadata(_Model):
    phone_number_id: str


class _Value(_Model):
    metadata: _Metadata
    contacts: list[_Contact] = []
    messages: list[_WaMessage] = []
    statuses: list[_Status] = []


class _Change(_Model):
    field: str
    value: _Value


class _Entry(_Model):
    changes: list[_Change] = []


class WebhookPayload(_Model):
    object: str
    entry: list[_Entry] = []


class ParsedBatch(BaseModel):
    """Everything in one webhook delivery for one business phone number."""

    phone_number_id: str
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []


def _to_inbound(msg: _WaMessage, names: dict[str, str | None]) -> InboundMessage:
    common: dict[str, Any] = {
        "channel": Channel.WHATSAPP,
        "external_id": msg.id,
        "sender": msg.from_,
        "sender_name": names.get(msg.from_),
        "raw": msg.model_dump(by_alias=True, exclude_none=True),
    }
    if msg.type == "text" and msg.text:
        return InboundMessage(type=MessageType.TEXT, body=msg.text.body, **common)
    if msg.type == "audio" and msg.audio:
        return InboundMessage(type=MessageType.AUDIO, media_ref=msg.audio.id, **common)
    if msg.type == "interactive" and msg.interactive and msg.interactive.button_reply:
        reply = msg.interactive.button_reply
        return InboundMessage(
            type=MessageType.INTERACTIVE, body=reply.title, interactive_id=reply.id, **common
        )
    return InboundMessage(type=MessageType.UNSUPPORTED, **common)


def parse_webhook(payload: dict[str, Any]) -> list[ParsedBatch]:
    data = WebhookPayload.model_validate(payload)
    batches: list[ParsedBatch] = []
    for entry in data.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            value = change.value
            names = {c.wa_id: c.profile.name if c.profile else None for c in value.contacts}
            batches.append(
                ParsedBatch(
                    phone_number_id=value.metadata.phone_number_id,
                    messages=[_to_inbound(m, names) for m in value.messages],
                    statuses=[
                        StatusUpdate(
                            external_id=s.id,
                            status=s.status,
                            error=s.errors[0].title if s.errors else None,
                        )
                        for s in value.statuses
                    ],
                )
            )
    return batches


# --- Outbound client ---


class WhatsAppError(Exception):
    """A failed call to the WhatsApp API. `retryable` is True for rate limits and server errors."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class WhatsAppClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        access_token: str,
        phone_number_id: str,
        graph_version: str,
        base_url: str = GRAPH_BASE_URL,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._phone_number_id = phone_number_id
        self._api = f"{base_url}/{graph_version}"

    async def _post_message(self, payload: dict[str, Any]) -> str:
        """Send a message; returns WhatsApp's message ID."""
        url = f"{self._api}/{self._phone_number_id}/messages"
        body = {"messaging_product": "whatsapp", **payload}
        data = await self._request("POST", url, json=body)
        messages = data.get("messages") or [{}]
        return str(messages[0].get("id", ""))

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, url, headers=self._headers, **kwargs)
        except httpx.TransportError as exc:
            raise WhatsAppError(f"network error: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise WhatsAppError(
                f"WhatsApp API {response.status_code}: {response.text[:300]}",
                retryable=retryable,
            )
        result: dict[str, Any] = response.json()
        return result

    async def send_text(self, to: str, text: str) -> str:
        return await self._post_message(
            {"to": to, "type": "text", "text": {"body": text, "preview_url": False}}
        )

    async def mark_as_read(self, message_id: str) -> None:
        await self._post_message({"status": "read", "message_id": message_id})

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Media is fetched in two steps: look up a short-lived URL, then download it."""
        info = await self._request("GET", f"{self._api}/{media_id}")
        try:
            response = await self._http.get(str(info["url"]), headers=self._headers)
        except httpx.TransportError as exc:
            raise WhatsAppError(f"network error: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise WhatsAppError(
                f"media download {response.status_code}",
                retryable=response.status_code >= 500,
            )
        return response.content, str(info.get("mime_type", "application/octet-stream"))
