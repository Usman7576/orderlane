"""Text normalization for catalog matching. Day 1 keeps it minimal; Day 2 adds units,
number words and filler-word removal."""

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s&.]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()
