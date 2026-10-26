"""Rule-based parsing of an order message into lines: quantity, unit, size and product words.

Handles both styles shops use: quantity first ("5 ctn tapal 950") and quantity last
("tapal 950 5 ctn"), separated by commas, new lines, "aur"/"and", or nothing at all."""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.matching.lexicon import (
    AMBIGUOUS_NUMBER_WORDS,
    FILLER_WORDS,
    NUMBER_WORDS,
    PIECE_DESCRIPTORS,
    UNIT_WORDS,
)
from app.matching.normalize import Size, clean, parse_number

# Punctuation separators are split before cleaning (cleaning removes commas);
# word separators after it.
_PUNCT_SEPARATORS = re.compile(r"[\n\r,;+]")
_WORD_SEPARATORS = re.compile(r"\s(?:aur|and|&|also|plus|or|sath|saath)\s")


@dataclass
class ParsedLine:
    raw: str
    words: list[str]  # product words for matching (no numbers, units or filler words)
    qty: Decimal | None = None
    unit: str | None = None
    sizes: list[Size] = field(default_factory=list)
    numbers: list[Decimal] = field(default_factory=list)  # bare numbers: a size or a quantity
    # Everything that describes the product, in the customer's order ("tapal 950"), for exact
    # alias lookup. Quantity, unit and filler words are left out.
    product_text: str = ""

    @property
    def mention(self) -> str:
        return " ".join(self.words)


@dataclass
class _Token:
    text: str
    number: Decimal | None = None
    unit: str | None = None
    size: Size | None = None


def _tokenize(segment: str) -> list[_Token]:
    words = segment.split()
    tokens: list[_Token] = []
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        item = _Token(word)
        if (size := Size.from_token(word)) is not None:
            item.size = size
        elif (number := parse_number(word)) is not None:
            item.number = number
        elif word in NUMBER_WORDS:
            item.number = NUMBER_WORDS[word]
        elif word in AMBIGUOUS_NUMBER_WORDS and (nxt in UNIT_WORDS or i == 0):
            item.number = AMBIGUOUS_NUMBER_WORDS[word]
        elif word in UNIT_WORDS:
            item.unit = UNIT_WORDS[word]
        tokens.append(item)
    return tokens


def _anchors(tokens: list[_Token]) -> list[int]:
    """Indexes of numbers directly followed by a unit word ("5 ctn")."""
    return [
        i
        for i in range(len(tokens) - 1)
        if tokens[i].number is not None and tokens[i + 1].unit is not None
    ]


def _split_on_quantities(tokens: list[_Token]) -> list[list[_Token]]:
    """Split "5 ctn tapal 2 dz surf" (no separators) into one group per quantity."""
    anchors = _anchors(tokens)
    if len(anchors) < 2:
        return [tokens]
    # Quantity first: each group starts at a quantity. Quantity last: each group ends
    # after "number unit".
    starts = anchors if anchors[0] == 0 else [0, *[a + 2 for a in anchors[:-1]]]
    bounds = [*starts, len(tokens)]
    return [
        tokens[bounds[i] : bounds[i + 1]]
        for i in range(len(starts))
        if tokens[bounds[i] : bounds[i + 1]]
    ]


def _parse_group(tokens: list[_Token], raw: str) -> ParsedLine | None:
    line = ParsedLine(raw=raw, words=[])
    used: set[int] = set()

    for i in _anchors(tokens):  # "5 ctn"
        line.qty, line.unit = tokens[i].number, tokens[i + 1].unit
        used |= {i, i + 1}
        break
    if line.unit is None:  # unit without a number: "dozen surf" means 1 dozen
        for i, t in enumerate(tokens):
            if t.unit is not None:
                line.unit = t.unit
                used.add(i)
                break
    if line.qty is None and tokens and tokens[0].number is not None and 0 not in used:
        line.qty = tokens[0].number  # "5 tapal 950": a leading number is the quantity
        used.add(0)

    product_tokens: list[str] = []
    for i, t in enumerate(tokens):
        if i in used or t.text in FILLER_WORDS or t.text == "?":
            continue
        product_tokens.append(t.text)
        if t.size is not None:
            line.sizes.append(t.size)
        elif t.number is not None:
            line.numbers.append(t.number)
        elif t.text in PIECE_DESCRIPTORS:
            line.words.append(t.text)
            line.unit = line.unit or "piece"
        else:
            line.words.append(t.text)
    line.product_text = " ".join(product_tokens)

    if line.unit is not None and line.qty is None and not line.numbers:
        line.qty = Decimal(1)
    if not line.words and not line.sizes:
        return None  # nothing that names a product ("bas", "thanks", a lone number)
    return line


def parse_lines(text: str) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    segments = [
        segment.strip()
        for part in _PUNCT_SEPARATORS.split(text)
        for segment in _WORD_SEPARATORS.split(f" {clean(part)} ")
    ]
    for segment in filter(None, segments):
        for group in _split_on_quantities(_tokenize(segment)):
            raw = " ".join(t.text for t in group)
            parsed = _parse_group(group, raw)
            if parsed is not None:
                lines.append(parsed)
    return lines


def parse_quantity(text: str) -> tuple[Decimal | None, str | None]:
    """The first quantity in a message with no product words, e.g. "6 carton kar do"."""
    tokens = _tokenize(clean(text))
    for i, item in enumerate(tokens):
        if item.number is not None:
            unit = tokens[i + 1].unit if i + 1 < len(tokens) else None
            return item.number, unit
    return None, None
