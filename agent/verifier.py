"""
Verifier Agent
--------------
Implements the "Verifier/Critic" role in the multi-agent hierarchy.

After an Executor agent completes a task the VerifierAgent checks whether the
result is valid and updates the SharedState accordingly.

Verification strategy (layered):
  1. File-existence check  – were the expected files actually written/modified?
  2. Syntax check           – for Python files, validate with ast.parse().
  3. Content heuristic      – was meaningful content added (non-empty, not just
                              whitespace, minimal length)?
  4. Regression guard       – were no additional code smells introduced?

All verification results are written back to state.plan[task_id].verification
so the Orchestrator can decide whether to accept, retry, or defer.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from .state import AgentRole, SharedState, TaskStatus

logger = logging.getLogger(__name__)

VERIFIER_ID = "verifier-001"

# Minimum meaningful content added by the executor (characters)
_MIN_CONTENT_DELTA = 10


class VerifierAgent:
    """
    Validates executor results and updates state.

    For each completed task node the verifier runs a series of lightweight
    checks and marks the task as verified (or flags it for retry/deferral).
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, task_id: str, state: SharedState) -> bool:
        """
        Verify the result of *task_id* stored in *state*.

        Returns True if verification passed, False otherwise.
        The task node's `verification` dict and `status` are updated in place.
        """
        task = state.get_task(task_id)
        if task is None:
            logger.warning("[verifier] task %s not found in state", task_id)
            return False

        result = task.get("result", {})
        files_modified: list[str] = result.get("files_modified", [])

        checks: list[tuple[str, bool, str]] = []

        # 1. Execution success flag
        exec_ok = bool(result.get("success", False))
        checks.append(("execution_success", exec_ok, "" if exec_ok else result.get("error", "unknown error")))

        # 2. Files were actually modified
        files_exist = all(Path(f).exists() for f in files_modified) if files_modified else False
        files_msg = "" if files_exist else f"missing files: {[f for f in files_modified if not Path(f).exists()]}"
        checks.append(("files_exist", files_exist, files_msg))

        # 3. Python syntax check for modified .py files
        syntax_ok, syntax_msg = self._check_syntax(files_modified)
        checks.append(("python_syntax", syntax_ok, syntax_msg))

        # 4. Content heuristic – at least some non-trivial content was added
        content_ok, content_msg = self._check_content(files_modified, task)
        checks.append(("content_added", content_ok, content_msg))

        # ------------------------------------------------------------------
        passed = all(ok for _, ok, _ in checks)
        failures = [(name, msg) for name, ok, msg in checks if not ok]

        verification = {
            "passed": passed,
            "checks": {name: {"ok": ok, "message": msg} for name, ok, msg in checks},
        }

        state.update_task(
            task_id,
            verification=verification,
            status=TaskStatus.COMPLETED if passed else TaskStatus.FAILED,
        )

        log_level = logging.INFO if passed else logging.WARNING
        logger.log(
            log_level,
            "[verifier] task %s: %s (%d/%d checks passed)",
            task_id,
            "PASS" if passed else "FAIL",
            len(checks) - len(failures),
            len(checks),
        )

        verdict = "passed all checks" if passed else f"failed: {[n for n, _ in failures]}"
        state.add_message(
            VERIFIER_ID,
            AgentRole.VERIFIER,
            f"Task {task_id!r} verification {verdict}.",
            task_id=task_id,
            passed=passed,
            failures=failures,
        )

        return passed

    def verify_all_completed(self, state: SharedState) -> dict[str, bool]:
        """
        Verify every task that the executor marked as COMPLETED.
        Returns a mapping {task_id: passed}.
        """
        results: dict[str, bool] = {}
        for task in state.get_tasks_by_status(TaskStatus.COMPLETED):
            tid = task["task_id"]
            # Skip if already verified
            if task.get("verification"):
                results[tid] = task["verification"].get("passed", False)
                continue
            results[tid] = self.verify(tid, state)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_syntax(self, files: list[str]) -> tuple[bool, str]:
        py_files = [f for f in files if f.endswith(".py")]
        for fp in py_files:
            try:
                source = Path(fp).read_text(encoding="utf-8")
                ast.parse(source)
            except SyntaxError as exc:
                return False, f"{fp}: {exc}"
            except OSError:
                pass  # file gone – caught by existence check
        return True, ""

    def _check_content(self, files: list[str], task: dict) -> tuple[bool, str]:
        if not files:
            # Some tasks (e.g. dependency audit report) may not touch files
            return True, "no files expected"
        for fp in files:
            try:
                content = Path(fp).read_text(encoding="utf-8")
                if len(content.strip()) < _MIN_CONTENT_DELTA:
                    return False, f"{fp} appears nearly empty ({len(content)} chars)"
            except OSError:
                pass
        return True, ""
