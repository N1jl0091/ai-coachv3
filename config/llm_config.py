"""
Swappable LLM configuration.

Balanced setup:
  - reasoning: gpt-4o       — coaching quality matters here
  - executor:  gpt-4o-mini  — structured tool calls, schemas do the work
  - router:    gpt-4o-mini  — just classifies intent, 50 tokens
  - analysis:  gpt-4o-mini  — fixed email format, fires once per run

Estimated cost: ~$1.50/month. $5 credit lasts ~3 months.
"""
from __future__ import annotations

LLM_JOBS = {
    "reasoning": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_tokens": 2000,
    },
    # Used for bulk workout / training block generation — needs gpt-4o to
    # reliably produce a full week of structured workouts in one call.
    "planning": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.3,
        "max_tokens": 4000,
    },
    "executor": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 1500,
    },
    "router": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens": 50,
    },
    "analysis": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.6,
        "max_tokens": 1500,
    },
}

PROVIDER_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai":    "https://api.openai.com/v1",
    "groq":      "https://api.groq.com/openai/v1",
    "ollama":    "http://localhost:11434/v1",
}

def get_job(job: str) -> dict:
    if job not in LLM_JOBS:
        raise KeyError(f"Unknown LLM job: {job!r}. Defined: {list(LLM_JOBS.keys())}")
    return LLM_JOBS[job]