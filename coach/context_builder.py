"""
Context builder.

Two modes:

  build_context(telegram_id)  — FULL snapshot. Used by reasoning.py where
                                 coaching quality matters more than tokens.

  build_minimal_context(telegram_id) — MINIMAL snapshot. Used by executor.py.
                                        Only fetches what the LLM always needs:
                                        profile, planned calendar (event IDs),
                                        and today's one-line wellness summary.
                                        The executor can fetch more via tools
                                        (get_wellness, get_recent_activities,
                                        get_sport_settings) if it needs them.
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
    """
    Full parallel fetch — used by reasoning where quality > tokens.
    """
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


async def build_minimal_context(
    telegram_id: str | None = None,
    *,
    client: IntervalsClient | None = None,
) -> dict[str, Any]:
    """
    Minimal fetch for the executor — only what's always needed.
    Fetches in parallel: profile + planned calendar + athlete (for sport settings) + today wellness.
    Activities and detailed wellness are omitted — executor fetches them via tools if needed.
    """
    from datetime import date, timedelta
    client = client or get_client()

    today = date.today().isoformat()
    two_weeks = (date.today() + timedelta(days=14)).isoformat()

    profile_task = (
        get_profile_dict(telegram_id) if telegram_id else _empty_profile()
    )

    profile, planned, wellness_today, athlete = await asyncio.gather(
        profile_task,
        client.get_events(today, two_weeks, category="WORKOUT"),
        client.get_wellness(today),
        client.get_athlete(),
        return_exceptions=True,
    )

    if isinstance(profile, Exception):
        profile = None
    if isinstance(planned, Exception):
        planned = []
    if isinstance(wellness_today, Exception):
        wellness_today = {}
    if isinstance(athlete, Exception):
        athlete = {}

    now_str = _now_in_athlete_timezone(profile)

    return {
        "now": now_str,
        "profile": profile or {},
        "planned_14d": planned or [],
        "today_wellness": wellness_today or {},
        "athlete_intervals": athlete or {},
        # These are intentionally empty — executor fetches via tools if needed.
        "activities_7d": [],
        "wellness_7d": [],
    }


def render_context_for_prompt(context: dict[str, Any]) -> str:
    """
    Turn the snapshot into a compact string the LLM can read.
    Works with both full and minimal context dicts.
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
        lines.append(
            f"  Goal: {profile.get('goal_event') or '—'}"
            f" ({profile.get('goal_date') or '—'},"
            f" type={profile.get('goal_type') or '—'},"
            f" target={profile.get('goal_time_target') or '—'})"
        )
        lines.append(f"  Injuries: {injuries}")
        lines.append(f"  Limiters: {limiters}")
        lines.append(f"  Available days: {', '.join(profile.get('available_days') or []) or '—'}")
        lines.append(f"  Preferred long day: {profile.get('preferred_long_day') or '—'}")
        lines.append(f"  Experience: {profile.get('experience_level') or '—'}")
        if profile.get("notes"):
            lines.append(f"  Notes: {profile.get('notes')}")

    # One-line wellness summary — enough for the executor to know readiness.
    today = context.get("today_wellness") or {}
    if today:
        ctl = today.get("ctl")
        atl = today.get("atl")
        tsb = round((ctl or 0) - (atl or 0), 1) if ctl is not None and atl is not None else None
        lines.append("")
        lines.append(
            f"TODAY: CTL={ctl} ATL={atl} TSB={tsb}"
            f" HRV={today.get('hrv')} restHR={today.get('restingHR')}"
        )

    # Full wellness detail — only present in full context.
    activities = context.get("activities_7d") or []
    if activities:
        lines.append("")
        lines.append("LAST 7 DAYS COMPLETED:")
        for a in activities[:5]:
            mins = round((a.get("moving_time") or 0) / 60)
            distance_km = round((a.get("distance") or 0) / 1000, 1) if a.get("distance") else None
            tss = a.get("icu_training_load") or "—"
            lines.append(
                f"  {a.get('start_date_local','')[:10]} {a.get('type'):>4}"
                f" — {a.get('name')}"
                f" ({mins}min{f', {distance_km}km' if distance_km else ''}, TSS {tss})"
            )

    planned = context.get("planned_14d") or []
    if planned:
        lines.append("")
        lines.append("NEXT 14 DAYS PLANNED:")
        for e in planned[:14]:
            mins = round((e.get("moving_time") or 0) / 60)
            lines.append(
                f"  {e.get('start_date_local','')[:10]} {(e.get('type') or ''):>4}"
                f" — {e.get('name')} ({mins}min, TSS {e.get('icu_training_load') or '—'})"
                f"  [id={e.get('id')}]"
            )

    # Sport settings — key thresholds only.
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
            )

    return "\n".join(lines)


def render_minimal_context_for_prompt(context: dict[str, Any]) -> str:
    """
    Ultra-compact context for the executor — target under 100 tokens.
    Key-value pairs, no prose, no filler.
    """
    parts: list[str] = []

    parts.append(context.get("now") or "")

    profile = context.get("profile") or {}
    if profile:
        sports = "+".join(profile.get("sports") or []) or "—"
        goal = f"{profile.get('goal_event') or '—'} {profile.get('goal_date') or ''} ({profile.get('goal_time_target') or '—'})"
        injuries = ", ".join(profile.get("current_injuries") or []) or "none"
        parts.append(
            f"{profile.get('name') or '—'} | {sports} | {profile.get('experience_level') or '—'}\n"
            f"Goal: {goal} | Injuries: {injuries}"
        )
        if profile.get("notes"):
            parts.append(f"Notes: {profile['notes']}")

    today = context.get("today_wellness") or {}
    if today:
        ctl = today.get("ctl")
        atl = today.get("atl")
        tsb = round((ctl or 0) - (atl or 0), 1) if ctl is not None and atl is not None else None
        parts.append(f"CTL={ctl} ATL={atl} TSB={tsb} HRV={today.get('hrv')} restHR={today.get('restingHR')}")

    planned = context.get("planned_14d") or []
    if planned:
        cal_lines = ["PLAN:"]
        for e in planned[:14]:
            date_str = (e.get("start_date_local") or "")[:10]
            sport = (e.get("type") or "")
            cal_lines.append(f"  {date_str} {sport} — {e.get('name')} [id={e.get('id')}]")
        parts.append("\n".join(cal_lines))

    athlete = context.get("athlete_intervals") or {}
    sport_settings = athlete.get("sportSettings") or []
    if sport_settings:
        thresh_lines = ["THRESH:"]
        for s in sport_settings:
            types = "/".join(s.get("types") or [])
            thresh_lines.append(
                f"  {types}: FTP={s.get('ftp')} LTHR={s.get('lthr')} "
                f"maxHR={s.get('max_hr')} threshPace={s.get('threshold_pace')}"
            )
        parts.append("\n".join(thresh_lines))

    return "\n".join(p for p in parts if p)


def _empty_profile() -> Any:
    async def _empty() -> dict:
        return {}
    return _empty()


def _now_in_athlete_timezone(profile: dict | None) -> str:
    tz_name = (profile or {}).get("timezone") or settings.ATHLETE_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")