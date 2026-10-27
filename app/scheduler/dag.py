"""Rule dependency graph: ordering, cycle detection, and ideal times."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.rules import Anchor, EventRule, SeriesRule
from app.schemas.scheduling import to_minutes

Rule = EventRule | SeriesRule


class RuleCycleError(ValueError):
    """Raised when rules depend on each other in a loop."""

    def __init__(self, rule_ids: list[str]) -> None:
        super().__init__(f"Rule cycle involving: {', '.join(rule_ids)}")
        self.rule_ids = rule_ids


@dataclass
class IdealItem:
    """An item's computed ideal time, before placement."""

    key: str
    label: str
    start: int
    end: int
    rule: Rule
    index: int | None = None


def _dependencies(rule: Rule, known: set[str]) -> list[str]:
    anchor = rule.anchor
    if anchor is None or anchor.ref not in ("event", "occurrence") or not anchor.id:
        return []
    return [anchor.id] if anchor.id in known else []


def topological_order(rules: list[Rule]) -> list[Rule]:
    """Sort rules so anchors come first. Ties keep the input (rule id) order."""
    by_id = {r.id: r for r in rules}
    remaining = {r.id: set(_dependencies(r, set(by_id))) for r in rules}
    ordered: list[Rule] = []
    while remaining:
        ready = [r.id for r in rules if r.id in remaining and not remaining[r.id]]
        if not ready:
            raise RuleCycleError(sorted(remaining))
        for rule_id in ready:
            ordered.append(by_id[rule_id])
            del remaining[rule_id]
        for deps in remaining.values():
            deps.difference_update(ready)
    return ordered


def _base_time(
    anchor: Anchor,
    after_end: bool,
    wake: int,
    spans: dict[str, tuple[int, int]],
    series: dict[str, list[str]],
) -> int | None:
    if anchor.ref == "wake_up":
        return wake
    if anchor.ref == "absolute":
        return to_minutes(anchor.time) if anchor.time else None
    if anchor.ref == "occurrence":
        occurrences = series.get(anchor.id or "", [])
        if not occurrences:
            return None
        if anchor.selector == "last":
            key = occurrences[-1]
        elif anchor.selector == "nth" and anchor.n and anchor.n <= len(occurrences):
            key = occurrences[anchor.n - 1]
        else:
            key = occurrences[0]
        return spans[key][1] if after_end else spans[key][0]
    span = spans.get(anchor.id or "")
    if span is None:
        return None
    return span[1] if after_end else span[0]


def compute_ideals(
    rules: list[Rule],
    wake: int,
    overrides: dict[str, tuple[int, int]],
    seeds: dict[str, tuple[int, int]],
) -> tuple[dict[str, IdealItem], list[Rule]]:
    """Return ideal items by key, plus rules whose anchor could not be resolved."""
    spans = dict(seeds)
    series: dict[str, list[str]] = {}
    items: dict[str, IdealItem] = {}
    unresolved: list[Rule] = []
    for rule in topological_order(rules):
        if isinstance(rule, SeriesRule):
            _expand_series(rule, wake, overrides, spans, series, items, unresolved)
        else:
            _place_event(rule, wake, overrides, spans, series, items, unresolved)
    return items, unresolved


def _place_event(
    rule: EventRule,
    wake: int,
    overrides: dict[str, tuple[int, int]],
    spans: dict[str, tuple[int, int]],
    series: dict[str, list[str]],
    items: dict[str, IdealItem],
    unresolved: list[Rule],
) -> None:
    if rule.anchor is None and rule.id not in overrides:
        return
    if rule.id in overrides:
        start, end = overrides[rule.id]
    else:
        base = _base_time(rule.anchor, rule.after_end, wake, spans, series)
        if base is None:
            unresolved.append(rule)
            return
        start = base + rule.offset_minutes
        end = start + rule.duration_minutes
    spans[rule.id] = (start, end)
    items[rule.id] = IdealItem(rule.id, rule.label, start, end, rule)


def _expand_series(
    rule: SeriesRule,
    wake: int,
    overrides: dict[str, tuple[int, int]],
    spans: dict[str, tuple[int, int]],
    series: dict[str, list[str]],
    items: dict[str, IdealItem],
    unresolved: list[Rule],
) -> None:
    base = _base_time(rule.anchor, False, wake, spans, series)
    if base is None:
        unresolved.append(rule)
        return
    count = rule.recurrence.count
    step = rule.recurrence.interval_minutes
    first = 1 if rule.recurrence.start_mode == "after_anchor" else 0
    keys: list[str] = []
    previous: int | None = None
    for number in range(1, count + 1):
        key = f"{rule.id}#{number}"
        if key in overrides:
            start, end = overrides[key]
        else:
            planned = base + rule.offset_minutes + step * (number - 1 + first)
            start = planned if previous is None else max(planned, previous + step)
            end = start + rule.duration_minutes
        previous = start
        spans[key] = (start, end)
        keys.append(key)
        items[key] = IdealItem(key, f"{rule.label} {number}/{count}", start, end, rule, number)
    series[rule.id] = keys
