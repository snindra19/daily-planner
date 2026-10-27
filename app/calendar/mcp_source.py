"""Read-only calendar source: an MCP client that calls the server's read tools directly.

No LLM is involved. A code-level allowlist refuses every tool except the configured read tools,
even when the server also offers create, update, or delete.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import tempfile
from datetime import date as Date
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from app.calendar.base import CalendarError
from app.config import Settings
from app.schemas.scheduling import CalendarEvent

LIST_CALENDARS_TOOL = "list-calendars"
TIMEOUT_SECONDS = 90
STDERR_TAIL = 1500
PASSED_THROUGH = (
    "PATH",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "HOME",
    "GOOGLE_OAUTH_CREDENTIALS",
    "ENABLED_TOOLS",
)
SECRET_SHAPES = re.compile(
    r"ya29\.\S+|1//\S+|GOCSPX-\S+|sk-ant-\S+|Bearer\s+\S+|[A-Za-z0-9_\-]{40,}"
)


def map_event(raw: dict[str, Any], day: Date, zone: ZoneInfo) -> CalendarEvent | None:
    """Map one server event to CalendarEvent. Switching servers only changes this function.

    Times become naive local times clamped to `day`. Cancelled events return None.
    """
    if raw.get("status") == "cancelled":
        return None
    start_info, end_info = raw.get("start") or {}, raw.get("end") or {}
    all_day = "dateTime" not in start_info
    day_start = datetime.combine(day, time.min)
    last_minute = datetime.combine(day, time(23, 59))
    if all_day:
        start, end = day_start, last_minute
    else:
        start = max(_local(start_info["dateTime"], zone), day_start)
        end = min(_local(end_info["dateTime"], zone), last_minute)
    return CalendarEvent(
        source_id=_safe_id(str(raw.get("id", ""))),
        title=raw.get("summary") or "(no title)",
        start=start,
        end=end,
        location=raw.get("location"),
        busy=raw.get("transparency") != "transparent",
        all_day=all_day,
    )


def _safe_id(value: str) -> str:
    """Item keys use ":" as a separator, so it cannot appear in an event id."""
    return value.replace(":", "_")


def _local(value: str, zone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(zone).replace(tzinfo=None)


def parse_events(text: str) -> list[dict[str, Any]]:
    """Read the event list out of a tool's text output."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CalendarError("The calendar server returned something unreadable.") from error
    events = data if isinstance(data, list) else data.get("events")
    if not isinstance(events, list):
        raise CalendarError("The calendar server's reply has no event list.")
    return events


def underlying_error(error: BaseException) -> BaseException:
    """Unwrap nested exception groups, preferring a CalendarError raised by this client."""
    leaves: list[BaseException] = []
    pending = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        else:
            leaves.append(current)
    return next((leaf for leaf in leaves if isinstance(leaf, CalendarError)), leaves[0])


def _describe(error: BaseException) -> str:
    return f"Could not reach the calendar server: {type(error).__name__}: {error}"


def redact(text: str) -> str:
    """Hide anything shaped like a token before server output reaches a page or a log."""
    return SECRET_SHAPES.sub("***", text)


def _failure(message: str, stderr: TextIO) -> CalendarError:
    """Build an error from a message plus the end of the server's stderr."""
    stderr.flush()
    stderr.seek(0)
    output = redact(stderr.read().strip())[-STDERR_TAIL:]
    return CalendarError(f"{message} Server stderr: {output}" if output else message)


def _read_env_file(path: str) -> dict[str, str]:
    """Read the server's env file, catching a Windows path damaged by double quotes."""
    if not Path(path).is_file():
        raise CalendarError(f"MCP_CALENDAR_ENV_FILE does not exist: {path}")
    values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    for name, value in values.items():
        if any(ord(character) < 32 for character in value):
            raise CalendarError(
                f"{name} in MCP_CALENDAR_ENV_FILE has a control character. Double quotes turn "
                "backslashes into escapes, so write Windows paths in single quotes."
            )
    return values


