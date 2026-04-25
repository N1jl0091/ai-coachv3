"""
Telegram command implementations.

Supports /start, /setup, /profile, /status, /end, /help.

The /setup flow is a one-question-at-a-time state machine driven through
session.setup_state. Each user reply progresses to the next question until
the profile is fully populated.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bot.session import Session, sessions
from db.logs import log_event
from db.profile import get_profile_dict, upsert_profile
from intervals.client import get_client
from intervals.wellness import get_today_snapshot

logger = logging.getLogger(__name__)


# ── /start ─────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session = sessions.get(chat_id)
    session.reset()

    profile = await get_profile_dict(chat_id)
    if profile and profile.get("name"):
        await update.message.reply_text(
            f"Welcome back, {profile['name']}. Ready when you are. "
            f"Try /status for today's snapshot, or just tell me what's on your mind."
        )
    else:
        await update.message.reply_text(
            "I'm your coach. Powered by your Intervals.icu data, on top of an LLM that knows training.\n\n"
            "Run /setup to get started — I'll ask you a handful of questions to build your profile."
        )

    await log_event("session_start", "Started session", metadata={"chat_id": chat_id})


# ── /help ──────────────────────────────────────────────────────────────────


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "  /setup — initial profile setup (one question at a time)\n"
        "  /profile — view your saved profile\n"
        "  /status — today's snapshot: form, sleep, planned session\n"
        "  /end — clear conversation memory and start fresh\n"
        "  /help — this list\n\n"
        "Anything else, just type. I'll plan, edit your calendar, log limiters, or talk training."
    )


# ── /setup ─────────────────────────────────────────────────────────────────


SETUP_QUESTIONS: list[dict[str, Any]] = [
    {"key": "name", "prompt": "What's your first name?"},
    {"key": "age", "prompt": "How old are you?", "type": "int"},
    {
        "key": "sports",
        "prompt": "Which sports do you train? (comma-separated, e.g. running, cycling, gym)",
        "type": "list",
    },
    {"key": "primary_sport", "prompt": "Of those, which is your primary focus?"},
    {
        "key": "available_days",
        "prompt": "Which days of the week can you train? (comma-separated, e.g. mon, tue, thu, sat, sun)",
        "type": "list",
    },
    {
        "key": "hours_per_week",
        "prompt": "Roughly how many hours per week can you train?",
        "type": "float",
    },
    {"key": "goal_event", "prompt": "What's your main goal event? (e.g. Cape Town Marathon, or 'general fitness')"},
    {
        "key": "goal_date",
        "prompt": "Goal date? (YYYY-MM-DD, or 'skip' if there isn't one)",
        "type": "date_or_skip",
    },
    {
        "key": "goal_type",
        "prompt": "Goal type? (finish / time / podium / fitness)",
    },
    {
        "key": "goal_time_target",
        "prompt": "Target time, if any? (e.g. 3:45:00, or 'skip')",
        "type": "skippable",
    },
    {
        "key": "current_injuries",
        "prompt": "Any current injuries or niggles? (comma-separated, or 'none')",
        "type": "list_or_none",
    },
    {
        "key": "preferred_long_day",
        "prompt": "Preferred long-session day? (sat / sun / etc, or 'skip')",
        "type": "skippable",
    },
    {
        "key": "preferred_intensity",
        "prompt": "Preferred intensity distribution? (polarised / threshold / mixed)",
    },
    {
        "key": "experience_level",
        "prompt": "Experience level? (beginner / intermediate / advanced)",
    },
    {
        "key": "email",
        "prompt": "What email should the post-activity analysis emails go to?",
    },
    {
        "key": "timezone",
        "prompt": "Your timezone? (e.g. Africa/Johannesburg, Europe/London, America/New_York)",
    },
]


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session = sessions.get(chat_id)
    session.reset()
    session.setup_state = {"index": 0, "answers": {}}
    first_q = SETUP_QUESTIONS[0]
    await update.message.reply_text(
        f"Setup time. I'll ask {len(SETUP_QUESTIONS)} short questions, one at a time. "
        f"Type your answers as normal messages.\n\n{first_q['prompt']}"
    )


async def handle_setup_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> bool:
    """
    Handle a message while /setup is active. Returns True if the message
    was consumed by setup (caller should NOT route it to the coach).
    """
    state = session.setup_state
    if not state:
        return False

    text = (update.message.text or "").strip()
    idx = state["index"]
    if idx >= len(SETUP_QUESTIONS):
        return False

    q = SETUP_QUESTIONS[idx]
    parsed = _parse_setup_answer(text, q)
    if parsed is _PARSE_FAILED:
        await update.message.reply_text(
            f"Didn't catch that. {q['prompt']}"
        )
        return True

    if parsed is not _SKIP:
        state["answers"][q["key"]] = parsed

    state["index"] += 1
    if state["index"] < len(SETUP_QUESTIONS):
        next_q = SETUP_QUESTIONS[state["index"]]
        await update.message.reply_text(next_q["prompt"])
        return True

    # Setup complete — persist.
    chat_id = str(update.effective_chat.id)
    answers = state["answers"]
    profile = await upsert_profile(chat_id, answers)
    session.setup_state = None
    name = profile.get("name") or "athlete"
    await update.message.reply_text(
        f"✓ Profile saved, {name}. You're set.\n\n"
        "Try /status for today's snapshot, or just tell me what you want to do."
    )
    await log_event(
        "profile_update",
        "Initial /setup completed",
        metadata={"chat_id": chat_id, "fields": list(answers.keys())},
    )
    return True


_SKIP = object()
_PARSE_FAILED = object()


def _parse_setup_answer(text: str, question: dict[str, Any]) -> Any:
    qtype = question.get("type")
    lower = text.lower()
    if qtype == "skippable" and lower in ("skip", "none", "-"):
        return _SKIP
    if qtype == "list_or_none" and lower in ("none", "no", "-"):
        return []
    if qtype == "list":
        items = [t.strip() for t in text.split(",") if t.strip()]
        return items if items else _PARSE_FAILED
    if qtype == "list_or_none":
        items = [t.strip() for t in text.split(",") if t.strip()]
        return items
    if qtype == "int":
        try:
            return int(text)
        except ValueError:
            return _PARSE_FAILED
    if qtype == "float":
        try:
            return float(text)
        except ValueError:
            return _PARSE_FAILED
    if qtype == "date_or_skip":
        if lower in ("skip", "none", "-"):
            return _SKIP
        try:
            return date_cls.fromisoformat(text).isoformat()
        except ValueError:
            return _PARSE_FAILED
    return text


# ── /profile ───────────────────────────────────────────────────────────────


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    profile = await get_profile_dict(chat_id)
    if not profile:
        await update.message.reply_text(
            "No profile yet. Run /setup to create one."
        )
        return

    lines = ["Your profile:"]
    pretty_keys = [
        ("name", "Name"),
        ("age", "Age"),
        ("sports", "Sports"),
        ("primary_sport", "Primary sport"),
        ("available_days", "Available days"),
        ("hours_per_week", "Hours/week"),
        ("goal_event", "Goal event"),
        ("goal_date", "Goal date"),
        ("goal_type", "Goal type"),
        ("goal_time_target", "Target time"),
        ("current_injuries", "Injuries"),
        ("limiters", "Limiters"),
        ("preferred_long_day", "Long day"),
        ("preferred_intensity", "Intensity"),
        ("experience_level", "Experience"),
        ("email", "Email"),
        ("timezone", "Timezone"),
        ("notes", "Notes"),
    ]
    for key, label in pretty_keys:
        v = profile.get(key)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  {label}: {v}")

    lines.append("")
    lines.append("Tell me to change anything ('update my email to ...', 'I hurt my knee', etc).")
    await update.message.reply_text("\n".join(lines))


# ── /status ────────────────────────────────────────────────────────────────


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    try:
        wellness = await get_today_snapshot()
    except Exception as exc:
        logger.warning("Wellness fetch failed: %s", exc)
        await update.message.reply_text(
            "Couldn't reach Intervals.icu for wellness right now. Try again in a moment."
        )
        return

    client = get_client()
    today = date_cls.today().isoformat()
    soon = (date_cls.today() + timedelta(days=2)).isoformat()
    try:
        events = await client.get_events(today, soon)
    except Exception as exc:
        logger.warning("Events fetch failed: %s", exc)
        events = []

    lines: list[str] = ["Today's snapshot:"]
    if wellness:
        ctl = wellness.get("ctl")
        atl = wellness.get("atl")
        tsb = wellness.get("tsb")
        lines.append(f"  Form (TSB): {tsb}    Fitness (CTL): {ctl}    Fatigue (ATL): {atl}")
        if wellness.get("hrv") is not None:
            lines.append(f"  HRV: {wellness['hrv']}    Resting HR: {wellness.get('resting_hr')}")
        if wellness.get("sleep_hours"):
            lines.append(
                f"  Last night's sleep: {wellness['sleep_hours']}h "
                f"(score {wellness.get('sleep_score', '—')})"
            )
        if wellness.get("readiness") is not None:
            lines.append(f"  Readiness: {wellness['readiness']}")
    else:
        lines.append("  No wellness data yet for today.")

    lines.append("")
    today_events = [e for e in events if str(e.get("start_date_local", ""))[:10] == today]
    if today_events:
        lines.append("Today's planned:")
        for e in today_events:
            mins = round((e.get("moving_time") or 0) / 60)
            lines.append(f"  • {e.get('name')} ({e.get('type')}, {mins}min)")
    else:
        lines.append("Nothing planned for today.")

    await update.message.reply_text("\n".join(lines))


# ── /end ───────────────────────────────────────────────────────────────────


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    sessions.end(chat_id)
    await update.message.reply_text(
        "Session ended. Starting fresh whenever you're ready."
    )
    await log_event("session_end", "Session ended via /end", metadata={"chat_id": chat_id})
