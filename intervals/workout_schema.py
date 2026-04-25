"""
Canonical workout schema.

Every workout the AI produces conforms to the `Workout` dataclass.
`Workout.to_intervals_payload()` serialises it into the JSON body the
Intervals.icu `POST /events` endpoint accepts, including the native
plain-text "structured workout" format inside the `description` field.

Intervals.icu native description format
---------------------------------------
Sections are plain-text headers. Steps start with "- " and use:
  - Duration:  30s  10m  1m30  (NOT "30sec" / "10min")
  - FTP %:     80%  80-90%
  - Ramp:      Ramp 60-80%
  - Watts:     200w  200-250w
  - Zone:      Z2  Z3
  - HR zone:   Z2 HR
  - HR %:      80% HR  (of max HR)
  - LTHR %:    100% LTHR
  - HR bpm:    140bpm  140-160bpm
  - Pace:      4:30/km  4:10-4:30/km
  - Pace zone: Z2 Pace
  - Cadence:   90rpm  90-100rpm  (appended after primary target)
  - RPE:       RPE 7
Repeats go on the section header: "Main set 4x"

Example:
  Warmup
  - 10m Z2 90rpm

  Main set 4x
  - 8m 95-105%
  - 2m Z1

  Cooldown
  - 10m Z1
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

# Map step.type → section header label. Empty = no header.
_SECTION_LABELS: dict[str, str] = {
    "warmup":   "Warmup",
    "cooldown": "Cooldown",
    "interval": "Main set",
    "steady":   "",
    "rest":     "",
    "note":     "",
}


@dataclass
class WorkoutStep:
    type: str = "steady"
    # Duration / distance (mutually exclusive — prefer duration for time-based sports)
    duration_seconds: int | None = None
    distance_meters: int | None = None
    # Primary intensity target
    # target_type values:
    #   ftp_percent  → 80% or 80-90% (percentage of FTP)
    #   power        → 200w or 200-250w (absolute watts)
    #   zone         → Z2 (power zone)
    #   hr           → 140bpm or 140-160bpm
    #   hr_zone      → Z2 HR
    #   hr_percent   → 80% HR (of max HR)
    #   lthr_percent → 100% LTHR (of threshold HR)
    #   pace         → 4:30/km (seconds per km in target_low/high)
    #   pace_zone    → Z2 Pace
    #   rpe          → RPE 7
    #   cadence      → 90rpm (use cadence_low / cadence_high instead when combined with power/HR)
    #   open         → no intensity target
    target_type: str = "open"
    target_low: float | None = None
    target_high: float | None = None
    zone: int | None = None          # power / HR zone number (1-7)
    ramp: bool = False               # True → render as "Ramp 60-80%"
    # Optional cadence range — appended to the step line alongside the primary target
    cadence_low: int | None = None
    cadence_high: int | None = None
    notes: str = ""
    repeat: int = 1                  # how many times this step repeats in its block

    def to_native_line(self) -> str:
        """Render as a single `- duration target cadence notes` line."""
        # --- duration / distance ---
        if self.distance_meters is not None and self.distance_meters > 0:
            prefix = _format_distance(self.distance_meters)
        elif self.duration_seconds is not None and self.duration_seconds > 0:
            prefix = _format_duration(self.duration_seconds)
        else:
            prefix = "open"

        parts = [f"- {prefix}"]

        target = _format_target(self)
        if target:
            parts.append(target)

        cadence = _format_cadence(self.cadence_low, self.cadence_high)
        if cadence:
            parts.append(cadence)

        if self.notes:
            parts.append(f"- {self.notes}")

        return " ".join(parts)


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
            f"rest {self.rest_seconds}s"
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
    date: str = ""                       # ISO YYYY-MM-DD
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

    def render_description(self) -> str:
        """
        Produce the Intervals.icu native plain-text description.
        Intervals parses this to render the structured step chart.
        """
        lines: list[str] = []

        if self.description:
            lines.append(self.description.strip())
            lines.append("")

        # Gym sessions: flat list of exercise lines.
        if self.gym_sets:
            for gs in self.gym_sets:
                lines.append(gs.to_native_line())
            return "\n".join(lines).strip()

        # Endurance: group steps into labelled sections.
        # A new section starts when:
        #   - repeat count changes (interval/rest blocks share a repeat count)
        #   - OR repeat==1 and step type changes
        sections: list[tuple[str, int, list[WorkoutStep]]] = []
        current_steps: list[WorkoutStep] = []
        current_type: str = ""
        current_repeat: int | None = None

        for step in self.steps:
            new_section = False
            if current_repeat is None:
                new_section = True
            elif step.repeat != current_repeat:
                new_section = True
            elif step.repeat == 1 and step.type != current_type and step.type not in ("rest",):
                new_section = True

            if new_section and current_steps:
                sections.append((current_type, current_repeat or 1, current_steps))
                current_steps = []

            current_steps.append(step)
            current_repeat = step.repeat
            if step.type not in ("rest",):
                current_type = step.type

        if current_steps:
            sections.append((current_type, current_repeat or 1, current_steps))

        for section_type, repeat, steps in sections:
            label = _SECTION_LABELS.get(section_type, "")
            if repeat > 1:
                header = f"{label} {repeat}x".strip() if label else f"{repeat}x"
            else:
                header = label

            if header:
                lines.append(header)
            for s in steps:
                lines.append(s.to_native_line())
            lines.append("")  # blank line between sections

        return "\n".join(lines).strip()

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
        total = 0
        for step in self.steps:
            if step.duration_seconds:
                total += step.duration_seconds * max(step.repeat, 1)
        for gs in self.gym_sets:
            total += gs.sets * 90 + gs.rest_seconds * gs.sets
        return total

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
                ramp=bool(s.get("ramp", False)),
                cadence_low=_int_or_none(s.get("cadence_low")),
                cadence_high=_int_or_none(s.get("cadence_high")),
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


# ─── formatting helpers ────────────────────────────────────────────────────


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
    """Intervals.icu duration tokens: 30s  10m  1m30"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs}s"


