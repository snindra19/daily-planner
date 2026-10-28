"""The medicine task must see the user's words and the meals, found by the live P1 run."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine
from app.services import rule_service

TEXT = "breakfast and dinner, meds twice a day after food"
MEAL = {
    "type": "event",
    "label": "Breakfast",
    "id": "breakfast",
    "category": "meal",
    "anchor": {"ref": "wake_up"},
}
DINNER = {
    "type": "event",
    "label": "Dinner",
    "id": "dinner",
    "category": "meal",
    "anchor": {"ref": "absolute", "time": "19:00"},
}
MEDS = {
    "type": "event",
    "label": "Meds",
    "id": "meds",
    "category": "medicine",
    "anchor": {"ref": "wake_up"},
    "timing_confirmed": False,
}


class Scripted:
    """Answers parse and medicine, and keeps the medicine task's context."""

    def __init__(self, doses: list[dict[str, str]]) -> None:
        self.doses = doses
        self.medicine_context: dict[str, Any] = {}

    def raw(self, task: str, schema: dict, primary: Any, context: Any) -> dict[str, Any]:
        if task == "parse":
            return {"rules": [{"payload": p} for p in (MEAL, DINNER, MEDS)]}
        if task == "medicine":
            self.medicine_context = context
            return {"summary": "Suggestion.", "doses": self.doses}
        return {"questions": []}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def test_medicine_task_receives_the_text_and_the_draft_meals(db: Session) -> None:
    client = Scripted([{"after": "breakfast"}, {"after": "dinner"}])
    drafts = rule_service.parse_text(db, TEXT, client)
    assert client.medicine_context["user_text"] == TEXT
    ids = {rule.get("id") for rule in client.medicine_context["draft_rules"]}
    assert {"breakfast", "dinner"} <= ids
    meds = next(rule for rule in drafts if rule.payload["id"] == "meds")
    assert len(meds.suggestion["doses"]) == 2


def test_suggestion_naming_an_unknown_meal_is_dropped(db: Session) -> None:
    client = Scripted([{"after": "second_breakfast"}])
    drafts = rule_service.parse_text(db, TEXT, client)
    meds = next(rule for rule in drafts if rule.payload["id"] == "meds")
    assert meds.suggestion is None
