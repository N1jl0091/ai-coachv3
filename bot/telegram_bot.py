"""
Telegram bot wiring.

Sets up the python-telegram-bot Application, registers commands and the
free-text message handler, and provides start/stop helpers used by main.py.

Every handler is wrapped in `safe_handle()` — the error boundary that
guarantees the athlete always gets a reply, even on failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.commands import (
    cmd_end,
    cmd_help,
    cmd_profile,
    cmd_setup,
    cmd_start,
    cmd_status,
    handle_setup_reply,
)
from bot.session import sessions
from coach import LLMError
from coach.router import route_message
from config import settings
from db.logs import log_event
from db.profile import get_profile_dict
from intervals.exceptions import IntervalsAPIError

logger = logging.getLogger(__name__)


# ── Owner gate ─────────────────────────────────────────────────────────────


def _owner_only(update: Update) -> bool:
    """Allow only the configured Telegram owner id, if one is set."""
    if not settings.TELEGRAM_OWNER_ID:
        return True  # no owner configured → permissive (single-user dev mode)
    return str(update.effective_user.id) == str(settings.TELEGRAM_OWNER_ID)


# ── Error boundary ─────────────────────────────────────────────────────────


HandlerFn = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def safe_handle(handler_fn: HandlerFn) -> HandlerFn:
    """Wrap a handler so any exception becomes a friendly Telegram reply + log."""

    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user is None or update.effective_message is None:
            return
        if not _owner_only(update):
            await update.effective_message.reply_text(
                "This bot is private. Sorry."
            )
            return
        try:
            await handler_fn(update, context)
        except IntervalsAPIError as exc:
            logger.exception("Intervals API error")
            await _safe_reply(
                update,
                "⚠️ Couldn't reach Intervals.icu right now. "
                "Your message is logged — try again in a moment.",
            )
            await log_event(
                "error", f"intervals_error: {exc}", severity="error",
                metadata={"handler": handler_fn.__name__},
            )
        except LLMError as exc:
            logger.exception("LLM error")
            await _safe_reply(
                update,
                "⚠️ AI hiccup on my end. Logged it — try rephrasing or try again.",
            )
            await log_event(
                "error", f"llm_error: {exc}", severity="error",
                metadata={"handler": handler_fn.__name__},
            )
        except Exception as exc:
            logger.exception("Unhandled handler error")
            await _safe_reply(
                update,
                "⚠️ Something unexpected happened. Logged for review.",
            )
            await log_event(
                "error", f"unhandled_error: {exc}", severity="critical",
                metadata={"handler": handler_fn.__name__},
            )

    wrapped.__name__ = f"safe_{handler_fn.__name__}"
    return wrapped


async def _safe_reply(update: Update, text: str) -> None:
    """Reply without re-raising — last resort if even Telegram is down."""
    try:
        if update.effective_message:
            await update.effective_message.reply_text(text)
    except Exception:
        logger.exception("Failed to deliver Telegram reply")


# ── Free-text message handler ──────────────────────────────────────────────


@safe_handle
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    if not text:
        return

    session = sessions.get(chat_id)

    # Setup state machine takes priority.
    if session.setup_state is not None:
        consumed = await handle_setup_reply(update, context, session)
        if consumed:
            return

    # If no profile yet, nudge towards /setup but still answer the message.
    if not session.history:
        profile = await get_profile_dict(chat_id)
        if not profile:
            await update.message.reply_text(
                "Heads-up — I don't have a profile for you yet. Run /setup whenever you're ready. "
                "I'll do my best with what I can pull from Intervals.icu in the meantime."
            )

    # Route to coach.
    session.add_user(text)
    reply = await route_message(chat_id, text, session.history)
    session.add_assistant(reply)

    await update.message.reply_text(reply)
    await log_event(
        "message_out",
        f"Replied to chat {chat_id} ({len(reply)} chars)",
        metadata={"chat_id": chat_id, "reply_len": len(reply)},
    )


# ── Wrapped commands ───────────────────────────────────────────────────────


_safe_start = safe_handle(cmd_start)
_safe_help = safe_handle(cmd_help)
_safe_setup = safe_handle(cmd_setup)
_safe_profile = safe_handle(cmd_profile)
_safe_status = safe_handle(cmd_status)
_safe_end = safe_handle(cmd_end)


# ── Application lifecycle ──────────────────────────────────────────────────


_app: Application | None = None


async def build_app() -> Application:
    """Construct and configure the Telegram Application."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set — Telegram bot can't start."
        )

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", _safe_start))
    app.add_handler(CommandHandler("help", _safe_help))
    app.add_handler(CommandHandler("setup", _safe_setup))
    app.add_handler(CommandHandler("profile", _safe_profile))
    app.add_handler(CommandHandler("status", _safe_status))
    app.add_handler(CommandHandler("end", _safe_end))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


async def start_bot() -> Application:
    """Initialise + start the Telegram bot polling in the current event loop."""
    global _app
    _app = await build_app()
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(drop_pending_updates=True)
    await log_event("system", "Telegram bot polling started")
    return _app


async def stop_bot() -> None:
    global _app
    if _app is None:
        return
    try:
        if _app.updater is not None:
            await _app.updater.stop()
        await _app.stop()
        await _app.shutdown()
    except Exception:
        logger.exception("Error stopping Telegram bot")
    finally:
        _app = None
        await log_event("system", "Telegram bot stopped")


def get_app() -> Application | None:
    return _app


async def send_owner_message(text: str) -> None:
    """Send a one-off message to the configured TELEGRAM_OWNER_ID."""
    if not _app or not settings.TELEGRAM_OWNER_ID:
        return
    try:
        await _app.bot.send_message(
            chat_id=int(settings.TELEGRAM_OWNER_ID), text=text
        )
    except Exception:
        logger.exception("Failed to send owner message")
