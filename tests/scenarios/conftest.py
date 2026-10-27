"""Database helpers for scenarios that go through the services."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine
from app.models import CommitmentRow, FoodOptionRow, QuotaRow, Rule
from app.services.profile_service import get_profile


@pytest.fixture
def db() -> Iterator[Session]:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def seed(session: Session, data: dict[str, Any], mode: str = "restricted") -> None:
    """Store a scenario fixture's rules, commitments, food, and quotas in the database."""
    profile = get_profile(session)
    profile.approval_mode = mode
    profile.sleep_time = data.get("sleep_time", "23:00")
    if "wake" in data:
        profile.default_wake_by_weekday = {str(day): data["wake"] for day in range(7)}
    if "weights" in data:
        profile.weights = data["weights"]
    for payload in data.get("rules", []):
        session.add(
            Rule(
                raw_text=payload.get("label", ""),
                payload=payload,
                kind=payload["type"],
                status="confirmed",
            )  # fmt: skip
        )
    for row in data.get("commitments", []):
        fields = {k: v for k, v in row.items() if k != "id"}
        session.add(CommitmentRow(**fields))
    for row in data.get("food_options", []):
        fields = {k: v for k, v in row.items() if k != "id"}
        session.add(FoodOptionRow(**fields))
    for row in data.get("quotas", []):
        session.add(QuotaRow(**row))
    session.commit()
