"""In-memory catalog index used for matching.

A small wholesaler has hundreds of products, not millions, so matching runs in memory with
rapidfuzz: fast, easy to test, and the evaluation runs without a database. The pg_trgm
indexes in PostgreSQL are kept for dashboard search and for larger catalogs later."""

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.matching.lexicon import FILLER_WORDS
from app.matching.normalize import Size, clean, normalize

_NAME_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(g|kg|ml|litre|l)\b", re.IGNORECASE)


@dataclass
class CatalogProduct:
    key: str  # SKU; also how evaluation results are compared
    name: str
    default_unit: str
    units: dict[str, int]  # unit name -> base quantity (piece = 1)
    aliases: list[str] = field(default_factory=list)
    product_id: int | None = None
    size: Size | None = None
    # Filled by CatalogIndex: alias texts without numbers, and every word in them.
    word_forms: list[str] = field(default_factory=list)
    vocabulary: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.size is None:
            m = _NAME_SIZE.search(self.name)
            if m:
                unit = m.group(2).lower().replace("litre", "l")
                self.size = Size.from_token(f"{m.group(1)}{unit}")


def words_only(text: str) -> str:
    """Product words without sizes, numbers or filler: "tapal danedar 950g" -> "tapal danedar"."""
    tokens = []
    for token in clean(text).split():
        if token in FILLER_WORDS or Size.from_token(token) or any(c.isdigit() for c in token):
            continue
        tokens.append(token)
    return " ".join(tokens)


class CatalogIndex:
    def __init__(self, products: list[CatalogProduct]) -> None:
        self.products = products
        self.by_key = {p.key: p for p in products}
        # Exact alias lookup: normalized alias -> product keys (more than one = ambiguous).
        self.exact: dict[str, set[str]] = defaultdict(set)
        for p in products:
            forms = {words_only(t) for t in [p.name, *p.aliases]}
            p.word_forms = sorted(f for f in forms if f)
            p.vocabulary = {word for form in p.word_forms for word in form.split()}
            for text in [p.name, *p.aliases]:
                self.exact[normalize(text)].add(p.key)

    @classmethod
    def from_csv(cls, path: Path) -> "CatalogIndex":
        from app.services.seeding import parse_units  # avoid an import cycle

        with path.open(encoding="utf-8", newline="") as f:
            products = [
                CatalogProduct(
                    key=row["sku"],
                    name=row["name"],
                    default_unit=row["default_unit"],
                    units=parse_units(row["units"]),
                    aliases=[a for a in row["aliases"].split("|") if a],
                )
                for row in csv.DictReader(f)
            ]
        return cls(products)

    @classmethod
    async def from_db(cls, session: AsyncSession, business_id: int) -> "CatalogIndex":
        from app.models import Product

        result = await session.execute(
            select(Product)
            .where(Product.business_id == business_id, Product.active.is_(True))
            .options(selectinload(Product.units), selectinload(Product.aliases))
        )
        products = [
            CatalogProduct(
                key=p.sku,
                name=p.name,
                default_unit=p.default_unit,
                units={u.unit_name: u.base_qty for u in p.units},
                aliases=[a.alias_text for a in p.aliases],
                product_id=p.id,
            )
            for p in result.scalars()
        ]
        return cls(products)
