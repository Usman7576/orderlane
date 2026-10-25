"""WhatsApp webhook. It only checks, saves and queues; all slow work happens in the worker,
so Meta always gets a fast 200 and does not retry the delivery."""

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.deps import QueueDep, SessionDep, SettingsDep
from app.integrations.whatsapp import parse_webhook, verify_signature
from app.services.inbound import (
    SavedMessage,
    apply_status,
    find_business_by_phone_number_id,
    save_inbound,
)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])
log = structlog.get_logger()


@router.get("", response_class=PlainTextResponse)
async def verify_webhook(
    settings: SettingsDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta calls this once when you register the webhook URL."""
    expected = settings.wa_verify_token.get_secret_value()
    if mode == "subscribe" and expected and token == expected and challenge is not None:
        return challenge
    raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")


@router.post("")
async def receive_webhook(
    request: Request, settings: SettingsDep, session: SessionDep, queue: QueueDep
) -> dict[str, str]:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(settings.wa_app_secret.get_secret_value(), body, signature):
        log.warning("webhook.bad_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")

    try:
        batches = parse_webhook(json.loads(body))
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("webhook.bad_payload", error=str(exc)[:200])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid payload") from exc

    saved: list[SavedMessage] = []
    for batch in batches:
        business = await find_business_by_phone_number_id(session, batch.phone_number_id)
        if business is None:
            log.warning("webhook.unknown_phone_number", phone_number_id=batch.phone_number_id)
            continue
        for message in batch.messages:
            result = await save_inbound(session, business.id, message)
            if result is None:
                log.info("webhook.duplicate", external_id=message.external_id)
            else:
                saved.append(result)
        for status_update in batch.statuses:
            await apply_status(session, status_update)
    await session.commit()

    # Queue only after commit, so the worker never looks for a row that isn't there yet.
    # If queueing fails, the job row stays "queued" and can be retried from the dashboard.
    for item in saved:
        try:
            await queue.enqueue_message(item.message_id, item.job_id)
        except Exception:
            log.exception("webhook.enqueue_failed", message_id=item.message_id)
    return {"status": "ok"}
