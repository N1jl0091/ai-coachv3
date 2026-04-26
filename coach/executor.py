"""
Executor — agentic tool-use loop.

Architecture: two-stage to minimise tokens sent to the expensive 70b model.

  Stage 1 — Tool selector (8b, ~200 tokens):
    Reads the message + calendar and returns the 1-2 tool names needed.
    Costs almost nothing. Prevents sending all 10 tool schemas every call.

  Stage 2 — Executor (70b, ~600-900 tokens):
    Receives ONLY the selected tool schemas + ultra-compact context.
    Runs the agentic loop (usually 1-2 iterations for simple ops).

Expected token budget per interaction:
  Simple edit/move:    ~1000-1300 tokens total
  Create workout:      ~1500-2000 tokens (includes workout builder ref)
  Multi-tool (list+update): ~1500 tokens
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from coach.context_builder import render_minimal_context_for_prompt
from coach.llm_client import get_llm
from config import settings
from db.logs import log_event
from db.profile import update_profile_fields
from intervals.client import get_client
from intervals.workouts import (
    bulk_create_workouts,
    create_workout_from_dict,
    delete_workout,
    list_workouts,
    move_workout,
    update_workout,
)

logger = logging.getLogger(__name__)


# ── Compressed tool schemas ────────────────────────────────────────────────
# One entry per tool. Descriptions are terse — the model knows what these
# operations mean. Verbose descriptions add tokens, not understanding.

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_workouts": {
        "name": "list_workouts",
        "description": "List planned workouts to find event_id",
        "input_schema": {
            "type": "object",
            "properties": {
                "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                "newest": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    "create_workout": {
        "name": "create_workout",
        "description": "Create and schedule a structured workout",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "sport": {"type": "string", "description": "Run|Ride|Swim|WeightTraining|etc"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "description": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "warmup|interval|rest|cooldown|steady"},
                            "duration_seconds": {"type": ["integer", "null"]},
                            "distance_meters": {"type": ["integer", "null"]},
                            "target_type": {"type": "string", "description": "pace_zone|ftp_percent|power|zone|hr_zone|hr|hr_percent|lthr_percent|pace|rpe|open"},
                            "target_low": {"type": ["number", "null"]},
                            "target_high": {"type": ["number", "null"]},
                            "zone": {"type": ["integer", "null"]},
                            "ramp": {"type": ["boolean", "null"]},
                            "cadence_low": {"type": ["integer", "null"]},
                            "cadence_high": {"type": ["integer", "null"]},
                            "notes": {"type": "string"},
                            "repeat": {"type": "integer"},
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
                        },
                    },
                },
                "planned_tss": {"type": "number"},
                "planned_duration_seconds": {"type": "integer"},
                "color": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "sport", "date"],
        },
    },
    "bulk_create_workouts": {
        "name": "bulk_create_workouts",
        "description": "Create multiple workouts (training blocks)",
        "input_schema": {
            "type": "object",
            "properties": {
                "workouts": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["workouts"],
        },
    },
    "move_workout": {
        "name": "move_workout",
        "description": "Reschedule a workout",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": ["integer", "string"]},
                "new_date": {"type": "string", "description": "YYYY-MM-DDTHH:MM:SS"},
            },
            "required": ["event_id", "new_date"],
        },
    },
    "update_workout": {
        "name": "update_workout",
        "description": "Edit an existing workout. updates fields: name, description, start_date_local (ISO datetime), steps (array), color, moving_time, icu_training_load",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": ["integer", "string"]},
                "updates": {"type": "object"},
            },
            "required": ["event_id", "updates"],
        },
    },
    "delete_workout": {
        "name": "delete_workout",
        "description": "Delete a calendar event",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": ["integer", "string"]},
            },
            "required": ["event_id"],
        },
    },
    "update_profile": {
        "name": "update_profile",
        "description": "Update athlete profile fields (injuries, goals, availability, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {"updates": {"type": "object"}},
            "required": ["updates"],
        },
    },
    "get_wellness": {
        "name": "get_wellness",
        "description": "Fetch detailed wellness/sleep/HRV (call only if needed)",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "1-7"}},
        },
    },
    "get_recent_activities": {
        "name": "get_recent_activities",
        "description": "Fetch recent completed activities (call only if needed)",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "1-14"}},
        },
    },
    "get_sport_settings": {
        "name": "get_sport_settings",
        "description": "Fetch FTP, zones, threshold pace (call only if needed)",
        "input_schema": {"type": "object", "properties": {}},
    },
}

# All tool names — used by selector
ALL_TOOLS = list(TOOL_SCHEMAS.keys())

# Tools that always come as a pair (need list first to find id)
_ALWAYS_WITH_LIST = {"update_workout", "move_workout", "delete_workout"}

# Regex for cheap tool pre-selection (avoid 8b call when obvious)
_TOOL_PATTERNS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\b(move|reschedule|postpone|shift)\b", re.I), ["move_workout"]),
    (re.compile(r"\b(delete|remove|cancel|scrap)\b", re.I), ["delete_workout"]),
    (re.compile(r"\b(injur|hurt|pull|tweak|sick|ill|travel|away|my goal|my race)\b", re.I), ["update_profile"]),
    (re.compile(r"\b(block|week|programme|program|multiple|series)\b", re.I), ["bulk_create_workouts"]),
    (re.compile(r"\b(create|add|schedule|new workout|new session|build me)\b", re.I), ["create_workout"]),
    (re.compile(r"\b(edit|update|change|alter|modify|structure|restructure|redo)\b", re.I), ["update_workout"]),
]


# ── Inline system prompt ───────────────────────────────────────────────────
# Intentionally terse. The model knows what these operations mean.
# Every token here is paid on EVERY executor call — keep it minimal.

_EXECUTOR_SYSTEM = """\
Coaching assistant. Act immediately, confirm with ✓ in one sentence.
- Running: use pace_zone targets (Z1 Pace…Z5 Pace). Never ftp_percent for runs.
- Cycling: use ftp_percent or zone.
- If event_id unknown, call list_workouts first.
- Resolve relative dates to ISO from the datetime in context.
- steps array on update_workout replaces the full workout structure."""


# ── Tool selector ──────────────────────────────────────────────────────────

_SELECTOR_SYSTEM = (
    "Return a comma-separated list of tool names needed to fulfil the request. "
    "Choose from: " + ", ".join(ALL_TOOLS) + ". "
    "No explanation. Examples: 'update_workout' / 'list_workouts,update_workout' / 'create_workout'"
)


async def _select_tools(user_text: str, context_summary: str) -> list[str]:
    """
    Stage 1: cheap 8b call that returns which tool(s) are needed.
    Falls back to regex heuristics first to avoid even this call.
    """
    # Regex shortcuts — free.
    for pattern, tools in _TOOL_PATTERNS:
        if pattern.search(user_text):
            # If it's an edit/move/delete and the calendar context has event IDs,
            # we can skip list_workouts — the executor will find the id directly.
            selected = list(tools)
            if selected[0] in _ALWAYS_WITH_LIST:
                # Add list_workouts as a fallback the executor can call if needed
                # by including it in the schema pool, not forcing it upfront.
                pass
            return selected

    # LLM selector — 8b, ultra cheap.
    try:
        llm = get_llm()
        result = await llm.chat(
            job="router",  # 8b model
            system=_SELECTOR_SYSTEM,
            messages=[{"role": "user", "content": f"Request: {user_text}\n\nCalendar:\n{context_summary}"}],
        )
        raw = (result.get("text") or "").strip().lower()
        selected = [t.strip() for t in raw.split(",") if t.strip() in TOOL_SCHEMAS]
        if selected:
            return selected
    except Exception as exc:
        logger.warning("Tool selector failed, using all tools: %s", exc)

    # Fallback: send all tools (safe but expensive).
    return ALL_TOOLS


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
    Two-stage agentic executor. Returns the final reply string.
    """
    context_block = render_minimal_context_for_prompt(context)

    # Stage 1: select tools (regex or 8b — very cheap).
    selected_tools = await _select_tools(user_text, context_block)

    # Always include list_workouts in the pool — it costs 1 schema if unused
    # but saves a whole extra LLM call when the executor needs it to look up an id.
    if "list_workouts" not in selected_tools:
        selected_tools = ["list_workouts"] + selected_tools

    # Build the schema list for only the selected tools.
    tools = [TOOL_SCHEMAS[t] for t in selected_tools if t in TOOL_SCHEMAS]

    # Append workout builder reference only for create/bulk operations.
    needs_builder = any(t in selected_tools for t in ("create_workout", "bulk_create_workouts"))
    builder_section = ""
    if needs_builder:
        builder_section = "\n\nWORKOUT SCHEMA:\n" + settings.load_prompt("workout_builder").strip()

    system = _EXECUTOR_SYSTEM + builder_section + "\n\nCONTEXT:\n" + context_block

    # Seed messages: last 2 turns from history (not 8) + current message.
    messages: list[dict[str, Any]] = []
    for h in history[-4:]:
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
            tools=tools,
        )

        text = result.get("text") or ""
        tool_calls = result.get("tool_calls") or []

        if not tool_calls:
            final_text = text.strip() or last_tool_summary
            break

        messages.append({
            "role": "assistant_tool_use",
            "content": text or "",
            "tool_calls": tool_calls,
        })

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
            return {"result": res, "summary": f"✓ {args.get('title', 'workout')} on {args.get('date')}."}

        if name == "bulk_create_workouts":
            workouts = args.get("workouts") or []
            res = await bulk_create_workouts(workouts)
            return {"result": res, "summary": f"✓ Created {res.get('count', len(workouts))} workouts."}

        if name == "list_workouts":
            res = await list_workouts(args.get("oldest"), args.get("newest"))
            return {"result": res, "summary": f"Found {len(res)} workout(s)."}

        if name == "move_workout":
            res = await move_workout(int(args["event_id"]), args["new_date"])
            return {"result": res, "summary": f"✓ Moved event {args['event_id']} to {args['new_date']}."}

        if name == "update_workout":
            res = await update_workout(int(args["event_id"]), args.get("updates", {}))
            return {"result": res, "summary": f"✓ Updated event {args['event_id']}."}

        if name == "delete_workout":
            res = await delete_workout(int(args["event_id"]))
            return {"result": res, "summary": f"✓ Deleted event {args['event_id']}."}

        if name == "update_profile":
            updates = args.get("updates") or {}
            res = await update_profile_fields(telegram_id, updates)
            await log_event(
                "profile_update",
                f"Profile fields updated: {list(updates.keys())}",
                metadata={"telegram_id": telegram_id, "fields": list(updates.keys())},
            )
            return {"result": {"ok": True, "fields": list(updates.keys())}, "summary": f"✓ Profile updated: {', '.join(updates.keys())}."}

        if name == "get_wellness":
            from datetime import date, timedelta
            days = min(int(args.get("days") or 1), 7)
            client = get_client()
            today = date.today().isoformat()
            if days <= 1:
                res = await client.get_wellness(today)
            else:
                oldest = (date.today() - timedelta(days=days)).isoformat()
                res = await client.get_wellness_range(oldest, today)
            return {"result": res, "summary": f"Wellness data fetched ({days} day(s))."}

        if name == "get_recent_activities":
            from datetime import date, timedelta
            days = min(int(args.get("days") or 7), 14)
            client = get_client()
            oldest = (date.today() - timedelta(days=days)).isoformat()
            res = await client.get_activities(oldest, date.today().isoformat())
            return {"result": res, "summary": f"Fetched {len(res)} activities over {days} days."}

        if name == "get_sport_settings":
            client = get_client()
            athlete = await client.get_athlete()
            return {"result": (athlete or {}).get("sportSettings") or [], "summary": "Sport settings fetched."}

        return {"result": {"ok": False, "error": f"unknown tool: {name}"}, "summary": f"⚠️ Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool %s failed", name)
        await log_event("error", f"Tool {name} raised: {exc}", severity="error", metadata={"tool": name, "args": args})
        return {"result": {"ok": False, "error": str(exc)}, "summary": f"⚠️ {name} failed: {exc}"}