"""Send customer messages through the real pipeline (queue, worker, agent, database)
without WhatsApp. Useful for demos and for testing the dashboard.

Usage (inside the api container):
    python -m scripts.replay --demo
    python -m scripts.replay --phone 923000000106 "2 ctn tapal 950" "1"

Messages use the "replay" channel: replies are saved but not sent anywhere.
"""

import argparse
import asyncio
import time
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Business, Message
from app.models.enums import Channel, MessageStatus, MessageType
from app.schemas.inbound import InboundMessage
from app.services.inbound import save_inbound
from app.services.queue import ArqJobQueue

# One conversation per seeded shop, chosen to show each path through the agent.
DEMO: list[tuple[str, list[str]]] = [
    ("923000000106", ["2 ctn tapal 950, 1 dz surf adha kilo"]),  # clear order: waits for customer
    ("923000000108", ["1 dozen ariel"]),  # new customer + unclear size: staff review
    ("923000000102", ["3 ctn dalda oil 1 litre"]),  # over credit limit: staff review
    ("923000000101", ["5 ctn olpers", "1"]),  # confirmed by typing 1: invoiced
    ("923000000104", ["ghee ka rate?"]),  # question: answered, no order
    ("923000000105", ["2 ctn pepsi 1.5", "order cancel kar dein"]),  # cancelled
    ("923000000103", ["kal wala oil ka tin leak tha"]),  # complaint: handed to staff
]


async def _wait_processed(factory, message_id: int, timeout_s: float = 30) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        async with factory() as session:
            status = await session.scalar(select(Message.status).where(Message.id == message_id))
        if status in (MessageStatus.PROCESSED, MessageStatus.FAILED):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"message {message_id} was not processed; is the worker running?")


async def send(conversations: list[tuple[str, list[str]]]) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    queue = ArqJobQueue(redis)
    async with factory() as session:
        business_id = await session.scalar(select(Business.id).order_by(Business.id).limit(1))
    if business_id is None:
        raise SystemExit("No business found. Run: python -m scripts.seed")

    for phone, messages in conversations:
        for text in messages:
            async with factory() as session:
                saved = await save_inbound(
                    session,
                    business_id,
                    InboundMessage(
                        channel=Channel.REPLAY,
                        external_id=f"replay-{uuid.uuid4()}",
                        sender=phone,
                        type=MessageType.TEXT,
                        body=text,
                    ),
                )
                await session.commit()
            assert saved is not None
            await queue.enqueue_message(saved.message_id, saved.job_id)
            # Wait, so follow-ups ("1", "cancel") see the order the first message created.
            await _wait_processed(factory, saved.message_id)
            print(f"{phone}: {text!r} processed")
    await redis.aclose()
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--demo", action="store_true", help="send the built-in demo conversations")
    parser.add_argument("--phone", help="customer phone (WhatsApp ID) for the messages")
    parser.add_argument("messages", nargs="*")
    args = parser.parse_args()
    if args.demo:
        conversations = DEMO
    elif args.phone and args.messages:
        conversations = [(args.phone, args.messages)]
    else:
        parser.error("use --demo, or --phone with one or more messages")
    asyncio.run(send(conversations))


if __name__ == "__main__":
    main()
