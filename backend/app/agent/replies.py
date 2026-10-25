"""Customer-facing reply texts. Built from database values with templates, never written by
the LLM, so prices and totals in a reply are always the real ones."""

from decimal import Decimal

from app.matching.catalog import CatalogIndex
from app.ordering.draft import Draft


def money(amount: Decimal) -> str:
    return f"Rs {amount:,.0f}"


def _qty(qty: Decimal | None) -> str:
    if qty is None:
        return "?"
    return f"{qty.normalize():f}"


def order_summary(draft: Draft, index: CatalogIndex, total: Decimal) -> str:
    rows = []
    for line in draft.lines:
        name = index.by_key[line.key].name if line.key in index.by_key else line.source_text
        price = f" = {money(line.line_total)}" if line.line_total is not None else ""
        rows.append(f"- {_qty(line.qty)} {line.unit or ''} {name}{price}".replace("  ", " "))
    return "\n".join([*rows, f"Total: {money(total)}"])


def confirmation(number: int | None, summary: str) -> str:
    header = f"Order #{number}" if number else "Your order"
    return f"{header}\n{summary}\n\nReply 1 to confirm or 2 to make changes."


REVIEW_HOLD = "Thanks! We're checking your order and will confirm it shortly."
HANDOFF = "Thanks for your message. Our team will get back to you soon."
CANCELLED = "Your order has been cancelled."
NOT_FOUND = "Sorry, we couldn't find that product. Our team will get back to you."


def balance_answer(balance: Decimal) -> str:
    return f"Your current balance is {money(balance)}."


def price_answer(rows: list[tuple[str, str, Decimal]]) -> str:
    return "\n".join(f"{name}: {money(price)} per {unit}" for name, unit, price in rows)


def stock_answer(rows: list[tuple[str, bool]]) -> str:
    return "\n".join(f"{name}: {'in stock' if ok else 'out of stock'}" for name, ok in rows)
