"""Loads the demo business, catalog, prices, stock and customers from CSV files."""

import csv
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.normalize import normalize
from app.models import (
    Base,
    Business,
    Customer,
    Inventory,
    LedgerEntry,
    PriceList,
    Product,
    ProductAlias,
    ProductUnit,
    User,
)
from app.models.enums import AliasSource, LedgerType, Role

BUSINESS_NAME = "Karachi Wholesale Traders"
# Tier B (bulk buyers) pays 3% less; full cartons get a further 2% off.
TIER_DISCOUNT = {"A": Decimal("1.00"), "B": Decimal("0.97")}
CARTON_DISCOUNT = Decimal("0.98")


@dataclass
class SeedResult:
    business_id: int
    products: int
    customers: int
    admin_created: bool


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def parse_units(spec: str) -> dict[str, int]:
    """ "carton:12;dozen:12" -> {"piece": 1, "carton": 12, "dozen": 12}."""
    units = {"piece": 1}
    for part in filter(None, spec.split(";")):
        name, qty = part.split(":")
        units[name.strip()] = int(qty)
    return units


def unit_price(piece_price: Decimal, unit: str, base_qty: int, tier: str) -> Decimal:
    price = piece_price * base_qty * TIER_DISCOUNT[tier]
    if unit == "carton":
        price *= CARTON_DISCOUNT
    return _money(price)


async def reset_database(session: AsyncSession) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def seed(
    session: AsyncSession,
    data_dir: Path,
    *,
    wa_phone_number_id: str | None,
    admin_email: str,
    admin_password_hash: str | None,
) -> SeedResult:
    """Idempotent for the business and admin; the catalog is only loaded into an empty business."""
    business = (
        await session.execute(select(Business).where(Business.name == BUSINESS_NAME))
    ).scalar_one_or_none()
    if business is None:
        business = Business(
            name=BUSINESS_NAME, currency="PKR", timezone="Asia/Karachi", settings={}
        )
        session.add(business)
        await session.flush()
    if wa_phone_number_id:
        business.wa_phone_number_id = wa_phone_number_id

    admin_created = False
    existing_admin = (
        await session.execute(select(User).where(User.email == admin_email))
    ).scalar_one_or_none()
    if existing_admin is None and admin_password_hash:
        session.add(
            User(
                business_id=business.id,
                email=admin_email,
                password_hash=admin_password_hash,
                role=Role.OWNER,
            )
        )
        admin_created = True

    has_products = (
        await session.execute(select(Product.id).where(Product.business_id == business.id).limit(1))
    ).first()
    products = customers = 0
    if not has_products:
        products = await _load_catalog(session, business.id, data_dir / "catalog.csv")
        customers = await _load_customers(session, business.id, data_dir / "customers.csv")
    await session.commit()
    return SeedResult(business.id, products, customers, admin_created)


async def _load_catalog(session: AsyncSession, business_id: int, path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            units = parse_units(row["units"])
            product = Product(
                business_id=business_id,
                sku=row["sku"],
                name=row["name"],
                brand=row["brand"] or None,
                category=row["category"] or None,
                base_unit="piece",
                default_unit=row["default_unit"],
                active=True,
            )
            product.units = [ProductUnit(unit_name=u, base_qty=q) for u, q in units.items()]
            names = [row["name"], *filter(None, row["aliases"].split("|"))]
            # Keyed by normalized text, so "close up" and "close-up" become one alias.
            by_normalized = {normalize(name): name for name in reversed(names)}
            product.aliases = [
                ProductAlias(
                    business_id=business_id,
                    alias_text=alias,
                    normalized_text=normalized,
                    source=AliasSource.SEED,
                )
                for normalized, alias in by_normalized.items()
            ]
            session.add(product)
            await session.flush()

            piece_price = Decimal(row["price_a"])
            for tier in TIER_DISCOUNT:
                for unit, qty in units.items():
                    session.add(
                        PriceList(
                            business_id=business_id,
                            tier=tier,
                            product_id=product.id,
                            unit_name=unit,
                            price=unit_price(piece_price, unit, qty, tier),
                        )
                    )
            session.add(
                Inventory(
                    product_id=product.id,
                    on_hand_base=int(row["stock_pieces"]),
                    reserved_base=0,
                )
            )
            count += 1
    return count


async def _load_customers(session: AsyncSession, business_id: int, path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            customer = Customer(
                business_id=business_id,
                wa_phone=row["wa_phone"],
                name=row["name"],
                price_tier=row["price_tier"],
                credit_limit=Decimal(row["credit_limit"]),
                is_new=row["is_new"] == "true",
            )
            session.add(customer)
            await session.flush()
            opening = Decimal(row["opening_balance"])
            if opening:
                session.add(
                    LedgerEntry(
                        business_id=business_id,
                        customer_id=customer.id,
                        type=LedgerType.ADJUSTMENT,
                        amount=opening,
                        ref="opening balance",
                    )
                )
            count += 1
    return count
