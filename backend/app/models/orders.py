from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin
from app.models.enums import OrderStatus


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("business_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    # Human-friendly number shown to customers, assigned per business.
    number: Mapped[int] = mapped_column()
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default=OrderStatus.DRAFT, index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # LangGraph thread paused for staff review of this order, resumed on approve/reject.
    review_thread_id: Mapped[str | None] = mapped_column(String(128))

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    unit_name: Mapped[str | None] = mapped_column(String(32))
    qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # What the customer actually wrote or said for this line.
    source_text: Mapped[str | None] = mapped_column(Text)
    match_method: Mapped[str | None] = mapped_column(String(16))
    match_confidence: Mapped[float | None] = mapped_column()
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderEvent(CreatedAtMixin, Base):
    """Audit log: every change to an order, by whom, written in the same transaction."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Invoice(CreatedAtMixin, Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    pdf_path: Mapped[str] = mapped_column(String(512))
