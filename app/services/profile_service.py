"""Profile access and the shared day-override store."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import DayOverrideRow, Profile
from app.schemas.scheduling import DEFAULT_PRIORITIES, DEFAULT_WEIGHTS

DEFAULT_WAKE = "07:00"


def get_profile(db: Session) -> Profile:
    """Return the single profile row, creating it with defaults on first use."""
    profile = db.query(Profile).first()
    if profile is None:
        profile = Profile(
            default_wake_by_weekday={str(d): DEFAULT_WAKE for d in range(7)},
            priority_order=list(DEFAULT_PRIORITIES),
            weights=dict(DEFAULT_WEIGHTS),
        )
        db.add(profile)
        db.commit()
    return profile


def get_changes(db: Session, iso: str) -> dict[str, Any]:
    """Return a date's override dict, empty if there is none."""
    row = db.query(DayOverrideRow).filter_by(date=iso).first()
    return dict(row.changes or {}) if row else {}


def update_changes(db: Session, iso: str, **values: Any) -> None:
    """Merge values into a date's override dict."""
    row = db.query(DayOverrideRow).filter_by(date=iso).first()
    if row is None:
        row = DayOverrideRow(date=iso, changes={})
        db.add(row)
    row.changes = {**(row.changes or {}), **values}
    db.commit()
