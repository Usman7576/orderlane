"""arq worker entry point: `arq app.workers.main.WorkerSettings`."""

from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Any, ClassVar

import httpx
import structlog
from arq.connections import RedisSettings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.graph import build_graph
from app.agent.state import AgentSettings
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.integrations.llm import LLM, AzureLLM
from app.integrations.speech import AzureSpeech, SpeechToText
from app.integrations.whatsapp import WhatsAppClient
from app.log import configure_logging
from app.services.processing import CatalogCache, Runtime
from app.workers.tasks import MAX_TRIES, process_inbound_message

settings = get_settings()
log = structlog.get_logger()


def make_llm(s: Settings) -> LLM | None:
    if not s.llm_enabled:
        return None
    return AzureLLM(
        endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key.get_secret_value(),
        api_version=s.azure_openai_api_version,
        deployment_small=s.azure_openai_deployment_small,
        deployment_large=s.azure_openai_deployment_large,
    )


def make_speech(s: Settings, http: httpx.AsyncClient) -> SpeechToText | None:
    if not s.speech_enabled:
        return None
    return AzureSpeech(
        http,
        key=s.azure_speech_key.get_secret_value(),
        region=s.azure_speech_region,
        languages=[lang.strip() for lang in s.speech_languages.split(",") if lang.strip()],
    )


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(settings.log_level, settings.log_json)
    stack = AsyncExitStack()
    engine = make_engine(settings.database_url)
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    # LangGraph saves paused runs (orders waiting for staff) in PostgreSQL via psycopg.
    psycopg_url = settings.database_url.replace("+asyncpg", "")
    checkpointer = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(psycopg_url))
    await checkpointer.setup()

    token = settings.wa_access_token.get_secret_value()
    whatsapp = (
        WhatsAppClient(
            http,
            access_token=token,
            phone_number_id=settings.wa_phone_number_id,
            graph_version=settings.wa_graph_version,
        )
        if token and settings.wa_phone_number_id
        else None
    )
    llm = make_llm(settings)
    speech = make_speech(settings, http)
    log.info(
        "worker.config",
        llm="azure" if llm else "rules only",
        speech="azure" if speech else "off",
        whatsapp="on" if whatsapp else "off",
    )

    ctx.update(
        stack=stack,
        engine=engine,
        http=http,
        session_factory=make_session_factory(engine),
        whatsapp=whatsapp,
        mark_as_read=settings.wa_mark_as_read,
        runtime=Runtime(
            graph=build_graph(checkpointer),
            catalog=CatalogCache(),
            llm=llm,
            speech=speech,
            whatsapp=whatsapp,
            storage_dir=settings.storage_dir,
            settings=AgentSettings(review_above_total=Decimal(str(settings.review_above_total))),
            price_input_per_m=settings.llm_price_input_per_m,
            price_output_per_m=settings.llm_price_output_per_m,
        ),
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["stack"].aclose()
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_inbound_message]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = MAX_TRIES
    job_timeout = 180
