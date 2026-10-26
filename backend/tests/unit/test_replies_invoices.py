import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.integrations.whatsapp import WhatsAppClient
from app.models import Message
from app.models.enums import Direction, MessageStatus, MessageType, ReplyKind
from app.services.invoice_pdf import InvoiceData, InvoiceLine, render_invoice
from app.services.order_actions import CustomerAction, parse_action
from app.services.outbound import select_replies


def _out(kind: ReplyKind, n: int) -> Message:
    return Message(
        id=n,
        direction=Direction.OUT,
        type=MessageType.TEXT,
        status=MessageStatus.QUEUED,
        body=f"{kind}-{n}",
        raw={"kind": kind.value},
    )


def test_only_newest_order_reply_is_sent_but_answers_always_are() -> None:
    queued = [
        _out(ReplyKind.CONFIRMATION, 1),
        _out(ReplyKind.ANSWER, 2),
        _out(ReplyKind.REVIEW_HOLD, 3),
        _out(ReplyKind.CONFIRMATION, 4),
    ]
    send, skip = select_replies(queued)
    assert [m.id for m in send] == [2, 4]
    assert [m.id for m in skip] == [1, 3]


def _in(body: str | None = None, button: str | None = None) -> Message:
    if button is not None:
        return Message(
            type=MessageType.INTERACTIVE,
            raw={"interactive": {"type": "button_reply", "button_reply": {"id": button}}},
        )
    return Message(type=MessageType.TEXT, body=body)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (_in(button="confirm:17"), CustomerAction("confirm", 17)),
        (_in(button="edit:3"), CustomerAction("edit", 3)),
        (_in(button="cancel:3"), CustomerAction("cancel", 3)),
        (_in(button="confirm:abc"), None),
        (_in("1"), CustomerAction("confirm", None)),
        (_in("Theek hai"), CustomerAction("confirm", None)),
        (_in("2"), CustomerAction("edit", None)),
        (_in("5 ctn tapal"), None),
        (_in("ok bhai 2 ctn aur bhejo"), None),
    ],
)
def test_parse_customer_action(message: Message, expected: CustomerAction | None) -> None:
    assert parse_action(message) == expected


def test_invoice_pdf_renders_with_special_characters() -> None:
    pdf = render_invoice(
        InvoiceData(
            number="INV-1-1001",
            order_number=1001,
            issued_at=datetime(2026, 10, 26, tzinfo=UTC),
            business_name="Karachi Wholesale Traders",
            customer_name="City Cash & Carry <Main>",
            currency="PKR",
            lines=[
                InvoiceLine(
                    "Head & Shoulders 360ml", Decimal(1), "dozen", Decimal(13386), Decimal(13386)
                )
            ],
            total=Decimal(13386),
            previous_balance=Decimal(250000),
        )
    )
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


async def test_whatsapp_buttons_upload_and_document() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "MEDIA9"})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(seen)}"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WhatsAppClient(http, access_token="t", phone_number_id="PN", graph_version="v23.0")
        await client.send_buttons(
            "92300", "Order #1", [("confirm:1", "Confirm"), ("edit:1", "Edit")]
        )
        media_id = await client.upload_media(b"%PDF-1.4", "application/pdf", "INV-1.pdf")
        await client.send_document("92300", media_id, "INV-1.pdf", "Invoice INV-1")
        with pytest.raises(ValueError):
            await client.send_buttons("92300", "x", [("a", "A")] * 4)

    buttons = json.loads(seen[0].content)
    assert buttons["type"] == "interactive"
    assert [b["reply"]["id"] for b in buttons["interactive"]["action"]["buttons"]] == [
        "confirm:1",
        "edit:1",
    ]
    assert seen[1].url.path == "/v23.0/PN/media"
    assert b'name="file"; filename="INV-1.pdf"' in seen[1].content
    document = json.loads(seen[2].content)
    assert document["document"] == {
        "id": "MEDIA9",
        "filename": "INV-1.pdf",
        "caption": "Invoice INV-1",
    }
