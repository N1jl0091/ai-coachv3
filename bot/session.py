"""
In-memory session store.

One session per chat_id. Holds the recent message history that the
coach feeds back to the LLM as context. `/end` clears it.

Also holds an optional in-progress 'setup' state machine so that the /setup
flow can ask one question at a time and remember which question is next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    chat_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    setup_state: dict[str, Any] | None = None
    last_active: float = field(default_factory=time.time)

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.last_active = time.time()

    def add_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.last_active = time.time()

    def reset(self) -> None:
        self.history.clear()
        self.setup_state = None


class SessionStore:
    """Process-local store of Session objects keyed by chat_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, chat_id: str) -> Session:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = Session(chat_id=chat_id)
        return self._sessions[chat_id]

    def end(self, chat_id: str) -> None:
        if chat_id in self._sessions:
            self._sessions[chat_id].reset()

    def remove(self, chat_id: str) -> None:
        self._sessions.pop(chat_id, None)


# Module-level singleton.
sessions = SessionStore()
