"""Shared dependencies, overridable in tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.calendar.base import CalendarSource
from app.calendar.mock import MockCalendar
from app.config import get_settings
from app.llm.client import LLMClient, LLMError, make_client


def get_llm() -> LLMClient | None:
    """Return the configured LLM client, or None when it cannot be built."""
    try:
        return make_client()
    except LLMError:
        return None


def get_calendar() -> CalendarSource:
    """Return the configured calendar source."""
    settings = get_settings()
    if settings.calendar_mode == "mcp":
        from app.calendar.mcp_source import McpCalendar

        return McpCalendar(settings)
    return MockCalendar(settings.mock_calendar_path)


def get_now() -> datetime:
    """Return the current time in the user's timezone."""
    return datetime.now(ZoneInfo(get_settings().app_timezone)).replace(tzinfo=None)


def tomorrow_of(now: datetime) -> Date:
    """Return the date after `now`."""
    return now.date() + timedelta(days=1)
