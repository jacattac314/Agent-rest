"""
Implementation Planner
----------------------
Uses the Claude Messages API to produce a structured, Markdown implementation
plan for an IMPLEMENT_MISSING_FEATURE task.

GitHub search results (code files and repositories) are injected as context so
Claude can reference real-world prior art when drafting the plan.

The finished plan is saved to reports/plans/<task-slug>.md and the path is
returned to the caller so it can be embedded in a pull request body.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import anthropic

from .github_search import GitHubSearchResults
from .identifier import MaintenanceTask

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior software architect creating an implementation plan for a missing feature.

Given:
  1. The feature request description and its location in the codebase
  2. Relevant GitHub search results (code examples and repositories for prior art)

Produce a detailed, actionable Markdown implementation plan with these sections:

  ## Summary
  One-paragraph overview of what will be built.

  ## Motivation
  Why this feature is needed and what problem it solves.

  ## Design
  High-level architecture, key data structures, and public interfaces.

  ## Implementation Steps
  Numbered checklist of concrete coding tasks, in dependency order.

  ## References
  Links to relevant GitHub code files and repositories found during research.

  ## Risks & Mitigations
  Known risks and how to address them.

Keep the plan concise, actionable, and grounded in the codebase context provided.
"""


class ImplementationPlanner:
    """
    Generates a detailed implementation plan for an IMPLEMENT_MISSING_FEATURE task
    by combining codebase context with GitHub search results via the Claude API.
    """

    def __init__(self, config: dict):
        self.client = anthropic.Anthropic()
        self.model = config["agent"].get("model", "claude-opus-4-6")
        output_dir = config.get("reporting", {}).get("output_dir", "reports")
        self.plans_dir = Path(output_dir) / "plans"
        self.plans_dir.mkdir(parents=True, exist_ok=True)

    def create_plan(
        self,
        task: MaintenanceTask,
        search_results: GitHubSearchResults,
        repo_root: str,
    ) -> str:
        """
        Generate and persist an implementation plan for the given task.

        Args:
            task: The IMPLEMENT_MISSING_FEATURE task to plan.
            search_results: GitHub research results to include as context.
            repo_root: Absolute path to the repository root.

        Returns:
            Path to the saved Markdown plan file.
        """
        user_message = self._build_user_message(task, search_results, repo_root)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            plan_text = response.content[0].text
        except anthropic.APIError as exc:
            logger.error("Claude API call failed during plan generation: %s", exc)
            plan_text = (
                f"# Implementation Plan — {task.title}\n\n"
                f"**Error:** Could not generate plan via Claude API: {exc}\n\n"
                f"**Feature description:** {task.description}\n"
            )

        slug = re.sub(r"[^a-z0-9]+", "-", task.id.lower()).strip("-")[:60]
        plan_path = self.plans_dir / f"{slug}.md"
        plan_path.write_text(plan_text, encoding="utf-8")
        logger.info("Implementation plan saved: %s", plan_path)
        return str(plan_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        task: MaintenanceTask,
        search_results: GitHubSearchResults,
        repo_root: str,
    ) -> str:
        lines = [
            "## Feature Request\n",
            f"**Title:** {task.title}\n",
            f"**Description:**\n{task.description}\n",
            f"**Repository:** `{repo_root}`",
            f"**File:** `{task.file_path}` (line {task.line})\n",
            "## GitHub Research Results\n",
        ]

        if search_results.error:
            lines.append(f"*GitHub search failed: {search_results.error}*\n")
        else:
            lines.append(f"**Search query used:** `{search_results.query}`\n")

            if search_results.repo_results:
                lines.append("### Relevant Repositories")
                for repo in search_results.repo_results:
                    lines.append(
                        f"- [{repo.full_name}]({repo.html_url}) "
                        f"⭐{repo.stars} · {repo.language}"
                        + (f" — {repo.description}" if repo.description else "")
                    )
                lines.append("")

            if search_results.code_results:
                lines.append("### Relevant Code Files")
                for code in search_results.code_results:
                    lines.append(
                        f"- [{code.repo_full_name}/{code.file_path}]({code.html_url})"
                    )
                lines.append("")

            if not search_results.repo_results and not search_results.code_results:
                lines.append("*No relevant results found on GitHub.*\n")

        return "\n".join(lines)
