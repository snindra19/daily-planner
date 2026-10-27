"""Inputs and outputs of the pure scheduler."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.rules import EventRule, SeriesRule

DEFAULT_WEIGHTS = {
    "per_minute_delay": 1,
    "during_busy": 60,
    "meal_downgraded": 20,
    "meal_skipped": 40,
}
DEFAULT_PRIORITIES = ["fixed", "medicines", "meals", "quota", "low_priority"]


def to_minutes(hhmm: str) -> int:
    """Convert "HH:MM" to minutes after midnight."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def to_hhmm(minutes: int) -> str:
    """Convert minutes after midnight to "HH:MM"."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_duration(minutes: int) -> str:
    """Format a duration the way the UI shows it, such as "1h" or "1h 30m"."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest}m"
    return f"{hours}h" if hours else f"{rest}m"


class Commitment(BaseModel):
    """A fixed recurring block such as a class, work, or worship."""

    id: str
    label: str
    days_of_week: list[int]
    start: str
    end: str
    location: str | None = None
    commute_minutes: int = 0
    in_window_kind: str = "class"
    breaks: list[dict[str, str]] = Field(default_factory=list)


class FoodOption(BaseModel):
    """A user-approved substitute for a meal."""

    id: str
    label: str
    duration_minutes: int
    allowed_during_busy: bool = False
    counts_as_meal: bool = False
    approved: bool = True


class Quota(BaseModel):
    """A weekly target with a monthly miss limit."""

    item_label: str
    target_per_week: int
    max_misses_per_month: int = 0


class QuotaRecord(BaseModel):
    """One completion record for a quota item."""

    date: str
    item_label: str
    status: Literal["done", "not_done", "skipped_by_user"]


class CalendarEvent(BaseModel):
    """A calendar event mapped to the app's shape and already classified."""

    source_id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    busy: bool = True
    all_day: bool = False
    classified_as: Literal["meal", "meeting", "ignore"] = "meeting"
    meal_type: str | None = None


class DayOverrides(BaseModel):
    """Changes that apply to a single date."""

    no_commitments_after: str | None = None
    removed_commitments: list[str] = Field(default_factory=list)
    approval_mode: Literal["restricted", "unrestricted"] | None = None
    late_mode: Literal["override", "continue"] | None = None
    restart_at: str | None = None
    reorder: list[dict[str, str]] = Field(default_factory=list)
    extra_rules: list[EventRule | SeriesRule] = Field(default_factory=list)


class StandingApproval(BaseModel):
    """Auto-allow proposals whose context contains every key in `match`."""

    match: dict[str, Any]
    action: dict[str, Any] = Field(default_factory=lambda: {"choice": "top"})


class Decision(BaseModel):
    """The user's answer to a proposal, keyed by proposal key."""

    choice: str | None = None
    rejected: bool = False
    suppress_auto: bool = False


class ProfileInput(BaseModel):
    """Profile values the scheduler reads."""

    sleep_time: str = "23:00"
    priority_order: list[str] = Field(default_factory=lambda: list(DEFAULT_PRIORITIES))
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    approval_mode: Literal["restricted", "unrestricted"] = "restricted"
    meal_merge_gap_minutes: int = 120
    default_event_commute_minutes: int = 30
    recommend_first: dict[str, str] = Field(default_factory=dict)
    home_words: list[str] = Field(default_factory=lambda: ["home"])


class Option(BaseModel):
    """One way of resolving a proposal."""

    id: str
    label: str
    score: float = 0
    breakdown: dict[str, float] = Field(default_factory=dict)
    placements: dict[str, dict[str, Any]] = Field(default_factory=dict)
    meal_skipped: str | None = None


class Proposal(BaseModel):
    """A change that needs the user, or was auto-applied under a mode or standing approval."""

    key: str
    kind: str
    summary: str
    reason: str
    item_keys: list[str] = Field(default_factory=list)
    chosen: Option
    alternatives: list[Option] = Field(default_factory=list)
    state: Literal["pending", "approved", "rejected", "auto", "standing"] = "pending"
    context: dict[str, Any] = Field(default_factory=dict)


class PlanItem(BaseModel):
    """One scheduled item. Times are minutes after midnight."""

    key: str
    label: str
    start: int
    end: int
    category: str
    tier: int = 5
    status: Literal["scheduled", "proposed", "auto", "done", "not_done", "fixed"] = "scheduled"
    silent: bool = False
    proposal_key: str | None = None
    rule_id: str | None = None
    promoted: bool = False
    flags: list[str] = Field(default_factory=list)

    @property
    def start_hhmm(self) -> str:
        """Start time as "HH:MM"."""
        return to_hhmm(self.start)

    @property
    def end_hhmm(self) -> str:
        """End time as "HH:MM"."""
        return to_hhmm(self.end)


class PlanRow(BaseModel):
    """A displayed row. Point items at the same minute share one row."""

    start: int
    end: int
    label: str
    item_keys: list[str]

    @property
    def start_hhmm(self) -> str:
        """Start time as "HH:MM"."""
        return to_hhmm(self.start)


class UnslottedItem(BaseModel):
    """An item that fits nowhere. Never dropped silently."""

    key: str
    label: str
    duration_minutes: int
    message: str
    recommendations: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    """A problem the user should see, with options where there are any."""

    kind: str
    message: str
    rule_ids: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class LateOpen(BaseModel):
    """Whether the late-open panel is needed and for which items."""

    needed: bool = False
    pending_keys: list[str] = Field(default_factory=list)


class PlanResult(BaseModel):
    """Everything build_plan returns."""

    date: str
    wake: int
    items: list[PlanItem] = Field(default_factory=list)
    rows: list[PlanRow] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    unslotted: list[UnslottedItem] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    needs_confirmation: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skip_warnings: dict[str, str] = Field(default_factory=dict)
    surfaced_events: list[str] = Field(default_factory=list)
    late_open: LateOpen = Field(default_factory=LateOpen)
    explanations_data: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def pending_approvals(self) -> list[Proposal]:
        """Proposals still waiting for the user."""
        return [p for p in self.proposals if p.state == "pending"]

    @property
    def auto_applied(self) -> list[Proposal]:
        """Proposals applied automatically in unrestricted mode."""
        return [p for p in self.proposals if p.state == "auto"]

    def item(self, key: str) -> PlanItem | None:
        """Return the item with this key, if any."""
        return next((i for i in self.items if i.key == key), None)
