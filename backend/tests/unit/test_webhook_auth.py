"""Webhook checks that happen before any database work."""

import httpx

from tests.conftest import FakeQueue
from tests.factories import VERIFY_TOKEN, signed, text_payload


async def test_verify_handshake_returns_challenge(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"},
    )
    assert response.status_code == 200
    assert response.text == "42"


async def test_verify_handshake_rejects_wrong_token(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )
    assert response.status_code == 403


async def test_post_without_signature_is_rejected(
    client: httpx.AsyncClient, queue: FakeQueue
) -> None:
    response = await client.post("/webhooks/whatsapp", json=text_payload("hi"))
    assert response.status_code == 401
    assert queue.calls == []


async def test_post_with_forged_signature_is_rejected(
    client: httpx.AsyncClient, queue: FakeQueue
) -> None:
    body, headers = signed(text_payload("hi"), secret="attacker-secret")
    response = await client.post("/webhooks/whatsapp", content=body, headers=headers)
    assert response.status_code == 401
    assert queue.calls == []


async def test_signed_but_malformed_payload_is_400(client: httpx.AsyncClient) -> None:
    body, headers = signed({"object": "whatsapp_business_account", "entry": "not-a-list"})
    response = await client.post("/webhooks/whatsapp", content=body, headers=headers)
    assert response.status_code == 400


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.json() == {"status": "ok"}
