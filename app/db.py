"""Database engine, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

Base = declarative_base()


def make_engine(url: str) -> Engine:
    """Create an engine. In-memory SQLite shares one connection across threads."""
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


def init_db(bind: Engine | None = None) -> None:
    """Create all tables."""
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=bind or engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
