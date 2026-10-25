from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Customer, PriceList, Product, ProductAlias, User
from app.services.seeding import seed

pytestmark = pytest.mark.db

DATA_DIR = Path(__file__).resolve().parents[2] / "seed"


async def test_seed_loads_catalog_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first = await seed(
            session,
            DATA_DIR,
            wa_phone_number_id="PN-SEED",
            admin_email="admin@orderlane.test",
            admin_password_hash="not-a-real-hash",
        )
    async with session_factory() as session:
        second = await seed(
            session,
            DATA_DIR,
            wa_phone_number_id="PN-SEED",
            admin_email="admin@orderlane.test",
            admin_password_hash="not-a-real-hash",
        )

    assert first.products > 50 and first.customers > 0 and first.admin_created
    assert (second.products, second.customers, second.admin_created) == (0, 0, False)
    assert first.business_id == second.business_id

    async with session_factory() as session:
        products = (await session.execute(select(func.count()).select_from(Product))).scalar_one()
        users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        customers = (await session.execute(select(func.count()).select_from(Customer))).scalar_one()
        tapal = (
            await session.execute(select(Product).where(Product.sku == "TAP-DAN-950"))
        ).scalar_one()
        carton_price = (
            await session.execute(
                select(PriceList.price).where(
                    PriceList.product_id == tapal.id,
                    PriceList.tier == "A",
                    PriceList.unit_name == "carton",
                )
            )
        ).scalar_one()
        alias_hit = (
            await session.execute(
                select(ProductAlias.product_id).where(
                    ProductAlias.normalized_text == "danedar bara"
                )
            )
        ).scalar_one()

    assert products == first.products
    assert users == 1
    assert customers == first.customers
    assert carton_price == Decimal("21756")  # 1850 x 12 x 0.98
    assert alias_hit == tapal.id
