from decimal import Decimal

import pytest

from app.matching.normalize import Size, clean, normalize
from app.matching.parse import parse_lines, parse_quantity


class TestNormalize:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Tapal 950 GM", "tapal 950g"),
            ("tapal 950gms", "tapal 950g"),
            ("Dalda 1 Litre", "dalda 1l"),
            ("coke 1.50 ltr", "coke 1.5l"),
            ("surf adha kilo", "surf 500g"),
            ("Close-Up", "closeup"),
            ("Head n Shoulders", "head&shoulders"),
            ("7-Up", "7up"),
            ("bhai please 5ctn bhej do", "5 ctn"),
        ],
    )
    def test_canonical_forms(self, text: str, expected: str) -> None:
        assert normalize(text) == expected

    def test_clean_keeps_quantities_and_units(self) -> None:
        assert clean("Bhai, 5 CTN Tapal!") == "bhai 5 ctn tapal"

    def test_size_tokens(self) -> None:
        assert Size.from_token("1kg") == Size(Decimal(1000), "g")
        assert Size.from_token("1.5l") == Size(Decimal(1500), "ml")
        assert Size.from_token("tapal") is None

    def test_bare_number_matches_size_in_display_unit(self) -> None:
        one_kg = Size(Decimal(1000), "g")
        assert one_kg.matches_number(Decimal(1)) and one_kg.matches_number(Decimal(1000))
        assert not one_kg.matches_number(Decimal(5))


def _summary(text: str) -> list[tuple[str, Decimal | None, str | None]]:
    return [(line.mention, line.qty, line.unit) for line in parse_lines(text)]


class TestParseLines:
    def test_quantity_first_with_separators(self) -> None:
        assert _summary("5 ctn tapal 950, 2 dz surf") == [
            ("tapal", Decimal(5), "carton"),
            ("surf", Decimal(2), "dozen"),
        ]

    def test_quantity_last_without_separators(self) -> None:
        assert _summary("kurkure 2 box slanty 1 box") == [
            ("kurkure", Decimal(2), "box"),
            ("slanty", Decimal(1), "box"),
        ]

    def test_word_separators_and_slang_units(self) -> None:
        assert _summary("3 peti olpers aur 2 darjan lux") == [
            ("olpers", Decimal(3), "carton"),
            ("lux", Decimal(2), "dozen"),
        ]

    def test_number_words_and_fractions(self) -> None:
        assert _summary("paanch carton olpers") == [("olpers", Decimal(5), "carton")]
        assert _summary("aadha carton milkpak") == [("milkpak", Decimal("0.5"), "carton")]
        assert _summary("1/2 ctn milkpak") == [("milkpak", Decimal("0.5"), "carton")]

    def test_do_is_two_only_before_a_unit(self) -> None:
        assert _summary("tapal do carton") == [("tapal", Decimal(2), "carton")]
        [line] = parse_lines("tapal bhej do")
        assert line.qty is None

    def test_piece_descriptors_set_piece_and_stay_in_mention(self) -> None:
        [line] = parse_lines("10 bori sunridge atta")
        assert (line.qty, line.unit) == (Decimal(10), "piece")
        assert "bori" in line.words

    def test_sizes_and_bare_numbers_are_kept_apart(self) -> None:
        [line] = parse_lines("tapal danedar 950g 5 ctn")
        assert line.sizes == [Size(Decimal(950), "g")]
        [line] = parse_lines("lux 24")
        assert (line.qty, line.numbers) == (None, [Decimal(24)])

    def test_chatter_is_not_a_line(self) -> None:
        assert parse_lines("bhai shukriya") == []

    def test_parse_quantity_for_edits(self) -> None:
        assert parse_quantity("6 carton kar do") == (Decimal(6), "carton")
        assert parse_quantity("theek hai") == (None, None)
