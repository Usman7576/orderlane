"""Which non-order messages reach staff, and which the agent answers itself."""

import pytest

from app.agent import replies
from app.agent.smalltalk import ChatKind, classify, needs_staff


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Assalam o alaikum", ChatKind.GREETING),
        ("AOA bhai", ChatKind.GREETING),
        ("hello sir", ChatKind.GREETING),
        ("ok thanks bhai", ChatKind.THANKS),
        ("JazakAllah", ChatKind.THANKS),
        ("shukriya", ChatKind.THANKS),
        ("ok", ChatKind.ACK),
        ("theek hai g", ChatKind.ACK),
        ("\U0001f44d", ChatKind.ACK),
        ("kal wala oil ka tin leak tha, bohat nuksan hua", ChatKind.COMPLAINT),
        ("pichli dafa 2 carton kam aaye the", ChatKind.COMPLAINT),
        ("aapka rider abhi tak nahi aaya", ChatKind.COMPLAINT),
        ("thanks, lekin maal kharab tha", ChatKind.COMPLAINT),
        ("salam, koi hai?", ChatKind.OTHER),
        ("mera order kab aayega", ChatKind.OTHER),
        ("payment bhej di hai", ChatKind.OTHER),
    ],
)
def test_classify(text: str, kind: ChatKind) -> None:
    assert classify(text) == kind


def test_only_complaints_and_other_messages_need_staff() -> None:
    assert [k for k in ChatKind if needs_staff(k)] == [ChatKind.COMPLAINT, ChatKind.OTHER]


def test_a_salam_is_answered_with_a_salam() -> None:
    assert replies.greeting("Assalam o alaikum").startswith("Wa alaikum assalam!")
    assert replies.greeting("hi").startswith("Hello!")
