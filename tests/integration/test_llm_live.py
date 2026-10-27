"""Live P1 setup through the real services. Skipped unless LLM_MODE=live is in the environment.

Run with:
    $env:LLM_MODE='live'; uv run pytest -m live -v tests/integration/test_llm_live.py
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import init_db, make_engine
from app.llm.client import LiveClient
from app.models import Question, Rule
from app.services import plan_service, rule_service
from tests.conftest import LIVE_LLM
from tests.scenarios.cases import P1_TEXT

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_LLM, reason="Needs LLM_MODE=live in the environment."),
]


def test_p1_text_becomes_rules_that_the_scheduler_can_plan() -> None:
    client = LiveClient(Settings())
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        drafts = rule_service.parse_text(db, P1_TEXT, client)
        assert drafts
        assert db.query(Rule).filter_by(status="confirmed").count() == 0
        assert db.query(Question).filter_by(status="pending").count() > 0

        medicines = [r for r in drafts if r.payload.get("category") == "medicine"]
        assert medicines, "The text mentions meds."
        assert all(r.payload.get("timing_confirmed") is False for r in medicines)
        assert all(r.suggestion for r in medicines)

        for rule in drafts:
            rule_service.confirm_rule(db, rule.id, None)
        monday = date(2026, 11, 2)
        result = plan_service.compute_plan(db, monday).result
        assert result.item("meds") is None
        assert result.needs_confirmation

        for rule in medicines:
            rule_service.accept_suggestion(db, rule.id)
        result = plan_service.compute_plan(db, monday).result
        assert not result.needs_confirmation
        doses = [i for i in result.items if i.category == "medicine"]
        assert len(doses) == 2
        labels = " ".join(i.label for i in result.items)
        assert "Leave for" in labels
        assert not [w for w in result.warnings if "anchor is missing" in w], result.warnings
