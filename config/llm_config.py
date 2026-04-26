"""
Swappable LLM configuration.
Touch one line to swap models.

Current setup: all jobs on gpt-4o-mini (OpenAI, pay-as-you-go).
Estimated cost for a personal coaching bot: ~$1-3/month.

To upgrade analysis quality later:
  "analysis": {"provider": "openai", "model": "gpt-4o-mini", ...}  ← current
  "analysis": {"provider": "openai", "model": "gpt-5.4-mini", ...} ← upgrade
"""
from __future__ import annotations

LLM_JOBS = {
    # Intent classification + tool pre-selection — must be fast and cheap.
    "router": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens": 50,
    },
    # Calendar/profile tool calls — needs solid instruction following.
    "executor": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 1500,
    },
    # Open coaching conversations — personality and nuance.
    "reasoning": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_tokens": 2000,
    },
    # Post-activity email — fires once per Strava upload.
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