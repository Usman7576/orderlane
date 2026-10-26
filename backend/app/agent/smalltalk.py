"""What kind of non-order message this is, so staff only see the ones that need a person.

Plain word lists, not the LLM: the answer decides whether a message reaches staff, so it
must be predictable. A message counts as small talk only when every word is small talk;
"salam, order kab aayega?" still goes to staff."""

import re
from enum import StrEnum


class ChatKind(StrEnum):
    GREETING = "greeting"  # answered automatically
    THANKS = "thanks"  # answered automatically
    ACK = "ack"  # "ok", "theek hai": nothing to answer
    COMPLAINT = "complaint"  # to staff, marked urgent
    OTHER = "other"  # to staff


GREETING_WORDS = {
    "salam", "salaam", "slam", "assalam", "asalam", "aslam", "assalamu", "assalamualaikum",
    "assalamoalaikum", "asalamualaikum", "aoa", "o", "alaikum", "alaikom", "alaykum", "walaikum",
    "hi", "hello", "helo", "hey", "adab",
}  # fmt: skip
THANKS_WORDS = {
    "thanks", "thank", "thx", "thnx", "ty", "shukriya", "shukria", "shukrya", "jazakallah",
    "jazak", "allah", "jzk", "meherbani", "mehrbani",
}  # fmt: skip
ACK_WORDS = {
    "ok", "okay", "okk", "k", "theek", "thik", "theak", "hai", "he", "acha", "achha", "accha",
    "g", "ji", "jee", "done", "fine", "noted", "sahi", "great", "good",
}  # fmt: skip
# Words that add nothing ("bhai", "sir") are dropped before deciding.
FILLER = {
    "bhai", "bhae", "bhaijan", "sir", "sahab", "sb", "jani", "boss", "you", "very", "much",
    "bohat", "bht",
}  # fmt: skip

COMPLAINT_WORDS = {
    "kharab", "kharaab", "khrab", "toota", "tuta", "tooti", "tuti", "phata", "phati", "leak",
    "expired", "expire", "expiry", "ghalat", "galat", "wrong", "damaged", "damage", "broken",
    "complaint", "shikayat", "shikayt", "missing", "refund", "wapas", "return", "nuksan",
    "nuqsan", "late", "dair", "der",
}  # fmt: skip
# "2 carton kam aaye", "rider nahi aaya", "maal nahi pohncha"
COMPLAINT_PHRASES = re.compile(
    r"\bkam\s+(?:aa?y[ae]|aya|aaya|mila|mile)\b"
    r"|\bnahi?\s+(?:aa?ya|aaya|ayi|aayi|aa?ye|pohn?cha|pahuncha|mila|mili)\b"
)

_WORD = re.compile(r"[a-z]+")
_EMOJI_ONLY = re.compile(r"^[\W_]+$")


def classify(text: str) -> ChatKind:
    lowered = text.lower()
    if COMPLAINT_PHRASES.search(lowered):
        return ChatKind.COMPLAINT
    words = [w for w in _WORD.findall(lowered) if w not in FILLER]
    if any(w in COMPLAINT_WORDS for w in words):
        return ChatKind.COMPLAINT
    if not words:
        # Only emoji or punctuation, e.g. a thumbs up.
        return ChatKind.ACK if _EMOJI_ONLY.match(lowered.strip() or ".") else ChatKind.OTHER
    small_talk = GREETING_WORDS | THANKS_WORDS | ACK_WORDS
    if not all(w in small_talk for w in words):
        return ChatKind.OTHER
    if any(w in THANKS_WORDS for w in words):
        return ChatKind.THANKS
    if any(w in GREETING_WORDS for w in words):
        return ChatKind.GREETING
    return ChatKind.ACK


def needs_staff(kind: ChatKind) -> bool:
    return kind in (ChatKind.COMPLAINT, ChatKind.OTHER)
