"""Dashboard sign-in. Every endpoint here is under /api and protected by CSRF checks."""

import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import Settings
from app.deps import (
    CurrentUser,
    LimiterDep,
    SessionDep,
    SettingsDep,
    client_ip,
    require_csrf,
)
from app.models import User
from app.services.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    audit,
    authenticate,
    create_session,
    end_all_sessions,
    end_session,
    limiter_keys,
    resolve_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(require_csrf)])
log = structlog.get_logger()

INVALID = "Invalid email or password."
LOCKED = "Too many failed attempts. Try again in a few minutes."


class LoginRequest(BaseModel):
    # Plain string on purpose: login only compares it, and demo domains like .test are fine.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    role: str
    business_id: int

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            business_id=user.business_id,
        )


class SessionOut(BaseModel):
    user: UserOut | None


def _set_csrf_cookie(response: Response, settings: Settings) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(24),
        httponly=False,  # the dashboard reads it and sends it back in a header
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/session", response_model=SessionOut)
async def get_current_session(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> SessionOut:
    """Who is signed in (or null). Also hands out the CSRF cookie the login form needs."""
    if not request.cookies.get(CSRF_COOKIE):
        _set_csrf_cookie(response, settings)
    token = request.cookies.get(SESSION_COOKIE)
    user = await resolve_session(session, token, settings) if token else None
    return SessionOut(user=UserOut.of(user) if user else None)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> UserOut:
    ip = client_ip(request)
    keys = limiter_keys(body.email, ip)
    if await limiter.is_locked(keys):
        audit(session, "login_locked", business_id=None, user_id=None, ip=ip)
        await session.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED)

    user = await authenticate(session, body.email, body.password)
    if user is None:
        await limiter.record_failure(keys)
        audit(session, "login_failed", business_id=None, user_id=None, ip=ip)
        await session.commit()
        log.info("auth.login_failed", ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID)

    await limiter.reset(keys)
    token = await create_session(session, user, settings, ip, request.headers.get("user-agent"))
    audit(session, "login", business_id=user.business_id, user_id=user.id, ip=ip)
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_max_days * 86400,
    )
    _set_csrf_cookie(response, settings)  # a fresh CSRF token for the new session
    return UserOut.of(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await end_session(session, token)
    audit(session, "logout", business_id=user.business_id, user_id=user.id, ip=client_ip(request))
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> None:
    await end_all_sessions(session, user.id)
    audit(
        session, "logout_all", business_id=user.business_id, user_id=user.id, ip=client_ip(request)
    )
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
