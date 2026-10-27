"""Live, read-only check against the real calendar. Skipped unless CALENDAR_MODE=mcp is set.

Run with the MCP_* variables set:
    $env:CALENDAR_MODE='mcp'; uv run pytest -m live -v
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import sessionmaker

from app.calendar.base import CalendarError
from app.calendar.mcp_source import McpCalendar
from app.config import Settings
from app.db import init_db, make_engine
from app.services import plan_service
from tests.conftest import LIVE_CALENDAR

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_CALENDAR, reason="Needs CALENDAR_MODE=mcp in the environment."),
]


@pytest.fixture(scope="module")
def source() -> McpCalendar:
    return McpCalendar(Settings())


def test_server_offers_the_configured_tool(source: McpCalendar) -> None:
    status = source.status()
    assert status["ok"], status
    assert status["tool"] in status["tools"]


def test_write_tools_are_not_on_the_allowlist(source: McpCalendar) -> None:
    allowed = source.status()["allowed"]
    assert not [name for name in allowed if name.split("-")[0] in ("create", "update", "delete")]


def test_write_is_refused_before_it_reaches_the_server(source: McpCalendar) -> None:
    with pytest.raises(CalendarError, match="Refused calendar tool create-event"):
        source.call_tool_for_test("create-event", {"summary": "must not be created"})


def test_real_events_flow_into_a_plan(source: McpCalendar) -> None:
    today = datetime.now(ZoneInfo(source.settings.app_timezone)).date()
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        for offset in range(8):
            view = plan_service.compute_plan(db, today + timedelta(days=offset), source=source)
            assert view.calendar.ok, view.calendar.message
