"""
Strava webhook router.

Two endpoints under `/webhook/strava`:

  GET  - subscription verification (handshake).
  POST - activity / athlete events.

The Strava subscription handshake is documented at:
https://developers.strava.com/docs/webhooks/

We accept events for new (`create`) activities only and dispatch them to a
background task that runs the full analysis pipeline. The webhook itself
returns 200 immediately — Strava treats anything else as a delivery failure
and will retry, which would re-trigger analysis.
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
    """
    Handshake endpoint Strava calls when creating a subscription.

    Strava sends:  ?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
    We must echo back the challenge if the verify token matches.
    """
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if mode != "subscribe":
        raise HTTPException(status_code=400, detail="Unsupported hub.mode")
    if token != settings.STRAVA_VERIFY_TOKEN:
        await log_event(
            "strava_webhook_verify",
            "Verify token mismatch — rejecting subscription handshake",
            severity="warning",
        )
        raise HTTPException(status_code=403, detail="Invalid verify token")
    if not challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")

    await log_event(
        "strava_webhook_verify",
        "Strava subscription handshake accepted",
        severity="info",
    )
    return {"hub.challenge": challenge}


@router.post("")
async def receive_event(request: Request) -> dict[str, Any]:
    """
    Receive a webhook event from Strava.

    Body shape (per Strava docs):
        {
          "aspect_type": "create" | "update" | "delete",
          "event_time": <epoch>,
          "object_id": <activity id or athlete id>,
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

    await log_event(
        "strava_webhook_event",
        f"object={object_type} aspect={aspect_type} id={object_id}",
        severity="info",
        metadata={"payload": payload},
    )

    # Only react to NEW activities — ignore updates, deletes, and athlete events.
    if object_type == "activity" and aspect_type == "create" and object_id:
        # Fire-and-forget: the analysis can take minutes (waits for Intervals sync).
        asyncio.create_task(_run_analysis(object_id))

    return {"ok": True}


async def _run_analysis(activity_id: str | int) -> None:
    """Wrapper so failures inside the background task get logged, not lost."""
    try:
        result = await analyse_activity(activity_id)
        await log_event(
            "activity_analysis_done",
            f"Activity {activity_id} analysis: {result.get('ok')}",
            severity="info" if result.get("ok") else "warning",
            metadata=result,
        )
    except Exception as exc:
        logger.exception("Background analysis crashed")
        await log_event(
            "activity_analysis_error",
            f"Background analysis crashed: {exc}",
            severity="error",
            metadata={"activity_id": str(activity_id)},
        )
