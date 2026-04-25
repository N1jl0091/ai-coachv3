"""
Swappable LLM configuration.

Three named jobs map to provider+model. Touch one line to swap models.
"""

from __future__ import annotations

LLM_JOBS: dict[str, dict] = {
    # Complex reasoning: planning, analysis, multi-step thinking, free chat.
    "reasoning": {
        "provider": "anthropic",          # anthropic | openai | groq | ollama
        "model": "claude-opus-4-6",
        "temperature": 0.7,
        "max_tokens": 2000,
    },

    # Tool execution: Intervals writes, profile edits, calendar ops.
    "executor": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "temperature": 0.2,               # low temp: precise, deterministic
        "max_tokens": 1500,
    },

    # Lightweight router/classifier — cheap, fast, just picks an intent label.
    "router": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "temperature": 0.0,
        "max_tokens": 50,
    },

    # Post-activity email analysis.
    "analysis": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.6,
        "max_tokens": 1500,
    },
}

# Provider base URLs (Anthropic + OpenAI use SDK defaults; Groq/Ollama are OpenAI-compatible).
PROVIDER_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",   # overridden by OLLAMA_BASE_URL env
}


def get_job(job: str) -> dict:
    """Return the config dict for a named job. Raises if the job isn't defined."""
    if job not in LLM_JOBS:
        raise KeyError(
            f"Unknown LLM job: {job!r}. Defined jobs: {list(LLM_JOBS.keys())}"
        )
    return LLM_JOBS[job]