class McpCalendar:
    """Starts the MCP server over stdio for each fetch and calls its read tools."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tool = settings.mcp_list_events_tool
        self.allowlist = {self.tool, LIST_CALENDARS_TOOL}
        self.zone = ZoneInfo(settings.app_timezone)

    def guard(self, name: str) -> None:
        """Refuse any tool that is not on the read-only allowlist."""
        if name not in self.allowlist:
            raise CalendarError(f"Refused calendar tool {name}: it is not on the read-only list.")

    def _server(self) -> StdioServerParameters:
        settings = self.settings
        if not settings.mcp_calendar_command or not self.tool:
            raise CalendarError("Set MCP_CALENDAR_COMMAND and MCP_LIST_EVENTS_TOOL.")
        return StdioServerParameters(
            command=settings.mcp_calendar_command,
            args=shlex.split(settings.mcp_calendar_args, posix=True),
            env=self.server_env(),
        )

    def server_env(self) -> dict[str, str]:
        """Merge the default environment, the process's values, and the env file, in that order."""
        env = get_default_environment()
        for name in PASSED_THROUGH:
            if os.environ.get(name):
                env[name] = os.environ[name]
        env_file = self.settings.mcp_calendar_env_file
        if env_file:
            env.update(_read_env_file(env_file))
        env.setdefault("ENABLED_TOOLS", ",".join(sorted(self.allowlist)))
        return env

    async def _with_session(self, work: Any, stderr: TextIO) -> Any:
        async with stdio_client(self._server(), errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await work(session)

    def _run(self, work: Any) -> Any:
        """Run one session to completion. Failures carry the cause and the server's stderr."""

        async def bounded(stderr: TextIO) -> Any:
            return await asyncio.wait_for(self._with_session(work, stderr), TIMEOUT_SECONDS)

        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr:
            try:
                return asyncio.run(bounded(stderr))
            except CalendarError:
                raise
            except TimeoutError as error:
                raise _failure("The calendar server did not answer in time.", stderr) from error
            except BaseExceptionGroup as group:
                leaf = underlying_error(group)
                if isinstance(leaf, CalendarError):
                    raise leaf from group
                raise _failure(_describe(leaf), stderr) from leaf
            except Exception as error:
                raise _failure(_describe(error), stderr) from error

    async def _tool_names(self, session: ClientSession) -> list[str]:
        listed = await session.list_tools()
        return sorted(tool.name for tool in listed.tools)

    async def _call(self, session: ClientSession, name: str, arguments: dict[str, Any]) -> str:
        """The only path to a tool call, so the allowlist cannot be bypassed."""
        self.guard(name)
        result = await session.call_tool(name, arguments)
        text = "".join(getattr(part, "text", "") for part in result.content)
        if result.is_error:
            raise CalendarError(f"The calendar tool failed: {text[:300]}")
        return text

    def events_for(self, date: Date) -> list[CalendarEvent]:
        """Return a date's events."""
        arguments = {
            "calendarId": self.settings.mcp_calendar_id,
            "timeMin": f"{date.isoformat()}T00:00:00",
            "timeMax": f"{(date + timedelta(days=1)).isoformat()}T00:00:00",
            "timeZone": self.settings.app_timezone,
        }

        async def work(session: ClientSession) -> str:
            names = await self._tool_names(session)
            if self.tool not in names:
                raise CalendarError(
                    f"Calendar MCP tool not found: {self.tool}. Available: {', '.join(names)}"
                )
            return await self._call(session, self.tool, arguments)

        mapped = [map_event(raw, date, self.zone) for raw in parse_events(self._run(work))]
        return [event for event in mapped if event is not None]

    def status(self) -> dict[str, Any]:
        """Report the server's tools and whether the configured tool exists."""
        names = self._run(self._tool_names)
        found = self.tool in names
        return {
            "mode": "mcp",
            "ok": found,
            "tool": self.tool,
            "error": None if found else f"Calendar MCP tool not found: {self.tool}",
            "tools": names,
            "allowed": sorted(self.allowlist),
        }

    def call_tool_for_test(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool through the guarded path. Used to prove the allowlist holds."""

        async def work(session: ClientSession) -> str:
            return await self._call(session, name, arguments)

        return self._run(work)
