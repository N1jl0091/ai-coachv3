"""Structured log event schema used across the app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Allowed values for `event_type` — keep in sync with callers.
EVENT_TYPES = {
    "message_in",      # incoming Telegram message
    "message_out",     # outbound Telegram reply
    "llm_call",        # LLM invocation (any provider, any job)
    "intervals_read",  # GET against Intervals.icu
    "intervals_write", # POST/PUT/DELETE against Intervals.icu
    "profile_update",  # athlete profile DB write
    "webhook",         # inbound webhook (Strava)
    "email_sent",      # outbound email
    "session_start",   # /start or first message of a new session
    "session_end",     # /end or session timeout
    "error",           # generic error
    "system",          # boot, shutdown, scheduled tasks
}


@dataclass
class LogEvent:
    timestamp: str
    event_type: str
    job: str = "system"
    model_used: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    severity: str = "info"
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
