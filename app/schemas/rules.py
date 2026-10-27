"""Rule payloads stored as JSON in the rules table (spec section 8)."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_DURATIONS = {"meal": 30, "medicine": 1, "quota": 60, "low_priority": 20}
MEAL_KEYWORDS = {
    "breakfast": "breakfast",
    "brunch": "breakfast",
    "lunch": "lunch",
    "dinner": "dinner",
    "supper": "dinner",
}


def slugify(text: str) -> str:
    """Return a stable lowercase id for a label."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class Anchor(BaseModel):
    """Where an item's ideal time comes from."""

    ref: Literal["wake_up", "event", "occurrence", "absolute"]
    id: str | None = None
    selector: Literal["first", "last", "nth"] | None = None
    n: int | None = None
    time: str | None = None


class EventRule(BaseModel):
    """A single item in the day."""

    type: Literal["event"] = "event"
    id: str | None = None
    label: str
    category: Literal["meal", "medicine", "quota", "low_priority", "task", "routine"] = "routine"
    anchor: Anchor | None = None
    offset_minutes: int = 0
    after_end: bool = False
    duration_minutes: int | None = None
    skippable: bool = False
    days_of_week: list[int] = Field(default_factory=lambda: list(range(7)))
    requires_food: bool = False
    food_source_ref: str | None = None
    min_gap_minutes: int | None = None
    tolerance_minutes: int | None = None
    allowed_during_busy: bool = False
    timing_confirmed: bool = True
    dose_group: str | None = None
    meal_type: str | None = None
    location: str | None = None
    deadline: str | None = None
    before_first_commitment: bool = False
    only_date: str | None = None

    @model_validator(mode="after")
    def _fill_defaults(self) -> EventRule:
        self.id = self.id or slugify(self.label)
        if self.duration_minutes is None:
            self.duration_minutes = DEFAULT_DURATIONS.get(self.category, 15)
        if self.category == "meal" and self.meal_type is None:
            lowered = self.label.lower()
            self.meal_type = next((v for k, v in MEAL_KEYWORDS.items() if k in lowered), None)
        return self


class Recurrence(BaseModel):
    """Repeat settings for a series."""

    interval_minutes: int
    count: int
    start_mode: Literal["after_anchor", "at_anchor"] = "after_anchor"


class SeriesRule(BaseModel):
    """An item repeated at a fixed interval from an anchor."""

    type: Literal["series"] = "series"
    id: str | None = None
    label: str
    category: Literal["medicine", "routine", "task"] = "medicine"
    anchor: Anchor
    offset_minutes: int = 0
    recurrence: Recurrence
    duration_minutes: int = 1
    allowed_during_busy: bool = False
    requires_food: bool = False
    food_source_ref: str | None = None
    min_gap_minutes: int | None = None
    tolerance_minutes: int | None = None
    timing_confirmed: bool = True
    dose_group: str | None = None
    days_of_week: list[int] = Field(default_factory=lambda: list(range(7)))
    only_date: str | None = None

    @model_validator(mode="after")
    def _fill_id(self) -> SeriesRule:
        self.id = self.id or slugify(self.label)
        return self


class ConstraintRule(BaseModel):
    """A time window for an event, with what to do when it cannot be met."""

    type: Literal["constraint"] = "constraint"
    id: str | None = None
    event_ref: str
    days_of_week: list[int] = Field(default_factory=lambda: list(range(7)))
    must_start_after: str | None = None
    must_end_by: str | None = None
    on_violation: Literal["substitute", "propose_move"] = "propose_move"
    substitute_label: str | None = None
    only_date: str | None = None

    @model_validator(mode="after")
    def _fill_id(self) -> ConstraintRule:
        self.id = self.id or f"constraint_{self.event_ref}"
        return self


class PreferenceRule(BaseModel):
    """A user preference, such as scoring weights."""

    type: Literal["preference"] = "preference"
    id: str | None = None
    key: str
    value: dict | list | str | int | float | bool

    @model_validator(mode="after")
    def _fill_id(self) -> PreferenceRule:
        self.id = self.id or f"preference_{self.key}"
        return self


RulePayload = Annotated[
    EventRule | SeriesRule | ConstraintRule | PreferenceRule, Field(discriminator="type")
]
