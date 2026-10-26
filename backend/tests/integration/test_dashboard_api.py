"""Dashboard API against PostgreSQL: sign-in security and the staff review workflow."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models import AuditEvent, Business, Job, Message, Order, User, UserSession
from app.models.enums import JobStatus, OrderStatus, Role
from app.services.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, hash_password
from app.services.processing import process_message
from tests.conftest import FakeQueue
from tests.integration.helpers import (
    NEW_PHONE,
    Factory,
    make_runtime,
    save_message,
    seeded_business,
)

pytestmark = pytest.mark.db

EMAIL = "staff@orderlane.test"
PASSWORD = "correct horse battery"


async def _add_user(factory: Factory, business_id: int, email: str = EMAIL) -> int:
    async with factory() as session:
        user = User(
            business_id=business_id,
            email=email,
            name="Staff",
            password_hash=hash_password(PASSWORD),
            role=Role.STAFF,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    await client.get("/api/auth/session")
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


async def _login(
    client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> httpx.Response:
    headers = await _csrf(client)
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password}, headers=headers
    )


def _headers(client: httpx.AsyncClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


async def _held_order(factory: Factory, business_id: int, tmp_path: Path, text: str) -> Order:
    """A first order from a new customer, which the agent always holds for staff review."""
    message_id, _ = await save_message(factory, business_id, body=text, sender=NEW_PHONE)
    async with factory() as session:
        await process_message(session, message_id, make_runtime(tmp_path))
        await session.commit()
    async with factory() as session:
        return (await session.execute(select(Order))).scalar_one()


# --- Sign-in ---


async def test_api_requires_sign_in(db_client: httpx.AsyncClient, session_factory: Factory) -> None:
    for path in ("/api/orders", "/api/products", "/api/customers", "/api/jobs", "/api/summary"):
        assert (await db_client.get(path)).status_code == 401, path


async def test_login_sets_secure_session_and_stores_only_a_hash(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)

    response = await _login(db_client)

    assert response.status_code == 200 and response.json()["email"] == EMAIL
    cookie_header = "; ".join(response.headers.get_list("set-cookie"))
    assert f"{SESSION_COOKIE}=" in cookie_header and "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    token = db_client.cookies[SESSION_COOKIE]
    async with session_factory() as session:
        stored = (await session.execute(select(UserSession))).scalar_one()
    assert stored.token_hash != token and len(stored.token_hash) == 64
    assert (await db_client.get("/api/orders")).status_code == 200


async def test_wrong_password_and_unknown_email_look_the_same(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    wrong = await _login(db_client, password="not the password")
    unknown = await _login(db_client, email="nobody@orderlane.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_repeated_failures_lock_the_account(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    for _ in range(5):
        assert (await _login(db_client, password="guess")).status_code == 401
    locked = await _login(db_client)  # even the right password is refused while locked
    assert locked.status_code == 429
    async with session_factory() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert actions.count("login_failed") == 5 and "login_locked" in actions


async def test_state_changing_requests_need_the_csrf_header(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await db_client.get("/api/auth/session")
    no_header = await db_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    wrong_header = await db_client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={CSRF_HEADER: "forged"},
    )
    assert no_header.status_code == wrong_header.status_code == 403


async def test_logout_ends_the_session(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await _login(db_client)
    token = db_client.cookies[SESSION_COOKIE]

    assert (
        await db_client.post("/api/auth/logout", headers=_headers(db_client))
    ).status_code == 204

    db_client.cookies.set(SESSION_COOKIE, token)  # replaying the old cookie must not work
    assert (await db_client.get("/api/orders")).status_code == 401


# --- Orders and review ---


async def test_inbox_and_order_detail(
    db_client: httpx.AsyncClient, session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    order = await _held_order(session_factory, business_id, tmp_path, "1 dozen ariel")
    await _login(db_client)

    page = (await db_client.get("/api/orders", params={"status": "pending_review"})).json()
    assert page["total"] == 1 and page["counts"]["pending_review"] == 1
    assert page["items"][0]["issues"] == ["first_order", "lines_to_check"]

    detail = (await db_client.get(f"/api/orders/{order.id}")).json()
    [line] = detail["lines"]
    assert line["needs_review"] and line["source_text"]
    assert {a["sku"] for a in line["alternatives"]} & {"ARL-DET-1KG", "ARL-DET-500"}
    assert detail["messages"][0]["body"] == "1 dozen ariel"
    assert detail["runs"] and detail["runs"][0]["steps"][0]["node"] == "interpret"
    assert detail["customer"]["is_new"] is True


async def test_edit_lines_then_approve_queues_the_review(
    db_client: httpx.AsyncClient, session_factory: Factory, tmp_path: Path, queue: FakeQueue
) -> None:
    business_id = await seeded_business(session_factory)
    user_id = await _add_user(session_factory, business_id)
    order = await _held_order(session_factory, business_id, tmp_path, "1 dozen ariel")
    await _login(db_client)

    edited = await db_client.put(
        f"/api/orders/{order.id}/lines",
        json={
            "lines": [{"sku": "ARL-DET-500", "unit": "dozen", "qty": "2", "source_text": "ariel"}]
        },
        headers=_headers(db_client),
    )
    assert edited.status_code == 200
    assert Decimal(edited.json()["total"]) == Decimal(8880)
    assert edited.json()["can_approve"] is True

    approved = await db_client.post(f"/api/orders/{order.id}/approve", headers=_headers(db_client))
    assert approved.status_code == 202
    assert queue.reviews == [(business_id, order.id, "approve", user_id)]


async def test_approve_is_refused_for_orders_not_in_review(
    db_client: httpx.AsyncClient, session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    order = await _held_order(session_factory, business_id, tmp_path, "1 ctn tapal 950")
    async with session_factory() as session:
        (await session.get_one(Order, order.id)).status = OrderStatus.INVOICED
        await session.commit()
    await _login(db_client)
    response = await db_client.post(f"/api/orders/{order.id}/approve", headers=_headers(db_client))
    assert response.status_code == 409


async def test_other_businesses_orders_are_invisible(
    db_client: httpx.AsyncClient, session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    order = await _held_order(session_factory, business_id, tmp_path, "1 ctn tapal 950")
    async with session_factory() as session:
        other = Business(name="Other Wholesale", settings={})
        session.add(other)
        await session.commit()
        other_id = other.id
    await _add_user(session_factory, other_id, email="other@orderlane.test")
    await _login(db_client, email="other@orderlane.test")

    assert (await db_client.get(f"/api/orders/{order.id}")).status_code == 404
    assert (await db_client.get("/api/orders")).json()["total"] == 0


# --- Catalog, customers, jobs ---


async def test_catalog_search_and_aliases(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await _login(db_client)

    found = (await db_client.get("/api/products", params={"q": "danedar bara"})).json()
    assert [p["sku"] for p in found["items"]] == ["TAP-DAN-950"]
    product = found["items"][0]
    assert product["prices"]["B"]["carton"] == "21103.00" and product["available"] == 240

    added = await db_client.post(
        "/api/products/TAP-DAN-950/aliases",
        json={"text": "laal dabba chai"},
        headers=_headers(db_client),
    )
    assert added.status_code == 201
    alias = next(a for a in added.json()["aliases"] if a["text"] == "laal dabba chai")
    assert alias["source"] == "staff"
    duplicate = await db_client.post(
        "/api/products/TAP-DAN-950/aliases",
        json={"text": "Laal Dabba Chai!"},
        headers=_headers(db_client),
    )
    assert duplicate.status_code == 409
    deleted = await db_client.delete(f"/api/aliases/{alias['id']}", headers=_headers(db_client))
    assert deleted.status_code == 204


async def test_customers_with_balances(
    db_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await _login(db_client)
    page = (await db_client.get("/api/customers", params={"q": "Bismillah"})).json()
    [customer] = page["items"]
    assert Decimal(customer["balance"]) == Decimal(71000)
    detail = (await db_client.get(f"/api/customers/{customer['id']}")).json()
    assert detail["ledger"][0]["ref"] == "opening balance"


async def test_failed_job_can_be_retried(
    db_client: httpx.AsyncClient, session_factory: Factory, queue: FakeQueue
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    message_id, job_id = await save_message(session_factory, business_id, body="hello")
    async with session_factory() as session:
        job = await session.get_one(Job, job_id)
        job.status, job.last_error = JobStatus.FAILED, "LLM timeout"
        await session.commit()
    await _login(db_client)

    [listed] = (await db_client.get("/api/jobs")).json()
    assert listed["last_error"] == "LLM timeout" and listed["message_preview"] == "hello"
    response = await db_client.post(f"/api/jobs/{job_id}/retry", headers=_headers(db_client))
    assert response.status_code == 202
    assert queue.retries == [(message_id, job_id)]
    async with session_factory() as session:
        assert (await session.get_one(Job, job_id)).status == JobStatus.QUEUED
        assert (await session.get_one(Message, message_id)).status == "received"


@pytest.mark.parametrize(
    ("field", "age"),
    [("last_seen_at", {"hours": 13}), ("expires_at", {"days": 0, "seconds": -1})],
    ids=["idle_for_13_hours", "past_absolute_limit"],
)
async def test_expired_sessions_are_refused_and_deleted(
    db_client: httpx.AsyncClient, session_factory: Factory, field: str, age: dict[str, int]
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await _login(db_client)
    async with session_factory() as session:
        row = (await session.execute(select(UserSession))).scalar_one()
        now = datetime.now(UTC)
        setattr(
            row,
            field,
            now - timedelta(**age) if field == "last_seen_at" else now + timedelta(**age),
        )
        await session.commit()

    assert (await db_client.get("/api/orders")).status_code == 401
    async with session_factory() as session:
        assert (await session.execute(select(UserSession))).first() is None


# --- Web chat simulator ---


async def test_simulator_messages_use_the_real_pipeline(
    db_client: httpx.AsyncClient, session_factory: Factory, queue: FakeQueue
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    await _login(db_client)

    text = await db_client.post(
        "/api/simulator/messages",
        json={"phone": "923001112233", "name": "Demo Shop", "text": "2 ctn tapal 950"},
        headers=_headers(db_client),
    )
    button = await db_client.post(
        "/api/simulator/messages",
        json={"phone": "923001112233", "button_id": "confirm:7", "button_title": "Confirm"},
        headers=_headers(db_client),
    )
    both = await db_client.post(
        "/api/simulator/messages",
        json={"phone": "923001112233", "text": "x", "button_id": "edit:7"},
        headers=_headers(db_client),
    )

    assert text.status_code == button.status_code == 202
    assert both.status_code == 422
    assert [m for m, _ in queue.calls] == [text.json()["message_id"], button.json()["message_id"]]
    async with session_factory() as session:
        saved = await session.get_one(Message, button.json()["message_id"])
    assert (saved.channel, saved.type) == ("webchat", "interactive")
    assert saved.raw == {
        "interactive": {"type": "button_reply", "button_reply": {"id": "confirm:7"}}
    }

    convo = (
        await db_client.get("/api/simulator/conversation", params={"phone": "923001112233"})
    ).json()
    assert [m["body"] for m in convo] == ["2 ctn tapal 950", "Confirm"]


# --- Metrics ---


async def test_metrics_count_live_orders_and_agent_runs(
    db_client: httpx.AsyncClient, session_factory: Factory, tmp_path: Path
) -> None:
    business_id = await seeded_business(session_factory)
    await _add_user(session_factory, business_id)
    for text in ("2 ctn tapal 950", "1 dz lux"):
        message_id, _ = await save_message(session_factory, business_id, body=text)
        async with session_factory() as session:
            await process_message(session, message_id, make_runtime(tmp_path))
    await _login(db_client)

    response = await db_client.get("/api/metrics", params={"days": 7})

    assert response.status_code == 200
    live = response.json()["live"]
    assert live["days"] == 7
    assert live["orders"] >= 1
    assert live["agent_runs"] == 2
    assert live["agent_errors"] == 0
    assert live["latency_p50_ms"] is not None
    assert live["match_methods"]  # lines matched by alias or fuzzy
