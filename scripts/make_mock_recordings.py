"""Write the hand-authored mock recordings used by offline mode and the acceptance scenarios."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import init_db, make_engine  # noqa: E402
from app.models import DecisionHistory  # noqa: E402
from app.schemas.llm import ParsedRule  # noqa: E402
from app.services.learning_service import summarize  # noqa: E402
from tests.scenarios import cases  # noqa: E402

OUT = ROOT / "fixtures" / "llm_recordings"


def event(rule_id: str, label: str, category: str, **fields: object) -> dict:
    return {
        "payload": {"type": "event", "id": rule_id, "label": label, "category": category, **fields},
        "raw_text": label,
        "confidence": 0.9,
    }


WAKE_PLUS = {"anchor": {"ref": "wake_up"}}
P1_PARSE = {
    "rules": [
        {
            "payload": {
                "type": "profile_setting",
                "default_wake_by_weekday": {
                    "0": "07:00", "1": "07:00", "2": "07:00", "3": "07:00", "4": "07:00",
                    "5": "09:00", "6": "09:00",
                },
            },
            "raw_text": "wake up 7 weekdays, 9 weekends",
        },
        event("breakfast", "Breakfast", "meal", **WAKE_PLUS, offset_minutes=60,
              duration_minutes=20, skippable=True),
        event("lunch", "Lunch", "meal", anchor={"ref": "event", "id": "breakfast"},
              offset_minutes=240, duration_minutes=30, skippable=True),
        event("dinner", "Dinner", "meal", anchor={"ref": "absolute", "time": "19:00"},
              duration_minutes=30, skippable=True),
        event("meds", "Meds", "medicine", requires_food=True, timing_confirmed=False,
              anchor={"ref": "wake_up"}),
        {
            "payload": {"type": "quota", "item_label": "Gym", "target_per_week": 4,
                        "max_misses_per_month": 1, "duration_minutes": 60},
            "raw_text": "gym 4x a week",
            "needs_clarification": "How long is a gym session, and where?",
            "confidence": 0.7,
        },
        event("yoga", "Yoga", "low_priority", duration_minutes=20),
        {
            "payload": {"type": "commitment", "label": "College", "days_of_week": [0, 2],
                        "start": "10:00", "end": "14:00", "location": "College",
                        "commute_minutes": 35},
            "raw_text": "college Mon/Wed 10-2",
        },
        {
            "payload": {"type": "commitment", "label": "College", "days_of_week": [0, 2],
                        "start": "16:00", "end": "18:00", "location": "College",
                        "commute_minutes": 35},
            "raw_text": "college Mon/Wed 4-6",
        },
    ]
}  # fmt: skip
P1_QUESTIONS = {
    "questions": [
        {"key": "meds_meals", "text": "Which meals should your meds go with?"},
        {"key": "gym_length", "text": "How long is a gym session?"},
        {"key": "gym_location", "text": "Where do you go to the gym?",
         "depends_on": ["gym_length"]},
    ]
}  # fmt: skip
MEDS_SUGGESTION = {
    "summary": "Suggestion: after breakfast and after dinner, at least 6 hours apart.",
    "doses": [{"after": "breakfast"}, {"after": "dinner"}],
    "min_gap_minutes": 360,
    "requires_food": True,
}
P2_PARSE = {
    "rules": [
        event("submit_assignment", "Submit assignment", "task", duration_minutes=30,
              before_first_commitment=True),
    ]
}  # fmt: skip
E7_PARSE = {
    "rules": [{"payload": cases.DINNER_AT_9, "raw_text": cases.E7_TEXT, "confidence": 0.95}]
}
E9_PARSE = {
    "rules": [event("vitamin_d", "Vitamin D", "medicine", timing_confirmed=False,
                    anchor={"ref": "wake_up"})]
}  # fmt: skip
VITAMIN_SUGGESTION = {
    "summary": "Suggestion: once a day after breakfast.",
    "doses": [{"after": "breakfast"}],
    "requires_food": True,
}
E11_PARSE = {
    "rules": [
        {
            "payload": {"type": "day_override", "no_commitments_after": "15:00"},
            "raw_text": cases.E11_TEXT,
        }
    ]
}
E7_CONTEXT = {"rule_id": 1, "raw_text": "dinner done by 8", **cases.DINNER_BY_8}
# Parsing fills in defaults, so the stored rule is what the contradiction check is hashed on.
STORED_DINNER_AT_9 = ParsedRule(payload=cases.DINNER_AT_9).payload.model_dump(exclude_none=True)
PREP = {
    "proposals": [
        {
            "label": "Pack medicines",
            "reason": "Dinner is away from home and your medicine follows it.",
            "relative_to": "cal:dinner_friends:leave",
            "offset_minutes": -30,
        },
        {
            "label": "Take laptop to dinner",
            "reason": "Your 9:30 PM meeting has no location, and dinner may run close to it.",
            "relative_to": "cal:dinner_friends:leave",
            "offset_minutes": -5,
        },
    ]
}


def analysis_summary(history: list[dict]) -> dict:
    """Run the app's own summary code on a history, so the recording's hash matches."""
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine)() as db:
        for row in history:
            db.add(DecisionHistory(**row))
        db.commit()
        return summarize(db, date.fromisoformat(cases.ANALYSIS_DAY))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    recordings = {
        "parse-p1": ("parse", {"text": cases.P1_TEXT}, P1_PARSE),
        "questions-p1": ("questions", {"text": cases.P1_TEXT}, P1_QUESTIONS),
        "medicine-meds": ("medicine", {"label": "Meds"}, MEDS_SUGGESTION),
        "parse-p2": ("parse", {"text": cases.P2_TEXT}, P2_PARSE),
        "parse-e7": ("parse", {"text": cases.E7_TEXT}, E7_PARSE),
        "parse-e9": ("parse", {"text": cases.E9_TEXT}, E9_PARSE),
        "medicine-vitamin": ("medicine", {"label": "Vitamin D"}, VITAMIN_SUGGESTION),
        "parse-e11": ("parse", {"text": cases.E11_TEXT}, E11_PARSE),
        "contradictions-e7": (
            "contradictions",
            {"new_rule": STORED_DINNER_AT_9, "existing": [E7_CONTEXT]},
            {"contradictions": [{"existing_rule_id": 1,
                                 "explanation": "Dinner at 9 PM cannot be done by 8 PM."}]},
        ),
        "prep-p3": ("prep", {"events": cases.P3_EVENTS}, PREP),
        "classify-c7": (
            "classify",
            {"title": "Catch-up at Chipotle"},
            {"classified_as": "meal", "meal_type": "lunch", "reason": "Chipotle is a restaurant."},
        ),
    }  # fmt: skip
    for name, (history, patterns) in cases.HISTORIES.items():
        recordings[f"analyze-{name}"] = (
            "analyze",
            analysis_summary(history),
            {"patterns": patterns},
        )
    for name, (task, primary, output) in recordings.items():
        record = {"task": task, "primary": primary, "output": output}
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {len(recordings)} recordings to {OUT}")


if __name__ == "__main__":
    main()
