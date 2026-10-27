"""Classify calendar events by remembered title, keywords, then flags (spec 11.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.rules import MEAL_KEYWORDS

UNCLEAR_WORDS = ("catch up", "catch-up", "catchup", "coffee", "drinks", "hangout", "get together")
UNCLEAR_PLACE = re.compile(r"\b(at|@)\s+\w+", re.IGNORECASE)


@dataclass
class Classification:
    """Result of classifying one event."""

    classified_as: str
    meal_type: str | None = None
    unclear: bool = False


def normalize_title(title: str) -> str:
    """Lowercase and collapse spaces, so the same title is remembered once."""
    return " ".join(title.lower().split())


def classify_event(
    title: str, all_day: bool, busy: bool, remembered: dict[str, tuple[str, str | None]]
) -> Classification:
    """Classify without the LLM. `unclear` means the LLM should be asked."""
    known = remembered.get(normalize_title(title))
    if known:
        return Classification(known[0], known[1])
    lowered = title.lower()
    for word, meal_type in MEAL_KEYWORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return Classification("meal", meal_type)
    if all_day or not busy:
        return Classification("ignore")
    if any(word in lowered for word in UNCLEAR_WORDS) or UNCLEAR_PLACE.search(title):
        return Classification("meeting", unclear=True)
    return Classification("meeting")
