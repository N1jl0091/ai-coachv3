"""
Reasoning brain.

Handles free-chat coaching messages: planning discussions, "how am I doing",
"what should I do tomorrow", analysis questions, etc. Uses the heaviest
configured model (LLM_JOBS['reasoning']).
"""

from __future__ import annotations

import logging
from typing import Any

from coach.context_builder import render_context_for_prompt
from coach.llm_client import get_llm
from config import settings

logger = logging.getLogger(__name__)


async def handle_reasoning_request(
    *,
    telegram_id: str,
    user_text: str,
    context: dict[str, Any],
    history: list[dict[str, str]],
) -> str:
    """Generate a coach-voice reply to a chat message."""
    coach_personality = settings.load_prompt("coach_personality")
    context_block = render_context_for_prompt(context)

    system = (
        coach_personality.strip()
        + "\n\n"
        + "──────── CONTEXT (refreshed on every message) ────────\n"
        + context_block
    )

    messages: list[dict[str, str]] = []
    # Replay recent conversation history (last 12 turns).
    for h in history[-12:]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_text})

    llm = get_llm()
    result = await llm.chat(
        job="reasoning",
        system=system,
        messages=messages,
    )
    return (result.get("text") or "").strip() or (
        "I'm here. Couldn't generate a response — try rephrasing?"
    )
