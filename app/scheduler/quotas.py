"""Weekly and monthly quota logic, counted from completion records (spec 6.6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta

from app.schemas.rules import slugify
from app.schemas.scheduling import Quota, QuotaRecord


@dataclass
class QuotaState:
    """Where a quota item stands on a date."""

    done_this_week: int
    misses_this_month: int
    weekly_target_met: bool
    miss_limit_reached: bool


def quota_state(quota: Quota, history: list[QuotaRecord], day: Date) -> QuotaState:
    """Count done records for the Monday-based week and misses for the calendar month."""
    week_start = day - timedelta(days=day.weekday())
    label = slugify(quota.item_label)
    done = misses = 0
    for record in history:
        if slugify(record.item_label) != label:
            continue
        when = Date.fromisoformat(record.date)
        if record.status == "done" and week_start <= when <= day:
            done += 1
        if record.status != "done" and (when.year, when.month) == (day.year, day.month):
            misses += 1
    return QuotaState(
        done_this_week=done,
        misses_this_month=misses,
        weekly_target_met=done >= quota.target_per_week,
        miss_limit_reached=misses >= quota.max_misses_per_month,
    )


def find_quota(label: str, quotas: list[Quota]) -> Quota | None:
    """Return the quota matching an item label, if any."""
    wanted = slugify(label)
    return next((q for q in quotas if slugify(q.item_label) == wanted), None)
