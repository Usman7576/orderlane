"""Processing one inbound message: customer, voice transcription, agent run, tracing, reply.

The worker task calls `process_message`; everything it needs arrives in `Runtime`, so tests
can pass fakes for WhatsApp, speech and the LLM."""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentGraph, AgentOutcome, run_message
from app.agent.prompts import PROMPT_VERSION
from app.agent.state import AgentDeps, AgentSettings
from app.agent.tools import DbOrderTools
from app.integrations.llm import LLM, Usage
from app.integrations.speech import SpeechToText
from app.integrations.whatsapp import WhatsAppClient
from app.matching.catalog import CatalogIndex
from app.matching.matcher import Matcher
from app.models import AgentRun, AgentStep, Message
from app.models.enums import Direction, MessageStatus, MessageType
from app.services.inbound import get_or_create_customer

log = structlog.get_logger()

CATALOG_TTL_SECONDS = 60


@dataclass
class CatalogCache:
    """Catalog indexes per business, rebuilt at most once a minute."""

    entries: dict[int, tuple[float, CatalogIndex]] = field(default_factory=dict)

    async def get(self, session: AsyncSession, business_id: int) -> CatalogIndex:
        cached = self.entries.get(business_id)
        if cached and time.monotonic() - cached[0] < CATALOG_TTL_SECONDS:
            return cached[1]
        index = await CatalogIndex.from_db(session, business_id)
        self.entries[business_id] = (time.monotonic(), index)
        return index


@dataclass
class Runtime:
    graph: AgentGraph
    catalog: CatalogCache
    llm: LLM | None
    speech: SpeechToText | None
    whatsapp: WhatsAppClient | None
    storage_dir: Path
    settings: AgentSettings
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0


@dataclass
class ProcessResult:
    message_id: int
    agent_run_id: int | None = None
    reply: str | None = None
    paused: bool = False
    skipped: str | None = None


async def _transcribe(session: AsyncSession, message: Message, rt: Runtime) -> str | None:
    if message.transcript:
        return message.transcript
    if rt.whatsapp is None or rt.speech is None or not message.media_ref:
        message.error = "voice note received but speech-to-text is not configured"
        return None
    audio, mime = await rt.whatsapp.download_media(message.media_ref)
    folder = rt.storage_dir / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{message.id}.ogg").write_bytes(audio)
    transcript = await rt.speech.transcribe(audio, mime)
    if transcript is None:
        message.error = "speech-to-text returned nothing"
        return None
    message.transcript = transcript.text
    await session.flush()
    return transcript.text


def _cost(usage: list[Usage], rt: Runtime) -> Decimal:
    fresh = [u for u in usage if not u.cached]
    tokens_in = sum(u.tokens_in for u in fresh)
    tokens_out = sum(u.tokens_out for u in fresh)
    cost = (tokens_in * rt.price_input_per_m + tokens_out * rt.price_output_per_m) / 1_000_000
    return Decimal(str(round(cost, 6)))


async def _record_run(
    session: AsyncSession,
    message: Message,
    thread_id: str,
    deps: AgentDeps,
    outcome: AgentOutcome | None,
    started: float,
    rt: Runtime,
    error: str | None = None,
) -> AgentRun:
    state: dict[str, Any] = outcome.state if outcome else {}
    run = AgentRun(
        message_id=message.id,
        thread_id=thread_id,
        status="failed" if error else ("paused" if outcome and outcome.paused else "done"),
        intent=state.get("intent"),
        route=state.get("route"),
        prompt_version=PROMPT_VERSION if rt.llm else "rules",
        latency_ms=int((time.perf_counter() - started) * 1000),
        tokens_in=sum(u.tokens_in for u in deps.usage),
        tokens_out=sum(u.tokens_out for u in deps.usage),
        cost_estimate=_cost(deps.usage, rt),
        error=error,
    )
    session.add(run)
    await session.flush()
    for step in deps.trace:
        session.add(
            AgentStep(
                run_id=run.id,
                node=step["node"],
                started_at=step["started_at"],
                ended_at=step["ended_at"],
                output=step["output"],
            )
        )
    return run


async def process_message(session: AsyncSession, message_id: int, rt: Runtime) -> ProcessResult:
    message = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
    if message.status == MessageStatus.PROCESSED:
        return ProcessResult(message_id, skipped="already processed")
    if message.sender is None:
        message.status = MessageStatus.PROCESSED
        return ProcessResult(message_id, skipped="no sender")

    customer = await get_or_create_customer(
        session, message.business_id, message.sender, message.sender_name
    )
    message.customer_id = customer.id

    if message.type == MessageType.AUDIO:
        text = await _transcribe(session, message, rt)
    elif message.type == MessageType.TEXT:
        text = message.body
    else:
        text = None  # button replies are handled on Day 4; stickers etc. are ignored
    if not text:
        message.status = MessageStatus.PROCESSED
        return ProcessResult(message_id, skipped=f"no text ({message.type})")

    index = await rt.catalog.get(session, message.business_id)
    deps = AgentDeps(
        tools=DbOrderTools(session, message.business_id, index),
        matcher=Matcher(index),
        llm=rt.llm,
        settings=rt.settings,
    )
    thread_id = f"message:{message.id}"
    started = time.perf_counter()
    outcome = await run_message(
        rt.graph,
        deps,
        thread_id=thread_id,
        business_id=message.business_id,
        customer_id=customer.id,
        text=text,
        message_id=message.id,
    )
    run = await _record_run(session, message, thread_id, deps, outcome, started, rt)

    if outcome.reply:
        # Queued here; Day 4 sends it (grouping bursts of messages into one reply).
        session.add(
            Message(
                business_id=message.business_id,
                customer_id=customer.id,
                channel=message.channel,
                direction=Direction.OUT,
                type=MessageType.TEXT,
                body=outcome.reply,
                status=MessageStatus.QUEUED,
            )
        )
    message.status = MessageStatus.PROCESSED
    log.info(
        "agent.run",
        message_id=message.id,
        intent=outcome.state.get("intent"),
        route=outcome.state.get("route"),
        paused=outcome.paused,
    )
    return ProcessResult(message_id, run.id, outcome.reply, outcome.paused)
