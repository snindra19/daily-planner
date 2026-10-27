"""Milestone 2: pages and actions work end to end on mock LLM and mock calendar."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.calendar.mock import StaticCalendar
from app.db import get_db, init_db, make_engine
from app.deps import get_calendar, get_llm, get_now
from app.main import app
from app.models import Rule
from app.schemas.scheduling import CalendarEvent

NOW = datetime(2026, 11, 2, 7, 0)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[get_calendar] = lambda: StaticCalendar()
    app.dependency_overrides[get_now] = lambda: NOW
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_rule(db: Session, payload: dict) -> Rule:
    rule = Rule(
        raw_text=payload.get("label", ""), payload=payload, kind=payload["type"], status="confirmed"
    )
    db.add(rule)
    db.commit()
    return rule


BREAKFAST = {
    "type": "event",
    "id": "breakfast",
    "label": "Breakfast",
    "category": "meal",
    "anchor": {"ref": "wake_up"},
    "offset_minutes": 60,
    "duration_minutes": 20,
}


@pytest.mark.parametrize(
    "path", ["/", "/tomorrow", "/profile", "/approvals", "/activity", "/learned"]
)
def test_every_page_renders_empty(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert "Today" in response.text


def test_calendar_status_reports_mock(client: TestClient) -> None:
    assert client.get("/calendar/status").json()["mode"] == "mock"


def test_today_shows_a_confirmed_rule(client: TestClient, session: Session) -> None:
    add_rule(session, BREAKFAST)
    page = client.get("/").text
    assert "Breakfast" in page
    assert "08:00" in page


def test_wake_entry_recomputes_and_shows_the_diff(client: TestClient, session: Session) -> None:
    add_rule(session, BREAKFAST)
    client.get("/")
    response = client.post(
        "/wake",
        data={"date": "2026-11-02", "kind": "actual", "time": "06:30"},
        headers={"referer": "http://test/"},
    )
    assert response.status_code == 200
    assert "07:30" in response.text
    assert "What changed" in response.text


def test_merged_row_key_with_hash_can_be_marked_done(client: TestClient, session: Session) -> None:
    add_rule(session, BREAKFAST)
    add_rule(
        session,
        {
            "type": "series",
            "id": "drops",
            "label": "Drops",
            "category": "medicine",
            "anchor": {"ref": "event", "id": "breakfast"},
            "recurrence": {"interval_minutes": 60, "count": 1},
        },
    )
    assert "Drops 1/1" in client.get("/").text
    response = client.post("/items/drops%231/status", data={"date": "2026-11-02", "status": "done"})
    assert response.status_code == 200
    assert ">done<" in response.text


def test_parse_without_an_llm_shows_an_error(client: TestClient) -> None:
    response = client.post("/rules/parse", data={"text": "wake at 7"})
    assert response.status_code == 200
    assert "not available" in response.text


def test_calendar_failure_shows_the_banner(client: TestClient, session: Session) -> None:
    app.dependency_overrides[get_calendar] = lambda: StaticCalendar(error="server not found")
    add_rule(session, BREAKFAST)
    page = client.get("/").text
    assert "Calendar not synced" in page
    assert "server not found" in page
    assert "Breakfast" in page


def test_calendar_title_is_escaped(client: TestClient) -> None:
    event = CalendarEvent(
        source_id="x",
        title="<script>alert(1)</script>",
        start=datetime(2026, 11, 2, 10),
        end=datetime(2026, 11, 2, 11),
    )
    app.dependency_overrides[get_calendar] = lambda: StaticCalendar([event])
    assert "<script>alert(1)</script>" not in client.get("/").text


def test_mode_switch_and_priorities_are_saved(client: TestClient) -> None:
    client.post("/profile/mode", data={"mode": "unrestricted"})
    assert "Unrestricted" in client.get("/profile").text
    client.post("/profile/priorities", data={"order": "fixed,medicines,meals,low_priority,quota"})
    page = client.get("/profile").text
    assert page.index('data-key="low_priority"') < page.index('data-key="quota"')


def test_add_commitment_and_food(client: TestClient) -> None:
    client.post(
        "/commitments",
        data={
            "label": "Class",
            "days": "0",
            "start": "10:00",
            "end": "14:00",
            "commute_minutes": 35,
        },
    )
    client.post("/foods", data={"label": "Apple", "duration_minutes": 1})
    page = client.get("/profile").text
    assert "Class: 0, 10:00 to 14:00" in page
    assert "Apple, 1 min" in page
    assert "Leave for class" in client.get("/").text


def test_delete_warns_about_dependents(client: TestClient, session: Session) -> None:
    breakfast = add_rule(session, BREAKFAST)
    add_rule(
        session,
        {
            "type": "event",
            "id": "pill",
            "label": "Pill",
            "category": "medicine",
            "anchor": {"ref": "event", "id": "breakfast"},
        },
    )
    response = client.delete(f"/rules/{breakfast.id}")
    assert "Pill" in response.headers["HX-Redirect"].replace("%20", " ")
    assert session.get(Rule, breakfast.id).status == "confirmed"
