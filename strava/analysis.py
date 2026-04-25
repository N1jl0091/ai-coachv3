"""
Post-activity analysis pipeline.

Triggered when Strava notifies us of a new activity. Flow:

  1. Wait until Intervals.icu has synced the activity from Strava (poll briefly).
  2. Pull the activity, athlete profile, and any planned workout for that day.
  3. Ask the analysis LLM (gpt-4o per `LLM_JOBS`) to produce an HTML email
     in the format defined in `prompts/activity_analysis.txt`.
  4. Send via Resend.
  5. Best-effort Telegram nudge to the owner ("📬 Activity analysis emailed").

Designed to be robust to flaky network / slow sync — every step logs and
failures are surfaced rather than swallowed silently.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from coach.llm_client import get_llm
from config import settings
from db.logs import log_event
from db.profile import get_profile_dict
from emails.resend_client import send_email
from intervals.client import get_client
from intervals.exceptions import IntervalsAPIError, IntervalsNotFoundError

logger = logging.getLogger(__name__)


# Polling cadence for Intervals sync. Strava → Intervals propagation usually
# takes <60 seconds but can spike — back off up to ~5 minutes.
SYNC_POLL_INTERVALS = [5, 10, 15, 20, 30, 30, 45, 45, 60, 60]


async def wait_for_intervals_sync(strava_activity_id: str | int) -> dict[str, Any] | None:
    """
    Poll Intervals.icu for the most recently uploaded activity around the
    time of the Strava webhook. We match by recency rather than strava_id
    because Garmin→Intervals sync doesn't preserve Strava's activity id.
    """
    client = get_client()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    for delay in SYNC_POLL_INTERVALS:
        await asyncio.sleep(delay)
        try:
            activities = await client.get_activities(
                yesterday,
                today,
                fields="id,start_date_local,type,name,strava_id",
            )
            if activities:
                # Activities are returned newest first.
                # Take the most recent one — it's the one that just synced.
                best = activities[0]
                activity_id = str(best["id"])
                activity = await client.get_activity(activity_id, intervals=True)
                if activity:
                    await log_event(
                        "intervals_sync",
                        f"Activity {activity_id} found via recency match "
                        f"(strava webhook id={strava_activity_id})",
                        severity="info",
                        metadata={
                            "activity_id": activity_id,
                            "strava_webhook_id": str(strava_activity_id),
                        },
                    )
                    return activity
        except IntervalsAPIError as exc:
            await log_event(
                "intervals_sync",
                f"Intervals lookup error: {exc}",
                severity="warning",
                metadata={"strava_activity_id": str(strava_activity_id)},
            )

    await log_event(
        "intervals_sync",
        f"No recent activity found in Intervals after polling "
        f"(strava webhook id={strava_activity_id})",
        severity="warning",
        metadata={"strava_activity_id": str(strava_activity_id)},
    )
    return None


async def _planned_workout_for(date_iso: str) -> dict[str, Any] | None:
    """Find the planned workout (if any) for a given date."""
    client = get_client()
    try:
        events = await client.get_events(oldest=date_iso, newest=date_iso, category="WORKOUT")
        return events[0] if events else None
    except IntervalsAPIError:
        return None


def _activity_summary(activity: dict[str, Any]) -> dict[str, Any]:
    """Reduce the activity dict to fields the LLM actually needs."""
    keys = [
        "id", "name", "type", "start_date_local", "moving_time", "elapsed_time",
        "distance", "average_speed", "max_speed",
        "average_heartrate", "max_heartrate",
        "average_watts", "weighted_average_watts", "max_watts", "kilojoules",
        "average_cadence", "total_elevation_gain", "calories",
        "icu_training_load", "icu_intensity", "icu_efficiency_factor",
        "icu_variability_index", "icu_pm_ftp", "icu_hrr",
        "icu_zone_times", "icu_hr_zone_times", "icu_pace_zone_times",
        "perceived_exertion", "feel", "kudos", "description",
    ]
    return {k: activity.get(k) for k in keys if activity.get(k) is not None}


async def _build_analysis(
    activity: dict[str, Any],
    planned: dict[str, Any] | None,
    profile: dict[str, Any] | None,
) -> str:
    """Call the analysis LLM and return the HTML body."""
    system = settings.load_prompt("activity_analysis")

    payload_lines = [
        "ACTIVITY SUMMARY:",
        _format_dict(_activity_summary(activity)),
        "",
        "PLANNED WORKOUT FOR THIS DAY:",
        _format_dict(planned) if planned else "(none — unplanned session)",
        "",
        "ATHLETE PROFILE:",
        _format_dict(profile) if profile else "(no profile saved)",
    ]

    user_message = "\n".join(payload_lines)

    llm = get_llm()
    result = await llm.chat(
        job="analysis",
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return (result.get("text") or "").strip()


def _format_dict(d: dict[str, Any] | None, indent: int = 0) -> str:
    if not d:
        return "{}"
    pad = "  " * indent
    lines: list[str] = []
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_format_dict(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}: {v}")
        else:
            lines.append(f"{pad}{k}: {v}")
    return "\n".join(lines)


async def analyse_activity(strava_activity_id: str | int) -> dict[str, Any]:
    """
    Full post-activity pipeline. Returns a status dict for logging / debugging.
    """
    await log_event(
        "activity_analysis_start",
        f"Starting analysis for Strava activity {strava_activity_id}",
        severity="info",
        metadata={"strava_id": str(strava_activity_id)},
    )

    activity = await wait_for_intervals_sync(strava_activity_id)
    if not activity:
        return {"ok": False, "reason": "intervals_sync_timeout"}

    activity_date = (
        (activity.get("start_date_local") or activity.get("start_date") or "")[:10]
    )
    if not activity_date:
        activity_date = (datetime.now(timezone.utc).date()).isoformat()

    planned = await _planned_workout_for(activity_date)

    try:
        profile = await get_profile_dict()
    except Exception as exc:
        logger.warning("Profile fetch failed (continuing without it): %s", exc)
        profile = None

    try:
        body_html = await _build_analysis(activity, planned, profile)
    except Exception as exc:
        await log_event(
            "activity_analysis_error",
            f"LLM analysis failed: {exc}",
            severity="error",
            metadata={"strava_id": str(strava_activity_id)},
        )
        return {"ok": False, "reason": "llm_failure", "error": str(exc)}

    if not body_html:
        await log_event(
            "activity_analysis_error",
            "LLM returned empty analysis body",
            severity="error",
            metadata={"strava_id": str(strava_activity_id)},
        )
        return {"ok": False, "reason": "empty_body"}

    activity_name = activity.get("name") or "Activity"
    sport = activity.get("type") or ""
    subject = f"Coach review · {activity_name}"
    if sport:
        subject = f"Coach review · {activity_name} ({sport})"

    email_result = await send_email(
        subject,
        body_html,
        metadata={
            "strava_id": str(strava_activity_id),
            "activity_name": activity_name,
            "activity_date": activity_date,
        },
    )

    return {
        "ok": email_result.get("ok", False),
        "email": email_result,
        "activity_id": activity.get("id"),
        "activity_date": activity_date,
    }
