"""Fill the configured database with the golden 16.5 routine, for trying the app offline.

Usage: uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import CommitmentRow, FoodOptionRow, QuotaRow, Rule  # noqa: E402
from app.services.profile_service import get_profile  # noqa: E402


def main() -> None:
    data = json.loads((ROOT / "fixtures/scenarios/golden_16_5.json").read_text(encoding="utf-8"))
    init_db()
    with SessionLocal() as db:
        if db.query(Rule).count():
            sys.exit("The database already has rules. Nothing changed.")
        profile = get_profile(db)
        profile.default_wake_by_weekday = {str(day): data["wake"] for day in range(7)}
        profile.weights = {**profile.weights, **data["weights"]}
        for payload in data["rules"]:
            db.add(
                Rule(
                    raw_text=payload["label"],
                    payload=payload,
                    kind=payload["type"],
                    status="confirmed",
                )
            )
        for row in data["commitments"]:
            days = list(range(7))
            fields = {k: v for k, v in row.items() if k not in ("id", "days_of_week")}
            db.add(CommitmentRow(days_of_week=days, **fields))
        for row in data["food_options"]:
            db.add(FoodOptionRow(**{k: v for k, v in row.items() if k != "id"}))
        for row in data["quotas"]:
            db.add(QuotaRow(**row))
        db.commit()
    print("Demo routine added.")


if __name__ == "__main__":
    main()
