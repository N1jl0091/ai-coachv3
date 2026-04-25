"""
Shared test fixtures.

Tests run without any external services. Network calls are mocked.
We point DATABASE_URL at an in-memory SQLite to keep `from db import …`
imports happy without a Postgres dependency.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Set test env BEFORE importing the app — settings reads at import time.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_OWNER_ID", "0")
os.environ.setdefault("INTERVALS_API_KEY", "test-key")
os.environ.setdefault("INTERVALS_ATHLETE_ID", "i1")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic")
os.environ.setdefault("OPENAI_API_KEY", "test-openai")
os.environ.setdefault("ATHLETE_TIMEZONE", "UTC")

# Make the package root importable regardless of where pytest is invoked.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def sample_workout_dict() -> dict:
    """A complete workout dict suitable for `Workout.from_dict`."""
    return {
        "title": "Threshold 4×8",
        "sport": "Run",
        "date": "2024-08-12",
        "description": "Bread-and-butter session.",
        "color": "#FF5722",
        "planned_tss": 75,
        "planned_duration_seconds": 3600,
        "tags": ["threshold"],
        "steps": [
            {
                "type": "warmup",
                "duration_seconds": 600,
                "target_type": "hr",
                "zone": 2,
                "notes": "easy spin-up",
            },
            {
                "type": "interval",
                "duration_seconds": 480,
                "target_type": "pace",
                "target_low": 240,
                "target_high": 250,
                "repeat": 4,
                "notes": "threshold rep",
            },
            {
                "type": "rest",
                "duration_seconds": 120,
                "target_type": "open",
                "repeat": 4,
            },
            {
                "type": "cooldown",
                "duration_seconds": 600,
                "target_type": "open",
            },
        ],
    }


@pytest.fixture
def sample_gym_workout_dict() -> dict:
    return {
        "title": "Lower body strength",
        "sport": "WeightTraining",
        "date": "2024-08-12",
        "color": "#607D8B",
        "gym_sets": [
            {"exercise": "Back Squat", "sets": 4, "reps": "5", "load": "85kg", "rest_seconds": 180},
            {"exercise": "Romanian Deadlift", "sets": 3, "reps": "8", "load": "70kg", "rest_seconds": 120},
            {"exercise": "Calf Raise", "sets": 3, "reps": "12", "load": "bodyweight", "rest_seconds": 45},
        ],
    }
