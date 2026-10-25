from decimal import Decimal
from pathlib import Path

import pytest

from app.matching.catalog import CatalogIndex, CatalogProduct
from app.matching.matcher import Decision, Matcher
from app.matching.normalize import Size
from app.matching.parse import parse_lines

CATALOG = Path(__file__).resolve().parents[2] / "seed" / "catalog.csv"


@pytest.fixture(scope="module")
def matcher() -> Matcher:
    return Matcher(CatalogIndex.from_csv(CATALOG))


def _match(matcher: Matcher, text: str, prefer: set[str] | None = None):  # type: ignore[no-untyped-def]
    [line] = parse_lines(text)
    return matcher.match(line, prefer)


def test_product_size_is_read_from_name() -> None:
    assert CatalogProduct("X", "Dalda Cooking Oil 5 Litre Tin", "piece", {"piece": 1}).size == Size(
        Decimal(5000), "ml"
    )
    assert CatalogProduct("Y", "Lay's Masala Small", "box", {"piece": 1}).size is None


@pytest.mark.parametrize(
    ("text", "sku"),
    [
        ("5 ctn tapal 950", "TAP-DAN-950"),
        ("5 ctn tapal 475", "TAP-DAN-475"),
        ("2 dz surf adha kilo", "SRF-EXC-500"),
        ("2 dz surf 1 kg", "SRF-EXC-1KG"),
        ("3 peti olpers", "OLP-MLK-1L"),
        ("2 cheeni ki bori", "GEN-SUG-50KG"),
        ("1 dz head and shoulders", "HNS-SHP-360"),
        ("2 ctn liptn yelow label 950", "LIP-YEL-950"),  # typos
        ("5 carton tapal danedar bara", "TAP-DAN-950"),  # "bara" only belongs to the 950g alias
    ],
)
def test_confident_matches(matcher: Matcher, text: str, sku: str) -> None:
    result = _match(matcher, text)
    assert result.decision is Decision.ACCEPTED
    assert result.best is not None and result.best.key == sku


@pytest.mark.parametrize("text", ["1 dozen ariel", "surf 2 dz", "2 ctn pepsi"])
def test_size_less_requests_for_multi_size_products_are_ambiguous(
    matcher: Matcher, text: str
) -> None:
    result = _match(matcher, text)
    assert result.decision is Decision.AMBIGUOUS
    assert "ambiguous_product" in result.issues
    assert len(result.candidates) >= 2


def test_unknown_product_has_no_match(matcher: Matcher) -> None:
    result = _match(matcher, "5 ctn xyzqw")
    assert result.decision is Decision.NONE


def test_draft_products_are_preferred_for_edits(matcher: Matcher) -> None:
    result = _match(matcher, "tapal 5 kar do", prefer={"TAP-DAN-475"})
    assert result.best is not None and result.best.key == "TAP-DAN-475"
    assert result.decision is Decision.ACCEPTED


def test_bare_number_is_size_or_quantity(matcher: Matcher) -> None:
    sized = _match(matcher, "nido 390")
    assert sized.qty is None and "unclear_quantity" in sized.issues
    counted = _match(matcher, "lux 24")
    assert counted.qty == Decimal(24)
    assert "unit_assumed" in counted.issues  # 24 of the default unit: staff should check


def test_packet_becomes_piece_when_not_sold_by_packet(matcher: Matcher) -> None:
    result = _match(matcher, "5 packet masoor daal")
    assert (result.qty, result.unit) == (Decimal(5), "piece")
