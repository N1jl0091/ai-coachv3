"""
Coach router.

Every incoming free-chat message goes through here. The router decides:
  - Tool-use intent (calendar edit / profile update) → executor.py
  - Anything else                                    → reasoning.py

A lightweight, low-temperature LLM call classifies the intent. We also use
quick keyword heuristics first to skip the LLM call entirely when the
classification is obvious.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from coach.context_builder import build_context, render_context_for_prompt
from coach.executor import handle_tool_request
from coach.llm_client import get_llm
from coach.reasoning import handle_reasoning_request
from db.logs import log_event

logger = logging.getLogger(__name__)


# Cheap regex-level shortcuts. If we match strongly here, we skip the
# classifier LLM call.
_TOOL_HINTS = re.compile(
    r"\b(add|create|schedule|move|reschedule|delete|remove|cancel|edit|update|change|build|plan|generate)\b",
    re.IGNORECASE,
)
_PROFILE_HINTS = re.compile(
    r"\b(injur(?:y|ed)|hurt|pulled|tweaked|sick|ill|travel(?:ling)?|away|"
    r"my (?:goal|race|event|name|age|email|timezone))\b",
    re.IGNORECASE,
)
_QUESTION_HINTS = re.compile(
    r"^(what|how|why|when|where|tell me|explain|show me|list|do i|am i)\b",
    re.IGNORECASE,
)


async def route_message(
    telegram_id: str,
    user_text: str,
    history: list[dict[str, str]],
) -> str:
    """Top-level: take an incoming message → decide path → return a reply string."""
    context = await build_context(telegram_id)
    intent = await _classify(user_text, context)

    await log_event(
        "message_in",
        f"intent={intent}: {user_text[:120]}",
        metadata={"intent": intent, "telegram_id": telegram_id},
    )

    if intent == "tool":
        return await handle_tool_request(
            telegram_id=telegram_id,
            user_text=user_text,
            context=context,
            history=history,
        )
    return await handle_reasoning_request(
        telegram_id=telegram_id,
        user_text=user_text,
        context=context,
        history=history,
    )


async def _classify(text: str, context: dict[str, Any]) -> str:
    """Return 'tool' or 'chat'."""
    # Heuristic shortcuts.
    text_stripped = text.strip()
    if _TOOL_HINTS.search(text_stripped) or _PROFILE_HINTS.search(text_stripped):
        # Ambiguity check: a question that *contains* tool keywords ("what
        # workouts did I do") is still a chat request. Run heuristic first,
        # then fall through to LLM if it looks like a question.
        if not _QUESTION_HINTS.match(text_stripped):
            return "tool"

    # LLM classifier — small, cheap.
    try:
        llm = get_llm()
        result = await llm.chat(
            job="router",
            system=_CLASSIFIER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Athlete said: {text!r}\n\n"
                        "Classify as exactly one word: 'tool' or 'chat'."
                    ),
                }
            ],
        )
        label = (result.get("text") or "").strip().lower().split()[0] if result.get("text") else "chat"
        if "tool" in label:
            return "tool"
    except Exception as exc:
        logger.warning("Router classifier failed, defaulting to chat: %s", exc)

    return "chat"


_CLASSIFIER_SYSTEM = """\
You classify a single athlete message into one of two categories.

Output 'tool' if the athlete is asking you to:
  - add, create, schedule, move, reschedule, delete, remove, edit, or update a workout
  - build, plan, or generate a session or training block
  - update their profile (injuries, limiters, goals, availability, equipment, name, etc.)

Output 'chat' for everything else — questions, observations, planning discussions,
analysis requests, "how am I doing", "what should I do tomorrow" (a question, not an instruction).

Output exactly one word: 'tool' or 'chat'. No punctuation, no explanation.
"""
