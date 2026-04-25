"""Custom exceptions raised by the Intervals.icu integration."""

from __future__ import annotations


class IntervalsAPIError(Exception):
    """Raised when an Intervals.icu HTTP call fails."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.status_code}] {super().__str__()}"
        return super().__str__()


class IntervalsNotFoundError(IntervalsAPIError):
    """Specific 404 from Intervals.icu — useful for distinguishing 'no event' from real failures."""


class WorkoutValidationError(Exception):
    """Raised when a Workout object can't be serialised to the Intervals payload."""
