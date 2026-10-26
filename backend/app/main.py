from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.api import (
    auth,
    catalog,
    customers,
    health,
    operations,
    orders,
    simulator,
    webhooks,
)
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.log import configure_logging
from app.services.auth import RedisLoginLimiter
from app.services.queue import ArqJobQueue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.session_factory = make_session_factory(engine)
    app.state.redis = redis
    app.state.queue = ArqJobQueue(redis)
    app.state.login_limiter = RedisLoginLimiter(
        redis, settings.login_max_failures, settings.login_lock_minutes * 60
    )
    yield
    await redis.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="OrderLane API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    for module in (auth, orders, catalog, customers, operations, simulator):
        app.include_router(module.router)
    return app


app = create_app()
