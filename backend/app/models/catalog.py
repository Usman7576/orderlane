from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin
from app.models.enums import AliasSource


class Product(CreatedAtMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "sku"),
        Index(
            "ix_products_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    # Smallest sellable unit, e.g. "piece" (one pack, bottle or bar).
    base_unit: Mapped[str] = mapped_column(String(32), default="piece")
    # Unit assumed when a customer gives a quantity without a unit.
    default_unit: Mapped[str] = mapped_column(String(32), default="piece")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    units: Mapped[list["ProductUnit"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["ProductAlias"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductUnit(Base):
    """How many base units a selling unit contains, e.g. carton = 12 pieces."""

    __tablename__ = "product_units"
    __table_args__ = (UniqueConstraint("product_id", "unit_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    unit_name: Mapped[str] = mapped_column(String(32))
    base_qty: Mapped[int] = mapped_column(Integer)

    product: Mapped[Product] = relationship(back_populates="units")


class ProductAlias(CreatedAtMixin, Base):
    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("business_id", "normalized_text", "product_id"),
        Index(
            "ix_product_aliases_normalized_text_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    alias_text: Mapped[str] = mapped_column(String(200))
    normalized_text: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), default=AliasSource.SEED)

    product: Mapped[Product] = relationship(back_populates="aliases")


class PriceList(Base):
    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("business_id", "tier", "product_id", "unit_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    tier: Mapped[str] = mapped_column(String(16))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    unit_name: Mapped[str] = mapped_column(String(32))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Inventory(Base):
    __tablename__ = "inventory"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    on_hand_base: Mapped[int] = mapped_column(Integer, default=0)
    reserved_base: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
