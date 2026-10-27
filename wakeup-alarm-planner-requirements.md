# Wake-Up Relative Daily Planner: Full Build Spec (v1)

## 0. How to use this document (instructions for Claude Code)

- This is the single source of truth. Read it fully before planning.
- Start by proposing a plan that follows the milestones in Section 15. Do not write UI code before the scheduler (Milestone 1) passes its tests.
- **Python is the primary language.** Keep JavaScript to the minimum listed in Section 3.
- The scheduler must be a **pure, deterministic Python module** with no database, network, or LLM calls inside it.
- Unit and scenario tests must **never call the live LLM or Google**. Use the mock modes in Sections 10.8 and 11.4.
- Where something is not specified, choose the simplest option, note it in `DECISIONS.md`, and continue.
- Never write to Google Calendar. Read-only, always.
- Google Calendar is read **through an MCP server**: the app is an MCP client that calls the server's read tools directly from Python code, with no LLM in between (Section 11.3).

---

## 1. Product summary

A personal daily planner that builds the day's alarms and reminders around when the user **actually** woke up.

- The user describes their routine once, in messy natural English: meals, medicines, gym, yoga, college or work, commute, worship, preferences.
- Every night, the user sets tomorrow's wake-up target, reviews tomorrow's pre-computed plan, and adds anything extra in plain English.
- Every morning, the user enters their actual wake-up time and the day is recomputed.
- The app reads Google Calendar (read-only). Calendar events outrank everything. A calendar meal replaces a planned meal.
- The AI infers tasks nobody stated (pack medicines before dinner out, take laptop to dinner if a meeting follows) and proposes them for approval.
- Medicines are never crammed or skipped automatically. Low-priority items can be violated only with the user's approval. **Nothing is ever skipped unless the user skips it.**
- Output is a list of items, each with an alarm/reminder toggle.

---

## 2. Scope

### v1 (this build): web app

- Runs locally in the browser, single user, no login.
- Alarms and reminders are **displayed only**. The alarm/reminder toggle is stored as a setting.
- The app updates only when opened or refreshed. No background jobs, no push notifications.
- Approvals wait in a queue until the user opens the app.
- Google Calendar is read-only.

### v2 (not now)

- Mobile app with real repeating alarms (vibration, ringtone), push notifications, responding to approvals from an alarm, scheduled night-review prompt.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12+** | User's primary language. |
| Env and packages | `uv` with `pyproject.toml` | Fast, reproducible installs. |
| Web framework | **FastAPI** | Async, typed, serves both pages and JSON. |
| UI | **Jinja2 templates + HTMX + Tailwind (CDN) + SortableJS (CDN)** | Keeps the UI almost entirely in Python. HTMX handles partial page updates; SortableJS is the only JS, for drag and drop lists. |
| Database | **SQLite** | Single user, zero setup. |
| ORM | **SQLAlchemy 1.x style** (`declarative_base()`, `Column(...)`), never 2.0 `Mapped[]` syntax | User's standard. |
| Validation | **Pydantic v2** + `pydantic-settings` for config | Rule payloads and LLM output are validated against Pydantic models. |
| LLM | **Anthropic Python SDK** (`anthropic`), model from env var, default `claude-sonnet-5-5` | Structured output through tool use, validated with Pydantic. |
| Calendar | **MCP client** using the official `mcp` Python SDK, connecting to a Google Calendar MCP server over stdio | User's choice. The same server can also be used by Claude Code during development. |
| Time | `zoneinfo` (stdlib) | All times are timezone-aware in the user's timezone. |
| Fuzzy matching | `rapidfuzz` | Matching typo'd labels ("eyed rops") and event titles. |
| Tests | `pytest`, `freezegun` | Deterministic time in tests. |
| Lint/format | `ruff` | One tool for both. |

---

## 4. Prerequisites and credentials

### 4.1 Installed on the machine

- Python 3.12+
- `uv` (`pip install uv` or the official installer)
- git

### 4.2 `.env` (create from `.env.example`; never commit `.env`)

```dotenv
# App
APP_SECRET_KEY=            # random string, for session cookies
APP_TIMEZONE=America/Bogota
DATABASE_URL=sqlite:///./planner.db

# LLM
LLM_MODE=mock              # mock | live
ANTHROPIC_API_KEY=         # required only when LLM_MODE=live
ANTHROPIC_MODEL=claude-sonnet-5-5

# Calendar
CALENDAR_MODE=mock         # mock | mcp
MCP_CALENDAR_COMMAND=      # command that starts the Google Calendar MCP server (stdio)
MCP_CALENDAR_ARGS=         # its arguments, space separated
MCP_CALENDAR_ENV_FILE=     # optional: env file with the server's Google credentials paths
MCP_LIST_EVENTS_TOOL=      # name of the server's list-events tool
```

The app must start and pass all tests with `LLM_MODE=mock` and `CALENDAR_MODE=mock` and no keys at all.

---

## 5. Architecture

### 5.1 Three layers

