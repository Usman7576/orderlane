"""Every dashboard API route must require sign-in, and every state-changing one a CSRF token.

The routes are discovered from the app, so a new endpoint is covered automatically."""

import re

import httpx
import pytest

from app.main import create_app
from app.services.auth import CSRF_COOKIE, CSRF_HEADER

PUBLIC = {("GET", "/api/auth/session"), ("POST", "/api/auth/login")}
SAFE = {"GET", "HEAD", "OPTIONS"}


def _api_routes() -> list[tuple[str, str]]:
    """From the OpenAPI schema, which lists every endpoint however routers are nested."""
    routes = []
    for path, operations in create_app().openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method in operations:
            if (method.upper(), path) not in PUBLIC:
                routes.append((method.upper(), path))
    return sorted(routes)


def _concrete(path: str) -> str:
    """Fill path parameters with plausible values ("approve" for the review action)."""
    path = path.replace("{action}", "approve")
    return re.sub(r"\{[^}]+\}", "1", path)


ROUTES = _api_routes()


def test_routes_were_discovered() -> None:
    assert len(ROUTES) >= 15


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] in SAFE])
async def test_reads_require_sign_in(client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, _concrete(path))
    assert response.status_code == 401, f"{method} {path} is readable without signing in"


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] not in SAFE])
async def test_writes_require_csrf_token(client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, _concrete(path), json={})
    assert response.status_code == 403, f"{method} {path} accepts a request without a CSRF token"


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] not in SAFE])
async def test_writes_require_sign_in_even_with_csrf(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    client.cookies.set(CSRF_COOKIE, "token")
    response = await client.request(
        method, _concrete(path), json={}, headers={CSRF_HEADER: "token"}
    )
    assert response.status_code in (401, 422), f"{method} {path} works without signing in"
