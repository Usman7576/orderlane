"""Builders for realistic WhatsApp webhook payloads and signed requests."""

import hashlib
import hmac
import json
from typing import Any

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
PHONE_NUMBER_ID = "1100000000001"


def _envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": value.pop("_phone_number_id", PHONE_NUMBER_ID),
                            },
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def _contact(wa_id: str, name: str) -> dict[str, Any]:
    return {"profile": {"name": name}, "wa_id": wa_id}


def text_payload(
    body: str,
    *,
    message_id: str = "wamid.TEXT1",
    sender: str = "923001234567",
    name: str = "Test Shop",
    phone_number_id: str = PHONE_NUMBER_ID,
) -> dict[str, Any]:
    return _envelope(
        {
            "_phone_number_id": phone_number_id,
            "contacts": [_contact(sender, name)],
            "messages": [
                {
                    "from": sender,
                    "id": message_id,
                    "timestamp": "1761480000",
                    "type": "text",
                    "text": {"body": body},
                }
            ],
        }
    )


def audio_payload(
    media_id: str = "MEDIA1", *, message_id: str = "wamid.AUDIO1", sender: str = "923001234567"
) -> dict[str, Any]:
    return _envelope(
        {
            "contacts": [_contact(sender, "Voice Shop")],
            "messages": [
                {
                    "from": sender,
                    "id": message_id,
                    "timestamp": "1761480000",
                    "type": "audio",
                    "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True},
                }
            ],
        }
    )


def button_payload(
    button_id: str, title: str, *, message_id: str = "wamid.BTN1", sender: str = "923001234567"
) -> dict[str, Any]:
    return _envelope(
        {
            "contacts": [_contact(sender, "Button Shop")],
            "messages": [
                {
                    "from": sender,
                    "id": message_id,
                    "timestamp": "1761480000",
                    "type": "interactive",
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": button_id, "title": title},
                    },
                }
            ],
        }
    )


def status_payload(message_id: str, status: str) -> dict[str, Any]:
    return _envelope(
        {
            "statuses": [
                {
                    "id": message_id,
                    "status": status,
                    "timestamp": "1761480001",
                    "recipient_id": "923001234567",
                }
            ]
        }
    )


def signed(payload: dict[str, Any], secret: str = APP_SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}
