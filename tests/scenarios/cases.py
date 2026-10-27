"""Inputs shared by the scenarios and the mock recording generator, so their hashes agree."""

from __future__ import annotations

P1_TEXT = (
    "wake up 7 weekdays, 9 weekends, 3 meals, meds twice a day after food, gym 4x a week, "
    "yoga when possible, college Mon/Wed 10-2 and 4-6, commute 35 min"
)
P2_TEXT = "submit assignment before class"
E7_TEXT = "dinner at 9 PM"
E9_TEXT = "new medicine: vitamin D"
E11_TEXT = "No class tomorrow afternoon"

DINNER_BY_8 = {
    "type": "constraint",
    "id": "dinner_window",
    "event_ref": "dinner",
    "must_end_by": "20:00",
    "on_violation": "propose_move",
}
DINNER_AT_9 = {
    "type": "event",
    "id": "dinner_at_9",
    "label": "Dinner",
    "category": "meal",
    "anchor": {"ref": "absolute", "time": "21:00"},
    "duration_minutes": 30,
}

P3_EVENTS = [
    {
        "source_id": "dinner_friends",
        "title": "Dinner with friends",
        "start": "2026-11-03T20:00:00",
        "end": "2026-11-03T21:00:00",
        "location": "Andres Carne de Res",
    },
    {
        "source_id": "late_meeting",
        "title": "Project meeting",
        "start": "2026-11-03T21:30:00",
        "end": "2026-11-03T22:00:00",
        "location": None,
    },
]

SNACK_AFTER = "meal_nearest_slot"
APPLE_DURING = "food:apple:during_busy"
FALLBACK_CONTEXT = {
    "kind": "fallback",
    "category": "medicine",
    "meal": "lunch",
    "block_kind": "meeting",
}


def decision(day: int, kind: str, context: dict, chosen: str | None, signal: str) -> dict:
    """Build one decision_history row for November 2026."""
    return {
        "date": f"2026-11-{day:02d}",
        "kind": kind,
        "context": context,
        "chosen": chosen,
        "signal": signal,
    }


L1_HISTORY = [
    *[
        decision(d, "fallback", FALLBACK_CONTEXT, SNACK_AFTER, "picked_alternative")
        for d in range(1, 7)
    ],
    decision(7, "fallback", FALLBACK_CONTEXT, APPLE_DURING, "approved"),
]
L2_HISTORY = [
    decision(d, "fallback", FALLBACK_CONTEXT, SNACK_AFTER, "picked_alternative") for d in (1, 2)
]
LAPTOP_CONTEXT = {"kind": "prep_reminder", "label": "Take laptop to dinner"}
L3_HISTORY = [decision(d, "prep_reminder", LAPTOP_CONTEXT, None, "rejected") for d in range(1, 5)]
YOGA_SKIP_CONTEXT = {"kind": "skip", "item": "yoga"}
L5_HISTORY = [decision(d, "skip", YOGA_SKIP_CONTEXT, "yoga", "manual_skip") for d in range(1, 6)]

WEIGHT_PATTERN = {
    "description": "Picks food after the meeting over food during the meeting",
    "kind": "fallback",
    "context": {"meal": "lunch", "block_kind": "meeting"},
    "consistent_signal": "picked_alternative",
    "consistent_chosen": SNACK_AFTER,
    "effect": {"type": "weight", "key": "during_busy_food", "value": 150},
}
LAPTOP_PATTERN = {
    "description": "Rejects the take laptop reminder",
    "kind": "prep_reminder",
    "context": {"label": "Take laptop to dinner"},
    "consistent_signal": "rejected",
    "effect": {"type": "dont_propose", "label": "Take laptop to dinner"},
}
YOGA_PATTERN = {
    "description": "Usually skips yoga",
    "kind": "skip",
    "context": {"item": "yoga"},
    "consistent_signal": "manual_skip",
    "effect": {"type": "recommendation", "item": "yoga", "text": "Skip Yoga"},
}
HISTORIES = {
    "l1": (L1_HISTORY, [WEIGHT_PATTERN]),
    "l2": (L2_HISTORY, [WEIGHT_PATTERN]),
    "l3": (L3_HISTORY, [LAPTOP_PATTERN]),
    "l5": (L5_HISTORY, [YOGA_PATTERN]),
}
ANALYSIS_DAY = "2026-11-20"
