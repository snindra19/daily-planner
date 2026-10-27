# Wake-Up Planner

A local, single-user daily planner that builds your alarms and reminders around when you actually woke up. You describe your routine in plain English, and each morning the day is recomputed from your real wake-up time. It reads Google Calendar but never writes to it. The AI proposes; a deterministic Python scheduler decides the times, and nothing is skipped unless you skip it.

v1 runs in the browser. Alarms are displayed and stored as a setting, and nothing rings.

## Run it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). The calendar server also needs Node.

```powershell
uv sync
Copy-Item .env.example .env
uv run python scripts/seed_demo.py   # optional: adds a sample routine
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The app has no login, so keep it on `127.0.0.1`.

With the defaults (`LLM_MODE=mock`, `CALENDAR_MODE=mock`) it runs offline with no keys. In mock mode, the AI only understands text that has a recording in `fixtures/llm_recordings/`, such as the sample in `tests/scenarios/cases.py`. Anything else shows "No mock recording". Mock calendar events go in `fixtures/mock_calendar.json`, keyed by date.

## Use real AI and your real calendar

In `.env`:

```dotenv
LLM_MODE=live
ANTHROPIC_API_KEY=your-key
ANTHROPIC_MODEL=claude-sonnet-5-5

CALENDAR_MODE=mcp
MCP_CALENDAR_COMMAND=cmd
MCP_CALENDAR_ARGS=/c npx @cocal/google-calendar-mcp
MCP_LIST_EVENTS_TOOL=list-events
MCP_CALENDAR_ENV_FILE=C:\path\to\calendar-server.env
```

The calendar server's env file needs the path to your Google OAuth client file. Use single quotes around Windows paths, since double quotes turn backslashes into escapes:

```dotenv
GOOGLE_OAUTH_CREDENTIALS='C:\path\to\gcp-oauth.keys.json'
ENABLED_TOOLS=list-events,list-calendars
```

Check the connection at <http://127.0.0.1:8000/calendar/status>. If the server fails, the app shows "Calendar not synced" with the error and plans from your rules alone.

Live LLM calls cost money. Token counts for each call go to the app's log.

## Pages

| Page | What it's for |
|---|---|
| Today | Enter your wake-up time, work through the day's list, and answer proposals. |
| Tomorrow | Night review: set the wake-up target, add extras, and sign off. |
| Approvals | Everything waiting for you, plus standing approvals. |
| Profile | Describe your routine, confirm rule cards, and set priorities, weights, and the approval mode. |
| Learned | Patterns found in your decisions, with their evidence. |
| Activity | Automatic changes in Unrestricted mode, each with Undo. |

**Restricted** mode (the default) waits for your approval on every proposal. **Unrestricted** applies the top proposal and logs it with an Undo. In both modes, medicine doses are never placed closer than their minimum gap, unconfirmed medicines aren't scheduled, the app never skips anything, you resolve contradictions yourself, and the calendar is never written to.

## Tests

```powershell
uv run pytest
uv run ruff check .
```

The normal run is offline and needs no keys, whatever `.env` says. Live tests are skipped unless the real environment asks for them:

```powershell
$env:LLM_MODE='live'; uv run pytest -m live tests/integration/test_llm_live.py     # uses your API key
$env:CALENDAR_MODE='mcp'; uv run pytest -m live tests/integration/test_mcp_live.py # reads your calendar; needs the MCP_* values
```

`uv run python scripts/live_check.py` with `LLM_MODE=live` runs every AI task once. To record a live answer as a mock, use `scripts/record_llm.py`. `scripts/make_mock_recordings.py` regenerates the hand-written recordings.

## Layout

- `app/scheduler/`: the pure scheduler, with no database, network, or LLM calls.
- `app/llm/`: live and mock clients, tasks, and prompts.
- `app/calendar/`: the mock source, the read-only MCP client, and title classification.
- `app/services/`: orchestration and database work.
- `app/routes/`, `app/templates/`: FastAPI routes and Jinja2 + HTMX pages.
- `fixtures/`: scenario rule sets, LLM recordings, and the mock calendar.
- `DECISIONS.md`: choices made where the spec was silent or disagreed with itself.
