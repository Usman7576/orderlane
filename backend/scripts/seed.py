"""Load demo data.

Usage (from backend/):
    uv run python -m scripts.seed           # add demo data if the database is empty
    uv run python -m scripts.seed --reset   # wipe every table first
"""

import argparse
import asyncio
import os
import secrets
from pathlib import Path

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.services.auth import hash_password
from app.services.seeding import reset_database, seed

DATA_DIR = Path(__file__).resolve().parent.parent / "seed"


async def main(reset: bool) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

    email = os.environ.get("SEED_ADMIN_EMAIL", "admin@orderlane.test").strip().lower()
    password = os.environ.get("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    generated = not os.environ.get("SEED_ADMIN_PASSWORD")

    async with factory() as session:
        if reset:
            await reset_database(session)
            await session.commit()
        result = await seed(
            session,
            DATA_DIR,
            wa_phone_number_id=settings.wa_phone_number_id or None,
            admin_email=email,
            admin_password_hash=hash_password(password),  # enforces the minimum length
        )
    await engine.dispose()

    print(
        f"Business id {result.business_id}: {result.products} products, "
        f"{result.customers} customers loaded."
    )
    if result.admin_created:
        print(f"Dashboard login: {email}")
        if generated:
            print(f"Generated password (shown once): {password}")
    if not settings.wa_phone_number_id:
        print("Note: WA_PHONE_NUMBER_ID is empty, so WhatsApp webhooks won't match a business yet.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    asyncio.run(main(parser.parse_args().reset))
