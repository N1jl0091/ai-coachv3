# ai-coach

A personal Telegram coaching bot for endurance athletes. Talks to you through
Telegram, manages your training calendar in **Intervals.icu**, watches your
**Strava** activities, emails you a coach-style review of every session, and
ships a static observability dashboard to **GitHub Pages** so you can see what
the bot is doing at a glance.

Single-user by design. Built to run on Railway (Postgres + a single web
service) or on your own machine.

```
            ┌──────────────┐
            │  Telegram    │  ← chat in / chat out
            └─────┬────────┘
                  │
                  ▼
   ┌──────────────────────────┐        ┌─────────────────┐
   │      ai-coach (this)     │ ─────▶ │ Intervals.icu   │ ← single source of truth
   │   FastAPI + APScheduler  │ ◀─────                 │
   └─────┬──────────────┬─────┘        └─────────────────┘
         │              │
         ▼              ▼                ┌─────────────────┐
   ┌─────────┐    ┌──────────┐          │ Strava          │ ← webhook in
   │ Postgres│    │ Resend   │ ─→ email └────────┬────────┘
   └─────────┘    └──────────┘                   │
         ▲              ▲                        │
         │              └────────────────────────┘
         │
         ▼
   ┌────────────────────────┐
   │ GitHub Pages dashboard │   ← pushed every 15 min
   └────────────────────────┘
```

---

