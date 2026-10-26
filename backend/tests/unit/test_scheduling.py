"""Delivery jobs must get IDs arq won't silently drop."""

from typing import Any

from app.workers.tasks import schedule_delivery


class RecordingRedis:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def enqueue_job(self, name: str, *args: Any, _job_id: str, _defer_by: float) -> None:
        self.job_ids.append(_job_id)


async def test_message_deliveries_share_one_id_per_inbound_message() -> None:
    redis = RecordingRedis()
    await schedule_delivery({"redis": redis}, 7, 42, 10)
    await schedule_delivery({"redis": redis}, 7, 42, 10)  # a retry of the same message
    assert redis.job_ids == ["deliver:7:42", "deliver:7:42"]


async def test_review_delivery_never_reuses_a_message_delivery_id() -> None:
    """Regression: after a staff approval the reply sat in "queued" forever, because its
    delivery job reused the ID of the customer's earlier message delivery."""
    redis = RecordingRedis()
    await schedule_delivery({"redis": redis}, 7, 42, 10)
    await schedule_delivery({"redis": redis}, 7, 42, 0, reason="review")
    await schedule_delivery({"redis": redis}, 7, 42, 0, reason="review")
    assert len(set(redis.job_ids)) == 3
