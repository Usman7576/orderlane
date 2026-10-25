"""Where an order goes next. Fixed rules, never the LLM: anything involving money, stock or
uncertainty goes to a person. The customer always confirms before an order is final."""

from app.ordering.draft import Draft

ASK_CUSTOMER = "ask_customer"
STAFF_REVIEW = "staff_review"
ANSWER = "answer"
HANDOFF = "handoff"
CANCELLED = "cancelled"

# Order-level issues that always need a person.
REVIEW_ISSUES = {
    "over_credit_limit",
    "first_order",
    "large_order",
    "low_confidence",
    "llm_unavailable",
    "out_of_stock",
    "no_price",
}


def decide_route(intent: str, draft: Draft, issues: list[str]) -> str:
    if intent == "question":
        return ANSWER
    if intent == "cancel":
        return CANCELLED
    if intent == "other" or not draft.lines:
        return HANDOFF
    if any(line.needs_review for line in draft.lines) or REVIEW_ISSUES & set(issues):
        return STAFF_REVIEW
    return ASK_CUSTOMER
