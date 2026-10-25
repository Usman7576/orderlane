"""arq worker entry point: `arq app.workers.main.WorkerSettings`."""

from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.integrations.whatsapp import WhatsAppClient
from app.log import configure_logging
from app.workers.tasks import MAX_TRIES, process_inbound_message

settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(settings.log_level, settings.log_json)
    engine = make_engine(settings.database_url)
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
    ctx["engine"] = engine
    ctx["http"] = http
    ctx["session_factory"] = make_session_factory(engine)
    ctx["mark_as_read"] = settings.wa_mark_as_read
    token = settings.wa_access_token.get_secret_value()
    ctx["whatsapp"] = (
        WhatsAppClient(
            http,
            access_token=token,
            phone_number_id=settings.wa_phone_number_id,
            graph_version=settings.wa_graph_version,
        )
        if token and settings.wa_phone_number_id
        else None
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_inbound_message]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = MAX_TRIES
    job_timeout = 120
