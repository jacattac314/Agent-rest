"""
Multi-Agent Orchestrator
------------------------
Central controller that drives the full maintenance pipeline through a
well-defined state machine.  Coordinates four agent roles:

  ┌──────────────────────────────────────────────────────┐
  │                   Orchestrator                        │
  │                                                      │
  │  Planner ──► Executor(s) ──► Verifier ──► Summary   │
  │     ↕              ↕              ↕                  │
  │              SharedState (single source of truth)    │
  └──────────────────────────────────────────────────────┘

Session lifecycle
-----------------
  CREATED → PLANNING → EXECUTING → VERIFYING → COMPLETED | FAILED

The orchestrator applies the following governance rules from config:
  • auto_execute_min_impact  – only tasks with impact ≥ N are run autonomously
  • human_review_min_risk    – tasks with risk ≥ N are deferred for human review
  • max_tasks_per_cycle      – cap on tasks executed per session
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from .executor import TaskExecutor
from .git_integration import GitIntegration
from .planner import PlannerAgent
from .scanner import RepositoryScanner
from .state import AgentRole, SessionStatus, SharedState, TaskStatus
from .verifier import VerifierAgent

logger = logging.getLogger(__name__)

ORCHESTRATOR_ID = "orchestrator-001"

# Human-readable session state dump location
_STATE_DIR = "logs/sessions"


class MultiAgentOrchestrator:
    """
    Drives one maintenance session through the full Planner → Executor →
    Verifier pipeline and returns a structured summary.

    Parameters
    ----------
    config : dict
        Loaded settings.toml as a Python dict.
    repo_root : str
        Absolute path to the repository being maintained.
    """

    def __init__(self, config: dict, repo_root: str) -> None:
        self.config = config
        self.repo_root = str(Path(repo_root).resolve())

        pri_cfg = config.get("prioritizer", {})
        self._auto_min_impact: int = pri_cfg.get("auto_execute_min_impact", 3)
        self._human_min_risk: int = pri_cfg.get("human_review_min_risk", 8)
        self._max_tasks: int = pri_cfg.get("max_tasks_per_cycle", 5)

        self._scanner = RepositoryScanner(config)
        self._planner = PlannerAgent(config)
        self._executor = TaskExecutor(config)
        self._verifier = VerifierAgent(config)
        self._git = GitIntegration(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_on_stars(self, progress_cb=None) -> list[dict]:
        """
        Fetch the authenticated user's GitHub starred repositories, clone each
        one, and run a maintenance session against it.

        Parameters
        ----------
        progress_cb : callable(repo_full_name, status, summary) | None
            Optional callback invoked after each repo is processed.

        Returns
        -------
        List of per-repo summary dicts, each with an added 'repo' key.
        """
        from .github_stars import GitHubStarsClient

        client = GitHubStarsClient(self.config)
        execute = self.config.get("github", {}).get("execute_tasks", False)

        repos = client.fetch_starred()
        cloned, clone_errors = client.clone_all(repos)

        summaries: list[dict] = []

        for repo in cloned:
            repo_summary: dict = {"repo": repo.full_name, "html_url": repo.html_url}
            try:
                # Run only scan+plan when execute_tasks=False (read-only mode)
                if execute:
                    orig_root = self.repo_root
                    self.repo_root = repo.local_path
                    summary = self.run()
                    self.repo_root = orig_root
                else:
                    summary = self._scan_only(repo.local_path)

                repo_summary.update(summary)
                logger.info(
                    "[orchestrator] %s → %d tasks identified",
                    repo.full_name,
                    summary.get("tasks_identified", 0),
                )
            except Exception as exc:
                repo_summary["error"] = str(exc)
                logger.warning("[orchestrator] failed on %s: %s", repo.full_name, exc)
            finally:
                if progress_cb:
                    progress_cb(repo.full_name, repo_summary.get("status", "error"), repo_summary)

            summaries.append(repo_summary)

        client.cleanup(cloned)

        if clone_errors:
            logger.warning("[orchestrator] clone errors: %s", clone_errors)

        return summaries

    def _scan_only(self, repo_path: str) -> dict:
        """
        Run scanner + planner in read-only mode (no execution, no commits).
        Returns a lightweight summary suitable for reporting.
        """
        state = SharedState.new_session(repo_path, goal="read-only scan")
        snapshot = self._scanner.scan(repo_path)
        state.record_metric("files_scanned", len(snapshot.files))
        state.record_metric("doc_gaps_found", len(snapshot.doc_gaps))
        state.record_metric("code_smells_found", len(snapshot.code_smells))

        self._planner.plan(snapshot, state)
        state.transition(SessionStatus.COMPLETED)
        self._save_state(state)

        plan_counts: dict[str, int] = {}
        for t in state.plan:
            kind = t.get("kind", "unknown")
            plan_counts[kind] = plan_counts.get(kind, 0) + 1

        return {
            "session_id": state.session_id,
            "tasks_identified": len(state.plan),
            "tasks_approved": 0,
            "tasks_succeeded": 0,
            "tasks_failed": 0,
            "tasks_deferred": 0,
            "tasks_skipped": len(state.plan),
            "deferred_titles": [],
            "task_results": [],
            "metrics": state.metrics,
            "plan_by_kind": plan_counts,
            "status": SessionStatus.COMPLETED,
        }

    def run(self) -> dict:
        """
        Execute a complete maintenance session and return a summary dict.

        The summary matches the shape expected by MaintenanceCycle / AuditLogger
        so existing reporting infrastructure works without changes.
        """
        state = SharedState.new_session(self.repo_root)
        state.add_message(
            ORCHESTRATOR_ID,
            AgentRole.ORCHESTRATOR,
            f"Session {state.session_id} started for {self.repo_root}",
        )

        try:
            self._phase_plan(state)
            self._phase_execute(state)
            self._phase_verify(state)
            state.transition(SessionStatus.COMPLETED)
        except Exception as exc:
            logger.error("[orchestrator] session %s failed: %s", state.session_id, exc, exc_info=True)
            state.transition(SessionStatus.FAILED)
            state.add_message(
                ORCHESTRATOR_ID,
                AgentRole.ORCHESTRATOR,
                f"Session aborted: {exc}",
                error=str(exc),
            )
        finally:
            self._save_state(state)

        return self._build_summary(state)

    # ------------------------------------------------------------------
    # Pipeline phases
    # ------------------------------------------------------------------

    def _phase_plan(self, state: SharedState) -> None:
        """Phase 1 – Planner builds the task graph."""
        state.transition(SessionStatus.PLANNING)
        snapshot = self._scanner.scan(self.repo_root)
        state.record_metric("files_scanned", len(snapshot.files))
        state.record_metric("doc_gaps_found", len(snapshot.doc_gaps))
        state.record_metric("code_smells_found", len(snapshot.code_smells))

        self._planner.plan(snapshot, state)

        state.add_message(
            ORCHESTRATOR_ID,
            AgentRole.ORCHESTRATOR,
            f"Planning complete. {len(state.plan)} tasks in queue.",
        )

    def _phase_execute(self, state: SharedState) -> None:
        """Phase 2 – Executor processes approved tasks."""
        state.transition(SessionStatus.EXECUTING)

        approved, deferred, skipped = self._classify_tasks(state)
        state.record_metric("tasks_approved", len(approved))
        state.record_metric("tasks_deferred", len(deferred))
        state.record_metric("tasks_skipped", len(skipped))

        state.add_message(
            ORCHESTRATOR_ID,
            AgentRole.ORCHESTRATOR,
            f"Execution plan: {len(approved)} approved, "
            f"{len(deferred)} deferred (high risk), "
            f"{len(skipped)} skipped (low impact or cap reached).",
            approved=[t["task_id"] for t in approved],
            deferred=[t["task_id"] for t in deferred],
        )

        for task_dict in deferred:
            state.update_task(
                task_dict["task_id"],
                status=TaskStatus.DEFERRED,
                result={"reason": f"risk_score={task_dict['risk_score']} >= threshold={self._human_min_risk}"},
            )

        for task_dict in skipped:
            state.update_task(task_dict["task_id"], status=TaskStatus.SKIPPED)

        executor_id = f"executor-{str(uuid.uuid4())[:6]}"
        for task_dict in approved:
            task_id = task_dict["task_id"]
            state.update_task(task_id, status=TaskStatus.IN_PROGRESS, assigned_to=executor_id)

            state.add_message(
                executor_id,
                AgentRole.EXECUTOR,
                f"Starting task {task_id!r}: {task_dict['title']}",
                task_id=task_id,
            )

            exec_result = self._executor.execute(
                _dict_to_maintenance_task(task_dict),
                self.repo_root,
            )

            if exec_result.success:
                commit_info = None
                if exec_result.files_modified:
                    commit_info = self._git.commit_task(
                        repo_root=self.repo_root,
                        task_id=task_dict["task_id"],
                        task_title=task_dict["title"],
                        files_modified=exec_result.files_modified,
                        summary=exec_result.summary or "",
                    )

                state.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    result={
                        "success": True,
                        "files_modified": exec_result.files_modified,
                        "summary": exec_result.summary,
                        "branch": commit_info.branch if commit_info else None,
                        "commit_sha": commit_info.commit_sha if commit_info else None,
                    },
                )
                state.add_message(
                    executor_id,
                    AgentRole.EXECUTOR,
                    f"Task {task_id!r} succeeded. Files: {exec_result.files_modified}",
                    task_id=task_id,
                    files=exec_result.files_modified,
                )
            else:
                state.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    result={"success": False, "error": exec_result.error or "unknown"},
                )
                state.add_message(
                    executor_id,
                    AgentRole.EXECUTOR,
                    f"Task {task_id!r} failed: {exec_result.error}",
                    task_id=task_id,
                    error=exec_result.error,
                )

    def _phase_verify(self, state: SharedState) -> None:
        """Phase 3 – Verifier validates all completed tasks."""
        state.transition(SessionStatus.VERIFYING)

        results = self._verifier.verify_all_completed(state)
        passed = sum(1 for ok in results.values() if ok)
        total = len(results)

        state.record_metric("verification_passed", passed)
        state.record_metric("verification_total", total)

        state.add_message(
            ORCHESTRATOR_ID,
            AgentRole.ORCHESTRATOR,
            f"Verification complete: {passed}/{total} tasks verified successfully.",
        )

    # ------------------------------------------------------------------
    # Task classification (governance policy)
    # ------------------------------------------------------------------

    def _classify_tasks(
        self, state: SharedState
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """
        Partition pending tasks into three buckets:
          approved  – will be executed autonomously
          deferred  – high-risk, needs human review
          skipped   – low-impact or over per-cycle cap
        """
        pending = state.get_pending_tasks()

        approved: list[dict] = []
        deferred: list[dict] = []
        skipped: list[dict] = []

        for task in pending:
            risk = task.get("risk_score", 0)
            impact = task.get("impact_score", 0)

            if risk >= self._human_min_risk:
                deferred.append(task)
            elif impact < self._auto_min_impact:
                skipped.append(task)
            elif len(approved) >= self._max_tasks:
                skipped.append(task)
            else:
                approved.append(task)

        return approved, deferred, skipped

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _build_summary(self, state: SharedState) -> dict:
        counts = state.plan_summary()
        task_results = []
        for t in state.plan:
            if t.get("status") in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                result = t.get("result", {})
                task_results.append({
                    "task_id": t["task_id"],
                    "task_title": t["title"],
                    "success": result.get("success", False),
                    "files_modified": result.get("files_modified", []),
                    "summary": result.get("summary", ""),
                    "error": result.get("error"),
                    "branch": result.get("branch"),
                    "commit_sha": result.get("commit_sha"),
                })

        deferred_titles = [
            t["title"] for t in state.plan if t.get("status") == TaskStatus.DEFERRED
        ]

        return {
            "session_id": state.session_id,
            "tasks_identified": len(state.plan),
            "tasks_approved": counts.get(TaskStatus.IN_PROGRESS, 0) + counts.get(TaskStatus.COMPLETED, 0) + counts.get(TaskStatus.FAILED, 0),
            "tasks_succeeded": counts.get(TaskStatus.COMPLETED, 0),
            "tasks_failed": counts.get(TaskStatus.FAILED, 0),
            "tasks_deferred": counts.get(TaskStatus.DEFERRED, 0),
            "tasks_skipped": counts.get(TaskStatus.SKIPPED, 0),
            "deferred_titles": deferred_titles,
            "task_results": task_results,
            "metrics": state.metrics,
            "status": state.status,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self, state: SharedState) -> None:
        try:
            path = Path(_STATE_DIR) / f"session_{state.session_id}.json"
            state.save(path)
            logger.debug("[orchestrator] state saved to %s", path)
        except Exception as exc:
            logger.warning("[orchestrator] could not save session state: %s", exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dict_to_maintenance_task(d: dict):
    """Convert a TaskNode dict back to a MaintenanceTask-compatible object."""
    from .identifier import MaintenanceTask, TaskKind

    kind_str = d.get("kind", "address_todo")
    try:
        kind = TaskKind(kind_str)
    except ValueError:
        kind = TaskKind.ADDRESS_TODO

    task = MaintenanceTask(
        id=d["task_id"],
        kind=kind,
        title=d["title"],
        description=d.get("description", ""),
        file_path=d.get("file_path") or None,
        line=d.get("line") or None,
    )
    task.impact_score = d.get("impact_score", 5)
    task.risk_score = d.get("risk_score", 3)
    task.priority = d.get("priority", task.impact_score * (10 - task.risk_score))
    task.context = d.get("context", {})
    return task
