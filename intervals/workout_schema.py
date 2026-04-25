"""
Canonical workout schema.

Every workout the AI produces conforms to the `Workout` dataclass below.
`Workout.to_intervals_payload()` serialises it into the JSON body the
Intervals.icu `POST /events` endpoint accepts, including the native
plain-text "structured workout" format inside the `description` field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intervals.exceptions import WorkoutValidationError

# Sport codes accepted by Intervals.icu.
VALID_SPORTS = {
    "Run", "Ride", "Swim", "WeightTraining", "Hike", "Walk",
    "AlpineSki", "BackcountrySki", "Badminton", "Canoeing", "Crossfit",
    "EBikeRide", "EMountainBikeRide", "Elliptical", "Golf", "GravelRide",
    "TrackRide", "Handcycle", "HighIntensityIntervalTraining", "Hockey",
    "IceSkate", "InlineSkate", "Kayaking", "Kitesurf", "MountainBikeRide",
    "Cyclocross", "NordicSki", "OpenWaterSwim", "Padel", "Pilates", "Pickleball",
    "Racquetball", "Rugby", "RockClimbing", "RollerSki", "Rowing", "Sail",
    "Skateboard", "Snowboard", "Snowshoe", "Soccer", "Squash", "StairStepper",
    "StandUpPaddling", "Surfing", "TableTennis", "Tennis", "TrailRun",
    "Transition", "Velomobile", "VirtualRide", "VirtualRow", "VirtualRun",
    "VirtualSki", "WaterSport", "Wheelchair", "Windsurf", "Workout", "Yoga", "Other",
}

WORKOUT_COLORS = {
    "easy":      "#4CAF50",
    "long":      "#2196F3",
    "tempo":     "#FF9800",
    "threshold": "#FF5722",
    "intervals": "#F44336",
    "vo2max":    "#9C27B0",
    "recovery":  "#8BC34A",
    "gym":       "#607D8B",
    "swim":      "#00BCD4",
    "race":      "#FFD700",
    "note":      "#9E9E9E",
}


@dataclass
class WorkoutStep:
    type: str = "steady"            # warmup | interval | rest | cooldown | steady | note
    duration_seconds: int | None = None
    distance_meters: int | None = None
    target_type: str = "open"       # pace | power | hr | rpe | cadence | open
    target_low: float | None = None
    target_high: float | None = None
    zone: int | None = None
    notes: str = ""
    repeat: int = 1

    def to_native_line(self) -> str:
        """Render this step as a single line of the Intervals.icu native format."""
        # Duration / distance prefix.
        if self.distance_meters is not None and self.distance_meters > 0:
            prefix = _format_distance(self.distance_meters)
        elif self.duration_seconds is not None and self.duration_seconds > 0:
            prefix = _format_duration(self.duration_seconds)
        else:
            prefix = "open"

        target = _format_target(self)

        line = prefix
        if target:
            line = f"{line} @ {target}"
        if self.notes:
            line = f"{line} - {self.notes}"
        return line


@dataclass
class GymSet:
    exercise: str
    sets: int = 3
    reps: str = "10"
    load: str = "bodyweight"
    rest_seconds: int = 60
    notes: str = ""

    def to_native_line(self) -> str:
        rest = (
            f"rest {self.rest_seconds}sec"
            if self.rest_seconds < 60
            else f"rest {self.rest_seconds // 60}min"
        )
        line = f"{self.exercise} — {self.sets}×{self.reps} @ {self.load}, {rest}"
        if self.notes:
            line = f"{line} ({self.notes})"
        return line


@dataclass
class Workout:
    title: str
    sport: str
    description: str = ""
    date: str = ""                      # ISO date YYYY-MM-DD
    steps: list[WorkoutStep] = field(default_factory=list)
    gym_sets: list[GymSet] = field(default_factory=list)
    planned_tss: float = 0.0
    planned_duration_seconds: int = 0
    tags: list[str] = field(default_factory=list)
    color: str = "#4CAF50"

    def validate(self) -> None:
        """Raise WorkoutValidationError if the object isn't acceptable to Intervals."""
        if not self.title:
            raise WorkoutValidationError("Workout.title is required.")
        if not self.date:
            raise WorkoutValidationError("Workout.date is required (ISO YYYY-MM-DD).")
        if self.sport not in VALID_SPORTS:
            raise WorkoutValidationError(
                f"Unknown sport {self.sport!r}. Use one of: {sorted(VALID_SPORTS)}"
            )
        if self.sport == "WeightTraining":
            if not self.gym_sets:
                raise WorkoutValidationError(
                    "WeightTraining workouts must have at least one GymSet."
                )
        else:
            if not self.steps and not self.gym_sets:
                raise WorkoutValidationError(
                    "Endurance workouts must have at least one WorkoutStep."
                )

    # ── Render the structured description that Intervals parses ────────────

    def render_description(self) -> str:
        """
        Convert this Workout into the Intervals.icu native plain-text format.
        For gym sessions, output a structured exercise block; for endurance
        sessions, output duration/distance steps with targets + repeats.
        """
        lines: list[str] = []
        if self.description:
            lines.append(self.description.strip())
            lines.append("")

        if self.gym_sets:
            for gs in self.gym_sets:
                lines.append(gs.to_native_line())
            return "\n".join(lines).strip()

        # Endurance sessions — group consecutive steps with repeat>1 into
        # "Repeat N times:" blocks.
        i = 0
        while i < len(self.steps):
            step = self.steps[i]
            if step.repeat > 1:
                # Collect the immediately-following step as the rest interval if its
                # repeat matches.
                block: list[WorkoutStep] = [step]
                j = i + 1
                while j < len(self.steps) and self.steps[j].repeat == step.repeat:
                    block.append(self.steps[j])
                    j += 1
                lines.append(f"Repeat {step.repeat} times:")
                for b in block:
                    lines.append(f"  {b.to_native_line()}")
                i = j
            else:
                lines.append(step.to_native_line())
                i += 1
        return "\n".join(lines).strip()

    # ── Convert to the JSON payload Intervals.icu expects ──────────────────

    def to_intervals_payload(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "start_date_local": self.date,
            "category": "WORKOUT",
            "type": self.sport,
            "name": self.title,
            "description": self.render_description(),
            "moving_time": int(self.planned_duration_seconds or self._estimate_duration()),
            "color": self.color,
        }
        if self.tags:
            payload["tags"] = list(self.tags)
        if self.planned_tss:
            payload["icu_training_load"] = round(float(self.planned_tss))
        return payload

    def _estimate_duration(self) -> int:
        """Sum the step durations as a fallback if planned_duration_seconds isn't set."""
        total = 0
        for step in self.steps:
            if step.duration_seconds:
                total += step.duration_seconds * max(step.repeat, 1)
        for gs in self.gym_sets:
            total += gs.sets * 90 + gs.rest_seconds * gs.sets  # rough
        return total

    # ── Construction from the LLM's JSON output ────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workout":
        steps = [
            WorkoutStep(
                type=s.get("type", "steady"),
                duration_seconds=_int_or_none(s.get("duration_seconds")),
                distance_meters=_int_or_none(s.get("distance_meters")),
                target_type=s.get("target_type", "open"),
                target_low=_float_or_none(s.get("target_low")),
                target_high=_float_or_none(s.get("target_high")),
                zone=_int_or_none(s.get("zone")),
                notes=s.get("notes", "") or "",
                repeat=int(s.get("repeat") or 1),
            )
            for s in data.get("steps") or []
        ]
        gym_sets = [
            GymSet(
                exercise=g.get("exercise", "Exercise"),
                sets=int(g.get("sets") or 3),
                reps=str(g.get("reps") or "10"),
                load=str(g.get("load") or "bodyweight"),
                rest_seconds=int(g.get("rest_seconds") or 60),
                notes=g.get("notes", "") or "",
            )
            for g in data.get("gym_sets") or []
        ]
        return cls(
            title=data.get("title", "Workout"),
            sport=data.get("sport", "Other"),
            description=data.get("description", "") or "",
            date=data.get("date", "") or "",
            steps=steps,
            gym_sets=gym_sets,
            planned_tss=float(data.get("planned_tss") or 0),
            planned_duration_seconds=int(data.get("planned_duration_seconds") or 0),
            tags=list(data.get("tags") or []),
            color=data.get("color") or "#4CAF50",
        )