| Layer | Job | Implemented by |
|---|---|---|
| **Understand** | Turn messy English into structured rules, preferences, and answers. Detect contradictions. | LLM |
| **Infer** | Work out tasks nobody stated (pack meds, take laptop), suggest unknown medicine timing, classify unclear calendar titles, explain decisions. | LLM |
| **Schedule** | Decide when everything happens, given priorities, constraints, quotas, calendar, and wake-up time. | Pure Python code |

**Hard rule:** the LLM never computes final times and never applies a change on its own. Everything it produces is either a rule card the user confirms, or an approval the user accepts.

### 5.2 Request flow

```
Browser (HTMX) -> FastAPI routes -> services (orchestration, DB)
                                       |-> scheduler (pure)      -> plan
                                       |-> llm (parse/infer)     -> rules, proposals
                                       |-> calendar (mock/google)-> events
```

### 5.3 Project structure

```
planner/
  app/
    main.py                # FastAPI app, router registration
    config.py              # pydantic-settings
    db.py                  # engine, SessionLocal, Base
    models.py              # SQLAlchemy 1.x Column-style tables
    schemas/               # Pydantic models (rules, plan items, LLM outputs)
    scheduler/             # PURE: no DB, network, or LLM
      engine.py            # build_plan(...)
      dag.py               # dependency graph, cycle detection
      placement.py         # tiered placement, free-slot search
      fallback.py          # food + medicine fallback planner
      quotas.py            # weekly/monthly quota logic
      conflicts.py         # indirect conflict detection
    llm/
      client.py            # live and mock clients, same interface
      parse.py             # text -> rules
      questions.py         # follow-up question generation
      contradictions.py    # new rule vs existing rules
      infer.py             # prep reminders, medicine timing suggestions
      explain.py           # one-line explanations
      prompts/             # prompt templates
    calendar/
      base.py              # CalendarSource interface
      mock.py              # reads fixtures/mock_calendar.json
      mcp_source.py        # MCP client: calls the server's read tools, maps output to CalendarEvent
      classify.py          # meal / meeting / ignore
    services/              # plan_service, rule_service, approval_service, ...
    routes/                # pages and HTMX partial routes
    templates/             # Jinja2
    static/
  fixtures/
    mock_calendar.json
    llm_recordings/        # recorded LLM outputs keyed by input hash
    scenarios/             # rule sets for acceptance tests
  tests/
    scheduler/
    llm/
    scenarios/
  DECISIONS.md
  .env.example
  pyproject.toml
  README.md
```

---

## 6. Domain concepts

### 6.1 Priority tiers (default order, user-editable in Profile)

1. **Fixed:** calendar events and explicitly stated commitments (classes, work, worship), including commute. Never moved.
2. **Medicines:** must happen the required number of times, each with at least a little food when the rule says so. Never crammed, never skipped by the app.
3. **Meals:** 3 a day by default. May be downgraded (meal to snack) or replaced (calendar meal) to serve medicines and fixed items.
4. **Quota items:** for example gym, 4 times a week, at most 1 miss per month.
5. **Low priority:** for example yoga. Fills leftover time.

Within the user's ordering, any item below tier 2 can be violated **only with the user's approval**.

### 6.2 Wake-up time levels

1. **Setup default:** usual wake-up per weekday (and usual sleep time, usual meal times).
2. **Night plan target:** tomorrow's wake-up, asked first in the night review. Overrides the default for that date.
3. **Morning actual:** entered in the morning. Overrides both and triggers the final recompute.

If no night target is entered, the setup default is used. If no morning actual is entered, the plan stands on the target.

### 6.3 Medicines

- Timing source is the user (from their doctor). Typical input: "twice a day, after food".
- If the user does not give spacing or which meals, the AI **suggests** (for example, after breakfast and after dinner) and the user confirms. A medicine with unconfirmed timing is **not scheduled**; it shows as "needs your confirmation".
- Confirmed spacing becomes `min_gap_minutes`, which the scheduler never breaks.
- `requires_food`: the dose must follow a meal, snack, or approved food option.

### 6.4 Food options

User-approved substitutes used when a meal cannot happen: full meal, snack (about 15 min), quick item like an apple (about 1 min, allowed during class or meetings). The AI may propose new options; they need approval once.

### 6.5 Approval modes

A global setting in Profile, switchable anytime, with an optional per-day override ("restricted just for tomorrow").

| Mode | Behavior |
|---|---|
| **Restricted** (default) | Every proposal (moves, substitutions, fallback choices, prep reminders, items during busy blocks, cramming changes, contradiction picks) waits in the approvals queue. |
| **Unrestricted** | The scheduler's top-scoring proposal is **applied automatically**. AI prep reminders are added automatically. Each auto-applied change is written to the activity log with its reason and alternatives, and can be undone with one tap. |

**Hard rules that apply in both modes** (these are not approvals, so unrestricted mode cannot bypass them):

- Medicines are never placed closer than `min_gap_minutes`.
- Nothing is ever skipped by the app. A skip is always a user action, so in unrestricted mode an item that cannot fit stays in the unslotted list with recommendations.
- A medicine with unconfirmed timing is not scheduled until the user confirms it.
- Contradictory rules are never resolved by the app; the user picks.
- Google Calendar is never written to.

Standing approvals still apply in restricted mode, so the user can stay restricted but auto-allow specific situations.

### 6.6 Quotas

Example: gym `target_per_week = 4`, `max_misses_per_month = 1`. Counted from completion records (Done / Not done). If the monthly miss is already used, the quota item is promoted and any skip shows "Gym miss limit reached this month. Skip anyway?"

---

## 7. Data model (SQLite, SQLAlchemy 1.x Column style)

| Table | Key columns | Notes |
|---|---|---|
| `profile` | `id`, `timezone`, `default_wake_by_weekday` (JSON), `sleep_time`, `night_review_time`, `priority_order` (JSON list), `weights` (JSON), `approval_mode` (`restricted`, `unrestricted`) | Single row. |
| `rules` | `id`, `raw_text`, `payload` (JSON, validated by Pydantic), `kind` (`event`, `series`, `constraint`, `preference`), `status` (`draft`, `confirmed`, `archived`), `source` (`user`, `ai_suggested`), `created_at` | Payload schemas in Section 8. |
| `commitments` | `id`, `label`, `days_of_week`, `start`, `end`, `location`, `commute_minutes`, `in_window_kind`, `breaks` (JSON) | Classes, work, worship. |
| `food_options` | `id`, `label`, `duration_minutes`, `allowed_during_busy`, `counts_as_meal`, `approved` | |
| `quotas` | `id`, `item_label`, `target_per_week`, `max_misses_per_month` | |
| `day_plans` | `id`, `date`, `target_wake`, `actual_wake`, `late_mode` (`override`, `continue`, null), `reviewed_at`, `plan_snapshot` (JSON) | One per date. |
| `item_status` | `id`, `date`, `item_key`, `status` (`pending`, `done`, `not_done`, `skipped_by_user`) | Feeds quotas and late-open flow. |
| `day_overrides` | `id`, `date`, `changes` (JSON) | "No class tomorrow afternoon". Can also hold `approval_mode` for that date. |
| `decision_history` | `id`, `date`, `kind` (fallback, during_busy, prep_reminder, cramming, reorder, skip, ...), `context` (JSON: weekday, time, item labels, busy block type, location known, mode), `options_offered` (JSON), `chosen` (JSON or null), `signal` (`approved`, `rejected`, `picked_alternative`, `undone`, `manual_edit`, `manual_skip`), `created_at` | Every user decision, in both modes. Undo in unrestricted mode is a negative signal. |
| `learned_preferences` | `id`, `description`, `match` (JSON), `effect` (JSON: a standing approval, a weight change, or "don't propose"), `evidence_count`, `consistency`, `status` (`proposed`, `active`, `revoked`), `created_at` | Patterns the AI found in `decision_history`. |
| `activity_log` | `id`, `date`, `change` (JSON), `reason`, `alternatives` (JSON), `applied_by` (`auto`, `user`), `undone` (bool), `created_at` | Every auto-applied change in unrestricted mode, with undo. |
| `approvals` | `id`, `date`, `kind`, `proposed_change` (JSON), `reason`, `alternatives` (JSON), `status`, `remember_as_rule` | |
| `standing_approvals` | `id`, `match` (JSON), `action` (JSON) | "Always allow meds during meetings". |
| `questions` | `id`, `text`, `condition`, `depends_on` (JSON), `answer`, `status` (`pending`, `answered`, `obsolete`) | |
| `calendar_cache` | `id`, `date`, `source_id`, `title`, `start`, `end`, `location`, `busy`, `all_day`, `classified_as`, `meal_type` | Refreshed on open/refresh. |
| `title_classifications` | `title_normalized`, `classified_as`, `meal_type` | Remembered user/AI classifications. |

Google OAuth tokens are managed by the MCP server, not stored by the app.

---

## 8. Rule payloads (Pydantic, stored as JSON)

```json
{ "type": "event", "label": "Breakfast", "category": "meal",
  "anchor": { "ref": "wake_up" }, "offset_minutes": 60,
  "duration_minutes": 20, "skippable": true, "days_of_week": [0,1,2,3,4,5,6] }
```

```json
{ "type": "event", "label": "Medicine A", "category": "medicine",
  "anchor": { "ref": "event", "id": "breakfast" }, "offset_minutes": 0, "after_end": true,
  "requires_food": true, "food_source_ref": "breakfast", "min_gap_minutes": 300,
  "tolerance_minutes": 120, "allowed_during_busy": true, "duration_minutes": 1 }
```

```json
{ "type": "series", "label": "Eye drops",
  "anchor": { "ref": "event", "id": "anchor_meds" },
  "recurrence": { "interval_minutes": 180, "count": 3, "start_mode": "after_anchor" },
  "category": "medicine", "duration_minutes": 1, "allowed_during_busy": true }
```

