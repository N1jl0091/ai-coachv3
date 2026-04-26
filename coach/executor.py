"""
Executor brain.

Runs tool-use loops to fulfil athlete instructions:
  - calendar writes (create / update / move / delete workouts)
  - profile edits (injuries, limiters, goals, availability, etc.)
  - workout building (delegated to the workout builder prompt + LLM)

Implements the "act first, confirm after" philosophy from the v3 plan.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from coach.context_builder import render_context_for_prompt
from coach.llm_client import get_llm
from config import settings
from db.logs import log_event
from db.profile import update_profile_fields
from intervals.workouts import (
    bulk_create_workouts,
    create_workout_from_dict,
    delete_workout,
    list_workouts,
    move_workout,
    update_workout,
)

logger = logging.getLogger(__name__)


# ── Tool definitions exposed to the executor LLM ───────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_workout",
        "description": (
            "Create and schedule a fully structured workout on the athlete's calendar. "
            "Use this whenever the athlete asks you to add, schedule, build, or generate a session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "sport": {
                    "type": "string",
                    "description": "Intervals sport code: Run, Ride, Swim, WeightTraining, etc.",
                },
                "description": {"type": "string"},
                "date": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "warmup|interval|rest|cooldown|steady|note"},
                            "duration_seconds": {"type": ["integer", "null"]},
                            "distance_meters": {"type": ["integer", "null"]},
                            "target_type": {
                                "type": "string",
                                "description": "ftp_percent|power|zone|hr|hr_zone|hr_percent|lthr_percent|pace|pace_zone|rpe|cadence|open",
                            },
                            "target_low": {"type": ["number", "null"]},
                            "target_high": {"type": ["number", "null"]},
                            "zone": {"type": ["integer", "null"], "description": "1-7"},
                            "ramp": {"type": ["boolean", "null"], "description": "True for ramp steps"},
                            "cadence_low": {"type": ["integer", "null"], "description": "rpm"},
                            "cadence_high": {"type": ["integer", "null"], "description": "rpm"},
                            "notes": {"type": "string"},
                            "repeat": {"type": "integer", "description": "Set same value on ALL steps in a repeated block"},
                        },
                    },
                },
                "gym_sets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "exercise": {"type": "string"},
                            "sets": {"type": "integer"},
                            "reps": {"type": "string"},
                            "load": {"type": "string"},
                            "rest_seconds": {"type": "integer"},
                            "notes": {"type": "string"},
                        },
                    },
                },
                "planned_tss": {"type": "number"},
                "planned_duration_seconds": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "color": {"type": "string"},
            },
            "required": ["title", "sport", "date"],
        },
    },
    {
        "name": "bulk_create_workouts",
        "description": (
            "Create multiple workouts in one go. Use for week-long or block-long plans. "
            "Each item is a full workout object with the same shape as create_workout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workouts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of workout objects (same schema as create_workout).",
                }
            },
            "required": ["workouts"],
        },
    },
    {
        "name": "list_workouts",
        "description": (
            "List planned workouts in a date range. Use this to find an event_id when the "
            "athlete refers to a session by date or type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "oldest": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "newest": {"type": "string", "description": "ISO YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "move_workout",
        "description": "Reschedule an existing workout to a different date/time. Use update_workout if you also need to change the structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": ["integer", "string"]},
                "new_date": {
                    "type": "string",
                    "description": "ISO datetime YYYY-MM-DDTHH:MM:SS (include time if athlete specified one, e.g. 2026-04-26T08:00:00)",
                },
            },
            "required": ["event_id", "new_date"],
        },
    },
    {
        "name": "update_workout",
        "description": "Patch an existing workout's name, description, or other fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": ["integer", "string"]},
                "updates": {"type": "object"},
            },
            "required": ["event_id", "updates"],
        },
    },
    {
        "name": "delete_workout",
        "description": "Delete a single workout from the calendar by its event_id.",
        "input_schema": {
            "type": "object",
            "properties": {"event_id": {"type": ["integer", "string"]}},
            "required": ["event_id"],
        },
    },
    {
        "name": "update_profile",
        "description": (
            "Update one or more fields on the athlete's profile. Allowed fields: "
            "name, age, sports, primary_sport, available_days, hours_per_week, "
            "goal_event, goal_date, goal_type, goal_time_target, current_injuries, "
            "limiters, preferred_long_day, preferred_intensity, experience_level, "
            "equipment, email, timezone, notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"updates": {"type": "object"}},
            "required": ["updates"],
        },
    },
]


# ── Main loop ──────────────────────────────────────────────────────────────


async def handle_tool_request(
    *,
    telegram_id: str,
    user_text: str,
    context: dict[str, Any],
    history: list[dict[str, str]],
    max_iterations: int = 6,
) -> str:
    """
    Run an agentic tool-use loop until the LLM stops calling tools.
    Returns the final user-facing reply text.
    """
    tool_executor_prompt = settings.load_prompt("tool_executor")
    workout_builder_prompt = settings.load_prompt("workout_builder")
    coach_personality = settings.load_prompt("coach_personality")
    context_block = render_context_for_prompt(context)

    system = (
        coach_personality.strip()
        + "\n\n"
        + tool_executor_prompt.strip()
        + "\n\nWORKOUT BUILDING REFERENCE (for create_workout / bulk_create_workouts arguments):\n"
        + workout_builder_prompt.strip()
        + "\n\n──────── CONTEXT (refreshed on every message) ────────\n"
        + context_block
    )

    # We keep messages in a generic form and convert to provider format
    # inside LLMClient. For Anthropic tool_use, we replay tool_use blocks
    # via 'assistant_tool_use' / 'tool' roles.
    messages: list[dict[str, Any]] = []
    for h in history[-8:]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_text})

    llm = get_llm()
    final_text = ""
    last_tool_summary = ""

    for iteration in range(max_iterations):
        result = await llm.chat(
            job="executor",
            system=system,
            messages=messages,
            tools=TOOLS,
        )

        text = result.get("text") or ""
        tool_calls = result.get("tool_calls") or []

        if not tool_calls:
            final_text = text.strip() or last_tool_summary
            break

        # Append the assistant turn (text + tool_calls) so the next request
        # can replay it correctly for either Anthropic or OpenAI-compatible
        # providers. The converters in llm_client.py reconstruct provider-
        # specific shapes from these generic fields.
        messages.append({
            "role": "assistant_tool_use",
            "content": text or "",
            "tool_calls": tool_calls,
        })

        # Execute each tool call and feed the results back.
        for call in tool_calls:
            tool_result = await _execute_tool(call, telegram_id)
            last_tool_summary = tool_result.get("summary", last_tool_summary)
            messages.append({
                "role": "tool",
                "tool_use_id": call["id"],
                "content": json.dumps(tool_result["result"]),
            })

    if not final_text:
        final_text = last_tool_summary or "✓ Done."

    return final_text


# ── Tool dispatch ───────────────────────────────────────────────────────────


async def _execute_tool(call: dict[str, Any], telegram_id: str) -> dict[str, Any]:
    name = call["name"]
    args = call["arguments"] or {}
    try:
        if name == "create_workout":
            res = await create_workout_from_dict(args)
            summary = f"✓ {args.get('title', 'workout')} on {args.get('date')}."
            return {"result": res, "summary": summary}

        if name == "bulk_create_workouts":
            workouts = args.get("workouts") or []
            res = await bulk_create_workouts(workouts)
            summary = f"✓ Created {res.get('count', len(workouts))} workouts."
            return {"result": res, "summary": summary}

        if name == "list_workouts":
            res = await list_workouts(args.get("oldest"), args.get("newest"))
            return {"result": res, "summary": f"Found {len(res)} workout(s)."}

        if name == "move_workout":
            res = await move_workout(int(args["event_id"]), args["new_date"])
            summary = f"✓ Moved event {args['event_id']} to {args['new_date']}."
            return {"result": res, "summary": summary}

        if name == "update_workout":
            res = await update_workout(int(args["event_id"]), args.get("updates", {}))
            summary = f"✓ Updated event {args['event_id']}."
            return {"result": res, "summary": summary}

        if name == "delete_workout":
            res = await delete_workout(int(args["event_id"]))
            summary = f"✓ Deleted event {args['event_id']}."
            return {"result": res, "summary": summary}

        if name == "update_profile":
            updates = args.get("updates") or {}
            res = await update_profile_fields(telegram_id, updates)
            await log_event(
                "profile_update",
                f"Profile fields updated: {list(updates.keys())}",
                metadata={"telegram_id": telegram_id, "fields": list(updates.keys())},
            )
            summary = f"✓ Profile updated: {', '.join(updates.keys())}."
            return {"result": {"ok": True, "fields": list(updates.keys())}, "summary": summary}

        return {
            "result": {"ok": False, "error": f"unknown tool: {name}"},
            "summary": f"⚠️ Unknown tool: {name}",
        }
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        await log_event(
            "error",
            f"Tool {name} raised: {exc}",
            severity="error",
            metadata={"tool": name, "args": args},
        )
        return {
            "result": {"ok": False, "error": str(exc)},
            "summary": f"⚠️ {name} failed: {exc}",
        }