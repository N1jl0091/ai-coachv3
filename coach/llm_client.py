"""
Unified LLM client.

One async interface for four providers:
  - anthropic  (native SDK, supports tool use)
  - openai     (native SDK, supports function calling)
  - groq       (OpenAI-compatible)
  - ollama     (OpenAI-compatible, local)

A "job" name (reasoning, executor, router, analysis) drives provider+model
selection through `config/llm_config.py`. Swap models in one line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from coach import LLMError
from config import settings
from config.llm_config import PROVIDER_URLS, get_job
from db.logs import log_event

logger = logging.getLogger(__name__)


class LLMClient:
    """Async unified client across providers."""

    # ── Public API ──────────────────────────────────────────────────────────

    async def chat(
        self,
        job: str,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Unified chat call.

        Returns a dict:
            {
              "text": str,                      # text the model produced (may be empty if tool-only)
              "tool_calls": list[dict],         # [{"id":..., "name":..., "arguments": dict}, ...]
              "raw": Any,                       # provider-specific response
              "model": str,
              "usage": {"in": int, "out": int},
              "latency_ms": int,
            }
        """
        cfg = get_job(job)
        provider = cfg["provider"]
        model = cfg["model"]
        temperature = cfg["temperature"] if temperature is None else temperature
        max_tokens = cfg["max_tokens"] if max_tokens is None else max_tokens

        start = time.perf_counter()
        try:
            if provider == "anthropic":
                result = await self._call_anthropic(
                    model, system, messages, temperature, max_tokens, tools, tool_choice
                )
            elif provider in ("openai", "groq", "ollama"):
                result = await self._call_openai_compatible(
                    provider, model, system, messages,
                    temperature, max_tokens, tools, tool_choice, json_mode,
                )
            else:
                raise LLMError(f"Unknown provider: {provider!r}")
        except LLMError:
            raise
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            await log_event(
                "llm_call",
                f"{provider}/{model} ({job}) failed: {exc}",
                job=job,
                model_used=model,
                latency_ms=elapsed,
                severity="error",
            )
            raise LLMError(str(exc)) from exc

        elapsed = int((time.perf_counter() - start) * 1000)
        result["latency_ms"] = elapsed
        result["model"] = model

        await log_event(
            "llm_call",
            f"{provider}/{model} ({job})",
            job=job,
            model_used=model,
            latency_ms=elapsed,
            tokens_in=result["usage"]["in"],
            tokens_out=result["usage"]["out"],
        )
        return result

    # ── Anthropic ───────────────────────────────────────────────────────────

    async def _call_anthropic(
        self,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        tool_choice: Any,
    ) -> dict[str, Any]:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not set")

        # Imported lazily to keep boot-time clean if a provider isn't installed.
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        kwargs: dict[str, Any] = dict(
            model=model,
            system=system,
            messages=_anthropic_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc

        text_chunks: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_chunks.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input or {},
                })

        usage = response.usage
        return {
            "text": "".join(text_chunks).strip(),
            "tool_calls": tool_calls,
            "raw": response,
            "usage": {
                "in": getattr(usage, "input_tokens", 0) or 0,
                "out": getattr(usage, "output_tokens", 0) or 0,
            },
        }

    # ── OpenAI / Groq / Ollama ──────────────────────────────────────────────

    async def _call_openai_compatible(
        self,
        provider: str,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        tool_choice: Any,
        json_mode: bool,
    ) -> dict[str, Any]:
        from openai import AsyncOpenAI

        if provider == "openai":
            api_key = settings.OPENAI_API_KEY
            base_url = PROVIDER_URLS["openai"]
        elif provider == "groq":
            api_key = settings.GROQ_API_KEY
            base_url = PROVIDER_URLS["groq"]
        else:  # ollama
            api_key = "ollama"
            base_url = settings.OLLAMA_BASE_URL or PROVIDER_URLS["ollama"]

        if provider == "openai" and not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        if provider == "groq" and not api_key:
            raise LLMError("GROQ_API_KEY is not set")

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        # Build the messages array with the system prompt up front.
        oai_messages: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            oai_messages.append({"role": m["role"], "content": m["content"]})

        kwargs: dict[str, Any] = dict(
            model=model,
            messages=oai_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = _openai_tools(tools)
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"{provider} call failed: {exc}") from exc

        msg = response.choices[0].message
        text = msg.content or ""
        tool_calls: list[dict] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            })

        usage = response.usage
        return {
            "text": text.strip() if text else "",
            "tool_calls": tool_calls,
            "raw": response,
            "usage": {
                "in": getattr(usage, "prompt_tokens", 0) or 0,
                "out": getattr(usage, "completion_tokens", 0) or 0,
            },
        }


# ─── helpers ───────────────────────────────────────────────────────────────


def _anthropic_messages(messages: list[dict[str, str]]) -> list[dict]:
    """
    Convert a generic role/content list into Anthropic's content block format.
    Tool-result entries (role='tool', tool_use_id=..., content=...) are
    wrapped in the structured form Anthropic expects.
    """
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": m["tool_use_id"],
                        "content": m["content"],
                    }
                ],
            })
        elif role == "assistant_tool_use":
            # Replay an assistant tool_use turn.
            out.append({"role": "assistant", "content": m["content"]})
        else:
            out.append({"role": role, "content": m["content"]})
    return out


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    """Tools come in OpenAI-style; convert to Anthropic schema."""
    out: list[dict] = []
    for t in tools:
        spec = t.get("function", t)
        out.append({
            "name": spec["name"],
            "description": spec.get("description", ""),
            "input_schema": spec.get("parameters") or spec.get("input_schema") or {
                "type": "object",
                "properties": {},
            },
        })
    return out


def _openai_tools(tools: list[dict]) -> list[dict]:
    """Already in OpenAI format if the caller provided 'function' wrapped specs."""
    out: list[dict] = []
    for t in tools:
        if "function" in t:
            out.append(t)
        else:
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters") or t.get("input_schema") or {
                        "type": "object",
                        "properties": {},
                    },
                },
            })
    return out


# Singleton convenience.
_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton
