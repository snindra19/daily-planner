"""Pure, deterministic day planner (spec section 9). No database, network, or LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, time
from typing import Any

from app.scheduler.conflicts import find_duplicates, find_indirect_conflicts
from app.scheduler.dag import IdealItem, compute_ideals
from app.scheduler.fallback import generate_candidates
from app.scheduler.placement import Block, DayState, is_point
from app.scheduler.quotas import find_quota, quota_state
from app.schemas.rules import ConstraintRule, EventRule, PreferenceRule, SeriesRule
from app.schemas.scheduling import (
    CalendarEvent,
    Commitment,
    Conflict,
    DayOverrides,
    Decision,
    FoodOption,
    LateOpen,
    Option,
    PlanItem,
    PlanResult,
    PlanRow,
    ProfileInput,
    Proposal,
    Quota,
    QuotaRecord,
    StandingApproval,
    UnslottedItem,
    format_duration,
    to_hhmm,
    to_minutes,
)

TIERS = {"medicine": 2, "meal": 3, "routine": 3, "task": 3, "quota": 4, "low_priority": 5}
ONLINE_WORDS = ("zoom", "meet.google", "google meet", "teams", "http", "online", "virtual")
MAX_PASSES = 200
MIN_SHORTENED_MINUTES = 15


@dataclass
class Inputs:
    """Everything one planning run reads."""

    date: Date
    rules: list[Any]
    commitments: list[Commitment]
    food_options: list[FoodOption]
    quotas: list[Quota]
    quota_history: list[QuotaRecord]
    calendar_events: list[CalendarEvent]
    day_overrides: DayOverrides
    item_status: dict[str, str]
    standing_approvals: list[StandingApproval]
    approved_decisions: dict[str, Decision]
    profile: ProfileInput


def _minutes(value: time | datetime | str) -> int:
    if isinstance(value, str):
        return to_minutes(value)
    return value.hour * 60 + value.minute


def build_plan(
    date: Date,
    wake_time: time | str,
    now: time | datetime | str | None,
    rules: list[Any],
    commitments: list[Commitment],
    food_options: list[FoodOption],
    quotas: list[Quota],
    quota_history: list[QuotaRecord],
    calendar_events: list[CalendarEvent],
    day_overrides: DayOverrides,
    item_status: dict[str, str],
    standing_approvals: list[StandingApproval],
    approved_decisions: dict[str, Decision],
    profile: ProfileInput,
) -> PlanResult:
    """Build one day's plan. The same inputs always give the same output."""
    inputs = Inputs(
        date,
        rules,
        commitments,
        food_options,
        quotas,
        quota_history,
        calendar_events,
        day_overrides,
        item_status,
        standing_approvals,
        approved_decisions,
        profile,
    )
    wake = _minutes(wake_time)
    overrides: dict[str, tuple[int, int]] = {}
    pinned: dict[str, str] = {}
    effective_wake = wake
    restart: int | None = None

    if day_overrides.late_mode and day_overrides.restart_at:
        restart = to_minutes(day_overrides.restart_at)
        base = _Planner(inputs, wake, {}, {}, None).run()
        _pin_done_items(base, item_status, overrides, pinned)
        if day_overrides.late_mode == "override":
            effective_wake = restart
        else:
            _replan_not_done(base, item_status, restart, inputs, overrides)

    if day_overrides.reorder:
        base = _Planner(inputs, effective_wake, dict(overrides), dict(pinned), restart).run()
        _apply_reorders(base, day_overrides.reorder, overrides, pinned)

    result = _Planner(inputs, effective_wake, overrides, pinned, restart).run()
    result.wake = wake
    if day_overrides.reorder:
        _warn_reorder_spacing(result)
    if now is not None and not day_overrides.late_mode:
        result.late_open = _late_open(result, item_status, wake, _minutes(now))
    return result


def _pin_done_items(
    base: PlanResult,
    status: dict[str, str],
    overrides: dict[str, tuple[int, int]],
    pinned: dict[str, str],
) -> None:
    for item in base.items:
        if status.get(item.key) == "done" and item.tier > 1:
            overrides[item.key] = (item.start, item.end)
            pinned[item.key] = "done"


def _replan_not_done(
    base: PlanResult,
    status: dict[str, str],
    restart: int,
    inputs: Inputs,
    overrides: dict[str, tuple[int, int]],
) -> None:
    """Move Not done items from before the restart into the rest of the day, in order."""
    by_id = {r.id: r for r in inputs.rules if isinstance(r, EventRule | SeriesRule)}
    cursor = restart
    moved_ends: dict[str, int] = {}
    late = [i for i in base.items if status.get(i.key) == "not_done" and i.start < restart]
    for item in sorted(late, key=lambda i: (i.start, i.key)):
        rule = by_id.get(item.rule_id or "")
        duration = item.end - item.start
        start = cursor
        food = getattr(rule, "food_source_ref", None)
        if food in moved_ends:
            start = max(start, moved_ends[food])
        overrides[item.key] = (start, start + duration)
        moved_ends[item.key] = start + duration
        if not is_point(duration):
            cursor = start + duration