```json
{ "type": "event", "label": "Dinner", "category": "meal",
  "anchor": { "ref": "occurrence", "id": "meds_series", "selector": "last" },
  "offset_minutes": -60 }
```

```json
{ "type": "constraint", "event_ref": "lunch", "days_of_week": [0,1,2,3,4],
  "must_start_after": "13:00", "must_end_by": "14:00",
  "on_violation": "substitute", "substitute_label": "Light snack" }
```

```json
{ "type": "preference", "key": "weights",
  "value": { "per_minute_delay": 1, "during_busy": 60, "meal_downgraded": 20, "meal_skipped": 40 } }
```

Anchors: `wake_up`, `event`, `occurrence` (`first`, `last`, `nth`), `absolute` (clock time). `start_mode` for series defaults to `after_anchor`.

---

## 9. Scheduler (pure Python)

### 9.1 Signature

```python
def build_plan(
    date, wake_time, now,              # now is used for late-open handling
    rules, commitments, food_options, quotas, quota_history,
    calendar_events, day_overrides, item_status,
    standing_approvals, approved_decisions, profile,
) -> PlanResult:  # items, pending_approvals, unslotted, conflicts, explanations_data
```

Same inputs must always give the same output. Minute resolution. Ties go to the earlier time, then to rule id order.

### 9.2 Steps

1. **Select day inputs:** rules, commitments, overrides, and calendar events matching the date and weekday.
2. **Build fixed blocks:** calendar events (meetings, calendar meals) and commitments, each with leave-by and return commute blocks (`commute_minutes`). Generate "Leave for <X>" items.
3. **Resolve the rule DAG:** topological sort, cycle detection (reject with the rule ids involved), compute ideal times from wake-up, expand series, resolve occurrence selectors.
4. **Apply calendar meals:** a calendar meal replaces the rule meal of the same `meal_type`; medicines tied to it re-anchor to it.
5. **Place medicines (tier 2):** at ideal time if free or `allowed_during_busy`. Enforce `min_gap_minutes` between doses. If the food source is missing or blocked, run the fallback planner (9.3).
6. **Place meals (tier 3):** at ideal time if free; otherwise apply constraints and `on_violation`, or propose the nearest free slot.
7. **Place quota items (tier 4):** first free slot of sufficient length, between wake-up and sleep time, respecting location (commute). Promote if the monthly miss is already used.
8. **Place low-priority items (tier 5):** first fit in leftover time.
9. **Fixed-block overlaps:** short items (`allowed_during_busy`) stay and become silent reminders during the block, as an approval unless a standing approval matches. Longer items get a proposed new slot.
10. **Merge** items at the exact same time into one row (underlying items stay separate).
11. **Unslotted:** anything that fits nowhere is listed with recommendations (skip, shorten, swap). Never dropped silently.
12. **Indirect conflicts:** report rule combinations that can never fit (for example "gym Mondays 6 PM" vs class until 6 plus commute).

### 9.3 Fallback planner (medicine loses its food)

1. Detect a medicine with `requires_food` whose food source cannot happen.
2. Mark the meal as proposed-skipped (needs approval, since nothing is skipped without the user).
3. Generate candidates within `tolerance_minutes`: the original meal in the nearest free slot; each approved food option in the nearest free slot; each `allowed_during_busy` food option at the ideal time inside the busy block.
4. Score: `delay * per_minute_delay + during_busy + meal_downgraded + meal_skipped` (penalties only when they occur).
5. Propose the lowest score as an approval, with the next two as alternatives. Apply automatically only if a standing approval matches.
6. No valid candidate: keep the medicine at its ideal time as a reminder and warn.

### 9.4 Late opening (`now` later than wake-up)

If the user opens the app and items between wake-up and `now` are still `pending`:

- **Override:** day restarts at `now`. All pending earlier items are re-planned into the rest of the day.
- **Continue:** user sorts earlier items into Done and Not done (drag between two lists). Only Not done items are re-planned.

Re-planning ("cramming") order: keep medicine spacing; downgrade meals to snacks where needed so medicines still have food; shorten flexible items; propose skipping lowest priority first. Every violation of tier 3 and below is an approval. Medicines are never moved closer than `min_gap_minutes`.

### 9.5 Very late wake-up

If breakfast and lunch would overlap or be closer than a configurable gap, propose merging them into one meal, keep medicine spacing, and push later doses accordingly.

### 9.6 Sleep time

Items that would end after sleep time are flagged with options (shorten, move, use quota miss).

---

## 10. LLM layer

### 10.1 General rules

- All calls go through `llm/client.py`, which has `LiveClient` (Anthropic SDK) and `MockClient` (returns recorded outputs from `fixtures/llm_recordings/`, keyed by a hash of the prompt inputs).
- Structured output via tool use: each task defines a Pydantic model; its JSON schema is the tool `input_schema`. Validate the result; retry once on invalid output, then surface "couldn't understand this".
- Temperature 0. Every call includes the current confirmed rules as context.
- The LLM never computes final times and never applies changes. Its output becomes rule drafts, questions, or approvals.