# ─── helpers ───────────────────────────────────────────────────────────────


def _int_or_none(v: Any) -> int | None:
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}sec"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}min{secs}sec"


def _format_distance(meters: int) -> str:
    if meters >= 1000 and meters % 1000 == 0:
        return f"{meters // 1000}km"
    if meters >= 1000:
        return f"{meters / 1000:.2f}km".rstrip("0").rstrip(".") + "km" if False else f"{meters / 1000:.1f}km"
    return f"{meters}m"


def _format_target(step: WorkoutStep) -> str:
    """Render a step's target in the native format Intervals understands."""
    t = step.target_type
    if t == "open":
        if step.zone:
            return f"Zone {step.zone}"
        return ""
    if t == "pace":
        if step.target_low and step.target_high:
            return f"{_secs_to_pace(step.target_low)}-{_secs_to_pace(step.target_high)}/km"
        if step.target_low:
            return f"{_secs_to_pace(step.target_low)}/km"
    if t == "power":
        if step.target_low and step.target_high:
            return f"{int(step.target_low)}-{int(step.target_high)}W"
        if step.target_low:
            return f"{int(step.target_low)}W"
    if t == "hr":
        if step.target_low and step.target_high:
            return f"{int(step.target_low)}-{int(step.target_high)}bpm"
        if step.target_low:
            return f"Zone {step.zone} HR" if step.zone else f"{int(step.target_low)}bpm"
    if t == "rpe":
        if step.target_low:
            return f"RPE {int(step.target_low)}"
    if t == "cadence":
        if step.target_low:
            return f"{int(step.target_low)}rpm"
    if step.zone:
        return f"Zone {step.zone}"
    return ""


def _secs_to_pace(seconds_per_km: float) -> str:
    s = int(round(seconds_per_km))
    return f"{s // 60}:{s % 60:02d}"
