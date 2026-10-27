"""FastAPI app and router registration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.calendar.base import CalendarError
from app.db import init_db
from app.deps import get_calendar
from app.routes import actions, pages

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("planner")


def _check_calendar() -> None:
    """Log whether the calendar server is usable. A problem never stops the app starting."""
    source = get_calendar()
    describe = getattr(source, "status", None)
    if describe is None:
        return
    try:
        status = describe()
    except CalendarError as error:
        log.warning("Calendar not synced: %s", error)
        return
    if not status["ok"]:
        log.warning("%s. Available: %s", status["error"], ", ".join(status["tools"]))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables and, in MCP mode, verify the calendar tool exists."""
    init_db()
    await asyncio.to_thread(_check_calendar)
    yield


app = FastAPI(title="Wake-Up Planner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(pages.router)
app.include_router(actions.router)