### 10.2 Parse (`POST /rules/parse`)

Input: raw text (any amount, messy, typos, nested references) plus existing rules. Output: list of draft rules, each with `confidence` and optional `needs_clarification` question. Also extracts preferences (weights, skippable, allowed_during_busy, food options). Drafts are shown as editable rule cards; nothing is saved until confirmed.

### 10.3 Follow-up questions

Generated from gaps: meal deadlines and durations, commitments (days, times, location, commute), sleep time, gym location and length, medicine timing. Questions form a dependency tree (`depends_on`); changing an answer marks dependent questions `obsolete` and removes records they created. After each answer: save, recompute, re-evaluate questions, show a diff.

### 10.4 Contradiction check

On every new or edited rule, the LLM compares it with existing confirmed rules and returns direct contradictions (for example "dinner at 9 PM" vs "dinner done by 8"). The user sees both and picks. Indirect conflicts (rules that never fit together) come from the scheduler (9.2 step 12), not the LLM.

### 10.5 Night-review inference (prep reminders)

Input: tomorrow's computed plan, calendar events (titles, times, locations), standing rules. Output: proposed prep items with a reason and a time relative to another item. Examples:

- Dinner with friends at 8 PM, away from home, medicine tied to dinner -> "Pack medicines", 30 min before leaving.
- Meeting at 9:30 PM after an 8 PM dinner, end time or location unknown -> "Take laptop to dinner". Assume the risky case when details are unknown.

All are approvals. "Always do this" turns one into a standing rule.

### 10.6 Medicine timing suggestion

When a medicine lacks timing, the LLM suggests which meals and spacing, clearly labeled as a suggestion. Not scheduled until the user confirms.

### 10.7 Calendar title classification and explanations

- Classify titles keyword matching cannot ("catch-up at Chipotle"); ask the user once; remember per title.
- Turn a scheduler decision and its score breakdown into a one-line explanation. Never changes the decision.

### 10.8 Learning from approval history

Goal: over time, unrestricted mode should choose what the user would have chosen.

1. **Record** every user decision in `decision_history`: approvals, rejections, picked alternatives, undos of auto-applied changes, manual edits, manual skips.
2. **Analyze** (LLM): run after the night review sign-off and on demand. Input is a compact summary, not raw unbounded logs: the last 60 days of decisions grouped by `kind` and context, with counts. Output (structured) is candidate patterns, for example:
   - "Picks snack after the meeting over apple during the meeting 6 of 7 times" -> raise `during_busy` weight for food items.
   - "Always approves meds during meetings" -> standing approval.
   - "Rejected 'take laptop' 4 of 4 times" -> stop proposing that prep reminder.
3. **Evidence threshold** (enforced in code, not by the LLM): a pattern is only accepted with at least 3 matching decisions and at least 80% consistency. Weaker patterns are discarded.
4. **Activation:**
   - Restricted mode: learned patterns are `proposed` and shown to the user to accept or reject.
   - Unrestricted mode: patterns that pass the threshold become `active` automatically, are logged, and are listed on the Learned page with revoke.
5. **How patterns are used:** they become explicit standing approvals, weight changes, or "don't propose" entries, which the scheduler and prep inference read as inputs. The scheduler stays deterministic; the LLM never picks today's option directly.
6. **Context for other LLM calls:** prep inference (10.5) and medicine timing suggestions (10.6) also receive active learned preferences and a short summary of recent rejections, so they stop proposing things the user keeps rejecting.
7. **Cannot be learned:** hard rules in 6.5 (medicine spacing, no app skips, unconfirmed medicine timing, contradictions, calendar writes). A pattern like "user always skips yoga" becomes a recommendation shown first, never an automatic skip.
8. **Drift:** a pattern is re-checked on each analysis; if consistency drops below 80% over its recent decisions, it is set back to `proposed` (restricted) or revoked with a log entry (unrestricted).

### 10.9 Mock mode

`LLM_MODE=mock` uses recordings, including pattern-analysis outputs. Provide recordings for every acceptance scenario so the full app works offline. A helper script records live outputs into fixtures when run with a real key.

---

## 11. Calendar layer

### 11.1 Interface

```python
class CalendarSource(Protocol):
    def events_for(self, date) -> list[CalendarEvent]: ...
```

### 11.2 Classification order

1. Remembered classification for the normalized title.
2. Keywords: breakfast, brunch, lunch, dinner, supper -> `meal` with `meal_type`.
3. All-day or free -> `ignore` for scheduling (still surfaced in night review, for example "Mom's birthday tomorrow").
4. Other busy events -> `meeting` (classes on the calendar count as fixed blocks).
5. Unclear -> LLM classification, then confirm once.

Duplicates (a rule commitment and a calendar event for the same block) are flagged, not double-counted.

### 11.3 Google Calendar via MCP (CALENDAR_MODE=mcp)

