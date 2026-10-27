# Decisions

Choices made where the spec was silent or its sections disagreed. The golden tests won every disagreement.

## Project layout

- The repo root is the spec's `planner/` folder, so `app/`, `fixtures/`, and `tests/` sit at the top level and the existing `.env` is found without extra config.

## Scheduler

1. **Golden 16.5 versus step 7 (gym).** Step 7 says "first free slot", which would put gym at 8:00 to 9:00, before breakfast. The golden test has gym at 19:30. Rule: quota and low-priority items are not placed before the end of the day's first meal.
2. **Golden 16.5 versus step 8 (yoga).** First fit would put yoga at 18:35, but the golden test has it at 20:30. Rule: flexible items are placed in priority order and each starts after the previous one ends.
3. **Fallback food options.** Spec 9.3 offers every food option in the nearest free slot, but an apple at 14:00 would score 51 in 16.5 and 111 in 16.6, contradicting both golden tables. Rule: options allowed during busy time are only candidates inside the busy block; other options are only candidates in free slots.
4. **"Nearest free slot" looks forward only.** An earlier slot would put a meal before its ideal time and beat the golden 16.6 candidates.
5. **Pending proposals show tentatively.** Golden 16.5 lists lunch at 14:00, while 9.3 requires approval. The plan shows the top option with status `proposed` until the user answers. An undone auto-change goes back to this state.
6. **Point items.** Items of 5 minutes or less do not occupy time, so a dose can share a minute with gym, as in the 16.5 note. Point items at the same minute share a row; longer items keep their own row.
7. **Scheduler proposal keys are stable per item** (`fallback:lunch`, `busy:medicine_2`, ...), so saved decisions survive a recompute.
8. **Calendar events without a location** get no commute block, because golden 16.6 needs lunch at 14:00 right after its meeting. They are marked `assumed_away`, and prep reminders use the default commute time for leave-by.
9. **Calendar events with a location** use `default_event_commute_minutes` (30), since events carry no commute length.
10. **Priority order.** Medicines and meals always go first because the hard rules depend on them. The Profile's order changes how quota and low-priority items rank against each other.
11. **Weekly quotas** count from Monday. Once the target is met, the item is not planned for the rest of the week.
12. **Late opening, Override** rebuilds the day with wake-up set to the restart time; Done items stay where they were.
13. **Meal merge gap** defaults to 120 minutes (`meal_merge_gap_minutes`).
14. **Rejecting a spacing change** puts the dose in the unslotted list. It is never placed closer than `min_gap_minutes`.
