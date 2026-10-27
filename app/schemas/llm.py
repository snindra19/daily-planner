"""Structured outputs of each LLM task. Each model's JSON schema is a tool input_schema."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.schemas.rules import ConstraintRule, EventRule, PreferenceRule, SeriesRule


class CommitmentDraft(BaseModel):
    """A class, job, or worship block."""

    type: Literal["commitment"] = "commitment"
    label: str
    days_of_week: list[int]
    start: str
    end: str
    location: str | None = None
    commute_minutes: int = 0
    in_window_kind: str = "class"


class FoodOptionDraft(BaseModel):
    """A substitute food the user accepts."""

    type: Literal["food_option"] = "food_option"
    label: str
    duration_minutes: int = 15
    allowed_during_busy: bool = False


class QuotaDraft(BaseModel):
    """A recurring goal such as gym four times a week."""

    type: Literal["quota"] = "quota"
    item_label: str
    target_per_week: int
    max_misses_per_month: int = 0
    duration_minutes: int = 60
    location: str | None = None


class ProfileDraft(BaseModel):
    """Profile settings stated in text. Weekday keys are 0 (Monday) to 6 (Sunday)."""

    type: Literal["profile_setting"] = "profile_setting"
    default_wake_by_weekday: dict[str, str] = Field(default_factory=dict)
    sleep_time: str | None = None


class DayOverrideDraft(BaseModel):
    """A change for one date. `date` may be left empty to mean the date being reviewed."""

    type: Literal["day_override"] = "day_override"
    date: str | None = None
    no_commitments_after: str | None = None
    removed_commitments: list[str] = Field(default_factory=list)


DraftPayload = Annotated[
    EventRule
    | SeriesRule
    | ConstraintRule
    | PreferenceRule
    | CommitmentDraft
    | FoodOptionDraft
    | QuotaDraft
    | ProfileDraft
    | DayOverrideDraft,
    Field(discriminator="type"),
]


class ParsedRule(BaseModel):
    """One draft rule card."""

    payload: DraftPayload
    raw_text: str = ""
    confidence: float = 1.0
    needs_clarification: str | None = None


class ParseResult(BaseModel):
    """Everything found in the user's text."""

    rules: list[ParsedRule]


class QuestionDraft(BaseModel):
    """A follow-up question. depends_on lists other question keys."""

    key: str
    text: str
    condition: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class QuestionsResult(BaseModel):
    """Follow-up questions for gaps in the rules."""

    questions: list[QuestionDraft]


class Contradiction(BaseModel):
    """A direct contradiction between the new rule and an existing one."""

    existing_rule_id: int
    explanation: str


class ContradictionsResult(BaseModel):
    """Direct contradictions only. Rules that merely never fit are not reported."""

    contradictions: list[Contradiction]


class PrepProposal(BaseModel):
    """A prep task nobody stated. The time is relative to another item; code computes the clock."""

    label: str
    reason: str
    relative_to: str
    offset_minutes: int = 0
    duration_minutes: int = 5


class PrepResult(BaseModel):
    """Prep proposals for a day."""

    proposals: list[PrepProposal]


class MedicineDose(BaseModel):
    """One suggested dose, taken after the named meal id."""

    after: str


class MedicineSuggestion(BaseModel):
    """A timing suggestion for a medicine. Never scheduled until the user confirms."""

    summary: str
    doses: list[MedicineDose]
    min_gap_minutes: int | None = None
    requires_food: bool = True


class ClassificationResult(BaseModel):
    """What an unclear calendar title is."""

    classified_as: Literal["meal", "meeting", "ignore"]
    meal_type: Literal["breakfast", "lunch", "dinner"] | None = None
    reason: str = ""


class ExplanationResult(BaseModel):
    """A one-line explanation of a scheduler decision."""

    text: str


class PatternCandidate(BaseModel):
    """A pattern in decision history. Code re-counts the evidence; these fields say what to count."""

    description: str
    kind: str
    context: dict[str, Any] = Field(default_factory=dict)
    consistent_signal: str | None = None
    consistent_chosen: str | None = None
    effect: dict[str, Any]


class PatternsResult(BaseModel):
    """Candidate patterns found in a history summary."""

    patterns: list[PatternCandidate]
