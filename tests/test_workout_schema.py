"""
Tests for `intervals.workout_schema`:
  - Workout.from_dict round-trip
  - render_description for endurance + repeat blocks
  - render_description for gym sets
  - validate() raising on bad input
  - to_intervals_payload shape
"""

from __future__ import annotations

import pytest

from intervals.exceptions import WorkoutValidationError
from intervals.workout_schema import (
    GymSet,
    VALID_SPORTS,
    Workout,
    WorkoutStep,
    WORKOUT_COLORS,
)


# ── from_dict ────────────────────────────────────────────────────────────────


def test_from_dict_endurance(sample_workout_dict):
    w = Workout.from_dict(sample_workout_dict)
    assert w.title == "Threshold 4×8"
    assert w.sport == "Run"
    assert w.date == "2024-08-12"
    assert w.color == "#FF5722"
    assert len(w.steps) == 4
    assert w.steps[0].type == "warmup"
    assert w.steps[0].duration_seconds == 600
    assert w.steps[1].repeat == 4
    assert w.steps[1].target_type == "pace"
    assert w.steps[1].target_low == 240


def test_from_dict_gym(sample_gym_workout_dict):
    w = Workout.from_dict(sample_gym_workout_dict)
    assert w.sport == "WeightTraining"
    assert len(w.gym_sets) == 3
    assert w.gym_sets[0].exercise == "Back Squat"
    assert w.gym_sets[0].sets == 4


def test_from_dict_uses_defaults_for_missing_fields():
    w = Workout.from_dict({"title": "Easy", "sport": "Run", "date": "2024-08-12",
                           "steps": [{"duration_seconds": 1800}]})
    assert w.color == "#4CAF50"
    assert w.planned_tss == 0.0
    assert w.steps[0].target_type == "open"
    assert w.steps[0].repeat == 1


# ── validate() ───────────────────────────────────────────────────────────────


def test_validate_rejects_missing_title():
    w = Workout(title="", sport="Run", date="2024-08-12",
                steps=[WorkoutStep(duration_seconds=1800)])
    with pytest.raises(WorkoutValidationError):
        w.validate()


def test_validate_rejects_missing_date():
    w = Workout(title="Easy", sport="Run", date="",
                steps=[WorkoutStep(duration_seconds=1800)])
    with pytest.raises(WorkoutValidationError):
        w.validate()


def test_validate_rejects_unknown_sport():
    w = Workout(title="Easy", sport="Quidditch", date="2024-08-12",
                steps=[WorkoutStep(duration_seconds=1800)])
    with pytest.raises(WorkoutValidationError):
        w.validate()


def test_validate_rejects_endurance_without_steps():
    w = Workout(title="Easy", sport="Run", date="2024-08-12")
    with pytest.raises(WorkoutValidationError):
        w.validate()


def test_validate_rejects_weights_without_gym_sets():
    w = Workout(title="Lift", sport="WeightTraining", date="2024-08-12")
    with pytest.raises(WorkoutValidationError):
        w.validate()


def test_validate_passes_for_complete_endurance(sample_workout_dict):
    Workout.from_dict(sample_workout_dict).validate()  # should not raise


def test_validate_passes_for_complete_gym(sample_gym_workout_dict):
    Workout.from_dict(sample_gym_workout_dict).validate()  # should not raise


# ── render_description() ─────────────────────────────────────────────────────


def test_render_description_endurance_includes_repeat_block(sample_workout_dict):
    out = Workout.from_dict(sample_workout_dict).render_description()
    assert "Repeat 4 times:" in out
    # The warm-up should be on its own (repeat=1).
    assert out.splitlines()[0].lower().startswith("bread")  # description first
    assert "10min" in out  # warmup duration formatted


def test_render_description_simple_steady():
    w = Workout(
        title="Easy",
        sport="Run",
        date="2024-08-12",
        steps=[WorkoutStep(type="steady", duration_seconds=2700, target_type="hr", zone=2)],
    )
    out = w.render_description()
    assert "45min" in out
    assert "Repeat" not in out  # no repeat block


def test_render_description_gym(sample_gym_workout_dict):
    w = Workout.from_dict(sample_gym_workout_dict)
    out = w.render_description()
    assert "Back Squat" in out
    assert "4×5" in out
    assert "85kg" in out
    assert "rest 3min" in out  # 180s -> 3min


# ── to_intervals_payload ─────────────────────────────────────────────────────


def test_to_intervals_payload_has_required_fields(sample_workout_dict):
    w = Workout.from_dict(sample_workout_dict)
    payload = w.to_intervals_payload()
    # Per the API reference, calendar workouts use category=WORKOUT.
    assert payload.get("category") == "WORKOUT"
    assert payload.get("type") == "Run"  # sport mapping
    assert payload.get("name") == "Threshold 4×8"
    assert payload.get("start_date_local", "").startswith("2024-08-12")
    assert payload.get("description")  # non-empty rendered description


def test_to_intervals_payload_includes_planned_tss(sample_workout_dict):
    w = Workout.from_dict(sample_workout_dict)
    payload = w.to_intervals_payload()
    # Planned load fields can be named differently across endpoints; just confirm
    # the value flows through somewhere sensible.
    flat = " ".join(f"{k}={v}" for k, v in payload.items())
    assert "75" in flat


# ── Constants sanity ─────────────────────────────────────────────────────────


def test_valid_sports_contains_essentials():
    for s in ("Run", "Ride", "Swim", "WeightTraining", "Other"):
        assert s in VALID_SPORTS


def test_workout_colors_keys_are_hex():
    for k, v in WORKOUT_COLORS.items():
        assert v.startswith("#") and len(v) == 7
