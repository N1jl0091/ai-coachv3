"""
Swappable LLM configuration.

Three named jobs map to provider+model. Touch one line to swap models.
"""

from __future__ import annotations

LLM_JOBS = {
    "reasoning": {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.7,
        "max_tokens": 2000,
    },
    "executor": {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.2,
        "max_tokens": 1500,
    },
    "analysis": {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.6,
        "max_tokens": 1500,
    },
    "router": {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "temperature": 0.0,
        "max_tokens": 50,
    }
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
