import time
from typing import Protocol

from arq.connections import ArqRedis

PROCESS_MESSAGE = "process_inbound_message"
REVIEW_ORDER = "review_order_task"


class JobQueue(Protocol):
    async def enqueue_message(self, message_id: int, job_id: int) -> None: ...
    async def retry_message(self, message_id: int, job_id: int) -> None: ...
    async def enqueue_review(
        self, business_id: int, order_id: int, action: str, user_id: int
    ) -> None: ...


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
            _job_id=f"message:{message_id}:retry:{time.time_ns()}",
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
            _job_id=f"review:{order_id}:{time.time_ns()}",
        )
