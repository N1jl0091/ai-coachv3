"""
Structured event logging.

Every external interaction (LLM call, Intervals API call, webhook hit, email
send, error) writes a `LogEvent` to:
  1. The Postgres `event_log` table  (durable; queried by the dashboard build)
  2. The local `logs.jsonl` file        (tail-friendly during dev)

`log_event(...)` is a fire-and-forget convenience used everywhere. It is async
because it talks to Postgres, but callers can `asyncio.create_task(...)` it
when they don't want to await.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, func, select

from config import settings
from db.database import session_scope
from db.models import EventLog
from observability.log_schema import LogEvent

logger = logging.getLogger(__name__)


async def log_event(
    event_type: str,
    message: str = "",
    *,
    job: str | None = None,
    model_used: str | None = None,
    latency_ms: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    severity: str = "info",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Persist a log event. Never raises — logging failures must not crash callers.
    """
    event = LogEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        job=job or "system",
        model_used=model_used or "",
        latency_ms=latency_ms or 0,
        tokens_in=tokens_in or 0,
        tokens_out=tokens_out or 0,
        severity=severity,
        message=message,
        metadata=metadata or {},
    )

    # Best-effort JSONL append (synchronous file IO is fine — small writes).
    try:
        with settings.LOGS_JSONL_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")
    except Exception as exc:  # pragma: no cover — IO is best effort
        logger.warning("Failed to write logs.jsonl: %s", exc)

    # Persist to Postgres.
    try:
        async with session_scope() as session:
            row = EventLog(
                event_type=event.event_type,
                job=event.job,
                model_used=event.model_used or None,
                latency_ms=event.latency_ms or None,
                tokens_in=event.tokens_in or None,
                tokens_out=event.tokens_out or None,
                severity=event.severity,
                message=event.message,
                event_metadata=event.metadata or None,
            )
            session.add(row)
    except Exception as exc:
        logger.warning("Failed to write event_log row (%s): %s", event_type, exc)

    # Standard Python logging mirror — keeps Railway logs useful.
    py_level = {
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(severity, logging.INFO)
    logger.log(py_level, "[%s] %s", event_type, message)


def log_event_nowait(event_type: str, message: str = "", **kwargs: Any) -> None:
    """Fire-and-forget version for non-async call sites."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(log_event(event_type, message, **kwargs))
    except RuntimeError:
        # No running loop — run synchronously.
        asyncio.run(log_event(event_type, message, **kwargs))


# ── Aggregation queries used by the dashboard builder ─────────────────────


async def fetch_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent N events as dicts."""
    async with session_scope() as session:
        result = await session.execute(
            select(EventLog).order_by(desc(EventLog.timestamp)).limit(limit)
        )
        rows = result.scalars().all()
        return [_row_to_dict(r) for r in rows]


async def fetch_events_since(since: datetime) -> list[dict[str, Any]]:
    """Return all events since a timestamp."""
    async with session_scope() as session:
        result = await session.execute(
            select(EventLog)
            .where(EventLog.timestamp >= since)
            .order_by(desc(EventLog.timestamp))
        )
        rows = result.scalars().all()
        return [_row_to_dict(r) for r in rows]


async def fetch_dashboard_metrics(window_days: int = 7) -> dict[str, Any]:
    """
    Build the aggregate metrics the GitHub Pages dashboard renders:
      - last_event_timestamp
      - messages_per_day (last 7 days)
      - llm_calls_by_job
      - avg_latency_by_job
      - tokens_per_day
      - intervals_success_rate
      - recent_errors
      - email_count
      - recent_events
    """
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = await fetch_events_since(since)

    metrics: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "last_event_timestamp": rows[0]["timestamp"] if rows else None,
        "total_events": len(rows),
    }

    # Messages per day.
    msg_per_day: dict[str, int] = {}
    for r in rows:
        if r["event_type"] == "message_in":
            day = r["timestamp"][:10]
            msg_per_day[day] = msg_per_day.get(day, 0) + 1
    metrics["messages_per_day"] = dict(sorted(msg_per_day.items()))

    # LLM calls by job + latency.
    llm_calls_by_job: dict[str, int] = {}
    latency_sum_by_job: dict[str, int] = {}
    latency_count_by_job: dict[str, int] = {}
    for r in rows:
        if r["event_type"] == "llm_call":
            job = r.get("job") or "unknown"
            llm_calls_by_job[job] = llm_calls_by_job.get(job, 0) + 1
            if r.get("latency_ms"):
                latency_sum_by_job[job] = latency_sum_by_job.get(job, 0) + r["latency_ms"]
                latency_count_by_job[job] = latency_count_by_job.get(job, 0) + 1
    metrics["llm_calls_by_job"] = llm_calls_by_job
    metrics["avg_latency_by_job"] = {
        job: round(latency_sum_by_job[job] / latency_count_by_job[job])
        for job in latency_sum_by_job
        if latency_count_by_job[job] > 0
    }

    # Token usage per day (tokens_in + tokens_out).
    tokens_per_day: dict[str, int] = {}
    for r in rows:
        if r["event_type"] == "llm_call":
            day = r["timestamp"][:10]
            total = (r.get("tokens_in") or 0) + (r.get("tokens_out") or 0)
            tokens_per_day[day] = tokens_per_day.get(day, 0) + total
    metrics["tokens_per_day"] = dict(sorted(tokens_per_day.items()))

    # Intervals API success/failure rate.
    intervals_total = 0
    intervals_failed = 0
    for r in rows:
        if r["event_type"] in ("intervals_read", "intervals_write"):
            intervals_total += 1
            if r["severity"] in ("error", "critical"):
                intervals_failed += 1
    if intervals_total > 0:
        metrics["intervals_success_rate"] = round(
            (intervals_total - intervals_failed) / intervals_total * 100, 1
        )
    else:
        metrics["intervals_success_rate"] = None
    metrics["intervals_total"] = intervals_total
    metrics["intervals_failed"] = intervals_failed

    # Errors.
    errors = [r for r in rows if r["severity"] in ("error", "critical")][:20]
    metrics["recent_errors"] = errors

    # Email count.
    email_events = [r for r in rows if r["event_type"] == "email_sent"]
    metrics["email_count"] = len(email_events)
    metrics["last_email_at"] = email_events[0]["timestamp"] if email_events else None

    # Recent session log (last 30 events, friendly for display).
    metrics["recent_events"] = rows[:30]

    return metrics


async def count_events_today() -> int:
    """Total event count for today (UTC)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_scope() as session:
        result = await session.execute(
            select(func.count()).select_from(EventLog).where(EventLog.timestamp >= today)
        )
        return int(result.scalar() or 0)


def _row_to_dict(r: EventLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "event_type": r.event_type,
        "job": r.job,
        "model_used": r.model_used,
        "latency_ms": r.latency_ms,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "severity": r.severity,
        "message": r.message,
        "metadata": r.event_metadata or {},
    }
