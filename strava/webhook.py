"""
Strava webhook router.

Two endpoints under `/webhook/strava`:

  GET  - subscription verification (handshake).
  POST - activity / athlete events.

Note: activities sync to Intervals.icu from Garmin directly (not via Strava).
We use Strava only for the webhook trigger. Matching to Intervals activities
is done by start time, not by Strava/Intervals ID.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import settings
from db.logs import log_event
from strava.analysis import analyse_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/strava", tags=["strava"])


@router.get("")
async def verify_subscription(request: Request) -> dict[str, Any]:
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if mode != "subscribe":
        raise HTTPException(status_code=400, detail="Unsupported hub.mode")
    if token != settings.STRAVA_VERIFY_TOKEN:
        await log_event("strava_webhook_verify", "Verify token mismatch", severity="warning")
        raise HTTPException(status_code=403, detail="Invalid verify token")
    if not challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")

    await log_event("strava_webhook_verify", "Strava subscription handshake accepted", severity="info")
    return {"hub.challenge": challenge}


@router.post("")
async def receive_event(request: Request) -> dict[str, Any]:
    """
    Strava webhook payload:
        {
          "aspect_type": "create" | "update" | "delete",
          "event_time": <unix epoch — when the activity was uploaded>,
          "object_id": <strava activity id>,
          "object_type": "activity" | "athlete",
          "owner_id": <athlete id>,
          "subscription_id": <int>,
          "updates": {...}
        }
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    object_id = payload.get("object_id")
    event_time = payload.get("event_time")  # unix epoch

    await log_event(
        "strava_webhook_event",
        f"object={object_type} aspect={aspect_type} id={object_id}",
        severity="info",
        metadata={"payload": payload},
    )

    if object_type == "activity" and aspect_type == "create" and object_id:
        # Pass event_time so analysis can match by start time in Intervals.
        asyncio.create_task(_run_analysis(object_id, event_time))

    return {"ok": True}


async def _run_analysis(strava_activity_id: str | int, event_time: int | None = None) -> None:
    try:
        result = await analyse_activity(strava_activity_id, event_time=event_time)
        await log_event(
            "activity_analysis_done",
            f"Activity {strava_activity_id} analysis: {result.get('ok')}",
            severity="info" if result.get("ok") else "warning",
            metadata=result,
        )
    except Exception as exc:
        logger.exception("Background analysis crashed")
        await log_event(
            "activity_analysis_error",
            f"Background analysis crashed: {exc}",
            severity="error",
            metadata={"strava_activity_id": str(strava_activity_id)},
        )