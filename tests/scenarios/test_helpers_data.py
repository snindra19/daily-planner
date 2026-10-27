"""Small fixtures shared by the scheduler scenarios."""

from __future__ import annotations

from collections.abc import Callable

from tests.helpers import load

MONDAY = "2026-11-02"
_routine = load("golden_16_5")
MEDS_ROUTINE = {
    "date": MONDAY,
    "wake": "08:00",
    "weights": _routine["weights"],
    "rules": [r for r in _routine["rules"] if r["id"] not in ("gym", "yoga")],
    "food_options": _routine["food_options"],
}


def absolute(hhmm: str) -> dict:
    return {"ref": "absolute", "time": hhmm}


def with_rules(data: dict, change: Callable[[dict], dict]) -> dict:
    return {**data, "rules": [change(dict(r)) for r in data["rules"]]}


def calendar(*events: tuple) -> dict:
    """Build calendar_events from (id, title, start, end, kind, meal_type[, location]) tuples."""
    rows = []
    for event in events:
        source_id, title, start, end, kind, meal_type, *location = event
        rows.append(
            {
                "source_id": source_id,
                "title": title,
                "start": f"{MONDAY}T{start}:00",
                "end": f"{MONDAY}T{end}:00",
                "classified_as": kind,
                "meal_type": meal_type,
                "location": location[0] if location else None,
            }
        )
    return {"calendar_events": rows}


def busy_day() -> dict:
    """A day with no room for gym, whatever the time."""
    return {
        "date": MONDAY,
        "wake": "07:00",
        "rules": [
            {
                "type": "event",
                "id": "gym",
                "label": "Gym",
                "category": "quota",
                "duration_minutes": 60,
            }
        ],
        "commitments": [
            {"id": "work", "label": "Work", "days_of_week": [0], "start": "06:00", "end": "23:30"}
        ],
        "quotas": [{"item_label": "Gym", "target_per_week": 4, "max_misses_per_month": 1}],
    }
