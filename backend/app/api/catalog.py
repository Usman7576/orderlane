"""Catalog for the dashboard: products with units, prices and stock, and their aliases."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, SessionDep, client_ip, require_csrf
from app.matching.normalize import normalize
from app.models import Inventory, PriceList, Product, ProductAlias, ProductUnit
from app.models.enums import AliasSource
from app.services.auth import audit

router = APIRouter(prefix="/api", tags=["catalog"], dependencies=[Depends(require_csrf)])


class UnitOut(BaseModel):
    name: str
    base_qty: int


class ProductOut(BaseModel):
    sku: str
    name: str
    brand: str | None
    category: str | None
    default_unit: str
    units: list[UnitOut]
    prices: dict[str, dict[str, Decimal]]  # tier -> unit -> price
    available: int | None  # in base units (pieces), after reservations
    alias_count: int


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    categories: list[str]


class AliasOut(BaseModel):
    id: int
    text: str
    source: str
    created_at: datetime


class ProductDetail(ProductOut):
    aliases: list[AliasOut]


async def _products_out(session: AsyncSession, products: list[Product]) -> list[ProductOut]:
    ids = [p.id for p in products]
    units: dict[int, list[UnitOut]] = {}
    for u in (
        await session.execute(
            select(ProductUnit)
            .where(ProductUnit.product_id.in_(ids))
            .order_by(ProductUnit.base_qty)
        )
    ).scalars():
        units.setdefault(u.product_id, []).append(UnitOut(name=u.unit_name, base_qty=u.base_qty))
    prices: dict[int, dict[str, dict[str, Decimal]]] = {}
    for pl in (
        await session.execute(select(PriceList).where(PriceList.product_id.in_(ids)))
    ).scalars():
        prices.setdefault(pl.product_id, {}).setdefault(pl.tier, {})[pl.unit_name] = pl.price
    stock = {
        i.product_id: i.on_hand_base - i.reserved_base
        for i in (
            await session.execute(select(Inventory).where(Inventory.product_id.in_(ids)))
        ).scalars()
    }
    aliases = dict(
        (
            await session.execute(
                select(ProductAlias.product_id, func.count())
                .where(ProductAlias.product_id.in_(ids))
                .group_by(ProductAlias.product_id)
            )
        ).all()
    )
    return [
        ProductOut(
            sku=p.sku,
            name=p.name,
            brand=p.brand,
            category=p.category,
            default_unit=p.default_unit,
            units=units.get(p.id, []),
            prices=prices.get(p.id, {}),
            available=stock.get(p.id),
            alias_count=int(aliases.get(p.id, 0)),
        )
        for p in products
    ]


async def _product(session: AsyncSession, business_id: int, sku: str) -> Product:
    product = await session.scalar(
        select(Product).where(Product.business_id == business_id, Product.sku == sku)
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return product


@router.get("/products", response_model=ProductPage)
async def list_products(
    user: CurrentUser,
    session: SessionDep,
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ProductPage:
    query = select(Product).where(Product.business_id == user.business_id, Product.active.is_(True))
    if category:
        query = query.where(Product.category == category)
    if q:
        term = q.strip()
        alias_match = select(ProductAlias.product_id).where(
            ProductAlias.business_id == user.business_id,
            ProductAlias.normalized_text.ilike(f"%{normalize(term)}%"),
        )
        query = query.where(
            or_(
                Product.name.ilike(f"%{term}%"),
                Product.sku.ilike(f"%{term}%"),
                Product.brand.ilike(f"%{term}%"),
                Product.id.in_(alias_match),
            )
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    products = list(
        (
            await session.execute(
                query.order_by(Product.category, Product.name).limit(limit).offset(offset)
            )
        ).scalars()
    )
    categories = [
        c
        for c in (
            await session.execute(
                select(Product.category)
                .where(Product.business_id == user.business_id, Product.category.is_not(None))
                .distinct()
                .order_by(Product.category)
            )
        ).scalars()
        if c
    ]
    return ProductPage(
        items=await _products_out(session, products), total=total, categories=categories
    )


@router.get("/products/{sku}", response_model=ProductDetail)
async def get_product(sku: str, user: CurrentUser, session: SessionDep) -> ProductDetail:
    product = await _product(session, user.business_id, sku)
    [base] = await _products_out(session, [product])
    aliases = (
        await session.execute(
            select(ProductAlias)
            .where(ProductAlias.product_id == product.id)
            .order_by(ProductAlias.id)
        )
    ).scalars()
    return ProductDetail(
        **base.model_dump(),
        aliases=[
            AliasOut(id=a.id, text=a.alias_text, source=a.source, created_at=a.created_at)
            for a in aliases
        ],
    )


class AliasIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


@router.post("/products/{sku}/aliases", response_model=ProductDetail, status_code=201)
async def add_alias(
    sku: str, body: AliasIn, request: Request, user: CurrentUser, session: SessionDep
) -> ProductDetail:
    product = await _product(session, user.business_id, sku)
    normalized = normalize(body.text)
    if not normalized:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "alias has no usable words")
    exists = await session.scalar(
        select(ProductAlias.id).where(
            ProductAlias.business_id == user.business_id,
            ProductAlias.product_id == product.id,
            ProductAlias.normalized_text == normalized,
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "this alias already exists")
    session.add(
        ProductAlias(
            business_id=user.business_id,
            product_id=product.id,
            alias_text=body.text.strip(),
            normalized_text=normalized,
            source=AliasSource.STAFF,
        )
    )
    audit(
        session,
        "alias_added",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        sku=sku,
        alias=body.text.strip(),
    )
    await session.commit()
    return await get_product(sku, user, session)


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(
    alias_id: int, request: Request, user: CurrentUser, session: SessionDep
) -> None:
    alias = await session.scalar(
        select(ProductAlias).where(
            ProductAlias.id == alias_id, ProductAlias.business_id == user.business_id
        )
    )
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alias not found")
    audit(
        session,
        "alias_deleted",
        business_id=user.business_id,
        user_id=user.id,
        ip=client_ip(request),
        alias=alias.alias_text,
    )
    await session.delete(alias)
    await session.commit()
