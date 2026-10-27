"""A local stdio MCP server for tests. It offers a write tool, so the allowlist can be proven."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-calendar")
marker = Path(sys.argv[1]) if len(sys.argv) > 1 else None


@server.tool(name="list-events")
def list_events(calendarId: str, timeMin: str, timeMax: str, timeZone: str) -> str:  # noqa: N803
    """Return one meeting, one all-day event, one free event, and one cancelled event."""
    day = timeMin[:10]
    return json.dumps(
        {
            "events": [
                {
                    "id": "standup:1",
                    "summary": "Standup",
                    "start": {"dateTime": f"{day}T14:00:00Z"},
                    "end": {"dateTime": f"{day}T14:30:00Z"},
                    "location": "Zoom",
                },
                {
                    "id": "bday",
                    "summary": "Mom's birthday",
                    "start": {"date": day},
                    "end": {"date": day},
                },
                {
                    "id": "free",
                    "summary": "Optional talk",
                    "start": {"dateTime": f"{day}T15:00:00-05:00"},
                    "end": {"dateTime": f"{day}T16:00:00-05:00"},
                    "transparency": "transparent",
                },
                {"id": "gone", "status": "cancelled"},
            ]
        }
    )


@server.tool(name="create-event")
def create_event(summary: str) -> str:
    """A write tool. Calling it leaves a marker file that the test checks for."""
    if marker:
        marker.write_text(summary, encoding="utf-8")
    return "created"


if __name__ == "__main__":
    server.run()