def _apply_reorders(
    base: PlanResult,
    reorders: list[dict[str, str]],
    overrides: dict[str, tuple[int, int]],
    pinned: dict[str, str],
) -> None:
    for move in reorders:
        item, target = base.item(move["item_key"]), base.item(move["after"])
        if item is None or target is None:
            continue
        overrides[target.key] = (target.start, target.end)
        pinned.setdefault(target.key, "user")
        overrides[item.key] = (target.end, target.end + item.end - item.start)
        pinned[item.key] = "user"


def _warn_reorder_spacing(result: PlanResult) -> None:
    for proposal in result.proposals:
        if proposal.kind == "spacing":
            result.warnings.append(f"Reorder breaks medicine spacing: {proposal.summary}")


def _late_open(result: PlanResult, status: dict[str, str], wake: int, now: int) -> LateOpen:
    if now <= wake:
        return LateOpen()
    keys = [
        i.key
        for i in result.items
        if i.tier > 1 and i.start < now and status.get(i.key, "pending") == "pending"
    ]
    return LateOpen(needed=bool(keys), pending_keys=keys)


@dataclass
class _Meta:
    """Placement details decided by a proposal."""

    label: str | None = None
    silent: bool = False
    status: str = "scheduled"
    proposal_key: str | None = None


@dataclass
class _Planner:
    """One pass over the steps in spec 9.2."""

    inp: Inputs
    wake: int
    overrides: dict[str, tuple[int, int]]
    pinned: dict[str, str]
    restart: int | None
    day: DayState = field(default_factory=DayState)
    result: PlanResult | None = None
    rules: list[Any] = field(default_factory=list)
    constraints: dict[str, ConstraintRule] = field(default_factory=dict)
    seeds: dict[str, tuple[int, int]] = field(default_factory=dict)
    replaced: set[str] = field(default_factory=set)
    decided_meals: set[str] = field(default_factory=set)
    decided_meds: set[str] = field(default_factory=set)
    merged: set[str] = field(default_factory=set)
    dropped: set[str] = field(default_factory=set)
    meta: dict[str, _Meta] = field(default_factory=dict)
    ideals: dict[str, IdealItem] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    mode: str = "restricted"

    def run(self) -> PlanResult:
        """Run every step and return the plan."""
        self.result = PlanResult(date=self.inp.date.isoformat(), wake=self.wake)
        self.mode = self.inp.day_overrides.approval_mode or self.inp.profile.approval_mode
        self.sleep = to_minutes(self.inp.profile.sleep_time)
        self.earliest = max(self.wake, self.restart or 0)
        self._select_inputs()
        self._build_fixed_blocks()
        self._recompute()
        self._propose_meal_merge()
        self._run_fallbacks()
        self._enforce_spacing()
        self._place_meals()
        self._place_anchored_items()
        self._place_medicines()
        self._place_tasks()
        self._place_flexible()
        self._flag_sleep()
        self._finish()
        return self.result

    # Step 1: select day inputs

    def _select_inputs(self) -> None:
        weekday = self.inp.date.weekday()
        iso = self.inp.date.isoformat()
        self.weights = dict(self.inp.profile.weights)
        extras = list(self.inp.day_overrides.extra_rules)
        for rule in [*self.inp.rules, *extras]:
            if isinstance(rule, PreferenceRule):
                if rule.key == "weights" and isinstance(rule.value, dict):
                    self.weights.update(rule.value)
                continue
            if weekday not in rule.days_of_week or rule.only_date not in (None, iso):
                continue
            if isinstance(rule, ConstraintRule):
                self.constraints[rule.event_ref] = rule
            elif rule.category == "medicine" and not rule.timing_confirmed:
                self.result.needs_confirmation.append(rule.label)
            else:
                self.rules.append(rule)

    # Step 2: fixed blocks

    def _build_fixed_blocks(self) -> None:
        todays = self._todays_commitments()
        self._add_commitments(todays)
        events = self._todays_events()
        kept, duplicates = find_duplicates(todays, events)
        self.result.conflicts.extend(duplicates)
        for event in kept:
            self._add_calendar_event(event)
        weekday = self.inp.date.weekday()
        event_rules = [r for r in self.rules if isinstance(r, EventRule)]
        self.result.conflicts.extend(
            find_indirect_conflicts(event_rules, self.inp.commitments, weekday)
        )

    def _todays_commitments(self) -> list[Commitment]:
        changes = self.inp.day_overrides
        cutoff = to_minutes(changes.no_commitments_after) if changes.no_commitments_after else None
        chosen = []
        for commitment in self.inp.commitments:
            if self.inp.date.weekday() not in commitment.days_of_week:
                continue
            if commitment.label in changes.removed_commitments:
                continue
            if cutoff is not None and to_minutes(commitment.start) >= cutoff:
                continue
            chosen.append(commitment)
        return sorted(chosen, key=lambda c: (to_minutes(c.start), c.id))

    def _todays_events(self) -> list[CalendarEvent]:
        events = []
        for event in self.inp.calendar_events:
            if event.start.date() != self.inp.date:
                continue
            if event.all_day or not event.busy or event.classified_as == "ignore":
                self.result.surfaced_events.append(event.title)
                continue
            events.append(event)
        return sorted(events, key=lambda e: (e.start, e.source_id))

    def _add_commitments(self, commitments: list[Commitment]) -> None:
        groups: list[list[Commitment]] = []
        for commitment in commitments:
            if groups and groups[-1][-1].location == commitment.location:
                groups[-1].append(commitment)
            else:
                groups.append([commitment])
        for group in groups:
            for commitment in group:
                self._add_commitment_block(commitment)
            self._add_group_commute(group)

    def _add_commitment_block(self, commitment: Commitment) -> None:
        start, end = to_minutes(commitment.start), to_minutes(commitment.end)
        key = f"commitment:{commitment.id}"
        cursor = start
        for gap in sorted(commitment.breaks, key=lambda b: b["start"]):
            gap_start, gap_end = to_minutes(gap["start"]), to_minutes(gap["end"])
            self.day.blocks.append(
                Block(f"{key}:{gap_start}", commitment.label, cursor, gap_start, "commitment")
            )
            cursor = gap_end
        self.day.blocks.append(Block(key, commitment.label, cursor, end, "commitment"))
        self.seeds[key] = (start, end)
        self._emit(key, commitment.label, start, end, "fixed", 1, status="fixed")

    def _add_group_commute(self, group: list[Commitment]) -> None:
        first, last = group[0], group[-1]
        commute = first.commute_minutes
        start, end = to_minutes(first.start), to_minutes(last.end)
        if not commute:
            self.day.away.append((start, end))
            return
        self._add_commute(
            f"commitment:{first.id}:leave",
            f"Leave for {first.label.lower()}",
            start - commute,
            start,
        )
        self._add_commute(
            f"commitment:{last.id}:return",
            f"Leave for home, arrive {to_hhmm(end + commute)}",
            end,
            end + commute,
        )
        self.day.away.append((start - commute, end + commute))

    def _add_commute(self, key: str, label: str, start: int, end: int) -> None:
        self.day.blocks.append(Block(key, label, start, end, "commute"))
        self.seeds[key] = (start, end)
        self._emit(key, label, start, end, "fixed", 1, status="fixed")

    def _add_calendar_event(self, event: CalendarEvent) -> None:
        key = f"cal:{event.source_id}"
        start = event.start.hour * 60 + event.start.minute
        end = event.end.hour * 60 + event.end.minute
        is_meal = event.classified_as == "meal"
        self.day.blocks.append(
            Block(key, event.title, start, end, "meal" if is_meal else "meeting")
        )
        self.seeds[key] = (start, end)
        flags = self._add_event_travel(event, key, start, end)
        rule_id = self._replace_rule_meal(event, key) if is_meal else None
        category = "meal" if is_meal else "fixed"
        self._emit(
            key, event.title, start, end, category, 1, status="fixed", rule_id=rule_id, flags=flags
        )

    def _add_event_travel(self, event: CalendarEvent, key: str, start: int, end: int) -> list[str]:
        commute = self.inp.profile.default_event_commute_minutes
        location = (event.location or "").lower()
        if any(word in location for word in ONLINE_WORDS) or any(
            word == location.strip() for word in self.inp.profile.home_words
        ):
            self.seeds[f"{key}:leave"] = (start, start)
            return []
        if not location:
            self.seeds[f"{key}:leave"] = (start - commute, start)
            self.day.away.append((start, end))
            return ["assumed_away"]
        self._add_commute(f"{key}:leave", f"Leave for {event.title}", start - commute, start)
        self._add_commute(
            f"{key}:return", f"Leave for home, arrive {to_hhmm(end + commute)}", end, end + commute
        )
        self.day.away.append((start - commute, end + commute))
        return []

    # Step 4: calendar meals

    def _replace_rule_meal(self, event: CalendarEvent, key: str) -> str | None:
        for rule in self.rules:
            if (
                isinstance(rule, EventRule)
                and rule.category == "meal"
                and rule.meal_type == event.meal_type
                and event.meal_type is not None
            ):
                self.overrides[rule.id] = self.seeds[key]
                self.replaced.add(rule.id)
                return rule.id
        return None

    # Step 3: rule DAG

    def _recompute(self) -> None:
        self.ideals, unresolved = compute_ideals(self.rules, self.wake, self.overrides, self.seeds)
        self.unresolved = unresolved

    # Spec 9.5: very late wake-up

    def _propose_meal_merge(self) -> None:
        meals = {i.rule.meal_type: i for i in self.ideals.values() if i.rule.category == "meal"}
        breakfast, lunch = meals.get("breakfast"), meals.get("lunch")
        if (
            not breakfast
            or not lunch
            or {breakfast.key, lunch.key} & (self.replaced | set(self.pinned))
        ):
            return
        gap = lunch.start - breakfast.end
        if gap >= self.inp.profile.meal_merge_gap_minutes:
            return
        label = f"{breakfast.label} + {lunch.label} (merged)"
        duration = max(breakfast.end - breakfast.start, lunch.end - lunch.start)
        merge = Option(id="merge", label=f"Merge into one meal at {to_hhmm(breakfast.start)}")
        keep = Option(id="keep_both", label=f"Keep {breakfast.label} and {lunch.label} separate")
        proposal = Proposal(
            key=f"merge:{breakfast.key}:{lunch.key}",
            kind="meal_merge",
            summary=f"Merge {breakfast.label} and {lunch.label}",
            reason=f"They would be only {max(gap, 0)} minutes apart after a late wake-up.",
            item_keys=[breakfast.key, lunch.key],
            chosen=merge,
            alternatives=[keep],
            context={"kind": "meal_merge"},
        )
        chosen = self._resolve(proposal)
        if chosen is None or chosen.id != "merge":
            return
        span = (breakfast.start, breakfast.start + duration)
        self.overrides[breakfast.key] = span
        self.overrides[lunch.key] = span
        self.merged.add(lunch.key)
        self.meta[breakfast.key] = self._meta_for(proposal, label=label)
        self._recompute()

    # Step 5 and spec 9.3: medicine loses its food

    def _run_fallbacks(self) -> None:
        handled: set[str] = set()
        for _ in range(MAX_PASSES):
            found = self._next_blocked_medicine(handled)
            if found is None:
                return
            medicine, meal, block = found
            handled.add(meal.key if meal else f"none:{medicine.key}")
            self._apply_fallback(medicine, meal, block)
            self._recompute()

    def _next_blocked_medicine(
        self, handled: set[str]
    ) -> tuple[IdealItem, IdealItem | None, Block | None] | None:
        status = self.inp.item_status
        medicines = [i for i in self.ideals.values() if i.rule.category == "medicine"]
        for medicine in sorted(medicines, key=lambda i: (i.start, i.key)):
            ref = medicine.rule.food_source_ref
            if not medicine.rule.requires_food or not ref or medicine.key in self.pinned:
                continue
            meal = self.ideals.get(ref)
            if meal is None:
                if f"none:{medicine.key}" not in handled:
                    return medicine, None, None
                continue
            if meal.key in handled or meal.key in self.replaced or meal.key in self.pinned:
                continue
            if meal.key in self.merged:
                continue
            block = self.day.block_at(meal.start, meal.end)
            if block or status.get(meal.key) == "skipped_by_user":
                return medicine, meal, block
        return None

    def _apply_fallback(
        self, medicine: IdealItem, meal: IdealItem | None, block: Block | None
    ) -> None:
        constraint = self.constraints.get(meal.key) if meal else None
        latest = 24 * 60
        if constraint and constraint.must_end_by:
            latest = to_minutes(constraint.must_end_by)
        if meal and self.inp.item_status.get(meal.key) == "skipped_by_user":
            latest = 0
        foods = sorted(self.inp.food_options, key=lambda f: f.id)
        options = generate_candidates(
            medicine, meal, latest, foods, self.day, self.weights, self._gap_ok
        )
        if not options:
            self.result.warnings.append(
                f"No food option works for {medicine.label}; kept at its ideal time as a reminder."
            )
            self.decided_meds.add(medicine.key)
            return
        name = meal.label if meal else "food"
        proposal = Proposal(
            key=f"fallback:{meal.key if meal else medicine.key}",
            kind="fallback",
            summary=f"{name} cannot happen: {options[0].label}",
            reason=f"{medicine.label} needs food and {name} is blocked.",
            item_keys=sorted(options[0].placements),
            chosen=options[0],
            alternatives=options[1:3],
            context={
                "kind": "fallback",
                "category": "medicine",
                "meal": meal.key if meal else None,
                "block_kind": block.kind if block else None,
            },
        )
        chosen = self._resolve(proposal)
        if chosen is None:
            self.result.warnings.append(
                f"{medicine.label} kept at its ideal time as a reminder; {name} needs a new time."
            )
            self.decided_meds.add(medicine.key)
            return
        for key, placed in chosen.placements.items():
            self.overrides[key] = (placed["start"], placed["end"])
            self.meta[key] = self._meta_for(
                proposal, label=placed["label"], silent=placed["silent"]
            )
        self.decided_meds.add(medicine.key)
        if meal:
            self.decided_meals.add(meal.key)
        else:
            self._emit_food_without_rule(chosen, medicine)

    def _emit_food_without_rule(self, chosen: Option, medicine: IdealItem) -> None:
        key = f"food_for_{medicine.key}"
        placed = chosen.placements.get(key)
        if placed:
            self.overrides.pop(key, None)
            meta = self.meta.get(key, _Meta())
            self._emit(
                key,
                placed["label"],
                placed["start"],
                placed["end"],
                "meal",
                3,
                status=meta.status,
                silent=placed["silent"],
                proposal_key=meta.proposal_key,
            )
            self.day.occupy(placed["start"], placed["end"])

    def _gap_ok(self, medicine: IdealItem, when: int) -> bool:
        group = self._dose_group(medicine)
        for other in self.ideals.values():
            if other.key == medicine.key or other.rule.category != "medicine":
                continue
            gap = self._gap_between(medicine, other)
            if self._dose_group(other) == group and gap and other.start <= medicine.start:
                if when - other.start < gap:
                    return False
        return True

    @staticmethod
    def _dose_group(item: IdealItem) -> str:
        return item.rule.dose_group or item.rule.id

    @staticmethod
    def _gap_between(first: IdealItem, second: IdealItem) -> int:
        return max(first.rule.min_gap_minutes or 0, second.rule.min_gap_minutes or 0)

    # Hard rule: doses never closer than min_gap_minutes

    def _enforce_spacing(self) -> None:
        rejected: set[str] = set()
        for _ in range(MAX_PASSES):
            pair = self._first_spacing_violation(rejected)
            if pair is None:
                return
            earlier, later, gap = pair
            target = earlier.start + gap
            duration = later.end - later.start
            option = Option(
                id="shift",
                label=f"Move {later.label} to {to_hhmm(target)} to keep {gap} minutes after "
                f"{earlier.label}",
                placements={later.key: {"start": target, "end": target + duration}},
            )
            proposal = Proposal(
                key=f"spacing:{later.key}",
                kind="spacing",
                summary=option.label,
                reason=f"Doses of this medicine must stay at least {gap} minutes apart.",
                item_keys=[later.key],
                chosen=option,
                context={"kind": "spacing", "category": "medicine"},
            )
            if self._resolve(proposal) is None:
                rejected.add(later.key)
                self.dropped.add(later.key)
                self._unslot(
                    later.key,
                    later.label,
                    duration,
                    f"{later.label} needs a new time.",
                    [f"Take {later.label} at {to_hhmm(target)} or later", "Skip this dose"],
                )
                continue
            self.overrides[later.key] = (target, target + duration)
            self.meta[later.key] = self._meta_for(proposal)
            self._recompute()

    def _first_spacing_violation(
        self, rejected: set[str]
    ) -> tuple[IdealItem, IdealItem, int] | None:
        groups: dict[str, list[IdealItem]] = {}
        for item in self.ideals.values():
            if item.rule.category == "medicine" and item.key not in rejected:
                groups.setdefault(self._dose_group(item), []).append(item)
        for name in sorted(groups):
            doses = sorted(groups[name], key=lambda i: (i.start, i.key))
            for earlier, later in zip(doses, doses[1:], strict=False):
                gap = self._gap_between(earlier, later)
                if gap and later.start - earlier.start < gap and later.key not in self._done_pins():
                    return earlier, later, gap
        return None

    def _done_pins(self) -> set[str]:
        return {k for k, v in self.pinned.items() if v == "done"}

    # Step 6: meals

    def _place_meals(self) -> None:
        meals = [i for i in self.ideals.values() if i.rule.category == "meal"]
        for meal in sorted(meals, key=lambda i: (i.start, i.key)):
            if meal.key in self.replaced or meal.key in self.merged or self._skipped(meal.key):
                continue
            if meal.key in self.pinned or meal.key in self.decided_meals:
                self._emit_ideal(meal)
                continue
            self._place_meal(meal)

    def _place_meal(self, meal: IdealItem) -> None:
        constraint = self.constraints.get(meal.key)
        start = meal.start
        latest = self.sleep
        if constraint:
            if constraint.must_start_after:
                start = max(start, to_minutes(constraint.must_start_after))
            if constraint.must_end_by:
                latest = to_minutes(constraint.must_end_by)
        duration = meal.end - meal.start
        if self.day.is_free(start, start + duration) and start + duration <= latest:
            self._emit_ideal(meal, start)
            return
        options = self._meal_options(meal, start, duration, latest, constraint)
        if not options:
            self._unslot(
                meal.key,
                meal.label,
                duration,
                _could_not_fit(meal.label, duration),
                ["Shorten it", f"Replace {meal.label} with a snack", "Skip it"],
            )
            return
        proposal = Proposal(
            key=f"move:{meal.key}",
            kind="move",
            summary=f"{meal.label} cannot happen at {to_hhmm(meal.start)}: {options[0].label}",
            reason=f"{meal.label} overlaps a fixed block or breaks its time window.",
            item_keys=[meal.key],
            chosen=options[0],
            alternatives=options[1:3],
            context={"kind": "move", "category": "meal"},
        )
        chosen = self._resolve(proposal)
        if chosen is None:
            self._unslot(
                meal.key,
                meal.label,
                duration,
                f"{meal.label} needs a new time.",
                ["Pick a new time", "Skip it"],
            )
            return
        placed = chosen.placements[meal.key]
        self.meta[meal.key] = self._meta_for(proposal, label=placed["label"])
        self._emit_ideal(meal, placed["start"], placed["end"] - placed["start"])

    def _meal_options(
        self,
        meal: IdealItem,
        start: int,
        duration: int,
        latest: int,
        constraint: ConstraintRule | None,
    ) -> list[Option]:
        options: list[Option] = []
        if constraint and constraint.on_violation == "substitute" and constraint.substitute_label:
            food = next(
                (f for f in self.inp.food_options if f.label == constraint.substitute_label), None
            )
            minutes = food.duration_minutes if food else 15
            slot = self.day.next_free(start, minutes, latest)
            if slot is not None:
                options.append(
                    _placement_option(
                        "substitute", meal.key, constraint.substitute_label, slot, minutes
                    )
                )
        slot = self.day.next_free(start, duration, latest)
        if slot is not None:
            options.append(_placement_option("nearest_slot", meal.key, meal.label, slot, duration))
        return options

    # Step 9 for items with an anchor that are not meals or medicines

    def _place_anchored_items(self) -> None:
        others = [i for i in self.ideals.values() if i.rule.category not in ("meal", "medicine")]
        for item in sorted(others, key=lambda i: (i.start, i.key)):
            if self._skipped(item.key):
                continue
            if item.key in self.pinned or self.day.is_free(item.start, item.end):
                self._emit_ideal(item)
                continue
            self._move_item(item)

    def _move_item(self, item: IdealItem) -> None:
        duration = item.end - item.start
        block = self.day.block_at(item.start, item.end)
        if is_point(duration) and item.rule.allowed_during_busy and block:
            self._propose_during_busy(item, block)
            return
        at_home = _wants_home(item.rule, self.inp.profile)
        slot = self.day.next_free(item.start, duration, self.sleep, at_home)
        if slot is None:
            self._unslot(
                item.key,
                item.label,
                duration,
                _could_not_fit(item.label, duration),
                ["Shorten it", "Move it to another day", "Skip it"],
            )
            return
        option = _placement_option("nearest_slot", item.key, item.label, slot, duration)
        proposal = Proposal(
            key=f"move:{item.key}",
            kind="move",
            summary=f"Move {item.label} to {to_hhmm(slot)}",
            reason=f"{item.label} overlaps a fixed block at {to_hhmm(item.start)}.",
            item_keys=[item.key],
            chosen=option,
            context={"kind": "move", "category": item.rule.category},
        )
        if self._resolve(proposal) is None:
            self._unslot(
                item.key,
                item.label,
                duration,
                f"{item.label} needs a new time.",
                ["Pick a new time", "Skip it"],
            )
            return
        self.meta[item.key] = self._meta_for(proposal)
        self._emit_ideal(item, slot)

    # Steps 5 and 9: medicines

    def _place_medicines(self) -> None:
        medicines = [i for i in self.ideals.values() if i.rule.category == "medicine"]
        for dose in sorted(medicines, key=lambda i: (i.start, i.key)):
            if self._skipped(dose.key) or dose.key in self.dropped:
                continue
            block = self.day.block_at(dose.start, dose.end)
            settled = dose.key in self.pinned or dose.key in self.decided_meds
            if block is None or settled:
                if dose.key not in self.meta and block is not None:
                    self.meta[dose.key] = _Meta(silent=True)
                self._emit_ideal(dose)
                continue
            self._propose_during_busy(dose, block)

    def _propose_during_busy(self, item: IdealItem, block: Block) -> None:
        duration = item.end - item.start
        options: list[Option] = []
        if item.rule.allowed_during_busy:
            food = " with something small" if getattr(item.rule, "requires_food", False) else ""
            options.append(
                Option(
                    id="during",
                    label=f"Take {item.label} during {block.label}{food} at {to_hhmm(item.start)}",
                    score=self.weights.get("during_busy", 60),
                    breakdown={"during_busy": self.weights.get("during_busy", 60)},
                    placements={item.key: {"start": item.start, "end": item.end, "silent": True}},
                )
            )
        after = block.end
        while (next_block := self.day.block_at(after, after + duration)) is not None:
            after = next_block.end
        delay = after - item.start
        options.append(
            Option(
                id="after",
                label=f"Take {item.label} right after {block.label} at {to_hhmm(after)}",
                score=delay * self.weights.get("per_minute_delay", 1),
                breakdown={"delay": delay * self.weights.get("per_minute_delay", 1)},
                placements={item.key: {"start": after, "end": after + duration, "silent": False}},
            )
        )
        options.sort(key=lambda o: (o.score, o.placements[item.key]["start"], o.id))
        proposal = Proposal(
            key=f"busy:{item.key}",
            kind="during_busy",
            summary=f"{item.label} falls during {block.label}",
            reason=f"{block.label} runs {to_hhmm(block.start)} to {to_hhmm(block.end)}.",
            item_keys=[item.key],
            chosen=options[0],
            alternatives=options[1:],
            context={
                "kind": "during_busy",
                "category": item.rule.category,
                "block_kind": block.kind,
                "label": item.rule.label,
            },
        )
        chosen = self._resolve(proposal)
        if chosen is None:
            self.result.warnings.append(
                f"{item.label} stays at {to_hhmm(item.start)} as a reminder."
            )
            self.meta[item.key] = _Meta(silent=True)
            self._emit_ideal(item)
            return
        placed = chosen.placements[item.key]
        self.meta[item.key] = self._meta_for(proposal, silent=placed["silent"])
        self._emit_ideal(item, placed["start"])

    # Step 7 for user tasks without a set time

    def _place_tasks(self) -> None:
        for rule in self._unanchored("task"):
            if self._skipped(rule.id):
                continue
            latest = self.sleep
            if rule.deadline:
                latest = to_minutes(rule.deadline)
            elif rule.before_first_commitment:
                latest = min((b.start for b in self.day.blocks), default=latest)
            at_home = _wants_home(rule, self.inp.profile)
            slot = self.day.next_free(self.earliest, rule.duration_minutes, latest, at_home)
            if slot is None:
                self._unslot(
                    rule.id,
                    rule.label,
                    rule.duration_minutes,
                    _could_not_fit(rule.label, rule.duration_minutes),
                    ["Shorten it", "Move it to another day", "Skip it"],
                )
                continue
            self._emit_rule(rule, slot)

    # Steps 7 and 8: quota and low-priority items

    def _place_flexible(self) -> None:
        first_meal = self._first_meal_end()
        cursor = max(self.earliest, first_meal if first_meal is not None else self.earliest)
        placed_labels: list[str] = []
        for rule, promoted in self._flexible_rules():
            if self._skipped(rule.id):
                continue
            slot = self.day.next_free(
                cursor, rule.duration_minutes, self.sleep, _wants_home(rule, self.inp.profile)
            )
            if slot is None and rule.category == "quota":
                slot = self._overflow_slot(rule, cursor)
            if slot is None:
                self._unslot_flexible(rule, placed_labels)
                continue
            self._emit_rule(rule, slot, promoted=promoted)
            placed_labels.append(rule.label)
            cursor = slot + rule.duration_minutes

    def _overflow_slot(self, rule: EventRule, cursor: int) -> int | None:
        slot = self.day.next_free(
            cursor,
            rule.duration_minutes,
            self.sleep + rule.duration_minutes,
            _wants_home(rule, self.inp.profile),
        )
        return slot if slot is not None and slot < self.sleep else None

    def _flexible_rules(self) -> list[tuple[EventRule, bool]]:
        order = {name: i for i, name in enumerate(self.inp.profile.priority_order)}
        chosen: list[tuple[int, int, EventRule, bool]] = []
        rules = self._unanchored("quota") + self._unanchored("low_priority")
        for index, rule in enumerate(rules):
            promoted = False
            quota = find_quota(rule.label, self.inp.quotas)
            if quota:
                state = quota_state(quota, self.inp.quota_history, self.inp.date)
                if state.weekly_target_met:
                    self.result.explanations_data.append(
                        {"kind": "quota_met", "item": rule.label, "done": state.done_this_week}
                    )
                    continue
                promoted = state.miss_limit_reached
                if promoted:
                    self.result.skip_warnings[rule.id] = (
                        f"{rule.label} miss limit reached this month. Skip anyway?"
                    )
            rank = -1 if promoted else order.get(rule.category, 99)
            chosen.append((rank, index, rule, promoted))
        return [(c[2], c[3]) for c in sorted(chosen, key=lambda c: (c[0], c[1]))]

    def _unslot_flexible(self, rule: EventRule, placed_labels: list[str]) -> None:
        minutes = rule.duration_minutes
        message = self.result.skip_warnings.get(rule.id) or _could_not_fit(rule.label, minutes)
        recommendations = [f"Skip {rule.label}"]
        for other in placed_labels:
            recommendations += [f"Shorten {other}", f"Swap {rule.label} with {other}"]
        if len(recommendations) == 1:
            recommendations.append(f"Shorten {rule.label}")
        hint = self.inp.profile.recommend_first.get(rule.id)
        if hint:
            recommendations = [hint, *[r for r in recommendations if r != hint]]
        self._unslot(rule.id, rule.label, minutes, message, recommendations)

    def _unanchored(self, category: str) -> list[EventRule]:
        return [
            r
            for r in self.rules
            if isinstance(r, EventRule)
            and r.category == category
            and r.anchor is None
            and r.id not in self.overrides
        ]

    def _first_meal_end(self) -> int | None:
        ends = [
            i.end
            for i in self.result.items
            if i.category == "meal" and not is_point(i.end - i.start)
        ]
        return min(ends) if ends else None

    # Spec 9.6: sleep time

    def _flag_sleep(self) -> None:
        for item in list(self.result.items):
            if item.end <= self.sleep or item.tier == 1 or item.key in self.pinned:
                continue
            if item.category == "medicine":
                self.result.warnings.append(f"{item.label} falls after sleep time.")
                continue
            self._propose_sleep_change(item)

    def _propose_sleep_change(self, item: PlanItem) -> None:
        item.flags.append("ends_after_sleep")
        duration = item.end - item.start
        options: list[Option] = []
        fit = self.sleep - item.start
        if fit >= MIN_SHORTENED_MINUTES:
            options.append(
                Option(
                    id="shorten",
                    label=f"Shorten {item.label} to {fit} minutes",
                    placements={item.key: {"start": item.start, "end": self.sleep}},
                )
            )
        slot = self.day.next_free(self.earliest, duration, self.sleep)
        if slot is not None:
            options.append(
                Option(
                    id="move",
                    label=f"Move {item.label} to {to_hhmm(slot)}",
                    placements={item.key: {"start": slot, "end": slot + duration}},
                )
            )
        if find_quota(item.label, self.inp.quotas):
            options.append(Option(id="use_quota_miss", label=f"Use a quota miss for {item.label}"))
        if not options:
            options.append(Option(id="keep", label=f"Keep {item.label} as is"))
        proposal = Proposal(
            key=f"sleep:{item.key}",
            kind="sleep",
            summary=f"{item.label} would end after sleep time ({to_hhmm(self.sleep)})",
            reason=f"{item.label} ends at {to_hhmm(item.end)}.",
            item_keys=[item.key],
            chosen=options[0],
            alternatives=options[1:],
            context={"kind": "sleep", "category": item.category},
        )
        chosen = self._resolve(proposal)
        item.proposal_key = proposal.key
        if proposal.state == "pending" or chosen is None:
            return
        if chosen.id == "use_quota_miss":
            self.result.items.remove(item)
            return
        placed = chosen.placements.get(item.key)
        if placed:
            item.start, item.end = placed["start"], placed["end"]
            item.flags.remove("ends_after_sleep")
            item.status = "auto" if proposal.state == "auto" else "scheduled"

    # Proposals

    def _resolve(self, proposal: Proposal) -> Option | None:
        """Decide how a proposal applies. Returns the option to apply, or None to apply none."""
        self.result.proposals.append(proposal)
        options = [proposal.chosen, *proposal.alternatives]
        decision = self.inp.approved_decisions.get(proposal.key)
        if decision and decision.rejected:
            proposal.state = "rejected"
            return None
        if decision and decision.choice:
            match = next((o for o in options if o.id == decision.choice), None)
            if match:
                _choose(proposal, options, match)
                proposal.state = "approved"
                return match
        suppress = bool(decision and decision.suppress_auto)
        standing = self._standing_for(proposal)
        if standing and not suppress:
            wanted = standing.action.get("choice", "top")
            match = next((o for o in options if o.id == wanted), proposal.chosen)
            _choose(proposal, options, match)
            proposal.state = "standing"
            return match
        proposal.state = "auto" if self.mode == "unrestricted" and not suppress else "pending"
        return proposal.chosen

    def _standing_for(self, proposal: Proposal) -> StandingApproval | None:
        for standing in self.inp.standing_approvals:
            if all(proposal.context.get(k) == v for k, v in standing.match.items()):
                return standing
        return None

    @staticmethod
    def _meta_for(proposal: Proposal, label: str | None = None, silent: bool = False) -> _Meta:
        status = {"pending": "proposed", "auto": "auto"}.get(proposal.state, "scheduled")
        return _Meta(label=label, silent=silent, status=status, proposal_key=proposal.key)

    # Output helpers

    def _skipped(self, key: str) -> bool:
        return self.inp.item_status.get(key) == "skipped_by_user"

    def _emit_rule(self, rule: EventRule, start: int, promoted: bool = False) -> None:
        self._emit(
            rule.id,
            rule.label,
            start,
            start + rule.duration_minutes,
            rule.category,
            TIERS.get(rule.category, 5),
            rule_id=rule.id,
            promoted=promoted,
        )
        self.day.occupy(start, start + rule.duration_minutes)

    def _emit_ideal(
        self, item: IdealItem, start: int | None = None, duration: int | None = None
    ) -> None:
        begin = item.start if start is None else start
        minutes = (item.end - item.start) if duration is None else duration
        meta = self.meta.get(item.key, _Meta())
        self._emit(
            item.key,
            meta.label or item.label,
            begin,
            begin + minutes,
            item.rule.category,
            TIERS.get(item.rule.category, 5),
            status=meta.status,
            silent=meta.silent,
            proposal_key=meta.proposal_key,
            rule_id=item.rule.id,
        )
        self.day.occupy(begin, begin + minutes)

    def _emit(
        self, key: str, label: str, start: int, end: int, category: str, tier: int, **extra: Any
    ) -> None:
        self.result.items.append(
            PlanItem(
                key=key, label=label, start=start, end=end, category=category, tier=tier, **extra
            )
        )

    def _unslot(
        self, key: str, label: str, minutes: int, message: str, recommendations: list[str]
    ) -> None:
        self.result.unslotted.append(
            UnslottedItem(
                key=key,
                label=label,
                duration_minutes=minutes,
                message=message,
                recommendations=recommendations,
            )
        )

    # Steps 10 to 12: finish

    def _finish(self) -> None:
        for rule in self.unresolved:
            self.result.warnings.append(f"{rule.label} could not be placed: its anchor is missing.")
        status = self.inp.item_status
        for item in self.result.items:
            recorded = status.get(item.key)
            if recorded in ("done", "not_done") and item.tier > 1:
                replanned = item.key in self.overrides and item.key not in self.pinned
                if recorded == "done" or not replanned:
                    item.status = recorded
        self.result.items.sort(key=lambda i: (i.start, i.tier, i.key))
        self.result.rows = _build_rows(self.result.items)
        for proposal in self.result.proposals:
            self.result.explanations_data.append(
                {
                    "key": proposal.key,
                    "kind": proposal.kind,
                    "summary": proposal.summary,
                    "score": proposal.chosen.score,
                    "breakdown": proposal.chosen.breakdown,
                    "alternatives": [
                        {"label": o.label, "score": o.score} for o in proposal.alternatives
                    ],
                }
            )


