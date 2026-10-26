"""Operations views: failed background jobs (with retry) and the dashboard summary."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.deps import CurrentUser, QueueDep, SessionDep, client_ip, require_csrf
from app.models import AgentRun, Job, Message, Order
from app.models.enums import Direction, JobStatus, MessageStatus, OrderStatus
from app.services.auth import audit

router = APIRouter(prefix="/api", tags=["operations"], dependencies=[Depends(require_csrf)])


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    last_error: str | None
    message_id: int | None
    message_preview: str | None
    created_at: datetime
    updated_at: datetime


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    user: CurrentUser,
    session: SessionDep,
    status_: str = Query("failed", alias="status"),
    limit: int = Query(50, ge=1, le=200),
) -> list[JobOut]:
    rows = (
        await session.execute(
            select(Job, Message)
            .join(Message, Message.id == Job.message_id)
            .where(Message.business_id == user.business_id, Job.status == status_)
            .order_by(Job.updated_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        JobOut(
            id=job.id,
            kind=job.kind,
            status=job.status,
            attempts=job.attempts,
            last_error=job.last_error,
            message_id=job.message_id,
            message_preview=(msg.body or msg.transcript or f"[{msg.type}]")[:120],
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        for job, msg in rows
    ]


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_job(
    job_id: int, request: Request, user: CurrentUser, session: SessionDep, queue: QueueDep
) -> dict[str, str]:
    row = (
        await session.execute(
            select(Job, Message)
            .join(Message, Message.id == Job.message_id)
            .where(Job.id == job_id, Message.business_id == user.business_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    job, message = row
    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"job is {job.status}, only failed jobs retry"
        )
    await session.execute(
        update(Job).where(Job.id == job.id).values(status=JobStatus.QUEUED, attempts=0)
    )
    message.status, message.error = MessageStatus.RECEIVED, None
    audit(
        session,
        "job_retried",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        job_id=job.id,
    )
    await session.commit()
    await queue.retry_message(message.id, job.id)
    return {"status": "queued"}


class Summary(BaseModel):
    orders_by_status: dict[str, int]
    needs_review: int
    failed_jobs: int
    open_messages: int
    messages_today: int
    agent_runs_today: int
    avg_latency_ms: int | None
    llm_cost_today: Decimal


@router.get("/summary", response_model=Summary)
async def summary(user: CurrentUser, session: SessionDep) -> Summary:
    since = datetime.now(UTC) - timedelta(hours=24)
    by_status = dict(
        (
            await session.execute(
                select(Order.status, func.count())
                .where(Order.business_id == user.business_id)
                .group_by(Order.status)
            )
        ).all()
    )
    failed = await session.scalar(
        select(func.count())
        .select_from(Job)
        .join(Message, Message.id == Job.message_id)
        .where(Message.business_id == user.business_id, Job.status == JobStatus.FAILED)
    )
    open_messages = await session.scalar(
        select(func.count()).where(
            Message.business_id == user.business_id,
            Message.attention.is_not(None),
            Message.handled_at.is_(None),
        )
    )
    messages = await session.scalar(
        select(func.count()).where(
            Message.business_id == user.business_id,
            Message.direction == Direction.IN,
            Message.created_at >= since,
        )
    )
    runs = (
        await session.execute(
            select(
                func.count(),
                func.avg(AgentRun.latency_ms),
                func.coalesce(func.sum(AgentRun.cost_estimate), 0),
            )
            .select_from(AgentRun)
            .join(Message, Message.id == AgentRun.message_id)
            .where(Message.business_id == user.business_id, AgentRun.created_at >= since)
        )
    ).one()
    return Summary(
        orders_by_status={str(k): int(v) for k, v in by_status.items()},
        needs_review=int(by_status.get(OrderStatus.PENDING_REVIEW, 0)),
        failed_jobs=int(failed or 0),
        open_messages=int(open_messages or 0),
        messages_today=int(messages or 0),
        agent_runs_today=int(runs[0] or 0),
        avg_latency_ms=int(runs[1]) if runs[1] is not None else None,
        llm_cost_today=Decimal(runs[2] or 0),
    )
