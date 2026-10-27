"""MCP client: mapping, read-only allowlist, and failures, against a local fake server."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.calendar.base import CalendarError
from app.calendar.mcp_source import (
    McpCalendar,
    map_event,
    parse_events,
    redact,
    underlying_error,
)
from app.config import Settings

DAY = date(2026, 11, 2)
ZONE = ZoneInfo("America/Bogota")
FAKE = Path(__file__).parent / "fake_mcp_server.py"


def calendar(tmp_path: Path, tool: str = "list-events", command: str | None = None) -> McpCalendar:
    args = f'"{FAKE.as_posix()}" "{(tmp_path / "written").as_posix()}"'
    settings = Settings(
        _env_file=None,
        mcp_calendar_command=command or sys.executable,
        mcp_calendar_args=args,
        mcp_list_events_tool=tool,
        app_timezone="America/Bogota",
    )
    return McpCalendar(settings)


def test_timed_event_converts_to_the_user_timezone() -> None:
    raw = {
        "id": "a",
        "summary": "Standup",
        "start": {"dateTime": "2026-11-02T14:00:00Z"},
        "end": {"dateTime": "2026-11-02T14:30:00Z"},
    }
    event = map_event(raw, DAY, ZONE)
    assert event.start == datetime(2026, 11, 2, 9, 0)
    assert event.end == datetime(2026, 11, 2, 9, 30)
    assert not event.all_day


def test_all_day_and_cancelled_events() -> None:
    all_day = map_event({"id": "a", "summary": "Trip", "start": {"date": "2026-11-01"}}, DAY, ZONE)
    assert all_day.all_day
    assert all_day.start.date() == DAY
    assert map_event({"id": "b", "status": "cancelled"}, DAY, ZONE) is None


def test_event_crossing_midnight_is_clamped_to_the_day() -> None:
    raw = {
        "id": "a",
        "summary": "Late",
        "start": {"dateTime": "2026-11-02T23:00:00-05:00"},
        "end": {"dateTime": "2026-11-03T01:00:00-05:00"},
    }
    event = map_event(raw, DAY, ZONE)
    assert event.end == datetime(2026, 11, 2, 23, 59)


def test_transparent_event_is_not_busy() -> None:
    raw = {
        "id": "a",
        "start": {"dateTime": "2026-11-02T10:00:00-05:00"},
        "end": {"dateTime": "2026-11-02T11:00:00-05:00"},
        "transparency": "transparent",
    }
    event = map_event(raw, DAY, ZONE)
    assert not event.busy
    assert event.title == "(no title)"


def test_unreadable_output_is_a_calendar_error() -> None:
    with pytest.raises(CalendarError):
        parse_events("not json")
    with pytest.raises(CalendarError):
        parse_events('{"nothing": []}')


def test_fetch_through_a_real_stdio_session(tmp_path: Path) -> None:
    events = calendar(tmp_path).events_for(DAY)
    assert [e.title for e in events] == ["Standup", "Mom's birthday", "Optional talk"]
    assert events[0].start == datetime(2026, 11, 2, 9, 0)
    assert events[0].source_id == "standup_1"
    assert events[1].all_day
    assert not events[2].busy


def test_status_lists_tools_and_the_allowlist(tmp_path: Path) -> None:
    status = calendar(tmp_path).status()
    assert status["ok"]
    assert status["tools"] == ["create-event", "list-events"]
    assert "create-event" not in status["allowed"]


def test_write_tool_is_refused_and_never_reaches_the_server(tmp_path: Path) -> None:
    source = calendar(tmp_path)
    with pytest.raises(CalendarError, match="Refused calendar tool create-event"):
        source.call_tool_for_test("create-event", {"summary": "should not exist"})
    assert not (tmp_path / "written").exists()


def test_guard_allows_only_read_tools(tmp_path: Path) -> None:
    source = calendar(tmp_path)
    source.guard("list-events")
    source.guard("list-calendars")
    for name in ("create-event", "update-event", "delete-event", "respond-to-event"):
        with pytest.raises(CalendarError):
            source.guard(name)


def test_missing_tool_names_what_is_available(tmp_path: Path) -> None:
    source = calendar(tmp_path, tool="events-list")
    with pytest.raises(CalendarError, match="Calendar MCP tool not found: events-list") as caught:
        source.events_for(DAY)
    assert "list-events" in str(caught.value)
    assert source.status()["ok"] is False


def test_server_that_cannot_start_is_a_calendar_error(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        calendar(tmp_path, command="this-command-does-not-exist").events_for(DAY)


def test_crash_reports_the_underlying_error_and_server_stderr() -> None:
    settings = Settings(
        _env_file=None,
        mcp_calendar_command=sys.executable,
        mcp_calendar_args="-c \"import sys; sys.stderr.write('credentials missing'); sys.exit(1)\"",
        mcp_list_events_tool="list-events",
    )
    with pytest.raises(CalendarError) as caught:
        McpCalendar(settings).status()
    message = str(caught.value)
    assert "TaskGroup" not in message
    assert "Connection closed" in message
    assert "credentials missing" in message


def test_underlying_error_unwraps_nested_groups() -> None:
    inner = ValueError("the real cause")
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [inner])])
    assert underlying_error(nested) is inner


def test_server_stderr_is_redacted() -> None:
    text = "failed token=ya29.abc123 and Bearer xyz and " + "a" * 50
    cleaned = redact(text)
    assert "ya29" not in cleaned
    assert "xyz" not in cleaned
    assert "a" * 50 not in cleaned
    assert "failed" in cleaned


def test_env_merges_defaults_with_the_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", r"C:\AppData")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    monkeypatch.setenv("PATH", r"C:\bin")
    env_file = tmp_path / "server.env"
    env_file.write_text(
        "GOOGLE_OAUTH_CREDENTIALS='C:\\Users\\nindr\\oauth.json'\nENABLED_TOOLS=list-events\n",
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        mcp_calendar_command="cmd",
        mcp_list_events_tool="list-events",
        mcp_calendar_env_file=str(env_file),
    )
    env = McpCalendar(settings).server_env()
    assert env["GOOGLE_OAUTH_CREDENTIALS"] == r"C:\Users\nindr\oauth.json"
    assert env["ENABLED_TOOLS"] == "list-events"
    assert env["APPDATA"] == r"C:\AppData"
    assert env["USERPROFILE"] == r"C:\Users\me"
    assert env["PATH"] == r"C:\bin"


def test_process_environment_values_reach_the_server(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS", r"C:\creds.json")
    monkeypatch.delenv("ENABLED_TOOLS", raising=False)
    settings = Settings(
        _env_file=None, mcp_calendar_command="cmd", mcp_list_events_tool="list-events"
    )
    env = McpCalendar(settings).server_env()
    assert env["GOOGLE_OAUTH_CREDENTIALS"] == r"C:\creds.json"
    assert env["ENABLED_TOOLS"] == "list-calendars,list-events"


def test_double_quoted_windows_path_is_caught(tmp_path: Path) -> None:
    env_file = tmp_path / "server.env"
    env_file.write_text(
        'GOOGLE_OAUTH_CREDENTIALS="C:\\Users\\nindr\\oauth.json"\n', encoding="utf-8"
    )
    settings = Settings(
        _env_file=None,
        mcp_calendar_command="cmd",
        mcp_list_events_tool="list-events",
        mcp_calendar_env_file=str(env_file),
    )
    with pytest.raises(CalendarError, match="single quotes"):
        McpCalendar(settings).server_env()


def test_missing_configuration_is_a_calendar_error() -> None:
    with pytest.raises(CalendarError, match="Set MCP_CALENDAR_COMMAND"):
        McpCalendar(Settings(_env_file=None)).events_for(DAY)
