# Decisions

Choices made where the spec was silent or its sections disagreed. The golden tests won every disagreement.

## Test changes where the spec left a value open

No expected value from a spec table was changed to make a test pass. These are the test edits made while fixing failures, and why:

- **E14, Medicine 2 at 16:30 (was 16:20 in my first draft of the test).** The spec gives no clock time for E14, only "keep medicine spacing; push later doses", and it does not say how long a merged meal lasts. I chose the longer of the two meals (30 minutes): 12:00 to 12:30, then doses at 12:30 and 16:30. The 16:20 assumed the 20-minute breakfast length.
- **Scenario database seed now uses the fixture's `wake`.** The E11 and L5 fixtures state their wake-up time, and the seed helper was ignoring it, so those tests ran at the 07:00 default. The fixture values are unchanged.
- **C7 test stub.** The stub rejected every LLM call. It now rejects only `classify`, which is what C7 is about, because recomputing also asks for an explanation of an unrelated proposal.
- **Golden 16.5 fixture corrected toward the spec.** My first version said "Leave for Class" and listed Gym before Medicine 3. Both now match the spec's table, and the code was changed to produce them: commitment labels are lowercased in "Leave for ...", and rows at the same minute keep tier order.

## LLM

- **No `temperature` or forced `tool_choice`.** Spec 10.1 asks for temperature 0 and structured output through tool use. `claude-sonnet-5-5` returns a 400 for a non-default temperature and for forced tool choice, and the API now rejects `temperature` on every model. The client uses `tool_choice: auto` with a prompt instruction naming the tool, then validates with Pydantic and retries once. Live outputs can vary between runs; the scheduler stays deterministic because it never reads them directly.
- **No `strict` tool schemas.** The rule payload schema is a large union with many optional fields. Pydantic validation plus the retry covers the same risk.
- **Mock recordings are keyed on a task's primary input only**, such as the text being parsed, not on the confirmed-rules context. Otherwise every recording would break whenever a rule changed.
- **Parsing covers more than the four rule kinds in Section 8.** P1 needs commitments, food options, quotas, and profile settings from text, and E11 needs day overrides. These are stored as draft rules and written to their own tables when confirmed. `rules.kind` therefore has extra values.

## Calendar

- **Unclear titles.** Spec 11.2 lists "other busy events are meetings" before "unclear goes to the LLM", which would make the last step unreachable. A title is treated as unclear when it has a social word (catch-up, coffee, drinks) or "at <place>". The LLM's answer applies right away and is confirmed once.
- **Food option ids are slugs of their labels**, so saved decisions such as `food:apple:during_busy` mean the same thing across databases.

## App

- **Routes are synchronous `def`.** FastAPI runs them in its thread pool, and the sync SQLAlchemy and SDK calls do not block the event loop.
- **Tables are created on startup.** There are no migrations, since v1 is a single local database.
- **SQLAlchemy 2.x package, 1.x style.** The spec's rule is about syntax: `declarative_base()` and `Column(...)`, never `Mapped[]`.
- **Extra columns beyond Section 7:** `approvals.key/origin/summary/context/chosen_option`, `rules.confidence/needs_clarification/suggestion`, `item_status.alarm_mode`, `questions.question_key/created_records`.
- **Extra routes beyond Section 13:** `/tomorrow/extra`, `/tomorrow/signoff`, `/profile/settings`, `/commitments`, `/foods`, `/quotas/{id}/delete`, `/items/{key}/alarm`, `/standing/{id}/revoke`.
- **Prep proposals run when Tomorrow opens**, and only again if the day's events changed, so reloading the page costs nothing.

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
