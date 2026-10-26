"""FastAPI dependencies. Tests replace these with `app.dependency_overrides`."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.models import User
from app.services.auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    LoginLimiter,
    resolve_session,
)
from app.services.queue import JobQueue

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    async with factory() as session:
        yield session


def get_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.queue)


def get_limiter(request: Request) -> LoginLimiter:
    return cast(LoginLimiter, request.app.state.login_limiter)


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
LimiterDep = Annotated[LoginLimiter, Depends(get_limiter)]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def require_csrf(request: Request) -> None:
    """Double-submit check: state-changing requests must echo the csrf cookie in a header.
    Another site can make the browser send our cookies, but it can't read them."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not hmac.compare_digest(cookie, header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")


async def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = await resolve_session(session, token, settings) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
