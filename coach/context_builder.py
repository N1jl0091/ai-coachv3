"""
Context builder.

Before EVERY LLM call we pull a fresh snapshot of the athlete's state.
Target: <1.5s. Achieved by `asyncio.gather`-ing all the independent reads.

The output is a dict that downstream modules render into a system message
prefix, plus a human-readable string for the LLM to reason against.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import pytz

from config import settings
from db.profile import get_profile_dict
from intervals.client import IntervalsClient, get_client

logger = logging.getLogger(__name__)


async def build_context(
    telegram_id: str | None = None,
    *,
    client: IntervalsClient | None = None,
) -> dict[str, Any]:
    """Parallel fetch of profile + Intervals snapshot."""
    client = client or get_client()

    profile_task = (
        get_profile_dict(telegram_id) if telegram_id else _empty_profile()
    )
    snapshot_task = client.build_context_snapshot()

    profile, snapshot = await asyncio.gather(
        profile_task, snapshot_task, return_exceptions=True
    )

    if isinstance(profile, Exception):
        logger.warning("Failed to load profile: %s", profile)
        profile = None
    if isinstance(snapshot, Exception):
        logger.warning("Failed to load Intervals snapshot: %s", snapshot)
        snapshot = {}

    now_str = _now_in_athlete_timezone(profile)

    return {
        "now": now_str,
        "profile": profile or {},
        "today_wellness": (snapshot or {}).get("today_wellness") or {},
        "wellness_7d": (snapshot or {}).get("wellness_7d") or [],
        "activities_7d": (snapshot or {}).get("activities_7d") or [],
        "planned_14d": (snapshot or {}).get("planned_14d") or [],
        "athlete_intervals": (snapshot or {}).get("athlete") or {},
    }


def render_context_for_prompt(context: dict[str, Any]) -> str:
    """
    Turn the snapshot into a compact, human-readable string the LLM can read.
    Kept terse on purpose — LLMs reason better with structured numbers than
    pages of JSON.
    """
    lines: list[str] = []

    lines.append(f"Current datetime: {context.get('now')}")

    profile = context.get("profile") or {}
    if profile:
        sports = ", ".join(profile.get("sports") or []) or "—"
        injuries = ", ".join(profile.get("current_injuries") or []) or "none"
        limiters = ", ".join(profile.get("limiters") or []) or "none"
        lines.append("")
        lines.append("ATHLETE PROFILE:")
        lines.append(f"  Name: {profile.get('name') or '—'}")
        lines.append(f"  Age: {profile.get('age') or '—'}")
        lines.append(f"  Sports: {sports}")
        lines.append(f"  Primary: {profile.get('primary_sport') or '—'}")
        lines.append(f"  Hours/week: {profile.get('hours_per_week') or '—'}")
        lines.append(f"  Goal: {profile.get('goal_event') or '—'}"
                     f" ({profile.get('goal_date') or '—'},"
                     f" type={profile.get('goal_type') or '—'},"
                     f" target={profile.get('goal_time_target') or '—'})")
        lines.append(f"  Injuries: {injuries}")
        lines.append(f"  Limiters: {limiters}")
        lines.append(f"  Available days: {', '.join(profile.get('available_days') or []) or '—'}")
        lines.append(f"  Preferred long day: {profile.get('preferred_long_day') or '—'}")
        lines.append(f"  Intensity preference: {profile.get('preferred_intensity') or '—'}")
        lines.append(f"  Experience level: {profile.get('experience_level') or '—'}")
        if profile.get("notes"):
            lines.append(f"  Notes: {profile.get('notes')}")

    today = context.get("today_wellness") or {}
    if today:
        ctl = today.get("ctl")
        atl = today.get("atl")
        tsb = (
            round((ctl or 0) - (atl or 0), 1)
            if ctl is not None and atl is not None else None
        )
        sleep_hours = round((today.get("sleepSecs") or 0) / 3600, 1) if today.get("sleepSecs") else None
        lines.append("")
        lines.append("TODAY'S WELLNESS:")
        lines.append(f"  CTL (fitness): {ctl}")
        lines.append(f"  ATL (fatigue): {atl}")
        lines.append(f"  TSB (form): {tsb}")
        lines.append(f"  Ramp rate: {today.get('rampRate')}")
        lines.append(f"  HRV: {today.get('hrv')}")
        lines.append(f"  Resting HR: {today.get('restingHR')}")
        lines.append(f"  Sleep: {sleep_hours}h (score {today.get('sleepScore')}, quality {today.get('sleepQuality')})")
        lines.append(f"  Readiness: {today.get('readiness')}")
        lines.append(f"  Steps: {today.get('steps')}")
        lines.append(f"  Subjective: fatigue {today.get('fatigue')}, soreness {today.get('soreness')}, mood {today.get('mood')}, motivation {today.get('motivation')}")

    activities = context.get("activities_7d") or []
    if activities:
        lines.append("")
        lines.append("LAST 7 DAYS COMPLETED:")
        for a in activities[:10]:
            mins = round((a.get("moving_time") or 0) / 60)
            distance_km = round((a.get("distance") or 0) / 1000, 1) if a.get("distance") else None
            tss = a.get("icu_training_load") or "—"
            lines.append(
                f"  {a.get('start_date_local','')[:10]} {a.get('type'):>4}"
                f" — {a.get('name')}"
                f" ({mins}min"
                f"{f', {distance_km}km' if distance_km else ''}"
                f", TSS {tss})"
            )

    planned = context.get("planned_14d") or []
    if planned:
        lines.append("")
        lines.append("NEXT 14 DAYS PLANNED:")
        for e in planned[:14]:
            mins = round((e.get("moving_time") or 0) / 60)
            lines.append(
                f"  {e.get('start_date_local','')[:10]} {e.get('type'):>4}"
                f" — {e.get('name')} ({mins}min, TSS {e.get('icu_training_load') or '—'})"
                f"  [id={e.get('id')}]"
            )

    # Sport settings — useful for workout building.
    athlete = context.get("athlete_intervals") or {}
    sport_settings = athlete.get("sportSettings") or []
    if sport_settings:
        lines.append("")
        lines.append("SPORT SETTINGS:")
        for s in sport_settings:
            types = "/".join(s.get("types") or [])
            lines.append(
                f"  {types}: FTP={s.get('ftp')} LTHR={s.get('lthr')}"
                f" maxHR={s.get('max_hr')} threshPace={s.get('threshold_pace')}"
                f" HR zones={s.get('hr_zones')}"
                f" power zones={s.get('power_zones')}"
                f" pace zones={s.get('pace_zones')}"
            )

    return "\n".join(lines)


def _empty_profile() -> Any:
    async def _empty() -> dict:
        return {}
    return _empty()


def _now_in_athlete_timezone(profile: dict | None) -> str:
    tz_name = (profile or {}).get("timezone") or settings.ATHLETE_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone(settings.ATHLETE_TIMEZONE)
    now = datetime.now(tz)
    # "Saturday, 25 April 2026, 13:42 SAST"
    return now.strftime("%A, %d %B %Y, %H:%M %Z")
