"""A customer's open draft order and the operations that change it.

Pure functions with no database, so the same rules are used by the agent (on real orders)
and by the evaluation (on in-memory drafts)."""

from dataclasses import dataclass, field, replace
from decimal import Decimal


@dataclass(frozen=True)
class DraftLine:
    key: str | None  # product SKU; None when no product could be matched
    qty: Decimal | None
    unit: str | None
    source_text: str = ""
    needs_review: bool = False  # ambiguous product, unclear quantity, unit assumed...
    issues: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    lines: tuple[DraftLine, ...] = ()
    cancelled: bool = False

    @property
    def keys(self) -> set[str]:
        return {line.key for line in self.lines if line.key}


@dataclass(frozen=True)
class AddLine:
    line: DraftLine


@dataclass(frozen=True)
class SetLine:
    """Change quantity (and unit, if given) of the draft line for this product."""

    key: str | None
    qty: Decimal | None
    unit: str | None = None
    source_text: str = ""


@dataclass(frozen=True)
class RemoveLine:
    key: str


@dataclass(frozen=True)
class CancelOrder:
    pass


Operation = AddLine | SetLine | RemoveLine | CancelOrder


@dataclass
class ApplyResult:
    draft: Draft
    warnings: list[str] = field(default_factory=list)


def apply_operations(draft: Draft, operations: list[Operation]) -> ApplyResult:
    lines = list(draft.lines)
    cancelled = draft.cancelled
    warnings: list[str] = []

    for op in operations:
        match op:
            case CancelOrder():
                lines, cancelled = [], True
            case AddLine(line=new):
                cancelled = False
                same = next(
                    (
                        i
                        for i, old in enumerate(lines)
                        if new.key and old.key == new.key and old.unit == new.unit
                    ),
                    None,
                )
                old_qty = lines[same].qty if same is not None else None
                if same is not None and old_qty is not None and new.qty is not None:
                    lines[same] = replace(lines[same], qty=old_qty + new.qty)
                else:
                    lines.append(new)
            case SetLine(key=key, qty=qty, unit=unit, source_text=text):
                index = _find(lines, key)
                if index is None:
                    warnings.append(f"set_unknown_line:{key}")
                    lines.append(
                        DraftLine(key, qty, unit, text, needs_review=True, issues=("not_in_draft",))
                    )
                    continue
                old = lines[index]
                lines[index] = replace(old, qty=qty, unit=unit or old.unit)
            case RemoveLine(key=key):
                index = _find(lines, key)
                if index is None:
                    warnings.append(f"remove_unknown_line:{key}")
                else:
                    del lines[index]

    return ApplyResult(Draft(tuple(lines), cancelled), warnings)


def _find(lines: list[DraftLine], key: str | None) -> int | None:
    if key is None:
        return 0 if len(lines) == 1 else None  # "make it 6" with a single-line draft
    return next((i for i, line in enumerate(lines) if line.key == key), None)
