"""
Athlete profile read/write helpers.

The profile is keyed by telegram_id. Single-user bot in practice, but
designed so multiple chat ids could each have their own profile.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any

from sqlalchemy import select

from db.database import session_scope
from db.models import AthleteProfile

logger = logging.getLogger(__name__)


# Fields the AI executor is allowed to update mid-conversation.
# (Everything except the primary key + audit columns.)
EDITABLE_FIELDS: set[str] = {
    "name",
    "age",
    "sports",
    "primary_sport",
    "available_days",
    "hours_per_week",
    "goal_event",
    "goal_date",
    "goal_type",
    "goal_time_target",
    "current_injuries",
    "limiters",
    "preferred_long_day",
    "preferred_intensity",
    "experience_level",
    "equipment",
    "email",
    "timezone",
    "notes",
}


async def get_profile(telegram_id: str) -> AthleteProfile | None:
    """Fetch a profile by telegram_id. Returns None if not found."""
    async with session_scope() as session:
        result = await session.execute(
            select(AthleteProfile).where(AthleteProfile.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def get_profile_dict(telegram_id: str) -> dict[str, Any] | None:
    """Same as get_profile but returns a plain dict (safe across sessions)."""
    profile = await get_profile(telegram_id)
    if profile is None:
        return None
    return _profile_to_dict(profile)


async def upsert_profile(telegram_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    Create or update a profile. Returns the resulting profile as a dict.
    Only fields in EDITABLE_FIELDS are accepted.
    """
    clean_updates = _validate_updates(updates)

    async with session_scope() as session:
        result = await session.execute(
            select(AthleteProfile).where(AthleteProfile.telegram_id == telegram_id)
        )
        profile = result.scalar_one_or_none()

        if profile is None:
            profile = AthleteProfile(telegram_id=telegram_id, **clean_updates)
            session.add(profile)
            logger.info("Created profile for telegram_id=%s", telegram_id)
        else:
            for key, value in clean_updates.items():
                setattr(profile, key, value)
            logger.info(
                "Updated profile for telegram_id=%s, fields=%s",
                telegram_id,
                list(clean_updates.keys()),
            )

        await session.flush()
        return _profile_to_dict(profile)


async def update_profile_fields(
    telegram_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Alias for upsert_profile — used by the executor."""
    return await upsert_profile(telegram_id, updates)


def _validate_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Drop any keys that aren't editable, normalise dates."""
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in EDITABLE_FIELDS:
            logger.warning("Ignoring unknown profile field: %s", key)
            continue

        # Normalise ISO date strings into date objects for the goal_date column.
        if key == "goal_date" and isinstance(value, str):
            try:
                value = date_cls.fromisoformat(value)
            except ValueError:
                logger.warning("Invalid goal_date format: %r", value)
                continue

        clean[key] = value
    return clean


def _profile_to_dict(profile: AthleteProfile) -> dict[str, Any]:
    """Serialise an AthleteProfile row to a plain dict."""
    return {
        "telegram_id": profile.telegram_id,
        "name": profile.name,
        "age": profile.age,
        "sports": list(profile.sports) if profile.sports else [],
        "primary_sport": profile.primary_sport,
        "available_days": list(profile.available_days) if profile.available_days else [],
        "hours_per_week": profile.hours_per_week,
        "goal_event": profile.goal_event,
        "goal_date": profile.goal_date.isoformat() if profile.goal_date else None,
        "goal_type": profile.goal_type,
        "goal_time_target": profile.goal_time_target,
        "current_injuries": list(profile.current_injuries) if profile.current_injuries else [],
        "limiters": list(profile.limiters) if profile.limiters else [],
        "preferred_long_day": profile.preferred_long_day,
        "preferred_intensity": profile.preferred_intensity,
        "experience_level": profile.experience_level,
        "equipment": profile.equipment or {},
        "email": profile.email,
        "timezone": profile.timezone,
        "notes": profile.notes,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
