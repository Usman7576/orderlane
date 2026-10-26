import uuid
from typing import Protocol

from arq.connections import ArqRedis

PROCESS_MESSAGE = "process_inbound_message"
REVIEW_ORDER = "review_order_task"
DELIVER_REPLIES = "deliver_replies"


class JobQueue(Protocol):
    async def enqueue_message(self, message_id: int, job_id: int) -> None: ...
    async def retry_message(self, message_id: int, job_id: int) -> None: ...
    async def enqueue_review(
        self, business_id: int, order_id: int, action: str, user_id: int
    ) -> None: ...
    async def enqueue_delivery(self, customer_id: int, after_message_id: int) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue_message(self, message_id: int, job_id: int) -> None:
        # The arq job ID makes enqueueing idempotent too: the same message is never queued twice.
        await self._redis.enqueue_job(
            PROCESS_MESSAGE, message_id, job_id, _job_id=f"message:{message_id}"
        )

    async def retry_message(self, message_id: int, job_id: int) -> None:
        """A manual retry from the dashboard gets its own job ID: arq remembers finished
        job IDs for a while and would ignore the original one."""
        await self._redis.enqueue_job(
            PROCESS_MESSAGE,
            message_id,
            job_id,
            _job_id=f"message:{message_id}:retry:{uuid.uuid4().hex}",
        )

    async def enqueue_review(
        self, business_id: int, order_id: int, action: str, user_id: int
    ) -> None:
        await self._redis.enqueue_job(
            REVIEW_ORDER,
            business_id,
            order_id,
            action,
            user_id,
            # Unique per request; a duplicate click fails harmlessly in the worker
            # because the order is no longer waiting for review.
            _job_id=f"review:{order_id}:{uuid.uuid4().hex}",
        )

    async def enqueue_delivery(self, customer_id: int, after_message_id: int) -> None:
        """Send a staff reply now. Unique ID: arq drops a job ID it has seen before."""
        await self._redis.enqueue_job(
            DELIVER_REPLIES,
            customer_id,
            after_message_id,
            _job_id=f"deliver:{customer_id}:staff:{uuid.uuid4().hex}",
        )