def _choose(proposal: Proposal, options: list[Option], match: Option) -> None:
    proposal.chosen = match
    proposal.alternatives = [o for o in options if o.id != match.id]


def _placement_option(option_id: str, key: str, label: str, start: int, minutes: int) -> Option:
    return Option(
        id=option_id,
        label=f"{label} at {to_hhmm(start)}",
        placements={key: {"label": label, "start": start, "end": start + minutes, "silent": False}},
    )


def _could_not_fit(label: str, minutes: int) -> str:
    return f"Couldn't fit: {label} ({format_duration(minutes)})"


def _wants_home(rule: Any, profile: ProfileInput) -> bool:
    location = (getattr(rule, "location", None) or "").strip().lower()
    return location in profile.home_words


def _build_rows(items: list[PlanItem]) -> list[PlanRow]:
    """Merge point items at the same minute into one row. Longer items keep their own row."""
    rows: list[PlanRow] = []
    point_rows: dict[int, PlanRow] = {}
    for item in items:
        if is_point(item.end - item.start):
            row = point_rows.get(item.start)
            if row:
                row.label = f"{row.label} + {item.label}"
                row.item_keys.append(item.key)
                continue
            row = PlanRow(start=item.start, end=item.end, label=item.label, item_keys=[item.key])
            point_rows[item.start] = row
            rows.append(row)
        else:
            rows.append(
                PlanRow(start=item.start, end=item.end, label=item.label, item_keys=[item.key])
            )
    return sorted(rows, key=lambda r: r.start)


__all__ = ["build_plan", "Conflict"]
