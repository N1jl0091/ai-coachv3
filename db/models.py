"""
ORM models.

- AthleteProfile: one row per Telegram user. Columns mirror the spec in
  the v3 plan: name, age, sports, goal, injuries, equipment, etc.
- EventLog: one row per structured log event (LLM call, API call, error, …).
  Backed by JSONB for flexible metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AthleteProfile(Base):
    __tablename__ = "athlete_profile"

    telegram_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    age: Mapped[int | None] = mapped_column(Integer)

    # text[] in Postgres
    sports: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    primary_sport: Mapped[str | None] = mapped_column(String)

    available_days: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    hours_per_week: Mapped[float | None] = mapped_column(Float)

    goal_event: Mapped[str | None] = mapped_column(String)
    goal_date: Mapped[datetime | None] = mapped_column(Date)
    goal_type: Mapped[str | None] = mapped_column(String)
    goal_time_target: Mapped[str | None] = mapped_column(String)

    current_injuries: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    limiters: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    preferred_long_day: Mapped[str | None] = mapped_column(String)
    preferred_intensity: Mapped[str | None] = mapped_column(String)
    experience_level: Mapped[str | None] = mapped_column(String)

    equipment: Mapped[dict | None] = mapped_column(JSONB)
    email: Mapped[str | None] = mapped_column(String)
    timezone: Mapped[str | None] = mapped_column(String, default="Africa/Johannesburg")
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    job: Mapped[str | None] = mapped_column(String, index=True)
    model_used: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String, default="info", index=True)
    message: Mapped[str | None] = mapped_column(Text)
    # JSONB for flexible per-event metadata
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
