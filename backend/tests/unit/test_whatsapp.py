import hashlib
import hmac

import httpx
import pytest

from app.integrations.whatsapp import (
    WhatsAppClient,
    WhatsAppError,
    parse_webhook,
    verify_signature,
)
from app.models.enums import MessageType
from tests.factories import (
    APP_SECRET,
    audio_payload,
    button_payload,
    status_payload,
    text_payload,
)


def _sig(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_accepts_valid_signature(self) -> None:
        body = b'{"hello": "world"}'
        assert verify_signature(APP_SECRET, body, _sig(body))

    def test_rejects_tampered_body(self) -> None:
        assert not verify_signature(APP_SECRET, b'{"total": 9}', _sig(b'{"total": 1}'))

    def test_rejects_wrong_secret(self) -> None:
        body = b"{}"
        assert not verify_signature(APP_SECRET, body, _sig(body, "other-secret"))

    @pytest.mark.parametrize("header", [None, "", "sha1=abc", "abc"])
    def test_rejects_missing_or_malformed_header(self, header: str | None) -> None:
        assert not verify_signature(APP_SECRET, b"{}", header)

    def test_rejects_when_secret_not_configured(self) -> None:
        body = b"{}"
        assert not verify_signature("", body, _sig(body, ""))


class TestParseWebhook:
    def test_text_message(self) -> None:
        [batch] = parse_webhook(text_payload("5 ctn tapal 950", sender="923001112222", name="Ali"))
        [msg] = batch.messages
        assert msg.type == MessageType.TEXT
        assert msg.body == "5 ctn tapal 950"
        assert msg.sender == "923001112222"
        assert msg.sender_name == "Ali"
        assert msg.external_id == "wamid.TEXT1"

    def test_audio_message_keeps_media_id(self) -> None:
        [batch] = parse_webhook(audio_payload("MEDIA-42"))
        [msg] = batch.messages
        assert msg.type == MessageType.AUDIO
        assert msg.media_ref == "MEDIA-42"
        assert msg.body is None

    def test_button_reply(self) -> None:
        [batch] = parse_webhook(button_payload("confirm:17", "Confirm"))
        [msg] = batch.messages
        assert msg.type == MessageType.INTERACTIVE
        assert msg.interactive_id == "confirm:17"
        assert msg.body == "Confirm"

    def test_unknown_type_is_kept_as_unsupported(self) -> None:
        payload = text_payload("x")
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message.pop("text")
        message["type"] = "sticker"
        message["sticker"] = {"id": "S1"}
        [batch] = parse_webhook(payload)
        assert batch.messages[0].type == MessageType.UNSUPPORTED

    def test_status_update(self) -> None:
        [batch] = parse_webhook(status_payload("wamid.OUT1", "delivered"))
        assert batch.messages == []
        [status] = batch.statuses
        assert (status.external_id, status.status) == ("wamid.OUT1", "delivered")

    def test_ignores_other_fields(self) -> None:
        payload = text_payload("hi")
        payload["entry"][0]["changes"][0]["field"] = "account_update"
        assert parse_webhook(payload) == []


class TestClient:
    async def test_send_text_returns_message_id(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"messages": [{"id": "wamid.OUT9"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WhatsAppClient(
                http, access_token="tok", phone_number_id="PN1", graph_version="v23.0"
            )
            assert await client.send_text("923001112222", "hello") == "wamid.OUT9"

        [request] = seen
        assert request.url.path == "/v23.0/PN1/messages"
        assert request.headers["Authorization"] == "Bearer tok"

    @pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (400, False)])
    async def test_errors_are_classified(self, status: int, retryable: bool) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(status, json={}))
        async with httpx.AsyncClient(transport=transport) as http:
            client = WhatsAppClient(http, access_token="t", phone_number_id="P", graph_version="v1")
            with pytest.raises(WhatsAppError) as info:
                await client.send_text("1", "x")
        assert info.value.retryable is retryable

    async def test_download_media_two_steps(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/MEDIA1"):
                return httpx.Response(
                    200, json={"url": "https://cdn.example/f", "mime_type": "audio/ogg"}
                )
            return httpx.Response(200, content=b"OggS-data")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WhatsAppClient(http, access_token="t", phone_number_id="P", graph_version="v1")
            data, mime = await client.download_media("MEDIA1")
        assert (data, mime) == (b"OggS-data", "audio/ogg")
