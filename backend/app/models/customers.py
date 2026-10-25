from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class Customer(CreatedAtMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("business_id", "wa_phone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    # WhatsApp ID (international number without "+"). Web chat customers use a "web:" prefix.
    wa_phone: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    price_tier: Mapped[str] = mapped_column(String(16), default="A")
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # True until the customer's first order is confirmed (first orders always go to review).
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)


class LedgerEntry(CreatedAtMixin, Base):
    """Positive amounts increase what the customer owes; payments are negative."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    ref: Mapped[str | None] = mapped_column(String(64))
