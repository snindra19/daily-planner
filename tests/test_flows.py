"""Late-open, skip, approval, and undo flows through the HTTP routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.calendar.mock import StaticCalendar
from app.db import get_db, init_db, make_engine
from app.deps import get_calendar, get_llm, get_now
from app.llm.client import MockClient
from app.main import app
from app.models import ActivityLog, Approval, DayPlan, DecisionHistory, ItemStatus
from app.schemas.scheduling import CalendarEvent
from tests.helpers import load
from tests.scenarios import cases
from tests.scenarios.conftest import seed

DAY = "2026-11-02"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


def make_client(session: Session, now: datetime, events: list | None = None) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_llm] = MockClient
    app.dependency_overrides[get_calendar] = lambda: StaticCalendar(events or [])
    app.dependency_overrides[get_now] = lambda: now
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_app_starts_and_serves_static_files() -> None:
    """Run the real startup code, which the other tests skip."""
    with TestClient(app) as client:
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/calendar/status").json()["mode"] == "mock"


def meeting_events() -> list[CalendarEvent]:
    return [CalendarEvent(**row) for row in load("golden_16_6")["calendar_events"]]


def woke_at_seven(db: Session) -> None:
    db.add(DayPlan(date=DAY, actual_wake="07:00"))
    db.commit()


def test_late_open_panel_then_override(session: Session) -> None:
    seed(session, load("golden_16_4"))
    woke_at_seven(session)
    client = make_client(session, datetime(2026, 11, 2, 9, 0))
    page = client.get("/").text
    assert "still open" in page
    assert 'data-key="breakfast"' in page
    page = client.post("/late/override").text
    assert "still open" not in page
    assert "10:00" in page


def test_late_open_continue_replans_only_not_done(session: Session) -> None:
    data = load("golden_16_6")
    seed(session, {**data, "rules": data["rules"][:2]})
    woke_at_seven(session)
    client = make_client(session, datetime(2026, 11, 2, 9, 0))
    client.get("/")
    client.post("/late/continue", data={"done": "breakfast", "not_done": "medicine_1"})
    statuses = {r.item_key: r.status for r in session.query(ItemStatus).all()}
    assert statuses == {"breakfast": "done", "medicine_1": "not_done"}
    page = client.get("/").text
    assert "09:00" in page
    assert "still open" not in page


def test_skip_over_the_miss_limit_needs_a_second_answer(session: Session) -> None:
    seed(session, load("golden_16_5"))
    session.add(ItemStatus(date="2026-11-01", item_key="gym", status="not_done"))
    session.commit()
    client = make_client(session, datetime(2026, 11, 2, 7, 0))
    client.get("/")
    page = client.post("/items/gym/status", data={"status": "skipped_by_user", "date": DAY}).text
    assert "Gym miss limit reached this month. Skip anyway?" in page
    assert not session.query(ItemStatus).filter_by(date=DAY, item_key="gym").first()
    client.post(
        "/items/gym/status", data={"status": "skipped_by_user", "date": DAY, "confirm": "yes"}
    )
    row = session.query(ItemStatus).filter_by(date=DAY, item_key="gym").one()
    assert row.status == "skipped_by_user"
    assert session.query(DecisionHistory).filter_by(signal="manual_skip").count() == 1


def test_picking_alternative_a_applies_it_and_records_the_signal(session: Session) -> None:
    seed(session, load("golden_16_6"))
    client = make_client(session, datetime(2026, 11, 2, 7, 0), meeting_events())
    client.get("/")
    row = session.query(Approval).filter_by(status="pending").one()
    page = client.post(
        f"/approvals/{row.id}",
        data={"action": "pick", "option_id": "meal_nearest_slot"},
        headers={"referer": "http://test/"},
    ).text
    assert "14:30" in page
    assert session.query(Approval).filter_by(status="pending").count() == 0
    signal = session.query(DecisionHistory).one()
    assert signal.signal == "picked_alternative"
    assert signal.chosen == "meal_nearest_slot"


def test_rejecting_the_fallback_keeps_the_dose_as_a_reminder(session: Session) -> None:
    seed(session, load("golden_16_6"))
    client = make_client(session, datetime(2026, 11, 2, 7, 0), meeting_events())
    client.get("/")
    row = session.query(Approval).filter_by(status="pending").one()
    page = client.post(
        f"/approvals/{row.id}", data={"action": "reject"}, headers={"referer": "http://test/"}
    ).text
    assert "kept at its ideal time as a reminder" in page
    assert "12:30" in page


def test_undo_route_restores_the_pending_proposal(session: Session) -> None:
    seed(session, load("golden_16_6"), mode="unrestricted")
    client = make_client(session, datetime(2026, 11, 2, 7, 0), meeting_events())
    assert ">auto<" in client.get("/").text
    entry = session.query(ActivityLog).one()
    assert "Undo" in client.get("/activity").text
    assert "Undone" in client.post(f"/activity/{entry.id}/undo").text
    page = client.get("/").text
    assert ">auto<" not in page
    assert ">proposed<" in page


def test_parse_route_returns_draft_cards_and_night_review_flow(session: Session) -> None:
    seed(session, load("golden_16_5"))
    client = make_client(session, datetime(2026, 11, 1, 22, 0))
    assert "Tomorrow isn't reviewed yet" in client.get("/").text
    page = client.post("/rules/parse", data={"text": cases.P1_TEXT}).text
    assert "AI suggestion:" in page
    page = client.post("/tomorrow/extra", data={"text": cases.P2_TEXT}).text
    assert "Submit assignment" in page
    page = client.post("/tomorrow/signoff").text
    assert "Signed off" in page
    assert "Tomorrow isn't reviewed yet" not in client.get("/").text


def test_restricted_just_for_tomorrow_route(session: Session) -> None:
    seed(session, load("golden_16_5"), mode="unrestricted")
    client = make_client(session, datetime(2026, 11, 1, 22, 0))
    assert "Restricted just for tomorrow" in client.get("/tomorrow").text
    response = client.post(
        "/profile/mode",
        data={"mode": "restricted", "date": DAY},
        headers={"referer": "http://test/tomorrow"},
    )
    assert "Use Unrestricted tomorrow again" in response.text


def test_learned_page_shows_evidence_and_revoke(session: Session) -> None:
    seed(session, load("golden_16_6"))
    for row in cases.L1_HISTORY:
        session.add(DecisionHistory(**row))
    session.commit()
    client = make_client(session, datetime(2026, 11, 20, 8, 0))
    page = client.post("/learned/analyze").text
    assert "6 of 7 times" in page
    assert "Accept" in page
    assert "Revoke" in page
