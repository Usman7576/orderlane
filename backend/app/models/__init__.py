"""Importing this package registers every table on Base.metadata (needed by Alembic)."""

from app.models.agent import AgentRun, AgentStep, EvalRun, LlmCache
from app.models.base import Base
from app.models.catalog import Inventory, PriceList, Product, ProductAlias, ProductUnit
from app.models.customers import Customer, LedgerEntry
from app.models.messaging import Job, Message
from app.models.orders import Invoice, Order, OrderEvent, OrderLine
from app.models.tenancy import Business, User

__all__ = [
    "AgentRun",
    "AgentStep",
    "Base",
    "Business",
    "Customer",
    "EvalRun",
    "Inventory",
    "Invoice",
    "Job",
    "LedgerEntry",
    "LlmCache",
    "Message",
    "Order",
    "OrderEvent",
    "OrderLine",
    "PriceList",
    "Product",
    "ProductAlias",
    "ProductUnit",
    "User",
]
