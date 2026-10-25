"""Match a parsed line to catalog products: exact alias first, then fuzzy words plus size.

The matcher never guesses silently. It returns ranked candidates and a decision:
ACCEPTED (confident), AMBIGUOUS (several close candidates, e.g. "surf" without a size)
or NONE. Ambiguous lines go to the LLM (Day 3) or to staff review."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from rapidfuzz import fuzz

from app.matching.catalog import CatalogIndex, CatalogProduct
from app.matching.lexicon import PIECE_DESCRIPTORS, PIECE_LIKE_UNITS
from app.matching.normalize import normalize
from app.matching.parse import ParsedLine

EXACT_ALIAS_SCORE = 1.2  # above any fuzzy score, so a known alias always ranks first
SIZE_MATCH_BONUS = 0.10
SIZE_MISMATCH_PENALTY = 0.30
PREFERRED_BONUS = 0.15  # products already in the customer's draft, for edits
TYPO_SIMILARITY = 0.8  # a word this similar counts as the same word ("danedr" ~ "danedar")
COVERAGE_WEIGHT = 0.6


class Decision(StrEnum):
    ACCEPTED = "accepted"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


@dataclass(frozen=True)
class MatchConfig:
    accept: float = 0.85  # minimum score to accept a fuzzy match
    margin: float = 0.08  # how far ahead of the runner-up it must be
    candidate_floor: float = 0.45  # weaker candidates are not returned


@dataclass
class Candidate:
    key: str
    score: float
    method: str  # "alias" or "fuzzy"


@dataclass
class MatchResult:
    line: ParsedLine
    decision: Decision
    candidates: list[Candidate] = field(default_factory=list)
    qty: Decimal | None = None
    unit: str | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def _size_score(line: ParsedLine, product: CatalogProduct) -> float:
    if product.size is None:
        return -SIZE_MISMATCH_PENALTY / 2 if line.sizes else 0.0
    if line.sizes:
        same = any(s == product.size for s in line.sizes)
        return SIZE_MATCH_BONUS if same else -SIZE_MISMATCH_PENALTY
    if any(product.size.matches_number(n) for n in line.numbers):
        return SIZE_MATCH_BONUS
    return 0.0


def _token_similarity(token: str, vocabulary: set[str]) -> float:
    """How well one customer word matches the product's words, allowing small typos."""
    if token in vocabulary:
        return 1.0
    best = max((fuzz.ratio(token, v) / 100 for v in vocabulary), default=0.0)
    return best if best >= TYPO_SIMILARITY else 0.0


def _word_score(words: list[str], product: CatalogProduct) -> float:
    """Two views, combined:
    - coverage: share of the customer's words found anywhere in the product's names and
      aliases ("danedar bara" is covered by the 950g pack, not by the 475g one);
    - closeness: similarity to the single closest name or alias, so extra unrelated words
      in a long product name don't count as a match."""
    # Packaging words ("bori", "tin") help when a product's aliases use them, and are
    # ignored otherwise, so "10 bori sunridge atta" isn't penalized for saying "bori".
    words = [w for w in words if w not in PIECE_DESCRIPTORS or w in product.vocabulary]
    if not words:
        return 0.0
    coverage = sum(_token_similarity(t, product.vocabulary) for t in words) / len(words)
    mention = " ".join(words)
    closeness = max(
        (fuzz.token_sort_ratio(mention, form) / 100 for form in product.word_forms), default=0.0
    )
    return COVERAGE_WEIGHT * coverage + (1 - COVERAGE_WEIGHT) * closeness


def _resolve_quantity(
    line: ParsedLine, product: CatalogProduct | None
) -> tuple[Decimal | None, str | None, list[str]]:
    """Pick the quantity and a unit the product actually has."""
    issues: list[str] = []
    qty = line.qty
    if qty is None:
        # Bare numbers are either the pack size ("tapal 950") or the quantity ("lux 24").
        leftovers = [
            n
            for n in line.numbers
            if not (product and product.size and product.size.matches_number(n))
        ]
        if leftovers:
            qty = leftovers[-1]
        else:
            issues.append("unclear_quantity")
    unit = line.unit
    if product is not None:
        if unit is None:
            unit = product.default_unit
            issues.append("unit_assumed")
        elif unit not in product.units:
            if unit in PIECE_LIKE_UNITS:
                unit = "piece"
            else:
                issues.append("unit_not_sold")
    return qty, unit, issues


class Matcher:
    def __init__(self, index: CatalogIndex, config: MatchConfig | None = None) -> None:
        self.index = index
        self.config = config or MatchConfig()

    def candidates(
        self, line: ParsedLine, prefer: set[str] | None = None, limit: int = 5
    ) -> list[Candidate]:
        prefer = prefer or set()
        exact_keys = self.index.exact.get(normalize(line.product_text), set())
        scored: list[Candidate] = []
        for product in self.index.products:
            if product.key in exact_keys:
                score, method = EXACT_ALIAS_SCORE, "alias"
            else:
                score = _word_score(line.words, product) + _size_score(line, product)
                method = "fuzzy"
            if product.key in prefer:
                score += PREFERRED_BONUS
            # Scores are not capped at 1: the size bonus must still separate two products
            # whose words match perfectly ("tapal 950" vs "tapal 475").
            if score >= self.config.candidate_floor:
                scored.append(Candidate(product.key, round(score, 4), method))
        scored.sort(key=lambda c: (-c.score, c.method != "alias", c.key))
        return scored[:limit]

    def match(self, line: ParsedLine, prefer: set[str] | None = None) -> MatchResult:
        candidates = self.candidates(line, prefer)
        if not candidates:
            qty, unit, issues = _resolve_quantity(line, None)
            return MatchResult(line, Decision.NONE, [], qty, unit, [*issues, "no_match"])

        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        runner_up = second.score if second else 0.0
        unique_alias = best.method == "alias" and (second is None or second.method != "alias")
        confident = (
            best.score >= self.config.accept and best.score - runner_up >= self.config.margin
        )
        decision = Decision.ACCEPTED if unique_alias or confident else Decision.AMBIGUOUS

        product = self.index.by_key[best.key]
        qty, unit, issues = _resolve_quantity(line, product)
        if decision is Decision.AMBIGUOUS:
            issues.append("ambiguous_product")
        return MatchResult(line, decision, candidates, qty, unit, issues)
