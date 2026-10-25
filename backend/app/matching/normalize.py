"""Text normalization shared by catalog aliases and customer messages.

Both sides go through the same function, so an alias like "Surf adha kilo" and a message
like "2 dz surf adha kilo" end up in the same canonical form ("surf 500g")."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.matching.lexicon import FILLER_WORDS, PHRASES

_SPACES = re.compile(r"\s+")
# Keep letters, digits, "&" (head&shoulders), "." and "/" (1.5, 1/2) and "?" (questions).
_PUNCT = re.compile(r"[^\w\s&./?]")
# "5ctn" -> "5 ctn", "ctn5" -> "ctn 5"; sizes like "950g" are rebuilt below.
_DIGIT_LETTER = re.compile(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)")
_SIZE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(g|gm|gms|gram|grams|grm|kg|kgs|kilo|kilos|ml|l|ltr|ltrs|litre|litres|liter|liters)\b"
)
_SIZE_UNIT = {
    **dict.fromkeys(["g", "gm", "gms", "gram", "grams", "grm"], "g"),
    **dict.fromkeys(["kg", "kgs", "kilo", "kilos"], "kg"),
    "ml": "ml",
    **dict.fromkeys(["l", "ltr", "ltrs", "litre", "litres", "liter", "liters"], "l"),
}
_SIZE_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)(g|kg|ml|l)$")
_TIMES = chr(0x00D7)  # multiplication sign, as in "5 [times] tapal"
_COMPILED_PHRASES = [(re.compile(p), r) for p, r in PHRASES]


@dataclass(frozen=True)
class Size:
    """A pack size in base units: grams for weight, millilitres for volume."""

    amount: Decimal
    dimension: str  # "g" or "ml"

    @classmethod
    def from_token(cls, token: str) -> "Size | None":
        m = _SIZE_TOKEN.match(token)
        if not m:
            return None
        value, unit = Decimal(m.group(1)), m.group(2)
        if unit == "kg":
            return cls(value * 1000, "g")
        if unit == "l":
            return cls(value * 1000, "ml")
        return cls(value, unit)

    def matches_number(self, number: Decimal) -> bool:
        """A bare number like "950" or "1.5" refers to this size if it is the size in
        its display unit (950 for 950g; 1 or 1000 for 1kg; 1.5 or 1500 for 1.5l)."""
        return number in (self.amount, self.amount / 1000)


def _canonical_size(match: re.Match[str]) -> str:
    amount = match.group(1).rstrip("0").rstrip(".") if "." in match.group(1) else match.group(1)
    return f"{amount}{_SIZE_UNIT[match.group(2)]}"


def clean(text: str) -> str:
    """Lowercase, unify spacing and punctuation, canonical sizes and known phrases.
    Keeps every word, so quantities and units can still be read afterwards."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace(_TIMES, " x ")
    text = _PUNCT.sub(" ", text)
    text = _DIGIT_LETTER.sub(" ", text)
    text = _SIZE.sub(_canonical_size, text)
    for pattern, replacement in _COMPILED_PHRASES:
        text = pattern.sub(replacement, text)
    return _SPACES.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Canonical form used for aliases and exact alias lookup: cleaned, without filler words."""
    tokens = [t for t in clean(text).replace("?", " ").split() if t not in FILLER_WORDS]
    return " ".join(tokens)


def parse_number(token: str) -> Decimal | None:
    if "/" in token:
        num, _, den = token.partition("/")
        try:
            return Decimal(num) / Decimal(den)
        except (InvalidOperation, ZeroDivisionError):
            return None
    try:
        return Decimal(token)
    except InvalidOperation:
        return None
