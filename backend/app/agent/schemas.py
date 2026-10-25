"""Structured outputs the LLM must return. Every field is required (nullable where optional),
which is what JSON-schema strict mode expects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntentName = Literal["new_order", "add", "modify", "cancel", "question", "other"]
UnitName = Literal["carton", "dozen", "box", "packet", "piece"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineInstruction(_Strict):
    operation: Literal["add", "set", "remove"] = Field(
        description="add: new item or more of it; set: change the quantity; remove: drop the item"
    )
    product_mention: str = Field(
        description="The product as the customer named it, in Latin letters, with brand and "
        "size words (e.g. 'tapal danedar 950g'). Transliterate Urdu script."
    )
    quantity: float | None = Field(description="Number of units; null if not stated")
    unit: UnitName | None = Field(description="Packaging unit; null if not stated")


class Interpretation(_Strict):
    intent: IntentName
    lines: list[LineInstruction]
    question_topic: Literal["price", "stock", "balance", "other"] | None = Field(
        description="Only for intent=question"
    )
    confidence: Literal["high", "medium", "low"]


class ProductChoice(_Strict):
    candidate_id: str | None = Field(
        description="The id of the matching candidate, or null if none clearly matches"
    )
    reason: str = Field(description="One short sentence")