- The app is an **MCP client** (official `mcp` Python SDK). It starts the Google Calendar MCP server as a stdio subprocess using `MCP_CALENDAR_COMMAND` and `MCP_CALENDAR_ARGS`, then calls tools directly from Python. **No LLM is involved in fetching events.**
- **Read-only enforcement in the app:** keep an allowlist containing only the list/read tools (`MCP_LIST_EVENTS_TOOL` and, if needed, a list-calendars tool). Any other tool call is refused in code, even if the server exposes create, update, or delete tools. Prefer configuring the server with read-only Google scope (`calendar.readonly`) if it supports it.
- On startup, call `list_tools` and verify the configured tool exists; if not, show "Calendar MCP tool not found" with the available tool names.
- `mcp_source.py` maps the server's output into `CalendarEvent` (title, start, end, location, busy, all-day). Keep this mapping in one function so switching to a different MCP server only changes that function.
- Fetch on app open, on wake-up entry, on night review, and on manual refresh. Then recompute and show a diff.
- On failure (server not starting, auth expired, tool error), show "Calendar not synced" with the error and plan from rules only.
- Never write to the calendar.
- **Server choice:** use a maintained Google Calendar MCP server that supports stdio and a list-events tool. Record the chosen server, its version, and its credential setup in `DECISIONS.md`.

### 11.4 Mock

`fixtures/mock_calendar.json` with events per date, editable by hand to drive scenarios.

---

## 12. UI (Jinja2 + HTMX, list format everywhere, no timeline view)

1. **Setup / Profile:** free-text box for describing the routine; rule cards (edit, confirm, delete); follow-up questions; commitments list; food options; quotas; **priority order as a drag and drop list**; weights as sliders; default wake-up per weekday, sleep time, night review time; calendar connect button.
2. **Night review (Tomorrow):** first asks tomorrow's wake-up target; shows tomorrow's list; free-text "anything else for tomorrow?"; AI prep proposals; approvals; "Sign off" button. If opened after `night_review_time` and tomorrow is not reviewed, show a banner prompting the review.
3. **Today:** wake-up entry; list of items (time, label, alarm/reminder toggle, Done / Not done); late-open panel (Override or Continue, with Done / Not done drag lists); approvals queue with Approve / Reject / Always / one-tap alternatives; unslotted items with recommendations; change diff after every recompute; drag to reorder an item for today (its dependent medicine follows, with a warning if spacing breaks).
4. **Approvals:** all pending approvals, plus standing approvals list with revoke.
5. **Mode switch:** Restricted / Unrestricted toggle in Profile, visible mode badge on Today and Tomorrow, and "restricted just for tomorrow" in the night review.
6. **Learned:** list of learned preferences with evidence ("6 of 7 times"), status, and Accept / Revoke.
7. **Activity log:** in unrestricted mode, auto-applied changes show an "auto" badge in the list; the log shows each change, reason, alternatives, and an Undo button (undo restores the previous state and recomputes).

---

## 13. Routes (summary)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Today |
| GET | `/tomorrow` | Night review |
| GET | `/profile` | Setup and profile |
| POST | `/wake` | Enter target or actual wake-up |
| POST | `/late/override`, `/late/continue` | Late-open handling |
| POST | `/rules/parse` | LLM parse to draft cards |
| POST | `/rules/{id}/confirm`, PUT `/rules/{id}`, DELETE `/rules/{id}` | Rule management (delete warns about dependents) |
| POST | `/questions/{id}/answer` | Answer follow-up |
| POST | `/profile/priorities` | Save drag and drop order |
| POST | `/items/{key}/status` | Done / Not done / skip |
| POST | `/items/reorder` | Today's list reorder |
| POST | `/approvals/{id}` | Approve / reject / always / pick alternative |
| POST | `/calendar/refresh` | Re-fetch through MCP and recompute |
| GET | `/calendar/status` | MCP server status and available tools |
| POST | `/profile/mode` | Set approval mode (global or for a date) |
| GET | `/activity` | Activity log |
| POST | `/activity/{id}/undo` | Undo an auto-applied change |
| GET | `/learned` | Learned preferences |
| POST | `/learned/analyze` | Run pattern analysis now |
| POST | `/learned/{id}` | Accept or revoke a learned preference |

---

## 14. Testing

- `tests/scheduler/`: unit tests for DAG, cycles, series, selectors, placement, fallback scoring, min gap, quotas, late-open modes, sleep time. Use `freezegun`.
- `tests/llm/`: schema validation, retry on invalid output, mock recordings round-trip.
- `tests/scenarios/`: every scenario in Section 16, driven by fixtures (rules, mock calendar, recorded LLM outputs).
- All tests pass offline with no keys.

---

## 15. Milestones

1. **Scheduler core** with hand-written rule fixtures. Golden tests 16.4 to 16.6 pass.
2. **Database, FastAPI, UI** (Today, Tomorrow, Profile, Approvals) using mock LLM and mock calendar.
3. **LLM layer live:** parse, questions, contradictions, prep inference, medicine suggestions, explanations. Recording script.
4. **Google Calendar live through MCP**, read-only, with the tool allowlist and the status page.
5. **Polish:** diffs, explanations, error states, README.

