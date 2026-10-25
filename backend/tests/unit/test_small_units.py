from decimal import Decimal

from app.log import mask_phone
from app.matching.normalize import normalize
from app.services.seeding import parse_units, unit_price


def test_mask_phone_keeps_last_four_digits() -> None:
    assert mask_phone("923001234567") == "********4567"
    assert mask_phone("customer 923001234567 wrote") == "customer ********4567 wrote"


def test_normalize_lowercases_and_strips_punctuation() -> None:
    assert normalize("  Close-Up  Toothpaste!! ") == "close up toothpaste"
    assert normalize("Head & Shoulders 360ml") == "head & shoulders 360ml"


def test_parse_units_always_includes_piece() -> None:
    assert parse_units("carton:12;dozen:12") == {"piece": 1, "carton": 12, "dozen": 12}
    assert parse_units("") == {"piece": 1}


def test_unit_price_applies_tier_and_carton_discounts() -> None:
    piece = Decimal("100")
    assert unit_price(piece, "piece", 1, "A") == Decimal("100")
    assert unit_price(piece, "dozen", 12, "A") == Decimal("1200")
    assert unit_price(piece, "dozen", 12, "B") == Decimal("1164")
    assert unit_price(piece, "carton", 12, "A") == Decimal("1176")
