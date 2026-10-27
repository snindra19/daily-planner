"""Indirect conflicts and duplicates between rules, commitments, and calendar events."""

from __future__ import annotations

from rapidfuzz import fuzz

from app.schemas.rules import EventRule
from app.schemas.scheduling import (
    CalendarEvent,
    Commitment,
    Conflict,
    format_duration,
    to_hhmm,
    to_minutes,
)

DUPLICATE_TITLE_SCORE = 70


def find_indirect_conflicts(
    rules: list[EventRule], commitments: list[Commitment], weekday: int
) -> list[Conflict]:
    """Report clock-time rules that can never fit a commitment plus its commute on this weekday."""
    conflicts: list[Conflict] = []
    todays = [c for c in commitments if weekday in c.days_of_week]
    for rule in rules:
        if not isinstance(rule, EventRule) or rule.anchor is None or rule.anchor.ref != "absolute":
            continue
        if weekday not in rule.days_of_week or rule.category in ("meal", "medicine"):
            continue
        start = to_minutes(rule.anchor.time) + rule.offset_minutes
        end = start + rule.duration_minutes
        for commitment in todays:
            busy_start = to_minutes(commitment.start) - commitment.commute_minutes
            busy_end = to_minutes(commitment.end) + commitment.commute_minutes
            if start < busy_end and busy_start < end:
                conflicts.append(_conflict(rule, commitment, start, busy_end))
                break
    return conflicts


def _conflict(rule: EventRule, commitment: Commitment, start: int, busy_end: int) -> Conflict:
    commute = commitment.commute_minutes
    detail = f"{commitment.label} until {commitment.end}"
    if commute:
        detail += f" plus {format_duration(commute)} commute"
    return Conflict(
        kind="indirect",
        message=(
            f"{rule.label} at {to_hhmm(start)} can never fit: {detail} "
            f"keeps you busy until {to_hhmm(busy_end)}."
        ),
        rule_ids=[rule.id, commitment.id],
        options=[
            f"Move {rule.label} to {to_hhmm(busy_end)} or later",
            f"Move {rule.label} to another day",
            f"Remove the {rule.label} rule",
        ],
    )


def find_duplicates(
    commitments: list[Commitment], events: list[CalendarEvent]
) -> tuple[list[CalendarEvent], list[Conflict]]:
    """Return events that do not duplicate a commitment, plus a flag for each duplicate."""
    kept: list[CalendarEvent] = []
    conflicts: list[Conflict] = []
    for event in events:
        start = event.start.hour * 60 + event.start.minute
        end = event.end.hour * 60 + event.end.minute
        match = next((c for c in commitments if _same_block(c, event, start, end)), None)
        if match is None:
            kept.append(event)
            continue
        conflicts.append(
            Conflict(
                kind="duplicate",
                message=(
                    f'Calendar event "{event.title}" duplicates {match.label} '
                    f"({match.start} to {match.end}). Counted once."
                ),
                rule_ids=[match.id, event.source_id],
            )
        )
    return kept, conflicts


def _same_block(commitment: Commitment, event: CalendarEvent, start: int, end: int) -> bool:
    if start != to_minutes(commitment.start) or end != to_minutes(commitment.end):
        return False
    return (
        fuzz.partial_ratio(commitment.label.lower(), event.title.lower()) >= DUPLICATE_TITLE_SCORE
    )
