"""
Periodically build the observability dashboard and push it to GitHub Pages.

Wired into APScheduler in `main.py`. Runs in three modes:

  1. Periodic (every N minutes by default) — rebuild + commit if changed.
  2. On-demand (`flush_now()`) — for manual flushes from a /admin command.
  3. Idempotent — only commits when the rendered logs.json content actually
     changed, to avoid noisy commit history.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

from github import Github, GithubException
from github.InputGitTreeElement import InputGitTreeElement

from config import settings
from db.logs import log_event
from observability.dashboard_builder import build_dashboard, metrics_summary_text

logger = logging.getLogger(__name__)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _github_push(files: dict[str, str], commit_message: str) -> dict[str, Any]:
    """
    Synchronously commit the given files to GitHub.

    `files` is a mapping of repo-relative path → text content. Multi-file
    commits are done in a single tree so we get one atomic commit per flush.
    Returns a status dict.
    """
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO:
        return {"ok": False, "reason": "github_not_configured"}

    try:
        gh = Github(settings.GITHUB_TOKEN)
        repo = gh.get_repo(settings.GITHUB_REPO)
        branch_name = settings.GITHUB_BRANCH

        ref = repo.get_git_ref(f"heads/{branch_name}")
        latest_sha = ref.object.sha
        base_commit = repo.get_git_commit(latest_sha)
        base_tree = base_commit.tree

        # Skip if every file is unchanged on the remote.
        unchanged = 0
        for repo_path, content in files.items():
            try:
                existing = repo.get_contents(repo_path, ref=branch_name)
                if isinstance(existing, list):
                    continue
                remote = base64.b64decode(existing.content).decode("utf-8", errors="replace")
                if remote.strip() == content.strip():
                    unchanged += 1
            except GithubException as exc:
                if exc.status != 404:
                    raise
        if unchanged == len(files):
            return {"ok": True, "skipped": True, "reason": "no_changes"}

        elements = [
            InputGitTreeElement(
                path=repo_path,
                mode="100644",
                type="blob",
                content=content,
            )
            for repo_path, content in files.items()
        ]
        new_tree = repo.create_git_tree(elements, base_tree)
        new_commit = repo.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[base_commit],
        )
        ref.edit(new_commit.sha)

        return {"ok": True, "commit_sha": new_commit.sha}
    except Exception as exc:
        logger.exception("GitHub push failed")
        return {"ok": False, "error": str(exc)}


async def flush_now(window_days: int = 7) -> dict[str, Any]:
    """Build dashboard locally and push to GitHub. Returns a status dict."""
    paths = await build_dashboard(window_days=window_days)

    html_path: Path = paths["html"]
    json_path: Path = paths["json"]

    html_content = _read_text(html_path)
    json_content = _read_text(json_path)

    files = {
        "docs/index.html": html_content,
        "docs/logs.json": json_content,
    }

    summary = metrics_summary_text(__import__("json").loads(json_content))
    commit_message = f"chore(observability): refresh dashboard ({summary})"

    result = await asyncio.to_thread(_github_push, files, commit_message)

    if result.get("ok") and not result.get("skipped"):
        await log_event(
            "dashboard_pushed",
            f"Dashboard pushed to {settings.GITHUB_REPO}@{settings.GITHUB_BRANCH} ({summary})",
            severity="info",
            metadata={"commit_sha": result.get("commit_sha"), "summary": summary},
        )
    elif result.get("ok") and result.get("skipped"):
        await log_event(
            "dashboard_unchanged",
            "Dashboard unchanged — skipping commit",
            severity="info",
            metadata={"summary": summary},
        )
    else:
        await log_event(
            "dashboard_push_failed",
            f"Dashboard push failed: {result.get('error') or result.get('reason')}",
            severity="error",
            metadata=result,
        )

    return result


async def scheduled_flush() -> None:
    """Entry-point used by APScheduler."""
    try:
        await flush_now()
    except Exception as exc:
        logger.exception("Scheduled flush crashed")
        await log_event(
            "dashboard_push_failed",
            f"Scheduled flush crashed: {exc}",
            severity="error",
        )
