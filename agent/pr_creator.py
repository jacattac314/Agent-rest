"""
Pull Request Creator
--------------------
Creates a GitHub pull request for a feature implementation plan branch.
Uses the GitHub REST API via the `requests` library.

The PR is always opened as a **draft** so a human developer can review
the plan, assign the work, and promote it to ready when appropriate.

Configuration (config/settings.toml [github] section or environment vars):
  token       — GitHub personal access token with `repo` scope
                (falls back to GITHUB_TOKEN environment variable)
  repo        — Target repository in 'owner/repo' format
  base_branch — Branch to merge into (default: "main")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


@dataclass
class PullRequestResult:
    success: bool
    pr_url: str = ""
    pr_number: int = 0
    error: str = ""


class PullRequestCreator:
    """
    Opens a GitHub pull request via the REST API.

    The PR body is populated with the full Markdown implementation plan so
    reviewers have all the context they need in one place.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, config: dict):
        github_cfg = config.get("github", {})
        self.token: str = github_cfg.get("token") or os.environ.get("GITHUB_TOKEN", "")
        self.repo: str = github_cfg.get("repo", "")          # e.g. "myorg/my-repo"
        self.base_branch: str = github_cfg.get("base_branch", "main")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_pr(
        self,
        branch: str,
        title: str,
        plan_path: str,
        task_description: str,
    ) -> PullRequestResult:
        """
        Open a draft pull request from `branch` → `base_branch`.

        Args:
            branch: Source branch that contains the plan commit.
            title: Pull request title.
            plan_path: Path to the Markdown implementation plan (used as PR body).
            task_description: Short description for the PR body intro paragraph.

        Returns:
            PullRequestResult with success status, PR URL, and PR number.
        """
        if not REQUESTS_AVAILABLE:
            return PullRequestResult(
                success=False,
                error="requests library not installed. Run: pip install requests",
            )

        if not self.token:
            return PullRequestResult(
                success=False,
                error=(
                    "No GitHub token configured. "
                    "Set [github] token in settings.toml or export GITHUB_TOKEN."
                ),
            )

        if not self.repo:
            return PullRequestResult(
                success=False,
                error=(
                    "No GitHub repository configured. "
                    "Set [github] repo = 'owner/repo' in settings.toml."
                ),
            )

        body = self._build_pr_body(plan_path, task_description)

        try:
            resp = requests.post(
                f"{self.BASE_URL}/repos/{self.repo}/pulls",
                json={
                    "title": title,
                    "head": branch,
                    "base": self.base_branch,
                    "body": body,
                    "draft": True,
                },
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "autonomous-maintenance-agent/1.0",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            pr_url = data.get("html_url", "")
            pr_number = data.get("number", 0)
            logger.info("Draft pull request created: %s", pr_url)
            return PullRequestResult(success=True, pr_url=pr_url, pr_number=pr_number)

        except requests.HTTPError as exc:
            error_msg = (
                f"GitHub API error {exc.response.status_code}: "
                f"{exc.response.text[:300]}"
            )
            logger.error("PR creation failed: %s", error_msg)
            return PullRequestResult(success=False, error=error_msg)
        except Exception as exc:
            logger.error("PR creation failed: %s", exc)
            return PullRequestResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_pr_body(self, plan_path: str, task_description: str) -> str:
        try:
            plan_content = Path(plan_path).read_text(encoding="utf-8")
        except Exception:
            plan_content = "*Could not read implementation plan file.*"

        return (
            f"## Feature Implementation Plan\n\n"
            f"**Description:** {task_description}\n\n"
            f"---\n\n"
            f"{plan_content}\n\n"
            f"---\n\n"
            f"*This draft PR was opened automatically by the Autonomous Maintenance Agent.*  \n"
            f"*Review the plan above, assign a developer, and mark as ready when implementation begins.*"
        )
