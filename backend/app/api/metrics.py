"""Metrics: offline evaluation results next to how the agent is doing on live traffic."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select

from app.config import get_settings
from app.deps import CurrentUser, SessionDep, require_csrf
from app.models import AgentRun, Message, Order, OrderEvent, OrderLine
from app.models.enums import OrderStatus

router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(require_csrf)])

CONFIRMED = (OrderStatus.CONFIRMED, OrderStatus.INVOICED)


class Evaluation(BaseModel):
    config: str
    timestamp: str
    git_sha: str | None
    summary: dict[str, Any]
    breakdown: dict[str, dict[str, Any]]


class LiveMetrics(BaseModel):
    days: int
    orders: int
    sent_to_review: int
    edited_by_staff: int
    confirmed_by_customer: int
    cancelled: int
    agent_runs: int
    agent_errors: int
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    llm_cost: Decimal
    routes: dict[str, int]
    match_methods: dict[str, int]


class Metrics(BaseModel):
    evaluations: list[Evaluation]
    live: LiveMetrics


def load_evaluations() -> list[Evaluation]:
    """The newest saved result of each evaluation config (files are named <date>-<config>.json,
    so a name sort is a date sort)."""
    folder = get_settings().eval_results_dir
    latest: dict[str, Evaluation] = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = data["meta"]
            latest[meta["config"]] = Evaluation(
                config=meta["config"],
                timestamp=meta["timestamp"],
                git_sha=meta.get("git_sha"),
                summary=data["summary"],
                breakdown=data["breakdown"],
            )
        except (OSError, ValueError, KeyError):
            continue  # a partial or older-format file shouldn't break the page
    return sorted(latest.values(), key=lambda e: e.config)


@router.get("", response_model=Metrics)
async def metrics(
    user: CurrentUser, session: SessionDep, days: int = Query(30, ge=1, le=365)
) -> Metrics:
    since = datetime.now(UTC) - timedelta(days=days)
    in_window = (Order.business_id == user.business_id, Order.created_at >= since)

    by_status = dict(
        (
            await session.execute(
                select(Order.status, func.count()).where(*in_window).group_by(Order.status)
            )
        ).all()
    )

    async def orders_with_event(*types: str) -> int:
        count = await session.scalar(
            select(func.count(distinct(OrderEvent.order_id)))
            .join(Order, Order.id == OrderEvent.order_id)
            .where(*in_window, OrderEvent.type.in_(types))
        )
        return int(count or 0)

    # An order went to review if staff decided on it, or it is still waiting for them.
    reviewed = await orders_with_event("approved", "rejected")
    pending = int(by_status.get(OrderStatus.PENDING_REVIEW, 0))

    runs_filter = (Message.business_id == user.business_id, AgentRun.created_at >= since)
    runs = (
        await session.execute(
            select(
                func.count(),
                func.count(AgentRun.error),
                func.percentile_cont(0.5).within_group(AgentRun.latency_ms),
                func.percentile_cont(0.95).within_group(AgentRun.latency_ms),
                func.coalesce(func.sum(AgentRun.cost_estimate), 0),
            )
            .select_from(AgentRun)
            .join(Message, Message.id == AgentRun.message_id)
            .where(*runs_filter)
        )
    ).one()
    routes = (
        await session.execute(
            select(AgentRun.route, func.count())
            .join(Message, Message.id == AgentRun.message_id)
            .where(*runs_filter, AgentRun.route.is_not(None))
            .group_by(AgentRun.route)
        )
    ).all()
    methods = (
        await session.execute(
            select(OrderLine.match_method, func.count())
            .join(Order, Order.id == OrderLine.order_id)
            .where(*in_window, OrderLine.match_method.is_not(None))
            .group_by(OrderLine.match_method)
        )
    ).all()

    return Metrics(
        evaluations=load_evaluations(),
        live=LiveMetrics(
            days=days,
            orders=sum(int(v) for v in by_status.values()),
            sent_to_review=reviewed + pending,
            edited_by_staff=await orders_with_event("lines_edited"),
            confirmed_by_customer=sum(int(by_status.get(s, 0)) for s in CONFIRMED),
            cancelled=int(by_status.get(OrderStatus.CANCELLED, 0)),
            agent_runs=int(runs[0] or 0),
            agent_errors=int(runs[1] or 0),
            latency_p50_ms=round(runs[2]) if runs[2] is not None else None,
            latency_p95_ms=round(runs[3]) if runs[3] is not None else None,
            llm_cost=Decimal(runs[4] or 0),
            routes={str(k): int(v) for k, v in routes},
            match_methods={str(k): int(v) for k, v in methods},
        ),
    )
