"""Dashboard authentication: passwords, server-side sessions, login rate limits, audit log.

Design (docs/PLAN.md section 24):
- Argon2id password hashes. Unknown emails are checked against a dummy hash, so a login
  takes the same time whether or not the account exists.
- The session cookie holds a random 256-bit token; the database stores only its SHA-256.
- Sessions end after 12 idle hours or 7 days, whichever comes first.
- 5 failed logins in 15 minutes lock that email and that IP for 15 minutes.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AuditEvent, User, UserSession

SESSION_COOKIE = "ol_session"
CSRF_COOKIE = "ol_csrf"
CSRF_HEADER = "X-CSRF-Token"
MIN_PASSWORD_LENGTH = 10
TOUCH_EVERY = timedelta(minutes=1)  # don't write last_seen_at on every request


@lru_cache
def _hasher() -> PasswordHash:
    return PasswordHash.recommended()


@lru_cache
def _dummy_hash() -> str:
    return _hasher().hash("not-a-real-password-just-for-timing")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher().hash(password)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None:
        _hasher().verify(password, _dummy_hash())  # same work as a real check
        return None
    valid, updated_hash = _hasher().verify_and_update(password, user.password_hash)
    if not valid:
        return None
    if updated_hash is not None:  # hashing parameters were strengthened since
        user.password_hash = updated_hash
    return user


async def create_session(
    session: AsyncSession, user: User, settings: Settings, ip: str | None, user_agent: str | None
) -> str:
    token = new_token()
    now = datetime.now(UTC)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.session_max_days),
            ip=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    await session.flush()
    return token


async def resolve_session(session: AsyncSession, token: str, settings: Settings) -> User | None:
    """The signed-in user for a session token, or None if it's unknown or expired."""
    row = (
        await session.execute(
            select(UserSession).where(UserSession.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(UTC)
    idle_limit = row.last_seen_at + timedelta(hours=settings.session_idle_hours)
    if now >= row.expires_at or now >= idle_limit:
        await session.delete(row)
        await session.commit()
        return None
    if now - row.last_seen_at >= TOUCH_EVERY:
        row.last_seen_at = now
        await session.commit()
    return await session.get(User, row.user_id)


async def end_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


async def end_all_sessions(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(UserSession).where(UserSession.user_id == user_id))


def audit(
    session: AsyncSession,
    action: str,
    *,
    business_id: int | None,
    user_id: int | None,
    ip: str | None,
    **detail: Any,
) -> None:
    session.add(
        AuditEvent(business_id=business_id, user_id=user_id, action=action, detail=detail, ip=ip)
    )


# --- Login rate limiting ---


class LoginLimiter(Protocol):
    async def is_locked(self, keys: list[str]) -> bool: ...
    async def record_failure(self, keys: list[str]) -> None: ...
    async def reset(self, keys: list[str]) -> None: ...


def limiter_keys(email: str, ip: str | None) -> list[str]:
    email_key = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24]
    return [f"login:email:{email_key}", f"login:ip:{ip or 'unknown'}"]


class RedisLoginLimiter:
    """Counts failures per key in Redis; the count expires after the lock window."""

    def __init__(self, redis: Any, max_failures: int, window_s: int) -> None:
        self._redis = redis
        self._max = max_failures
        self._window = window_s

    async def is_locked(self, keys: list[str]) -> bool:
        counts = [await self._redis.get(key) for key in keys]
        return any(int(c or 0) >= self._max for c in counts)

    async def record_failure(self, keys: list[str]) -> None:
        for key in keys:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window)

    async def reset(self, keys: list[str]) -> None:
        # Only the email counter: a correct password shouldn't unlock an IP that is
        # guessing other people's passwords.
        await self._redis.delete(keys[0])


@dataclass
class MemoryLoginLimiter:
    """For tests and single-process development."""

    max_failures: int = 5
    counts: dict[str, int] = field(default_factory=dict)

    async def is_locked(self, keys: list[str]) -> bool:
        return any(self.counts.get(k, 0) >= self.max_failures for k in keys)

    async def record_failure(self, keys: list[str]) -> None:
        for key in keys:
            self.counts[key] = self.counts.get(key, 0) + 1

    async def reset(self, keys: list[str]) -> None:
        self.counts.pop(keys[0], None)
