"""
Git Integration
---------------
Wraps GitPython to provide safe, agent-friendly git operations:
  - Create a per-task branch off the current HEAD
  - Stage only the files modified by the executor
  - Commit with a structured message
  - Return branch info so the caller can open a PR later (if desired)

All operations are conservative: the module never force-pushes, never
touches the main/master branch, and always validates before committing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from git import GitCommandError, InvalidGitRepositoryError, Repo
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


@dataclass
class CommitInfo:
    branch: str
    commit_sha: str
    files_committed: list[str]
    message: str


class GitIntegration:
    """
    Lightweight git helper for the maintenance agent.
    """

    def __init__(self, config: dict):
        self.auto_commit: bool = config["git"].get("auto_commit", True)
        self.branch_prefix: str = config["git"].get("branch_prefix", "agent/maintenance")
        self.author_name: str = config["git"].get("author_name", "Autonomous Maintenance Agent")
        self.author_email: str = config["git"].get("author_email", "agent@maintenance.bot")

    def commit_task(
        self,
        repo_root: str,
        task_id: str,
        task_title: str,
        files_modified: list[str],
        summary: str,
    ) -> CommitInfo | None:
        """
        Stage `files_modified`, then commit to a new branch named after the task.
        Returns None when auto_commit is disabled, git is unavailable, or there
        is nothing to commit.
        """
        if not self.auto_commit:
            logger.info("auto_commit=false; skipping git commit for task %s", task_id)
            return None

        if not GIT_AVAILABLE:
            logger.warning("gitpython not installed; skipping git commit")
            return None

        if not files_modified:
            logger.info("No files modified for task %s; nothing to commit", task_id)
            return None

        try:
            repo = Repo(repo_root, search_parent_directories=True)
        except InvalidGitRepositoryError:
            logger.warning("Not a git repository: %s", repo_root)
            return None

        # Build a safe branch name
        slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:50]
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        branch_name = f"{self.branch_prefix}/{slug}-{ts}"

        try:
            # Save original branch before switching so we can restore it on rollback
            original_branch = repo.active_branch

            # Create and checkout a new branch from current HEAD
            new_branch = repo.create_head(branch_name)
            new_branch.checkout()

            # Stage only the files the executor touched
            existing = [
                f for f in files_modified
                if (Path(repo.working_tree_dir) / f).exists()
            ]
            if not existing:
                logger.info("No existing modified files to stage for task %s", task_id)
                original_branch.checkout()
                repo.delete_head(branch_name, force=True)
                return None

            repo.index.add(existing)

            # Check if there's actually a diff staged
            if not repo.index.diff("HEAD") and not repo.untracked_files:
                logger.info("Nothing staged for task %s", task_id)
                original_branch.checkout()
                repo.delete_head(branch_name, force=True)
                return None

            commit_message = (
                f"chore(agent): {task_title}\n\n"
                f"Task ID: {task_id}\n"
                f"{summary}\n\n"
                f"Files changed:\n"
                + "\n".join(f"  - {f}" for f in existing)
                + "\n\nApplied by Autonomous Maintenance Agent"
            )

            actor = repo.config_reader()
            author_name = self.author_name
            author_email = self.author_email

            commit = repo.index.commit(
                commit_message,
                author=_make_actor(author_name, author_email),
                committer=_make_actor(author_name, author_email),
            )

            logger.info("Committed task %s → branch %s (%s)", task_id, branch_name, commit.hexsha[:8])
            return CommitInfo(
                branch=branch_name,
                commit_sha=commit.hexsha,
                files_committed=existing,
                message=commit_message,
            )

        except GitCommandError as exc:
            logger.error("Git error for task %s: %s", task_id, exc)
            return None


def _make_actor(name: str, email: str):
    """Return a git.Actor-like object compatible with GitPython."""
    try:
        from git import Actor
        return Actor(name, email)
    except ImportError:
        return None
