import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.deps import get_queue, get_session
from app.main import create_app
from app.models import Base
from tests.factories import APP_SECRET, VERIFY_TOKEN

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@dataclass
class FakeQueue:
    calls: list[tuple[int, int]] = field(default_factory=list)

    async def enqueue_message(self, message_id: int, job_id: int) -> None:
        self.calls.append((message_id, job_id))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL or "postgresql+asyncpg://unused/unused",
        wa_app_secret=SecretStr(APP_SECRET),
        wa_verify_token=SecretStr(VERIFY_TOKEN),
        wa_mark_as_read=False,
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


# --- Database (only for tests marked `db`) ---


def _migrate(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    get_settings.cache_clear()


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    # Alembic's env.py starts its own event loop, so run it in a worker thread.
    await asyncio.to_thread(_migrate, TEST_DATABASE_URL)
    eng = make_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = make_session_factory(engine)
    yield factory
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


# --- HTTP client ---


@pytest.fixture
def no_db_session() -> Iterator[MagicMock]:
    yield MagicMock(spec=AsyncSession)


async def _client(
    settings: Settings, queue: FakeQueue, session_override: object
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_session] = session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client(
    settings: Settings, queue: FakeQueue, no_db_session: MagicMock
) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose handlers must not touch the database."""

    async def override() -> AsyncIterator[MagicMock]:
        yield no_db_session

    async for c in _client(settings, queue, override):
        yield c


@pytest.fixture
async def db_client(
    settings: Settings, queue: FakeQueue, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[httpx.AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async for c in _client(settings, queue, override):
        yield c
