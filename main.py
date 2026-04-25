"""
ai-coach — application entry point.

Bootstraps:
  - Logging
  - Postgres (auto-creates tables on first run)
  - Telegram long-polling (runs alongside FastAPI in the same process)
  - APScheduler — periodic dashboard flush + initial flush on startup
  - Strava webhook router
  - /health endpoint for Railway healthchecks

Run locally:        uvicorn main:app --reload --port 8000
Run on Railway:     handled by Procfile / railway.toml
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from bot.telegram_bot import start_bot, stop_bot
from config import settings
from db.database import init_db, shutdown_db
from db.logs import log_event
from intervals.client import close_client
from observability.flush import scheduled_flush
from strava.webhook import router as strava_router

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down chatty libraries.
    for noisy in ("httpx", "httpcore", "telegram.ext", "apscheduler", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger.info("ai-coach starting up …")

    missing = settings.validate_required()
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"See .env.example."
        )

    await init_db()
    await log_event("startup", "ai-coach booting", severity="info")

    # Telegram long-polling.
    try:
        await start_bot()
        logger.info("Telegram bot started")
    except Exception as exc:
        logger.exception("Telegram bot failed to start")
        await log_event("startup_error", f"Telegram bot failed: {exc}", severity="error")

    # Scheduler: dashboard flush every 15 minutes + an initial flush 30s after boot.
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        scheduled_flush,
        IntervalTrigger(minutes=120),
        id="dashboard_flush",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    app.state.scheduler = scheduler

    logger.info("ai-coach ready")
    try:
        yield
    finally:
        logger.info("ai-coach shutting down …")
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("Scheduler shutdown failed")
        try:
            await stop_bot()
        except Exception:
            logger.exception("Telegram bot shutdown failed")
        try:
            await close_client()
        except Exception:
            logger.exception("Intervals client close failed")
        try:
            await shutdown_db()
        except Exception:
            logger.exception("DB shutdown failed")
        await log_event("shutdown", "ai-coach stopped", severity="info")


app = FastAPI(
    title="ai-coach",
    description="Personal Telegram coaching bot, Intervals.icu-backed.",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(strava_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "ai-coach",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Railway healthcheck endpoint. Always cheap — no external calls."""
    return {"status": "ok"}


@app.post("/admin/flush")
async def admin_flush() -> dict[str, object]:
    """
    Manual trigger for the observability flush. Useful for debugging.
    Not authenticated — Railway's URL is private; consider putting it behind
    a secret if you expose this app publicly.
    """
    from observability.flush import flush_now
    result = await flush_now()
    return {"flushed": True, "result": result}


if __name__ == "__main__":
    # Convenience for `python main.py` — equivalent to uvicorn with our config.
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
