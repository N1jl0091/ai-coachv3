"""
Tests for `coach.context_builder.render_context_for_prompt`.

The function is sync — it just formats the context dict produced by
`build_context`. We feed it carefully shaped fixtures to confirm the
rendered text contains all the salient fields a coach would need.
"""

from __future__ import annotations

from coach.context_builder import render_context_for_prompt


def test_render_with_empty_context():
    out = render_context_for_prompt({"now": "2024-08-12 06:00 SAST"})
    assert "Current datetime" in out
    assert "2024-08-12" in out


def test_render_with_full_profile():
    ctx = {
        "now": "2024-08-12 06:00 SAST",
        "profile": {
            "name": "Sam",
            "age": 36,
            "sports": ["running", "cycling"],
            "primary_sport": "running",
            "hours_per_week": 8,
            "goal_event": "Cape Town Marathon",
            "goal_date": "2024-10-20",
            "goal_type": "A",
            "goal_time_target": "3:30",
            "current_injuries": ["mild Achilles"],
            "limiters": ["time", "heat"],
            "available_days": ["Mon", "Tue", "Wed", "Thu", "Sat", "Sun"],
            "preferred_long_day": "Sun",
            "preferred_intensity": "balanced",
            "experience_level": "intermediate",
            "notes": "Likes Norwegian thresholds",
        },
    }
    out = render_context_for_prompt(ctx)
    assert "Sam" in out
    assert "36" in out
    assert "running, cycling" in out
    assert "Cape Town Marathon" in out
    assert "mild Achilles" in out
    assert "Sun" in out
    assert "Norwegian thresholds" in out


def test_render_with_wellness_data():
    ctx = {
        "now": "2024-08-12 06:00 SAST",
        "today_wellness": {
            "date": "2024-08-12",
            "hrv": 78,
            "resting_hr": 47,
            "sleep_hours": 7.5,
            "fatigue": 2,
            "soreness": 1,
            "ctl": 65,
            "atl": 50,
            "form": 15,
        },
    }
    out = render_context_for_prompt(ctx)
    # Some numeric values should make it through.
    for needle in ("78", "47", "7.5", "65"):
        assert needle in out


def test_render_with_recent_activities_section():
    ctx = {
        "now": "2024-08-12 06:00 SAST",
        "recent_activities": [
            {
                "name": "Easy 8k",
                "type": "Run",
                "start_date_local": "2024-08-11T06:00:00",
                "moving_time": 2700,
                "icu_training_load": 35,
                "icu_intensity": 65,
            },
            {
                "name": "Long ride",
                "type": "Ride",
                "start_date_local": "2024-08-10T07:00:00",
                "moving_time": 9000,
                "icu_training_load": 110,
            },
        ],
    }
    out = render_context_for_prompt(ctx)
    assert "Easy 8k" in out
    assert "Long ride" in out


def test_render_with_planned_workouts():
    ctx = {
        "now": "2024-08-12 06:00 SAST",
        "planned_workouts": [
            {
                "start_date_local": "2024-08-13",
                "name": "Threshold 4×8",
                "type": "Run",
                "icu_training_load": 75,
            }
        ],
    }
    out = render_context_for_prompt(ctx)
    assert "Threshold 4×8" in out
    assert "2024-08-13" in out


def test_render_does_not_crash_on_none_fields():
    ctx = {
        "now": "2024-08-12",
        "profile": {"name": None, "age": None, "sports": None},
        "today_wellness": None,
        "recent_activities": [],
        "planned_workouts": [],
    }
    out = render_context_for_prompt(ctx)
    assert isinstance(out, str)
    assert len(out) > 0