def _format_distance(meters: int) -> str:
    if meters >= 1000 and meters % 1000 == 0:
        return f"{meters // 1000}km"
    if meters >= 1000:
        return f"{meters / 1000:.1f}km"
    return f"{meters}m"


def _format_target(step: WorkoutStep) -> str:
    """
    Render the primary intensity target per the Intervals.icu native format.
    Cadence is handled separately in `_format_cadence`.
    """
    t = step.target_type
    lo = step.target_low
    hi = step.target_high
    z  = step.zone

    # FTP percentage
    if t == "ftp_percent":
        if step.ramp and lo is not None and hi is not None:
            return f"Ramp {int(lo)}-{int(hi)}%"
        if lo is not None and hi is not None:
            return f"{int(lo)}-{int(hi)}%"
        if lo is not None:
            return f"{int(lo)}%"
        if z is not None:
            return f"Z{z}"
        return ""

    # Absolute watts
    if t == "power":
        if step.ramp and lo is not None and hi is not None:
            return f"Ramp {int(lo)}-{int(hi)}w"
        if lo is not None and hi is not None:
            return f"{int(lo)}-{int(hi)}w"
        if lo is not None:
            return f"{int(lo)}w"
        return ""

    # Power zone
    if t == "zone":
        if z is not None:
            return f"Z{z}"
        return ""

    # HR absolute bpm
    if t == "hr":
        if lo is not None and hi is not None:
            return f"{int(lo)}-{int(hi)}bpm"
        if lo is not None:
            return f"{int(lo)}bpm"
        if z is not None:
            return f"Z{z} HR"
        return ""

    # HR zone
    if t == "hr_zone":
        if z is not None:
            return f"Z{z} HR"
        return ""

    # HR as % of max HR
    if t == "hr_percent":
        if lo is not None and hi is not None:
            return f"{int(lo)}-{int(hi)}% HR"
        if lo is not None:
            return f"{int(lo)}% HR"
        return ""

    # LTHR percentage
    if t == "lthr_percent":
        if lo is not None and hi is not None:
            return f"{int(lo)}-{int(hi)}% LTHR"
        if lo is not None:
            return f"{int(lo)}% LTHR"
        return ""

    # Absolute pace (target_low/high in seconds per km)
    if t == "pace":
        if lo is not None and hi is not None:
            return f"{_secs_to_pace(lo)}-{_secs_to_pace(hi)}/km"
        if lo is not None:
            return f"{_secs_to_pace(lo)}/km"
        return ""

    # Pace zone
    if t == "pace_zone":
        if z is not None:
            return f"Z{z} Pace"
        return ""

    # RPE
    if t == "rpe":
        if lo is not None:
            return f"RPE {int(lo)}"
        return ""

    # Cadence-only step (no other target)
    if t == "cadence":
        return _format_cadence(
            _int_or_none(lo) if lo is not None else None,
            _int_or_none(hi) if hi is not None else None,
        )

    # open — may still have a zone
    if t == "open":
        if z is not None:
            return f"Z{z}"
        return ""

    # Fallback: unknown type but has a zone
    if z is not None:
        return f"Z{z}"
    return ""


def _format_cadence(low: int | None, high: int | None) -> str:
    if low is not None and high is not None:
        return f"{low}-{high}rpm"
    if low is not None:
        return f"{low}rpm"
    return ""


def _secs_to_pace(seconds_per_km: float) -> str:
    s = int(round(seconds_per_km))
    return f"{s // 60}:{s % 60:02d}"