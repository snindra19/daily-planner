"""Fallback planner for a medicine that loses its food source (spec 9.3)."""

from __future__ import annotations

from app.scheduler.dag import IdealItem
from app.scheduler.placement import DayState
from app.schemas.scheduling import FoodOption, Option, to_hhmm

DEFAULT_TOLERANCE = 120


def score_candidate(
    delay: int,
    weights: dict[str, float],
    during_busy: bool,
    downgraded: bool,
    skipped: bool = False,
) -> tuple[float, dict[str, float]]:
    """Return the total score and its breakdown. Penalties apply only when they occur."""
    busy_weight = weights.get("during_busy_food", weights.get("during_busy", 60))
    breakdown: dict[str, float] = {"delay": max(delay, 0) * weights.get("per_minute_delay", 1)}
    if during_busy:
        breakdown["during_busy"] = busy_weight
    if downgraded:
        breakdown["meal_downgraded"] = weights.get("meal_downgraded", 20)
    if skipped:
        breakdown["meal_skipped"] = weights.get("meal_skipped", 40)
    return sum(breakdown.values()), breakdown


def _food_entry(key: str, label: str, start: int, duration: int, silent: bool) -> dict:
    return {"key": key, "label": label, "start": start, "end": start + duration, "silent": silent}


def generate_candidates(
    medicine: IdealItem,
    meal: IdealItem | None,
    meal_latest_end: int,
    food_options: list[FoodOption],
    day: DayState,
    weights: dict[str, float],
    is_gap_ok,
) -> list[Option]:
    """Build every valid candidate for the medicine, best first."""
    tolerance = medicine.rule.tolerance_minutes
    tolerance = DEFAULT_TOLERANCE if tolerance is None else tolerance
    ideal = medicine.start
    ideal_food = meal.start if meal else ideal
    meal_key = meal.key if meal else f"food_for_{medicine.key}"
    meal_label = meal.label if meal else "food"
    meal_gone = f"{meal_label} skipped" if meal else None
    duration = medicine.end - medicine.start
    options: list[Option] = []

    def after_food(
        option_id: str, label: str, food_start: int, food_minutes: int, downgraded: bool
    ):
        dose_at = food_start + food_minutes
        delay = dose_at - ideal
        if delay > tolerance or not is_gap_ok(medicine, dose_at):
            return
        score, breakdown = score_candidate(delay, weights, False, downgraded)
        options.append(
            Option(
                id=option_id,
                label=f"{label} at {to_hhmm(food_start)}, {medicine.label} at {to_hhmm(dose_at)}",
                score=score,
                breakdown=breakdown,
                placements={
                    meal_key: _food_entry(meal_key, label, food_start, food_minutes, False),
                    medicine.key: _food_entry(
                        medicine.key, medicine.label, dose_at, duration, False
                    ),
                },
                meal_skipped=meal_gone if downgraded else None,
            )
        )

    if meal is not None:
        minutes = meal.end - meal.start
        slot = day.next_free(ideal_food, minutes, meal_latest_end)
        if slot is not None:
            after_food("meal_nearest_slot", meal.label, slot, minutes, False)

    usable = [f for f in food_options if f.approved]
    can_downgrade = meal is None or meal.rule.skippable
    for food in usable if can_downgrade else []:
        if food.allowed_during_busy:
            continue
        slot = day.next_free(ideal_food, food.duration_minutes, 24 * 60)
        if slot is not None:
            after_food(f"food:{food.id}", food.label, slot, food.duration_minutes, True)

    for food in usable if can_downgrade else []:
        block = day.block_at(ideal, ideal + 1)
        if not food.allowed_during_busy or not medicine.rule.allowed_during_busy or block is None:
            continue
        if not is_gap_ok(medicine, ideal):
            continue
        score, breakdown = score_candidate(0, weights, True, True)
        options.append(
            Option(
                id=f"food:{food.id}:during_busy",
                label=f"{food.label} + {medicine.label} at {to_hhmm(ideal)}, during {block.label}",
                score=score,
                breakdown=breakdown,
                placements={
                    meal_key: _food_entry(meal_key, food.label, ideal, food.duration_minutes, True),
                    medicine.key: _food_entry(medicine.key, medicine.label, ideal, duration, True),
                },
                meal_skipped=meal_gone,
            )
        )

    def order(option: Option) -> tuple:
        return (option.score, option.placements[medicine.key]["start"], option.id)

    return sorted(options, key=order)
