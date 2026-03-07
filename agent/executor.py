"""
Task Executor
-------------
The heart of the agentic loop.  Uses the Claude Agent SDK (claude-agent-sdk)
to autonomously implement each approved MaintenanceTask.

The Agent SDK gives the sub-agent access to real file-system tools
(Read, Write, Edit, Glob, Grep, Bash) so it can discover, understand,
and modify the target repository without any hand-holding.

Execution contract
  - One SDK session per task
  - The agent works inside the target repo (cwd=repo_root)
  - Permission mode: "acceptEdits" — file edits are auto-approved
  - Bash commands still prompt unless bypassPermissions is enabled
  - Max turns and budget_usd guard against runaway sessions
  - On completion the executor returns an ExecutionResult with the list
    of files modified (extracted from audit log via a PostToolUse hook)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import anyio

try:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        SystemMessage,
        query,
    )
    AGENT_SDK_AVAILABLE = True
except ImportError:
    AGENT_SDK_AVAILABLE = False

from .identifier import MaintenanceTask, TaskKind

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    task_id: str
    success: bool
    files_modified: list[str] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None


def _build_prompt(task: MaintenanceTask, repo_root: str) -> str:
    """Construct the agent prompt for a given maintenance task."""
    base = f"""You are an autonomous maintenance agent working inside the repository at `{repo_root}`.

## Your Task
**Kind:** {task.kind.value}
**Title:** {task.title}

## Description
{task.description}

## Target File
{task.file_path or "(See description — may span multiple files)"}
{f"Starting at line: {task.line}" if task.line else ""}

## Instructions
1. Read the relevant file(s) to understand the existing code.
2. Implement the requested change carefully and correctly.
3. Do NOT change any logic, behaviour, or public API unless the task explicitly asks for it.
4. Keep changes minimal and focused.
5. After making changes, do a final read of the modified file(s) to verify correctness.
6. Summarise what you did in 2-3 sentences.

Begin now.
"""
    # Append task-kind-specific guidance
    if task.kind == TaskKind.ADD_README:
        base += """
## README Guidelines
- Start with a one-line project description
- Include: Installation, Usage, Configuration, Contributing, License sections
- Use clear Markdown formatting
- Base the content on what you discover by reading the existing source files
"""
    elif task.kind in (TaskKind.ADD_FUNCTION_DOCSTRING, TaskKind.ADD_MODULE_DOCSTRING, TaskKind.ADD_CLASS_DOCSTRING):
        base += """
## Docstring Guidelines
- Use the Google-style docstring format
- For functions: describe Args, Returns, and Raises where applicable
- Keep it concise — one sentence summary + details only where non-obvious
"""
    elif task.kind == TaskKind.ADD_TYPE_HINTS:
        base += """
## Type Hint Guidelines
- Add return type annotations only; do not change parameter names or defaults
- Use `from __future__ import annotations` if not already present for forward refs
- Prefer built-in generics (list, dict, tuple) over typing module equivalents (Python 3.9+)
"""
    elif task.kind == TaskKind.AUDIT_DEPENDENCIES:
        base += """
## Dependency Audit Guidelines
- Read the manifest file carefully
- Identify: unpinned packages, overly broad version ranges, known-deprecated packages
- Write a brief audit report as a comment block or inline in a new DEPENDENCY_AUDIT.md file
- Do NOT modify the actual dependency file unless the fix is trivial and safe
"""
    return base


class TaskExecutor:
    """
    Executes a single MaintenanceTask using the Claude Agent SDK.
    Falls back to a no-op stub when the SDK is not installed.
    """

    ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

    def __init__(self, config: dict):
        self.max_turns: int = config["agent"].get("max_turns", 30)
        self.max_budget_usd: float = config["agent"].get("max_budget_usd", 2.0)
        self.model: str = config["agent"].get("model", "claude-opus-4-6")

    def execute(self, task: MaintenanceTask, repo_root: str) -> ExecutionResult:
        """Blocking wrapper around the async _run coroutine."""
        return anyio.run(self._run, task, repo_root)

    async def _run(self, task: MaintenanceTask, repo_root: str) -> ExecutionResult:
        result = ExecutionResult(task_id=task.id)

        if not AGENT_SDK_AVAILABLE:
            result.success = False
            result.error = (
                "claude-agent-sdk is not installed. "
                "Run: pip install claude-agent-sdk"
            )
            logger.error(result.error)
            return result

        files_modified: list[str] = []

        async def _track_edit(input_data, tool_use_id, context):
            fp = input_data.get("tool_input", {}).get("file_path")
            if fp and fp not in files_modified:
                files_modified.append(fp)
            return {}

        prompt = _build_prompt(task, repo_root)
        options = ClaudeAgentOptions(
            cwd=repo_root,
            allowed_tools=self.ALLOWED_TOOLS,
            permission_mode="acceptEdits",
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            model=self.model,
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Edit|Write", hooks=[_track_edit])
                ]
            },
        )

        summary_parts: list[str] = []
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    summary_parts.append(message.result)
        except Exception as exc:
            result.success = False
            result.error = str(exc)
            logger.exception("Agent SDK execution failed for task %s", task.id)
            return result

        result.success = True
        result.files_modified = files_modified
        result.summary = " ".join(summary_parts)
        result.finished_at = datetime.utcnow()
        logger.info(
            "Task %s completed — %d file(s) modified",
            task.id,
            len(files_modified),
        )
        return result
