"""Calendar source that reads fixtures/mock_calendar.json."""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

from app.calendar.base import CalendarError
from app.schemas.scheduling import CalendarEvent


class MockCalendar:
    """Reads events per date from a hand-editable JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def events_for(self, date: Date) -> list[CalendarEvent]:
        """Return the file's events for a date."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CalendarError(f"Could not read the mock calendar: {error}") from error
        return [CalendarEvent(**event) for event in data.get(date.isoformat(), [])]


class StaticCalendar:
    """In-memory calendar for tests. Set `error` to simulate a sync failure."""

    def __init__(self, events: list[CalendarEvent] | None = None, error: str | None = None) -> None:
        self.events = events or []
        self.error = error

    def events_for(self, date: Date) -> list[CalendarEvent]:
        """Return the events on a date, or raise the configured error."""
        if self.error:
            raise CalendarError(self.error)
        return [e for e in self.events if e.start.date() == date]
