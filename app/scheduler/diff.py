"""Compare two plan snapshots so the UI can show what changed."""

from __future__ import annotations

from typing import Any

from app.schemas.scheduling import to_hhmm


def diff_snapshots(
    old: list[dict[str, Any]] | None, new: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Return added, removed, and moved items between two lists of item dicts."""
    if old is None:
        return []
    before = {i["key"]: i for i in old}
    after = {i["key"]: i for i in new}
    changes: list[dict[str, str]] = []
    for key, item in after.items():
        previous = before.get(key)
        if previous is None:
            changes.append({"change": "added", "key": key, "text": f"Added {_describe(item)}"})
        elif (previous["start"], previous["end"]) != (item["start"], item["end"]):
            changes.append(
                {
                    "change": "moved",
                    "key": key,
                    "text": f"{item['label']}: {to_hhmm(previous['start'])} to "
                    f"{to_hhmm(item['start'])}",
                }
            )
        elif previous["label"] != item["label"]:
            changes.append(
                {
                    "change": "changed",
                    "key": key,
                    "text": f"{previous['label']} is now {item['label']}",
                }
            )
    for key, item in before.items():
        if key not in after:
            changes.append({"change": "removed", "key": key, "text": f"Removed {_describe(item)}"})
    return changes


def _describe(item: dict[str, Any]) -> str:
    return f"{item['label']} at {to_hhmm(item['start'])}"