## Table of contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick start](#quick-start)
5. [Environment variables](#environment-variables)
6. [Database — Railway Postgres or local](#database--railway-postgres-or-local)
7. [Strava webhook registration](#strava-webhook-registration)
8. [GitHub Pages observability dashboard](#github-pages-observability-dashboard)
9. [Running locally](#running-locally)
10. [Deploying to Railway](#deploying-to-railway)
11. [Project layout](#project-layout)
12. [Telegram commands](#telegram-commands)
13. [Tests](#tests)
14. [Troubleshooting](#troubleshooting)
15. [Notes on the `emails/` folder name](#notes-on-the-emails-folder-name)

---

## What it does

- **Chat with your coach on Telegram.** Free-text conversations get routed
  through one of three LLM "jobs":
  - **reasoning** (default: Anthropic Claude Opus) — open-ended coaching,
    strategy, periodization questions.
  - **executor** (default: Claude Sonnet, low temperature) — turns commands
    like *"replace Tuesday's intervals with an easy 50-min Z2 run"* into
    actual Intervals.icu calendar changes via tool-calling.
  - **analysis** (default: gpt-4o) — writes the post-activity email after
    every Strava upload.

  Swap providers/models in one line in `config/llm_config.py`. Anthropic,
  OpenAI, Groq, and Ollama are all supported.

- **Acts, then confirms.** The executor never asks for permission — it
  performs the change in Intervals.icu and reports back what it did. This is
  intentional: it's *your* coach, not a junior PM.

- **Never goes silent.** Every Telegram message gets a reply, even on error.

- **Single source of truth = Intervals.icu.** No shadow workout DB. The bot
  writes plans straight into Intervals.icu and reads them back when it needs
  context.

- **Strava → email pipeline.** When you finish a session, Strava calls our
  webhook, we wait for Intervals to ingest the activity, then send you an
  HTML email with the analysis (250–350 words, structured per the prompt in
  `config/prompts/activity_analysis.txt`).

- **Static observability dashboard.** APScheduler rebuilds a dark
  engineering-ops dashboard every 15 minutes and pushes
  `docs/index.html` + `docs/logs.json` to your GitHub repo. Enable Pages
  for `/docs` on the main branch and you have a public-or-private status
  page with charts for messages/day, tokens/day, LLM calls per job, latency,
  Intervals API success rate, recent errors, and a live event feed.

---

## Architecture

- **One process.** FastAPI hosts the Strava webhook + `/health` +
  `/admin/flush`; the Telegram bot runs via long-polling inside the same
  asyncio loop, started from the FastAPI lifespan hook.
- **Postgres** (`asyncpg` + SQLAlchemy 2.0 async) stores two tables:
  - `athlete_profile` — your training profile (single row, single user).
  - `event_log` — structured event log for every LLM call, Intervals API
    call, webhook hit, email send, and error. This powers the dashboard.
- **APScheduler** runs the dashboard flush every 15 minutes plus an initial
  flush 30 seconds after boot.
- **Resend** sends the post-activity emails. Wrapped in `asyncio.to_thread`
  so the (sync) Resend SDK doesn't block the loop.
- **PyGithub** commits the dashboard files in a single tree per flush so
  history stays clean. Idempotent — only commits when content actually
  changed.

---

## Prerequisites

- **Python 3.11+**
- **Postgres 14+** (Railway provides this; for local dev you can run one
  in Docker — see below).
- **A Telegram bot token** from [@BotFather](https://t.me/BotFather).
- **Your Telegram user id** from [@userinfobot](https://t.me/userinfobot)
  (this is the only user the bot will respond to).
- **An Intervals.icu account** with an athlete id + API key.
- **An Anthropic API key** (and optionally OpenAI / Groq keys depending on
  which LLM jobs you want to use).
- **A Strava API application** (free) for the webhook.
- **A Resend account** for emails.
- **A GitHub repo + Personal Access Token** with `repo` scope for the
  dashboard push.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/<you>/ai-coach.git
cd ai-coach

# 2. Virtualenv + dependencies
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Config
cp .env.example .env
# … then open .env and fill in every value (see next section)

# 4. Make sure Postgres is reachable. For local dev:
docker run -d --name ai-coach-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_coach \
  -p 5432:5432 \
  postgres:16

# 5. Run
uvicorn main:app --reload --port 8000
```

Open Telegram, message your bot `/start`, then `/setup` to fill in your
profile. Tables are created automatically on first boot.

---

## Environment variables

Every variable lives in `.env` (local) or your Railway service settings
(production). The full list is documented in `.env.example`. Required
variables (the app refuses to start without them):

| Variable               | What                                                                     |
|------------------------|--------------------------------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`   | From @BotFather after `/newbot`.                                         |
| `INTERVALS_API_KEY`    | Intervals.icu → Settings → Developer.                                    |
| `INTERVALS_ATHLETE_ID` | Same page — looks like `i12345`.                                         |
| `DATABASE_URL`         | `postgresql+asyncpg://user:pass@host:port/dbname`. Railway injects this. |

Strongly recommended:

| Variable               | What                                                                     |
|------------------------|--------------------------------------------------------------------------|
| `TELEGRAM_OWNER_ID`    | Your Telegram user id (from @userinfobot). Without this *anyone* who finds your bot can use it. |
| `ANTHROPIC_API_KEY`    | Default reasoning + executor provider.                                   |
| `OPENAI_API_KEY`       | Default analysis (gpt-4o) provider.                                      |
| `STRAVA_CLIENT_ID`     | strava.com/settings/api                                                  |
| `STRAVA_CLIENT_SECRET` | strava.com/settings/api                                                  |
| `STRAVA_VERIFY_TOKEN`  | A random string you choose — must match what you POST to Strava when you create the subscription. |
| `RESEND_API_KEY`       | resend.com → API keys.                                                   |
| `RESEND_FROM_EMAIL`    | A verified sender domain on Resend.                                      |
| `RESEND_TO_EMAIL`      | Where activity reviews land — usually your own inbox.                    |
| `GITHUB_TOKEN`         | PAT with `repo` scope, used to commit the dashboard.                     |
| `GITHUB_REPO`          | `your-username/ai-coach`.                                                |
| `GITHUB_BRANCH`        | Defaults to `main`.                                                      |
| `ATHLETE_TIMEZONE`     | IANA tz, e.g. `Africa/Johannesburg`. Used when injecting "today is …" into prompts. |

DigitalOcean / Render / Fly all work too — set the same env vars and run
`uvicorn main:app --host 0.0.0.0 --port $PORT`.

### Picking your LLMs

`config/llm_config.py` maps each job → provider + model. To swap, edit one
line. To add a new provider, add a branch in `coach/llm_client.py` —
OpenAI-compatible providers (Groq, Ollama, vLLM, etc.) need no new code,
just a new `provider` value.

---

## Database — Railway Postgres or local

### Railway

1. Create a new Railway project.
2. Add a **PostgreSQL** plugin. Railway auto-creates `DATABASE_URL` and
   injects it into your service. (We normalise `postgres://` → `postgresql+asyncpg://`
   on load, so you don't have to fix the scheme.)

### Local Docker

```bash
docker run -d --name ai-coach-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_coach \
  -p 5432:5432 \
  postgres:16
```

Then in `.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_coach
```

### Schema

Tables are created automatically on first boot via `Base.metadata.create_all`
(no Alembic — this is a single-user app, schema migrations are handled by
editing `db/models.py` and bumping a Postgres column manually if needed).

---

## Strava webhook registration

The webhook endpoint is `POST/GET /webhook/strava`. Strava verifies your
subscription at creation time with a `GET` handshake; the bot handles that
automatically.

### One-time setup

1. Create a Strava API application at
   [strava.com/settings/api](https://www.strava.com/settings/api). Note the
   **Client ID** and **Client Secret**. Set them in `.env` /
   Railway settings.

2. Choose a `STRAVA_VERIFY_TOKEN` — any random string. Set it in `.env`.

3. Deploy the bot somewhere with a public HTTPS URL (Railway gives you one
   for free). Note the URL — call it `$BOT_URL`.

4. Register the subscription:

   ```bash
   curl -X POST https://www.strava.com/api/v3/push_subscriptions \
     -F client_id="$STRAVA_CLIENT_ID" \
     -F client_secret="$STRAVA_CLIENT_SECRET" \
     -F callback_url="$BOT_URL/webhook/strava" \
     -F verify_token="$STRAVA_VERIFY_TOKEN"
   ```

   Strava will immediately call `GET $BOT_URL/webhook/strava` with the
   challenge; the bot echoes it back and Strava registers the subscription.
   On success you get a JSON body like `{"id": 12345}`.

5. To check or delete the subscription later:

   ```bash
   # List
   curl -G https://www.strava.com/api/v3/push_subscriptions \
     -d client_id="$STRAVA_CLIENT_ID" \
     -d client_secret="$STRAVA_CLIENT_SECRET"

   # Delete (replace 12345 with your subscription id)
   curl -X DELETE \
     "https://www.strava.com/api/v3/push_subscriptions/12345?client_id=$STRAVA_CLIENT_ID&client_secret=$STRAVA_CLIENT_SECRET"
   ```

You only need ONE subscription per app. If you redeploy to a new URL,
delete the old one first and re-register.

> **Note.** Strava webhook events fire when an activity is *uploaded* to
> Strava, but Intervals.icu still has to ingest it from Strava (which can
> take 30–120 seconds). The bot polls Intervals for up to ~5 minutes
> waiting for the sync.

---

## GitHub Pages observability dashboard

The bot pushes `docs/index.html` and `docs/logs.json` to your repo every
15 minutes. To see them:

1. **Generate a Personal Access Token** at
   [github.com/settings/tokens](https://github.com/settings/tokens) with
   the `repo` scope. Set it as `GITHUB_TOKEN`.

2. **Set `GITHUB_REPO`** to `your-username/ai-coach` (or wherever you've
   pushed this code). The bot needs write access to this repo to commit
   the dashboard.

3. **Enable GitHub Pages** in the repo:
   - GitHub repo → Settings → Pages.
   - **Source:** *Deploy from a branch*.
   - **Branch:** `main` and folder `/docs`.
   - Save. After a few seconds your dashboard is live at
     `https://<your-username>.github.io/<repo-name>/`.

4. **Trigger an initial flush** to confirm things work:

   ```bash
   curl -X POST $BOT_URL/admin/flush
   ```

   Or just wait 30 seconds after boot — the first flush runs automatically.

If you don't want a public dashboard you have two options:
- Mark the GitHub repo as private (GitHub Pages still works on the **Pro**
  plan).
- Don't set `GITHUB_TOKEN` / `GITHUB_REPO` at all. The bot will still build
  the dashboard locally into `docs/` but won't push it anywhere. The
  observability data is still in Postgres if you want to query it directly.

---

## Running locally

```bash
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

You'll see startup logs like:

```
ai-coach starting up …
Telegram bot started
ai-coach ready
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Now message your bot in Telegram. Expected flow:

1. `/start` — bot says hi.
2. `/setup` — bot asks 16 profile questions one at a time.
3. `/status` — sanity check.
4. Free text — *"build me a 4-week marathon prep block starting Monday"* →
   the executor LLM creates events in your Intervals.icu calendar.

A few useful endpoints while developing:

| URL                              | What                                          |
|----------------------------------|-----------------------------------------------|
| `http://localhost:8000/`         | Sanity ping.                                  |
| `http://localhost:8000/health`   | Used by Railway healthcheck.                  |
| `http://localhost:8000/docs`     | FastAPI's auto-generated Swagger UI.          |
| `http://localhost:8000/admin/flush` | Manually trigger a dashboard rebuild + push. |

For Strava webhooks while developing, expose your local server with
[ngrok](https://ngrok.com/) (`ngrok http 8000`) and use the resulting URL
when registering the subscription.

---

## Deploying to Railway

Railway is the path of least resistance. Two services are needed: one
Postgres plugin, one web service.

1. Create a Railway project.
2. Add the **PostgreSQL** plugin → `DATABASE_URL` is auto-injected.
3. Connect your GitHub repo as a service. Railway will detect the
   `Procfile` and `railway.toml` and build a NIXPACKS image automatically.
4. In the service variables tab, set every env var listed above.
5. Once the service is up, copy its public URL and follow the
   [Strava webhook registration](#strava-webhook-registration) section.
6. Confirm the dashboard works by hitting `$BOT_URL/admin/flush` once.

The default `Procfile` uses:

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

`railway.toml` configures NIXPACKS + a healthcheck on `/health`.

---

## Project layout

```
ai-coach/
├── main.py                       # FastAPI app + lifespan + scheduler
├── requirements.txt
├── Procfile
├── railway.toml
├── pytest.ini
├── .env.example
├── .gitignore
├── README.md                     # ← you are here
│
├── config/
│   ├── settings.py               # env vars + load_prompt() helper
│   ├── llm_config.py             # LLM_JOBS: provider/model per job
│   └── prompts/
│       ├── coach_personality.txt
│       ├── workout_builder.txt
│       ├── tool_executor.txt
│       └── activity_analysis.txt
│
├── bot/
│   ├── telegram_bot.py           # build_app + start/stop, error boundary
│   ├── commands.py               # /start /help /setup /profile /status /end
│   └── session.py                # in-memory chat session per chat id
│
├── coach/
│   ├── llm_client.py             # unified async client (anthropic/openai/groq/ollama)
│   ├── context_builder.py        # assemble profile + wellness + activities
│   ├── router.py                 # decide which job handles each message
│   ├── reasoning.py              # open-ended coaching conversations
│   └── executor.py               # tool-calling agentic loop
│
├── intervals/
│   ├── client.py                 # async Intervals.icu HTTP client
│   ├── workout_schema.py         # Workout/WorkoutStep/GymSet → native format
│   ├── workouts.py               # high-level CRUD wrappers
│   ├── wellness.py               # today / weekly trend helpers
│   └── exceptions.py
│
├── strava/
│   ├── webhook.py                # FastAPI router, GET handshake + POST events
│   └── analysis.py               # post-activity pipeline → email
│
├── emails/
│   └── resend_client.py          # send_email() with HTML shell
│
├── db/
│   ├── database.py               # async engine + session_scope
│   ├── models.py                 # AthleteProfile + EventLog
│   ├── profile.py                # CRUD on the single profile row
│   └── logs.py                   # structured event log + dashboard metrics
│
├── observability/
│   ├── log_schema.py             # LogEvent dataclass
│   ├── dashboard_builder.py      # render docs/index.html + docs/logs.json
│   └── flush.py                  # APScheduler job + GitHub commit
│
├── docs/                         # GitHub Pages root
│   ├── index.html
│   └── logs.json
│
└── tests/
    ├── conftest.py
    ├── test_workout_schema.py
    ├── test_intervals_client.py
    └── test_context_builder.py
```

---

## Telegram commands

| Command    | What                                                                |
|------------|---------------------------------------------------------------------|
| `/start`   | Bot intro + profile status.                                          |
| `/help`    | Command list.                                                        |
| `/setup`   | 16-question onboarding to fill the athlete profile (one at a time). |
| `/profile` | Show your current profile.                                           |
| `/status`  | Quick sanity check (recent activities, today's wellness).            |
| `/end`     | End the current chat session — clears short-term history.            |
| Free text  | Goes through the router → reasoning or executor LLM.                 |

The bot **only** responds to the Telegram user id you set as
`TELEGRAM_OWNER_ID`. Anyone else gets a polite "this bot is private" reply.

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

The test suite uses `pytest-asyncio` in auto mode and `aiosqlite` so it
runs without a Postgres dependency. It covers:

- `intervals/workout_schema.py` — round-trip from JSON, render to native
  format (endurance + repeat blocks + gym sets), validation failures.
- `intervals/client.py` — error mapping (404 → `IntervalsNotFoundError`,
  5xx → `IntervalsAPIError`), 204 handling, and that `build_context_snapshot`
  degrades gracefully on partial upstream failures. Uses `httpx.MockTransport`
  to avoid network calls.
- `coach/context_builder.py` — `render_context_for_prompt` correctly
  formats profile, wellness, recent activities, and planned workouts.

---

## Troubleshooting

**The bot won't start: `Missing required environment variables`.**
You're missing one of `TELEGRAM_BOT_TOKEN`, `INTERVALS_API_KEY`,
`INTERVALS_ATHLETE_ID`, or `DATABASE_URL`. Check `.env` (local) or your
Railway service variables.

**Telegram messages don't arrive.**
Either the bot isn't reachable (check `/health`), or you're messaging from
an account other than `TELEGRAM_OWNER_ID`. The bot silently drops messages
from non-owners.

**Strava webhook never fires.**
Confirm your subscription exists with the `GET /push_subscriptions` curl
command in the Strava section. If you redeployed and the URL changed, you
must delete and re-create the subscription.

**The dashboard isn't updating on GitHub Pages.**
Hit `POST /admin/flush` and watch the response — it'll tell you whether
the push was skipped (no changes), failed (auth?), or succeeded. Confirm
your PAT has `repo` scope and `GITHUB_REPO` matches `owner/repo` exactly.

**Activity analysis email never arrives.**
Check the dashboard's Recent events feed for `activity_analysis_*` events.
Common causes: Resend domain not verified, `RESEND_TO_EMAIL` missing,
Intervals didn't sync the activity within the polling window (check
`intervals_sync` events).

**LLM calls are timing out / costing too much.**
Edit `config/llm_config.py` — drop the model size for the relevant job, or
reduce `max_tokens`. For local development, point any job at Ollama and
run a quantized model on your machine.

---

## Notes on the `emails/` folder name

You'll see this package called `emails/` rather than the more obvious
`email/`. That's deliberate — Python's standard library already ships an
`email` package (used internally by libraries like `smtplib`, the Resend
SDK, and httpx). If we named our folder `email/`, importing
`from email.resend_client import ...` would shadow the stdlib and break
half the ecosystem at random.

`emails/` (plural) sidesteps the conflict. It's the same convention used
by Django's `django.contrib.auth.signals` over `signals`, etc.

---

## License

MIT. Use it, fork it, modify it. No warranty — this is *your* coach,
running on *your* infrastructure, sending emails to *your* inbox.