---

## 16. Acceptance scenarios

### 16.1 Positive workflows

| ID | Situation | Expected |
|---|---|---|
| P1 | First setup: "wake up 7 weekdays, 9 weekends, 3 meals, meds twice a day after food, gym 4x a week, yoga when possible, college Mon/Wed 10-2 and 4-6, commute 35 min" | Rule cards created; follow-ups asked (which meals for meds, gym length and location); AI suggests meds after breakfast and dinner; user confirms. |
| P2 | Night review | Asks tomorrow's wake-up first; shows tomorrow's list; user adds "submit assignment before class"; it is slotted; user signs off. |
| P3 | Tomorrow: calendar dinner with friends 8 PM, meeting 9:30 PM | Calendar dinner replaces planned dinner; meds tied to it; "Pack medicines" 30 min before leaving; "Take laptop to dinner"; both as approvals. |
| P4 | Planned wake 8, actual 8 | No changes. |
| P5 | Planned 8, actual 7:30 | Recompute from 7:30; fixed blocks unchanged. |
| P6 | Monday with classes, wake 8 | See golden test 16.5. |

### 16.2 Edge cases

| ID | Situation | Expected |
|---|---|---|
| E1 | Woke 7, opened 9, Override | Day restarts at 9; 7 to 9 items re-planned. |
| E2 | Woke 7, opened 9, Continue; breakfast Done, meds Not done | Only meds re-planned, after food. |
| E3 | Lunch fits nowhere, meds due | Fallback planner proposes snack or food option for meds; approval. |
| E4 | Late morning meds would land too close to afternoon meds | Afternoon meds shift to keep `min_gap_minutes`; approval; never crammed closer. |
| E5 | Day full, gym monthly miss already used | "Gym miss limit reached this month. Skip anyway?"; nothing skipped without an answer. |
| E6 | Yoga does not fit | Unslotted with options: skip, shorten gym, swap. Never silently dropped. |
| E7 | New rule "dinner at 9 PM" vs existing "dinner done by 8" | Contradiction shown with both rules; user picks. |
| E8 | New rule "gym Mondays 6 PM" vs class until 6 plus 35 min commute | Indirect conflict flagged with options. |
| E9 | New medicine without timing | AI suggestion shown as suggestion; medicine not scheduled until confirmed. |
| E10 | Night review skipped | Setup default wake-up used; plan stands. |
| E11 | "No class tomorrow afternoon" | Day override; lunch, gym, yoga re-slotted for that date only. |
| E12 | Item cannot be slotted | "Couldn't fit: assignment (1h)" with suggestions. |
| E13 | User drags lunch after gym in Today | Lunch meds follow; warning if spacing breaks. |
| E14 | Wake 11 AM | Propose merging breakfast and lunch; keep medicine spacing; push later doses. |
| E15 | Gym would end after sleep time | Flagged; shorten, move, or use quota miss. |

### 16.3 Calendar cases

| ID | Situation | Expected |
|---|---|---|
| C1 | "Lunch with Alex 12:30" | Replaces rule lunch; lunch meds attach to it. |
| C2 | Meeting 2 to 3, meds at 2:30 | Proposal: take during meeting with something small, or right after; approval. |
| C3 | Meeting 12 to 2 blocks lunch | Fallback planner options; approval. |
| C4 | Classes exist both as rules and calendar events | Flagged as duplicate, counted once. |
| C5 | New 9 AM meeting appears after night review | On open or refresh, recompute and show diff. |
| C6 | Event cancelled | Freed time re-used; its approvals removed. |
| C7 | "Catch-up at Chipotle" | Classified as meal, confirmed once, remembered. |
| C8 | Event with location vs without | Location used for leave-by and prep reminders; no location means assume away from home. |
| C9 | All-day events | Do not block time; may appear in night review. |
| C10 | Calendar not connected or sync fails | "Calendar not synced" banner; plan from rules only. |

### 16.3a Approval mode cases

| ID | Situation | Expected |
|---|---|---|
| M1 | Golden test 16.6 in restricted mode | C is a pending approval with A and B as alternatives. |
| M2 | Golden test 16.6 in unrestricted mode | C is applied automatically, logged with A and B as alternatives; Undo restores the pre-change plan and creates no new auto-change for that item until the next recompute input changes. |
| M3 | Unrestricted, P3 dinner + meeting | "Pack medicines" and "Take laptop" added automatically with "auto" badge and log entries. |
| M4 | Unrestricted, yoga does not fit | Yoga is not skipped; it stays in unslotted with recommendations. |
| M5 | Unrestricted, late-open cramming would put meds closer than `min_gap_minutes` | Meds keep their spacing; the hard rule wins. |
| M6 | Unrestricted, new medicine without timing | Still not scheduled until the user confirms. |
| M7 | Global unrestricted, "restricted just for tomorrow" | Tomorrow's proposals wait for approval; other days auto-apply. |
| M8 | Restricted with a standing approval for meds during meetings | That case auto-applies; everything else waits. |

### 16.3b Learning cases

| ID | Situation | Expected |
|---|---|---|
| L1 | History: user picked "snack after meeting" over "apple during meeting" 6 of 7 times | Pattern found; `during_busy` weight raised for food items; in unrestricted, golden test 16.6 now auto-applies the after-meeting option. |
| L2 | Only 2 matching decisions | No pattern (below threshold). |
| L3 | "Take laptop" rejected 4 of 4 times | Prep inference stops proposing it; listed on Learned with revoke. |
| L4 | Restricted mode, pattern passes threshold | Shown as `proposed`; nothing changes until accepted. |
| L5 | User always skips yoga | Recommendation "skip yoga" shown first; yoga is still never skipped automatically. |
| L6 | Learned pattern's consistency drops to 50% | Reverted to `proposed` (restricted) or revoked with a log entry (unrestricted). |
| L7 | Undo of an auto-applied change | Recorded as a negative signal in `decision_history`. |

### 16.4 Golden test: original routine, wake 7:00, no commitments or calendar

Rules: hygiene wake+15; breakfast wake+60; eye drops and medicines wake+90 (anchor); eye drops every 3 h thrice after anchor; medicines every 6 h twice after anchor; dinner 1 h before last medicine; lunch 1 h before first medicine.

| Time | Item |
|---|---|
| 7:15 | Brush, bath, washroom |
| 8:00 | Breakfast |
| 8:30 | Eye drops + medicines (anchor) |
| 11:30 | Eye drops 1/3 |
| 13:30 | Lunch |
| 14:30 | Eye drops 2/3 + Medicine 1/2 (merged row) |
| 17:30 | Eye drops 3/3 |
| 19:30 | Dinner |
| 20:30 | Medicine 2/2 |

### 16.5 Golden test: Monday with classes, wake 8:00

Fixture: classes 10:00 to 14:00 and 16:00 to 18:00, commute 35 min each way, stays on campus between classes. Breakfast wake+60 (20 min). Meds after each meal, `requires_food`, 3 per day, `min_gap_minutes` 240. Lunch breakfast+4h (30 min), skippable. Dinner 19:00 (30 min). Gym 60 min at home, quota 4/week. Yoga 20 min, low priority. Sleep 23:00. Food option: apple (1 min, allowed during busy). Weights: delay 1/min, during busy 60, meal downgraded 20.

| Time | Item |
|---|---|
| 9:00 | Breakfast |
| 9:20 | Medicine 1 |
| 9:25 | Leave for class |
| 10:00 to 14:00 | Class |
| 14:00 | Lunch (ideal 13:00 blocked; lunch at 14:00 scores 60 vs apple during class 80) |
| 14:30 | Medicine 2 |
| 16:00 to 18:00 | Class |
| 18:00 | Leave for home, arrive 18:35 |
| 19:00 | Dinner |
| 19:30 | Medicine 3 |
| 19:30 to 20:30 | Gym (first free 60-min slot after dinner) |
| 20:30 to 20:50 | Yoga |

Note: Medicine 3 and gym both start at 19:30; Medicine 3 is a 1-minute item and stays as its own row at 19:30.

### 16.6 Golden test: fallback planner

Rules: breakfast wake+60; Medicine 1 after breakfast; lunch breakfast+4h, skippable, 30 min; Medicine 2 after lunch, requires food, tolerance 120. Food options: snack 15 min; apple 1 min, allowed during busy. Weights: 1/min delay, during busy 60, meal downgraded 20. Calendar meeting 11:00 to 14:00. Wake 7:00.

| Candidate | Score |
|---|---|
| A. Full lunch 14:00, Medicine 2 at 14:30 (120 min late) | 120 |
| B. Snack 14:00, Medicine 2 at 14:15 (105 min late) | 125 |
| C. Apple + Medicine 2 at 12:30, during meeting | **80** |

Expected: C proposed, A and B as alternatives, lunch skip itself requires approval. With `during_busy` weight 150, A wins.

---

## 17. Decisions already made (do not revisit without the user)

- Python primary; FastAPI + Jinja2 + HTMX; SQLite; SQLAlchemy 1.x Column style.
- LLM-first parsing; LLM never decides final times; scheduler is deterministic.
- Google Calendar read-only, never written to, accessed through an MCP server with an app-side tool allowlist.
- List-based UI only, no timeline view.
- Nothing skipped without the user. Medicines never crammed below `min_gap_minutes`.
- Two approval modes: restricted (default, everything waits for approval) and unrestricted (top proposal auto-applied, logged, undoable). Hard rules in 6.5 apply in both.
- The AI learns from approval history, but learned patterns become explicit preferences (standing approvals, weights, don't-propose) that the deterministic scheduler reads. Code enforces the evidence threshold. Hard rules cannot be learned away.
- Medicine timing comes from the user; AI suggestions require confirmation.
- v1 is web only; real alarms are v2.
