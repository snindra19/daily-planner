"""Fetch, classify, and cache calendar events. Read-only with respect to the calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from sqlalchemy.orm import Session

from app.calendar.base import CalendarError, CalendarSource
from app.calendar.classify import classify_event, normalize_title
from app.llm import tasks
from app.llm.client import LLMClient, LLMError
from app.models import Approval, CalendarCache, TitleClassification
from app.schemas.scheduling import CalendarEvent


@dataclass
class SyncStatus:
    """Outcome of a calendar sync."""

    ok: bool = True
    message: str = ""


def sync(db: Session, day: Date, source: CalendarSource, llm: LLMClient | None) -> SyncStatus:
    """Refresh a date's cache. On failure the cache is cleared, so the plan uses rules only."""
    iso = day.isoformat()
    try:
        events = source.events_for(day)
    except CalendarError as error:
        db.query(CalendarCache).filter_by(date=iso).delete()
        db.commit()
        return SyncStatus(False, str(error))
    remembered = {
        row.title_normalized: (row.classified_as, row.meal_type)
        for row in db.query(TitleClassification).all()
    }
    db.query(CalendarCache).filter_by(date=iso).delete()
    for event in events:
        db.add(_cache_row(db, iso, event, remembered, llm))
    db.commit()
    return SyncStatus()


def _cache_row(
    db: Session,
    iso: str,
    event: CalendarEvent,
    remembered: dict[str, tuple[str, str | None]],
    llm: LLMClient | None,
) -> CalendarCache:
    result = classify_event(event.title, event.all_day, event.busy, remembered)
    classified_as, meal_type = result.classified_as, result.meal_type
    if result.unclear and llm is not None:
        classified_as, meal_type = _ask_llm(db, iso, event.title, llm, classified_as, meal_type)
    return CalendarCache(
        date=iso,
        source_id=event.source_id,
        title=event.title,
        start=event.start.isoformat(),
        end=event.end.isoformat(),
        location=event.location,
        busy=event.busy,
        all_day=event.all_day,
        classified_as=classified_as,
        meal_type=meal_type,
    )


def _ask_llm(
    db: Session, iso: str, title: str, llm: LLMClient, fallback: str, fallback_meal: str | None
) -> tuple[str, str | None]:
    """Use the LLM's guess now and ask the user to confirm it once."""
    normalized = normalize_title(title)
    existing = db.query(Approval).filter_by(key=f"classify:{normalized}").first()
    if existing:
        change = existing.proposed_change
        return change["classified_as"], change["meal_type"]
    try:
        answer = tasks.classify(llm, title)
    except LLMError:
        return fallback, fallback_meal
    db.add(
        Approval(
            date=iso,
            key=f"classify:{normalized}",
            kind="classification",
            origin="llm",
            summary=f'Treat "{title}" as {answer.classified_as}?',
            reason=answer.reason,
            proposed_change={
                "title": normalized,
                "classified_as": answer.classified_as,
                "meal_type": answer.meal_type,
            },
            alternatives=[{"id": "meeting", "label": "It's a meeting, no meal"}],
            context={"kind": "classification"},
        )
    )
    db.flush()
    return answer.classified_as, answer.meal_type


def load_events(db: Session, iso: str) -> list[CalendarEvent]:
    """Return a date's cached events."""
    rows = db.query(CalendarCache).filter_by(date=iso).order_by(CalendarCache.start).all()
    return [
        CalendarEvent(
            source_id=row.source_id,
            title=row.title,
            start=datetime.fromisoformat(row.start),
            end=datetime.fromisoformat(row.end),
            location=row.location,
            busy=row.busy,
            all_day=row.all_day,
            classified_as=row.classified_as,
            meal_type=row.meal_type,
        )
        for row in rows
    ]
