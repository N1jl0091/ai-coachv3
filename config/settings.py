"""
Centralised settings — every env var is read here.

Values are loaded from `.env` (local) or the host environment (Railway).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve the project root so paths work regardless of CWD.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present (no-op in production).
load_dotenv(PROJECT_ROOT / ".env")


def _get(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"See .env.example for the full list."
        )
    return val


# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str | None = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_ID: str | None = _get("TELEGRAM_OWNER_ID")

# ── AI Providers ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str | None = _get("ANTHROPIC_API_KEY")
OPENAI_API_KEY: str | None = _get("OPENAI_API_KEY")
GROQ_API_KEY: str | None = _get("GROQ_API_KEY")
OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://localhost:11434/v1") or ""

# ── Intervals.icu ───────────────────────────────────────────────────────────
INTERVALS_API_KEY: str | None = _get("INTERVALS_API_KEY")
INTERVALS_ATHLETE_ID: str | None = _get("INTERVALS_ATHLETE_ID")

# ── Strava ──────────────────────────────────────────────────────────────────
STRAVA_CLIENT_ID: str | None = _get("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET: str | None = _get("STRAVA_CLIENT_SECRET")
STRAVA_VERIFY_TOKEN: str = _get("STRAVA_VERIFY_TOKEN", "ai-coach-verify") or "ai-coach-verify"

# ── Email ───────────────────────────────────────────────────────────────────
RESEND_API_KEY: str | None = _get("RESEND_API_KEY")
RESEND_FROM_EMAIL: str = _get("RESEND_FROM_EMAIL", "coach@example.com") or "coach@example.com"
RESEND_TO_EMAIL: str | None = _get("RESEND_TO_EMAIL")

# ── Database ────────────────────────────────────────────────────────────────
DATABASE_URL: str = (
    _get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_coach",
    )
    or ""
)
# Railway sometimes provides `postgres://` — SQLAlchemy + asyncpg wants
# `postgresql+asyncpg://`. Normalise here.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ── GitHub Pages push ───────────────────────────────────────────────────────
GITHUB_TOKEN: str | None = _get("GITHUB_TOKEN")
GITHUB_REPO: str | None = _get("GITHUB_REPO")
GITHUB_BRANCH: str = _get("GITHUB_BRANCH", "main") or "main"

# ── Runtime ─────────────────────────────────────────────────────────────────
PORT: int = int(_get("PORT", "8000") or "8000")
LOG_LEVEL: str = _get("LOG_LEVEL", "INFO") or "INFO"
ATHLETE_TIMEZONE: str = _get("ATHLETE_TIMEZONE", "Africa/Johannesburg") or "Africa/Johannesburg"

# ── Paths ───────────────────────────────────────────────────────────────────
PROMPTS_DIR: Path = PROJECT_ROOT / "config" / "prompts"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
LOGS_JSONL_PATH: Path = PROJECT_ROOT / "logs.jsonl"


def load_prompt(name: str) -> str:
    """Load a prompt template by file name (without extension)."""
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def validate_required() -> list[str]:
    """
    Return a list of missing-but-required env vars. Empty list = good to go.
    Used by main.py at startup to fail loud if anything critical is missing.
    """
    missing: list[str] = []
    required = [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("INTERVALS_API_KEY", INTERVALS_API_KEY),
        ("INTERVALS_ATHLETE_ID", INTERVALS_ATHLETE_ID),
        ("DATABASE_URL", DATABASE_URL),
    ]
    for name, value in required:
        if not value:
            missing.append(name)
    return missing
