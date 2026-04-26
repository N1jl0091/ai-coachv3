"""
High-level calendar operations.

This module is the layer the executor LLM's tool-calls hit. Each function
takes plain inputs (Python types or dicts), validates them, calls the
appropriate IntervalsClient method, and returns a structured result for the
agent to confirm to the athlete.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from intervals.client import IntervalsClient, get_client
from intervals.exceptions import IntervalsAPIError
from intervals.workout_schema import Workout

logger = logging.getLogger(__name__)


async def create_workout_from_dict(
    workout_dict: dict[str, Any], client: IntervalsClient | None = None
) -> dict[str, Any]:
    """Build a Workout from a plain dict and POST it to the calendar."""
    client = client or get_client()
    workout = Workout.from_dict(workout_dict)
    payload = workout.to_intervals_payload()
    result = await client.create_event(payload)
    return {
        "ok": True,
        "event_id": result.get("id") if isinstance(result, dict) else None,
        "title": workout.title,
        "date": workout.date,
        "sport": workout.sport,
    }


async def update_workout(
    event_id: int, updates: dict[str, Any], client: IntervalsClient | None = None
) -> dict[str, Any]:
    """Patch a single calendar event."""
    client = client or get_client()
    payload = dict(updates)
    # Normalise key aliases.
    if "date" in payload and "start_date_local" not in payload:
        payload["start_date_local"] = payload.pop("date")
    if "title" in payload and "name" not in payload:
        payload["name"] = payload.pop("title")
    # Intervals.icu requires full ISO-8601 datetime for start_date_local.
    # If the LLM supplied a bare date ("2026-04-26") add a time component.
    if "start_date_local" in payload:
        val = str(payload["start_date_local"])
        if "T" not in val:
            payload["start_date_local"] = val + "T00:00:00"
    result = await client.update_event(event_id, payload)
    return {
        "ok": True,
        "event_id": event_id,
        "fields": list(payload.keys()),
        "result": result,
    }


async def move_workout(
    event_id: int, new_date: str, client: IntervalsClient | None = None
) -> dict[str, Any]:
    client = client or get_client()
    # Intervals.icu requires full datetime — append time if only date given.
    if "T" not in new_date:
        new_date = new_date + "T00:00:00"
    await client.move_event(event_id, new_date)
    return {"ok": True, "event_id": event_id, "moved_to": new_date}


async def delete_workout(
    event_id: int, client: IntervalsClient | None = None
) -> dict[str, Any]:
    client = client or get_client()
    await client.delete_event(event_id)
    return {"ok": True, "event_id": event_id}


async def list_workouts(
    oldest: str | None = None,
    newest: str | None = None,
    client: IntervalsClient | None = None,
) -> list[dict[str, Any]]:
    """Return a simplified list of scheduled workouts in a date range."""
    client = client or get_client()
    if oldest is None:
        oldest = date_cls.today().isoformat()
    if newest is None:
        newest = (date_cls.today() + timedelta(days=14)).isoformat()
    events = await client.get_events(oldest, newest)
    out: list[dict[str, Any]] = []
    for e in events:
        out.append(
            {
                "event_id": e.get("id"),
                "date": e.get("start_date_local"),
                "type": e.get("type"),
                "name": e.get("name"),
                "description": e.get("description"),
                "moving_time": e.get("moving_time"),
                "training_load": e.get("icu_training_load"),
                "tags": e.get("tags") or [],
            }
        )
    return out


async def find_workout_by_description(
    query: str,
    *,
    oldest: str | None = None,
    newest: str | None = None,
    client: IntervalsClient | None = None,
) -> list[dict[str, Any]]:
    """
    Substring-match a date or sport-type query against scheduled workouts.
    Used by the executor when the athlete says "Tuesday's tempo" — we
    search the calendar window for matching events.
    """
    workouts = await list_workouts(oldest=oldest, newest=newest, client=client)
    needle = query.lower()
    matches: list[dict[str, Any]] = []
    for w in workouts:
        haystack = " ".join(
            str(x) for x in (w.get("name"), w.get("type"), w.get("date"), *(w.get("tags") or []))
        ).lower()
        if needle in haystack:
            matches.append(w)
    return matches


async def bulk_create_workouts(
    workouts: list[dict[str, Any]], client: IntervalsClient | None = None
) -> dict[str, Any]:
    client = client or get_client()

    if not workouts:
        raise ValueError("bulk_create_workouts called with empty workouts list")

    payloads = []
    skipped = []
    for w in workouts:
        try:
            payloads.append(Workout.from_dict(w).to_intervals_payload())
        except Exception as exc:
            logger.warning("Skipping invalid workout %r: %s", w.get("title"), exc)
            skipped.append({"title": w.get("title"), "error": str(exc)})

    if not payloads:
        raise ValueError("No valid workouts to create after validation")

    try:
        results = await client.bulk_create_events(payloads)
        return {"ok": True, "count": len(results), "results": results, "skipped": skipped}
    except IntervalsAPIError as exc:
        logger.error("Bulk create failed, falling back to sequential: %s", exc)
        # Fall back to creating one at a time.
        results = []
        for payload in payloads:
            try:
                result = await client.create_event(payload)
                results.append(result)
            except IntervalsAPIError as inner_exc:
                logger.error("Sequential create failed for %r: %s", payload.get("name"), inner_exc)
                skipped.append({"title": payload.get("name"), "error": str(inner_exc)})
        if results:
            return {"ok": True, "count": len(results), "results": results, "skipped": skipped}
        raise