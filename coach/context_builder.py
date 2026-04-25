"""
Context builder — trimmed for token efficiency.
Target: ~1500 tokens per context render (down from ~10k).
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
        "activities_7d": (snapshot or {}).get("activities_7d") or [],
        "planned_14d": (snapshot or {}).get("planned_14d") or [],
        "athlete_intervals": (snapshot or {}).get("athlete") or {},
    }


def render_context_for_prompt(context: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append(f"Current datetime: {context.get('now')}")

    # ── Profile (essential fields only) ──────────────────────────────────
    profile = context.get("profile") or {}
    if profile:
        injuries = ", ".join(profile.get("current_injuries") or []) or "none"
        limiters = ", ".join(profile.get("limiters") or []) or "none"
        goal_target = profile.get("goal_time_target")
        goal_target_str = f", target {goal_target}" if goal_target else ""

        lines.append("")
        lines.append("ATHLETE:")
        lines.append(
            f"  {profile.get('name') or '—'}, {profile.get('age') or '—'}yo "
            f"| {profile.get('experience_level') or '—'} "
            f"| Primary: {profile.get('primary_sport') or '—'} "
            f"| {profile.get('hours_per_week') or '—'}h/week"
        )
        lines.append(
            f"  Goal: {profile.get('goal_event') or '—'} "
            f"{profile.get('goal_date') or ''} "
            f"({profile.get('goal_type') or '—'}{goal_target_str})"
        )
        lines.append(f"  Injuries: {injuries} | Limiters: {limiters}")
        lines.append(
            f"  Available: {', '.join(profile.get('available_days') or []) or '—'} "
            f"| Long day: {profile.get('preferred_long_day') or '—'} "
            f"| Intensity: {profile.get('preferred_intensity') or '—'}"
        )
        if profile.get("notes"):
            lines.append(f"  Notes: {profile['notes']}")

    # ── Today's wellness (numbers only, no nulls) ─────────────────────────
    today = context.get("today_wellness") or {}
    if today:
        ctl = today.get("ctl")
        atl = today.get("atl")
        tsb = round((ctl or 0) - (atl or 0), 1) if ctl and atl else "—"
        sleep_h = round((today.get("sleepSecs") or 0) / 3600, 1)
        lines.append("")
        lines.append(
            f"WELLNESS: CTL={ctl} ATL={atl} TSB={tsb} "
            f"| HRV={today.get('hrv')} RestHR={today.get('restingHR')} "
            f"| Sleep={sleep_h}h score={today.get('sleepScore')} "
            f"| Readiness={today.get('readiness')} "
            f"| Fatigue={today.get('fatigue')} Mood={today.get('mood')}"
        )

    # ── Last 5 activities (one line each) ─────────────────────────────────
    activities = (context.get("activities_7d") or [])[:5]
    if activities:
        lines.append("")
        lines.append("LAST 5 SESSIONS:")
        for a in activities:
            mins = round((a.get("moving_time") or 0) / 60)
            km = round((a.get("distance") or 0) / 1000, 1)
            km_str = f", {km}km" if km else ""
            lines.append(
                f"  {str(a.get('start_date_local', ''))[:10]} "
                f"{a.get('type', '')} — {a.get('name', '')} "
                f"({mins}min{km_str}, "
                f"TSS={a.get('icu_training_load', '—')})"
            )

    # ── Next 7 planned (one line each, keep event id for executor) ────────
    planned = (context.get("planned_14d") or [])[:7]
    if planned:
        lines.append("")
        lines.append("NEXT 7 PLANNED:")
        for e in planned:
            mins = round((e.get("moving_time") or 0) / 60)
            lines.append(
                f"  {str(e.get('start_date_local', ''))[:10]} "
                f"{e.get('type', '')} — {e.get('name', '')} "
                f"({mins}min, TSS={e.get('icu_training_load', '—')}) "
                f"[id={e.get('id')}]"
            )

    # ── Sport thresholds only (no zone arrays) ────────────────────────────
    sport_settings = (context.get("athlete_intervals") or {}).get("sportSettings") or []
    if sport_settings:
        lines.append("")
        lines.append("THRESHOLDS:")
        for s in sport_settings:
            types = "/".join(s.get("types") or [])
            lines.append(
                f"  {types}: FTP={s.get('ftp')}W "
                f"LTHR={s.get('lthr')} maxHR={s.get('max_hr')} "
                f"threshPace={s.get('threshold_pace')}s/km"
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
    return now.strftime("%A, %d %B %Y, %H:%M %Z")