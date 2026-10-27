"""Calendar source interface."""

from __future__ import annotations

from datetime import date as Date
from typing import Protocol

from app.schemas.scheduling import CalendarEvent


class CalendarError(Exception):
    """Fetching events failed. The message is shown in the "Calendar not synced" banner."""


class CalendarSource(Protocol):
    """Anything that can list a date's events."""

    def events_for(self, date: Date) -> list[CalendarEvent]:
        """Return the events on a date."""
        ...
