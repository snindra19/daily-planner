"""Milestone 3 live check: run every LLM task once against the real API and validate the output.

Prints each task's result and token use. Never prints the API key. Needs LLM_MODE=live.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.llm import tasks  # noqa: E402
from app.llm.client import LiveClient, LLMError  # noqa: E402
from tests.scenarios import cases  # noqa: E402


def checks(client: LiveClient) -> list[tuple[str, object]]:
    """Run each task and return (name, check result)."""
    parsed = tasks.parse(client, cases.P1_TEXT, [])
    kinds = sorted({r.payload.type for r in parsed.rules})
    meds = [r.payload for r in parsed.rules if getattr(r.payload, "category", "") == "medicine"]
    found = tasks.questions(
        client, cases.P1_TEXT, [r.payload.model_dump() for r in parsed.rules], []
    )
    contradiction = tasks.contradictions(
        client,
        cases.DINNER_AT_9,
        [{"rule_id": 1, "raw_text": "dinner done by 8", **cases.DINNER_BY_8}],
    )
    keys = ["cal:dinner_friends:leave", "cal:late_meeting", "dinner_med"]
    prep = tasks.prep(client, cases.P3_EVENTS, {"item_keys": keys, "plan": [], "rules": []})
    medicine = tasks.medicine(client, "Vitamin D", [])
    classified = tasks.classify(client, "Catch-up at Chipotle")
    explained = tasks.explain(
        client,
        {
            "summary": "Lunch moved to 14:00",
            "score": 60,
            "breakdown": {"delay": 60},
            "alternatives": [{"label": "Apple during class", "score": 80}],
        },
    )
    patterns = tasks.analyze(
        client,
        {
            "window_days": 60,
            "groups": [
                {
                    "kind": "prep_reminder",
                    "context": {"label": "Take laptop"},
                    "signal": "rejected",
                    "chosen": None,
                    "count": 4,
                }
            ],
        },
    )
    return [
        ("parse finds rule types", kinds),
        ("parse leaves meds timing unconfirmed", [m.timing_confirmed for m in meds]),
        ("questions", [q.text for q in found.questions]),
        ("contradictions", [c.explanation for c in contradiction.contradictions]),
        ("prep", [(p.label, p.relative_to, p.offset_minutes) for p in prep.proposals]),
        ("medicine", medicine.summary),
        ("classify", (classified.classified_as, classified.meal_type)),
        ("explain", explained.text),
        ("analyze", [(p.description, p.effect) for p in patterns.patterns]),
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    if settings.llm_mode != "live":
        sys.exit("Set LLM_MODE=live for this check.")
    try:
        for name, value in checks(LiveClient(settings)):
            print(f"{name}: {value}")
    except LLMError as error:
        sys.exit(f"FAILED: {error}")
    print("All LLM tasks returned valid output.")


if __name__ == "__main__":
    main()
