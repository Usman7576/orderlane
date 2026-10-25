from typing import Protocol

from arq.connections import ArqRedis

PROCESS_MESSAGE = "process_inbound_message"


class JobQueue(Protocol):
    async def enqueue_message(self, message_id: int, job_id: int) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue_message(self, message_id: int, job_id: int) -> None:
        # The arq job ID makes enqueueing idempotent too: the same message is never queued twice.
        await self._redis.enqueue_job(
            PROCESS_MESSAGE, message_id, job_id, _job_id=f"message:{message_id}"
        )
