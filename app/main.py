"""FastAPI app and router registration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes import actions, pages

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup."""
    init_db()
    yield


app = FastAPI(title="Wake-Up Planner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(pages.router)
app.include_router(actions.router)
